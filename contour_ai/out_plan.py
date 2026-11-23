"""2D 지형도(CAD 평면 DXF)와 사이트 다이어그램(SVG) (v2.2).

- plan_2d.dxf : 실제 좌표(TM, m). 등고선(주곡선·계곡선, 높이 Z), 표고 글자, 건물 윤곽(층수별 레이어 + 층수 글자),
                길 면(차도·보도·교통섬 …), 길 경계선, 물, 녹지, 지적, 나무, 가로등, 대지 경계, 모델 범위, 항공사진(있으면).
                배치도·대상지 분석의 바탕으로 AutoCAD 등에서 바로 쓴다.
- site_diagram.svg : 같은 내용을 주제별 레이어(일러스트레이터·피그마에서 레이어로 보임)로, 모형 축척 mm 크기.
                무채색 + 대지 경계만 파랑.

등고선은 모델의 계단판 경계 그대로라 레이저 판·3D 모델과 같은 선이다.
"""
import base64
import io
import math
import os

import numpy as np
from shapely.ops import linemerge

from .geom_util import as_polys

PATH_KO = {"road": "차도", "sidewalk": "보도", "island": "교통섬", "stairs": "계단", "bike": "자전거도로"}
GREEN_KO = {"forest": "숲", "garden": "조경", "grass": "잔디"}


def _lines(g):
    """선·면 경계에서 LineString 목록."""
    if g is None or g.is_empty:
        return []
    if g.geom_type in ("Polygon", "MultiPolygon"):
        g = g.boundary
    if g.geom_type == "LineString":
        return [g]
    if g.geom_type in ("MultiLineString", "GeometryCollection"):
        out = []
        for x in g.geoms:
            out += _lines(x)
        return out
    return []


def contour_lines(regions, clip):
    """계단판 경계 = 그 높이의 등고선. 모델 테두리(범위 네모)를 따라가는 부분은 뺀다. [(LineString, 높이)]"""
    edge = clip.exterior.buffer(0.05)
    out = []
    for L in list(regions)[1:]:
        b = regions[L].boundary.difference(edge)
        merged = linemerge(_lines(b)) if not b.is_empty else b
        for ls in _lines(merged):
            if ls.length >= 2.0:
                out.append((ls, float(L)))
    return out


def is_index(L, interval):
    """계곡선(굵은 등고선): 간격 다섯 번째마다. 0.1m 단위 간격도 오차 없이 판단."""
    k = round(L / interval)
    return abs(L / interval - k) < 1e-6 and k % 5 == 0


def _label_point(ls):
    p = ls.interpolate(0.5, normalized=True)
    a = ls.interpolate(max(0.0, 0.5 * ls.length - 1.0)); b = ls.interpolate(min(ls.length, 0.5 * ls.length + 1.0))
    ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
    if ang > 90:
        ang -= 180
    elif ang < -90:
        ang += 180
    return p.x, p.y, ang


def plan_layers(st):
    """모델 단계 값(st)에서 2D 도면 재료를 모은다. 두 출력(DXF·SVG)이 같은 것을 그린다."""
    clip = st.clip
    lv = list(st.regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else float(getattr(st.a, "interval", 5) or 5)
    d = dict(clip=clip, interval=interval)
    d["contours"] = contour_lines(st.regions, clip)
    d["buildings"] = [(p, int(m["floors"]), float(m.get("base", 0.0)), float(m.get("top", 0.0)))
                      for m in st.masses for p in as_polys(m["poly"].intersection(clip))]
    d["paths"] = [(PATH_KO.get(k, k), p) for k, g in (getattr(st, "path_areas", None) or {}).items()
                  if g is not None for p in as_polys(g.intersection(clip))]
    d["edges"] = [ls for g in (getattr(st, "edge_lines", None) or []) for ls in _lines(g.intersection(clip))]
    d["water"] = [p for g in st.data.get("water_areas", ()) for p in as_polys(g.intersection(clip))]
    d["greens"] = [(GREEN_KO.get(k, k), p) for k, g in (getattr(st, "green_geom", None) or {}).items()
                   for p in as_polys(g.intersection(clip))]
    d["parcels"] = [p for g, _ in (getattr(st, "parcels", None) or []) if g is not None
                    for p in as_polys(g.intersection(clip))]
    bx0, by0, bx1, by1 = clip.bounds
    d["trees"] = [(x, y, r) for x, y, z, r, k in st.trees if bx0 <= x <= bx1 and by0 <= y <= by1]
    d["lights"] = [(t[0], t[1]) for t in st.lights]
    site = getattr(st, "site_poly", None)
    d["site"] = [p for p in as_polys(site.intersection(clip))] if site is not None else []
    return d


def _aerial(st):
    """항공사진(npz)이 있으면 (RGB 배열, 왼쪽 아래 x, y, 칸 크기)."""
    p = getattr(st.a, "aerial", None)
    if not p or not os.path.exists(str(p)):
        return None
    try:
        z = np.load(p)
        rgb = z["rgb"]; cell = float(z["cell"]); x0 = float(z["x0"]); y1 = float(z["y1"])
        return rgb, x0, y1 - rgb.shape[0] * cell, cell
    except Exception:
        return None


def _save_aerial_jpg(rgb, path, max_px=4000):
    from PIL import Image
    im = Image.fromarray(np.asarray(rgb, np.uint8))
    if max(im.size) > max_px:
        k = max_px / max(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)))
    im.save(path, quality=85)
    return im.size


# ---------------------------------------------------------------- DXF
def write_plan_dxf(path, d, aerial=None):
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.M
    doc.header["$INSUNITS"] = 6
    L = doc.layers

    def lay(name, color, lw=-3):
        if name not in L:
            L.add(name, color=color, lineweight=lw)
        return name
    msp = doc.modelspace()
    iv = d["interval"]
    th = max(1.0, min(4.0, (d["clip"].bounds[2] - d["clip"].bounds[0]) / 250))   # 글자 높이 (m)
    if aerial is not None:
        rgb, x0, y0, cell = aerial
        img = os.path.splitext(path)[0] + "_항공사진.jpg"
        w, h = _save_aerial_jpg(rgb, img)
        idef = doc.add_image_def(filename=os.path.basename(img), size_in_pixel=(w, h))
        msp.add_image(idef, insert=(x0, y0), size_in_units=(rgb.shape[1] * cell, rgb.shape[0] * cell),
                      dxfattribs={"layer": lay("항공사진", 8)})
    lay("범위", 8)
    msp.add_lwpolyline(list(d["clip"].exterior.coords), close=True, dxfattribs={"layer": "범위"})
    for name, p in d["greens"]:
        for r in [p.exterior, *p.interiors]:
            msp.add_lwpolyline(list(r.coords), close=True, dxfattribs={"layer": lay(f"녹지_{name}", 3)})
    for p in d["water"]:
        for r in [p.exterior, *p.interiors]:
            msp.add_lwpolyline(list(r.coords), close=True, dxfattribs={"layer": lay("물", 5)})
    for p in d["parcels"]:
        msp.add_lwpolyline(list(p.exterior.coords), close=True, dxfattribs={"layer": lay("지적", 9, 9)})
    for name, p in d["paths"]:
        for r in [p.exterior, *p.interiors]:
            msp.add_lwpolyline(list(r.coords), close=True, dxfattribs={"layer": lay(f"도로_{name}", 8)})
    for ls in d["edges"]:
        msp.add_lwpolyline(list(ls.coords), dxfattribs={"layer": lay("도로_경계선", 8)})
    for ls, z in d["contours"]:
        big = is_index(z, iv)
        name = lay("등고선_계곡선", 7, 35) if big else lay("등고선_주곡선", 8, 13)
        msp.add_lwpolyline(list(ls.coords), dxfattribs={"layer": name, "elevation": z})
        if big and ls.length > th * 12:
            x, y, ang = _label_point(ls)
            msp.add_text(f"{z:g}", height=th * 0.8, rotation=ang,
                         dxfattribs={"layer": lay("등고선_표고", 7)}).set_placement((x, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    for p, fl, base, top in d["buildings"]:
        name = lay(f"건물_{fl:02d}층", 7)
        msp.add_lwpolyline(list(p.exterior.coords), close=True, dxfattribs={"layer": name, "elevation": base})
        for r in p.interiors:
            msp.add_lwpolyline(list(r.coords), close=True, dxfattribs={"layer": name, "elevation": base})
        if p.area > th * th * 6:
            c = p.representative_point()
            msp.add_text(f"{fl}F", height=th * 0.7, dxfattribs={"layer": lay("건물_층수", 7)}
                         ).set_placement((c.x, c.y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    for x, y, r in d["trees"]:
        msp.add_circle((x, y), max(0.5, r), dxfattribs={"layer": lay("나무", 3)})
    for x, y in d["lights"]:
        msp.add_point((x, y), dxfattribs={"layer": lay("가로등", 2)})
    for p in d["site"]:
        for r in [p.exterior, *p.interiors]:
            msp.add_lwpolyline(list(r.coords), close=True, dxfattribs={"layer": lay("대지경계", 5, 50)})
    doc.saveas(path)
    return path


# ---------------------------------------------------------------- SVG
def write_diagram_svg(path, d, scale=1000, aerial=None):
    """주제별 레이어 SVG. 크기는 모형 축척 mm (1:scale). 일러스트레이터·피그마에서 <g> 가 레이어로 열린다."""
    x0, y0, x1, y1 = d["clip"].bounds
    k = 1000.0 / scale                      # m → mm
    W, H = (x1 - x0) * k, (y1 - y0) * k
    sw = max(0.08, W / 1500)               # 기본 선 굵기 (mm)

    def P(x, y):
        return f"{(x - x0) * k:.2f},{(y1 - y) * k:.2f}"

    def ring_d(coords, close=True):
        pts = [P(x, y) for x, y, *_ in coords]
        return "M" + " L".join(pts) + (" Z" if close else "")

    def poly_d(p):
        return " ".join(ring_d(r.coords) for r in [p.exterior, *p.interiors])
    out = [f'<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="{W:.1f}mm" height="{H:.1f}mm" '
           f'viewBox="0 0 {W:.2f} {H:.2f}">',
           f'<title>사이트 다이어그램 1:{scale}</title>',
           '<style>text{font-family:Pretendard,"Noto Sans KR",sans-serif}</style>',
           f'<rect width="{W:.2f}" height="{H:.2f}" fill="#ffffff"/>']

    def group(gid, label, body, attrs=""):
        if body:
            out.append(f'<g id="{gid}" inkscape:groupmode="layer" inkscape:label="{label}" {attrs}>')
            out.extend(body)
            out.append("</g>")
    if aerial is not None:
        rgb, ax0, ay0, cell = aerial
        from PIL import Image
        im = Image.fromarray(np.asarray(rgb, np.uint8))
        if max(im.size) > 2400:
            f_ = 2400 / max(im.size); im = im.resize((int(im.width * f_), int(im.height * f_)))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=80)
        ax1, ay1 = ax0 + rgb.shape[1] * cell, ay0 + rgb.shape[0] * cell
        group("aerial", "항공사진", [f'<image x="{(ax0 - x0) * k:.2f}" y="{(y1 - ay1) * k:.2f}" width="{(ax1 - ax0) * k:.2f}" '
                                   f'height="{(ay1 - ay0) * k:.2f}" preserveAspectRatio="none" '
                                   f'xlink:href="data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"/>'],
              'style="display:none"')
    group("green", "녹지", [f'<path d="{poly_d(p)}" fill="#e6e6e3" fill-rule="evenodd"/>' for _, p in d["greens"]])
    group("water", "물", [f'<path d="{poly_d(p)}" fill="#d4d4d4" fill-rule="evenodd"/>' for p in d["water"]])
    road = {"차도": "#9a9a98", "보도": "#ededeb", "교통섬": "#ededeb", "계단": "#d8d8d6", "자전거도로": "#cfcfcd"}
    names = list(dict.fromkeys(n for n, _ in d["paths"]))
    for i, n in enumerate(names):
        group(f"road{i}", f"도로_{n}", [f'<path d="{poly_d(p)}" fill="{road.get(n, "#cccccc")}" fill-rule="evenodd"/>'
                                        for m, p in d["paths"] if m == n])
    group("parcel", "지적", [f'<path d="{ring_d(p.exterior.coords)}" fill="none" stroke="#b5b5b5" stroke-width="{sw * 0.6:.3f}" '
                            f'stroke-dasharray="{sw * 4:.2f} {sw * 2:.2f}"/>' for p in d["parcels"]])
    iv = d["interval"]
    group("contour_minor", "등고선_주곡선", [f'<path d="{ring_d(ls.coords, False)}" fill="none" stroke="#9c9c9c" stroke-width="{sw * 0.6:.3f}"/>'
                                          for ls, z in d["contours"] if not is_index(z, iv)])
    group("contour_index", "등고선_계곡선", [f'<path d="{ring_d(ls.coords, False)}" fill="none" stroke="#4a4a4a" stroke-width="{sw * 1.2:.3f}"/>'
                                          for ls, z in d["contours"] if is_index(z, iv)])
    fs = max(1.2, W / 140)
    labels = []
    for ls, z in d["contours"]:
        if is_index(z, iv) and ls.length * k > fs * 12:
            x, y, ang = _label_point(ls)
            labels.append(f'<text x="{(x - x0) * k:.2f}" y="{(y1 - y) * k:.2f}" font-size="{fs:.2f}" fill="#4a4a4a" '
                          f'text-anchor="middle" dominant-baseline="middle" transform="rotate({-ang:.1f} {(x - x0) * k:.2f} {(y1 - y) * k:.2f})" '
                          f'stroke="#ffffff" stroke-width="{fs * 0.25:.2f}" paint-order="stroke">{z:g}</text>')
    group("contour_label", "등고선_표고", labels)
    fmax = max([fl for _, fl, _, _ in d["buildings"]] or [1])
    blds = []
    for p, fl, _, _ in sorted(d["buildings"], key=lambda b: b[1]):
        g = int(225 - 150 * min(1.0, fl / max(10, fmax)))     # 높을수록 진하게
        blds.append(f'<path d="{poly_d(p)}" fill="rgb({g},{g},{g})" stroke="#202020" stroke-width="{sw * 0.6:.3f}" '
                    f'fill-rule="evenodd"><title>{fl}층</title></path>')
    group("buildings", "건물", blds)
    group("trees", "나무", [f'<circle cx="{(x - x0) * k:.2f}" cy="{(y1 - y) * k:.2f}" r="{max(0.5, r) * k:.2f}" fill="none" '
                           f'stroke="#707070" stroke-width="{sw * 0.5:.3f}"/>' for x, y, r in d["trees"]])
    group("site", "대지경계", [f'<path d="{poly_d(p)}" fill="rgba(37,99,235,0.08)" stroke="#2563eb" stroke-width="{sw * 2.5:.3f}" '
                              f'fill-rule="evenodd"/>' for p in d["site"]])
    # 축척 막대 + 북쪽
    t = max(1.0, (x1 - x0) / 5)                               # 축척 막대: 폭의 1/5 쯤, 1·2·5 단위
    e = 10 ** math.floor(math.log10(t))
    bar = max(m * e for m in (1, 2, 5) if m * e <= t)
    bx, by = W * 0.04, H * 0.96
    out.append(f'<g id="legend" inkscape:groupmode="layer" inkscape:label="축척·방위">'
               f'<rect x="{bx:.2f}" y="{by - sw * 3:.2f}" width="{bar * k:.2f}" height="{sw * 3:.2f}" fill="#111"/>'
               f'<text x="{bx:.2f}" y="{by - sw * 6:.2f}" font-size="{fs:.2f}" fill="#111">0</text>'
               f'<text x="{bx + bar * k:.2f}" y="{by - sw * 6:.2f}" font-size="{fs:.2f}" fill="#111" text-anchor="middle">{bar:g} m</text>'
               f'<text x="{bx + bar * k + fs * 2:.2f}" y="{by:.2f}" font-size="{fs:.2f}" fill="#111">1:{scale}</text>'
               f'<path d="M{W * 0.95:.2f},{H * 0.04:.2f} l{fs * 0.8:.2f},{fs * 2.4:.2f} l{-fs * 1.6:.2f},0 Z" fill="#111"/>'
               f'<text x="{W * 0.95:.2f}" y="{H * 0.04 + fs * 3.6:.2f}" font-size="{fs:.2f}" fill="#111" text-anchor="middle">N</text></g>')
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path


def write_plan_outputs(out_dir, st, log=print):
    """2D 지형도 DXF + 사이트 다이어그램 SVG. 실패해도 모델은 그대로 (로그만)."""
    try:
        d = plan_layers(st)
        aerial = _aerial(st)
        write_plan_dxf(os.path.join(out_dir, "plan_2d.dxf"), d, aerial)
        write_diagram_svg(os.path.join(out_dir, "site_diagram.svg"), d, scale=int(st.a.scale or 1000), aerial=aerial)
        return dict(contours=len(d["contours"]), buildings=len(d["buildings"]), parcels=len(d["parcels"]))
    except Exception as e:
        log(f"   (2D 지형도·다이어그램을 만들지 못했습니다: {type(e).__name__}: {e})")
        return None

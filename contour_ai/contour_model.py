"""수치지형도(DXF) -> 건축 콘타 모델 (Rhino 3DM, STL, 레이저 커팅용 층별 DXF).

사용 예:
    python contour_model.py map.dxf --bbox 200900 553600 201400 554100 \
        --scale 1000 --sheet 5 --out out/
"""
import argparse
import json
import math
import os
import time
from collections import defaultdict

import ezdxf
import numpy as np
import rhino3dm as r3
import trimesh
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from shapely import make_valid
from shapely.geometry import (LineString, MultiLineString, MultiPolygon, Point,
                              Polygon, box)
from shapely.ops import polygonize, unary_union

from .progress import extrude, fill, low_trees, ribbon

# ---------------------------------------------------------------- 레이어 코드
CONTOUR_LAYERS = ("F0017111", "F0017114")          # 주곡선, 계곡선
SPOT_LAYERS = ("F0027217",)                        # 표고점
BUILDING_PREFIX = "B00141"                         # 건물 (B0014111~B0014118)
ROAD_PREFIXES = ("A001", "A002", "A003")           # 도로 관련 선
WALL_PREFIX = "B0024"                              # 담장
WATER_PREFIX = "E00"                               # 하천 등

# 층수 데이터가 없을 때 쓰는 임시 층수 (레이어별 추정)
FALLBACK_FLOORS = {
    "B0014111": 2, "B0014112": 4, "B0014113": 12, "B0014114": 2,
    "B0014115": 2, "B0014116": 3, "B0014117": 2, "B0014118": 1,
}
FALLBACK_FLOORS.update({"일반주택": 2, "연립주택": 4, "아파트": 12, "주택외건물": 3,
                        "무벽건물": 1, "가건물": 1, "공사중건물": 2, "온실": 1})
FLOOR_HEIGHT = 3.3
SOURCE_KO = {"data": "수치지도", "estimate": "추정", "register": "건축물대장(높이)", "register_floors": "건축물대장(층수)"}
INLAY_KINDS = ("road", "bike")   # 계단판에 판 두께로 끼워 넣는 길 (차도·자전거도로)
ROAD_THICKNESS = 1.0             # 곡면 대지 위 길 한 층 두께(m). 1:1000에서 1mm 판

# 건물 용도별 Rhino 레이어 색 (화면 구분용, 렌더 재질과 무관)
USE_COLORS = {"주택": (246, 241, 226), "근린생활시설": (242, 220, 196), "교육연구시설": (205, 222, 240),
              "종교시설": (228, 210, 236), "업무시설": (208, 216, 232), "의료시설": (238, 206, 206),
              "숙박시설": (240, 228, 204), "노유자(노인및어린이)시설": (216, 236, 206),
              "문화및집회시설": (236, 216, 236), "자동차관련시설": (214, 214, 204), "기타시설": (228, 228, 226)}
ROLE_KO = {"body": "본체", "roof": "지붕", "parapet": "파라펫", "penthouse": "옥탑", "canopy": "캐노피",
           "column": "캐노피", "frame": "공사중", "floorline": "층선"}


# ---------------------------------------------------------------- 입력
def load_dxf(path, bbox):
    doc = ezdxf.readfile(path, encoding="cp949")
    msp = doc.modelspace()
    clip = box(*bbox)
    big = clip.buffer(200)  # 보간용으로 주변 등고선까지 읽음
    data = defaultdict(list)
    for e in msp.query("LWPOLYLINE"):
        layer = e.dxf.layer
        pts = [(x, y) for x, y, *_ in e.get_points()]
        if len(pts) < 2:
            continue
        if layer in CONTOUR_LAYERS:
            ls = LineString(pts)
            if ls.intersects(big):
                data["contours"].append((ls, float(e.dxf.elevation)))
        elif layer.startswith(BUILDING_PREFIX) and e.closed and len(pts) >= 3:
            pg = make_valid(Polygon(pts))
            if pg.intersects(clip):
                data["buildings"].append((pg, layer, 0, ""))
        else:
            kind = ("roads" if layer.startswith(ROAD_PREFIXES) else
                    "walls" if layer.startswith(WALL_PREFIX) else
                    "water" if layer.startswith(WATER_PREFIX) else None)
            if kind:
                if e.closed:
                    pts.append(pts[0])
                ls = LineString(pts)
                if ls.intersects(clip):
                    data[kind].append(ls.intersection(clip))
    for e in msp.query("INSERT"):
        if e.dxf.layer in SPOT_LAYERS:
            x, y, z = e.dxf.insert
            if big.contains(Point(x, y)):
                data["spots"].append((x, y, z))
    return data


def load_shp(folder, bbox):
    """수치지도 2.0 SHP 폴더. 건물 층수(층수 필드)와 종류가 들어 있다."""
    import shapefile
    from shapely.geometry import shape

    def read(name):
        fn = os.path.join(folder, name)
        if not os.path.exists(fn + ".shp"):
            return []
        r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
        names = [f[0] for f in r.fields[1:]]
        return [(shape(sr.shape.__geo_interface__), dict(zip(names, sr.record)))
                for sr in r.iterShapeRecords()]
    clip = box(*bbox)
    big = clip.buffer(200)
    data = defaultdict(list)
    for g, rec in read("N3L_F0010000"):
        if g.intersects(big):
            for ls in getattr(g, "geoms", [g]):
                data["contours"].append((LineString([c[:2] for c in ls.coords]), float(rec["등고수치"])))
    for g, rec in read("N3P_F0020000"):
        if big.contains(g):
            data["spots"].append((g.x, g.y, float(rec["수치"])))
    for g, rec in read("N3A_B0010000"):
        g = make_valid(g)
        if g.intersects(clip):
            data["buildings"].append((g, rec.get("종류", ""), int(rec.get("층수") or 0), rec.get("명칭", ""), rec))
    # 절토·성토면의 위 끝·아래 끝 선, 옹벽(높이)을 지형 꺾임선으로 (1:5000 이상 수치지도에 있음)
    for g, rec in read("N3L_F0030000"):
        if g.intersects(big):
            for ls in getattr(g, "geoms", [g]):
                data["breaks"].append((LineString([c[:2] for c in ls.coords]), str(rec.get("구분") or ""),
                                       str(rec.get("상하구분") or ""), 0.0))
    for g, rec in read("N3L_F0040000"):
        if g.intersects(big):
            try:
                h = float(rec.get("높이") or 0)
            except (TypeError, ValueError):
                h = 0.0
            for ls in getattr(g, "geoms", [g]):
                data["breaks"].append((LineString([c[:2] for c in ls.coords]), str(rec.get("종류") or "옹벽"),
                                       str(rec.get("상하구분") or ""), h))
    # 논: 계단식 대지에서 한 필씩 평평하게 (비탈의 큰 논은 다랑이로)
    for g, rec in read("N3A_D0010000"):
        if str(rec.get("구분") or "").strip() == "논":
            g = make_valid(g)
            if g.intersects(clip.buffer(20)):
                data["paddies"].append(g)
    for name, kind in [("N3A_A0010000", "roads"), ("N3A_A0033320", "roads"),
                       ("N3L_B0020000", "walls"), ("N3A_E0010001", "water")]:
        for g, rec in read(name):
            if g.intersects(clip):
                if kind == "roads" and g.geom_type.endswith("Polygon"):
                    data["road_areas"].append(make_valid(g))     # 계단식 대지: 길 면은 터가 넘지 않는다
                line = g.boundary if g.geom_type.endswith("Polygon") else g
                data[kind].append(line.intersection(clip))
    return data


# ---------------------------------------------------------------- 지형
def breakline_points(breaks, interp, step=2.0, reach=(1.5, 3.0, 5.0, 7.0)):
    """절토·성토·옹벽 선에 높이를 붙인 점. 반환: (점 (n,2), 높이 (n,))

    수치지도의 이 선들에는 높이가 없다. 등고선 보간면을 선 양쪽(reach m)에서 재어
    위 끝(상단) 선은 둘레의 높은 값, 아래 끝(하단) 선은 낮은 값을 준다. 그러면 5m 등고선 사이에 있는
    절토면의 꺾임(평평한 단 → 급한 비탈)이 보간면에 생긴다. 높이가 적힌 옹벽은 선 양쪽에 0.3m 띄운 두 줄로
    위쪽·아래쪽 높이를 따로 준다(아래쪽 = 위쪽 - 옹벽 높이).
    """
    P, Z = [], []
    for ls, kind, side, h in breaks:
        if ls.length < 1.0:
            continue
        n = max(2, int(ls.length / step) + 1)
        d = np.linspace(0, ls.length, n)
        xy = np.array([ls.interpolate(t).coords[0][:2] for t in d])
        tan = np.gradient(xy, axis=0)
        tan /= np.maximum(np.linalg.norm(tan, axis=1), 1e-9)[:, None]
        nrm = np.column_stack([-tan[:, 1], tan[:, 0]])
        samp = np.stack([interp(xy + s_ * r * nrm) for r in reach for s_ in (1, -1)] + [interp(xy)], axis=1)
        top, bot = np.nanmax(samp, axis=1), np.nanmin(samp, axis=1)
        if h > 0.3:
            # 옹벽: 어느 쪽이 높은지는 양쪽 평균으로
            hi = np.nanmean(samp[:, 0::2][:, :len(reach)], axis=1) >= np.nanmean(samp[:, 1::2][:, :len(reach)], axis=1)
            sgn = np.where(hi, 1.0, -1.0)[:, None]
            P += [xy + 0.3 * sgn * nrm, xy - 0.3 * sgn * nrm]
            Z += [top, np.maximum(top - h, bot - 1.0)]
            continue
        if side == "상단":
            P.append(xy); Z.append(top)
        elif side == "하단":
            P.append(xy); Z.append(bot)
    if not P:
        return np.zeros((0, 2)), np.zeros(0)
    return np.vstack(P), np.concatenate(Z)


def build_interpolator(contours, spots, extra=None):
    pts, zs = [], []
    for ls, z in contours:
        for x, y in ls.segmentize(5).coords:
            pts.append((x, y)); zs.append(z)
    for x, y, z in spots:
        pts.append((x, y)); zs.append(z)
    pts, zs = np.array(pts), np.array(zs)
    if extra is not None and len(extra[0]):
        ok = np.isfinite(extra[1])
        pts, zs = np.vstack([pts, extra[0][ok]]), np.concatenate([zs, extra[1][ok]])
    lin = LinearNDInterpolator(pts, zs)
    near = NearestNDInterpolator(pts, zs)

    def f(xy):
        xy = np.atleast_2d(xy)
        v = lin(xy)
        bad = np.isnan(v)
        if bad.any():
            v[bad] = near(xy[bad])
        return v
    return f


def as_polys(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def chaikin(coords, n=2, bbox=None, tol=1e-6):
    """닫힌 링을 Chaikin 방식으로 n번 깎아 부드러운 곡선으로.

    bbox 를 주면 모델 테두리 위의 점은 움직이지 않는다. 테두리를 따라가는 긴 변의 끝(모델 모서리)을
    깎으면 변 길이의 1/4 씩 큰 삼각형이 잘려 나가 계단판 모서리가 비었다(2026-11-19 사용자 신고)."""
    pts = np.asarray(coords, float)[:-1, :2]
    for _ in range(n):
        if len(pts) < 3:
            break
        nxt = np.roll(pts, -1, axis=0)
        q, r = 0.75 * pts + 0.25 * nxt, 0.25 * pts + 0.75 * nxt
        if bbox is not None:
            eps = tol
            pin = ((np.abs(pts[:, 0] - bbox[0]) < eps) | (np.abs(pts[:, 0] - bbox[2]) < eps) |
                   (np.abs(pts[:, 1] - bbox[1]) < eps) | (np.abs(pts[:, 1] - bbox[3]) < eps))
            q[pin] = pts[pin]
            pin_n = np.roll(pin, -1)
            r[pin_n] = nxt[pin_n]
        pts = np.column_stack([q, r]).reshape(-1, 2)
        keep = np.r_[True, np.any(np.abs(np.diff(pts, axis=0)) > 1e-9, axis=1)]
        pts = pts[keep]
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
    return np.vstack([pts, pts[:1]])


def smooth_poly(g, n=2, bbox=None, tol=1e-6):
    out = []
    for p in as_polys(g):
        if len(p.exterior.coords) < 4:
            continue
        holes = [chaikin(h.coords, n, bbox, tol) for h in p.interiors if len(h.coords) >= 4]
        out.append(make_valid(Polygon(chaikin(p.exterior.coords, n, bbox, tol), holes)))
    return unary_union(out) if out else g


def terrace_regions(contours, interp, bbox, interval, min_area, cell=0.5, smooth=1.5, sigma=4.0, chaikin_n=3, keep=None,
                    keep_w=None):
    """보간한 지형면을 레벨마다 잘라 계단 영역을 만든다.

    수치지도 등고선은 건물·도로에서 끊겨 있어 그대로 폐합할 수 없으므로,
    등고선 정점으로 TIN을 만든 뒤 격자(cell m)에서 등치 영역을 뽑는다.
    등고선 위에서는 TIN 값이 정확히 그 표고라 원래 선을 따라간다.
    반환: ({level: 영역}, 바닥 레벨) — level 이상인 지면 영역.
    """
    import contourpy
    clip = box(*bbox)
    xs = np.arange(bbox[0], bbox[2] + cell, cell)
    ys = np.arange(bbox[1], bbox[3] + cell, cell)
    X, Y = np.meshgrid(xs, ys)
    Z = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
    if sigma > 0:
        # 끊긴 등고선 사이 TIN의 각진 면이 톱니처럼 보이지 않도록 지형면을 살짝 고르게
        from scipy.ndimage import gaussian_filter
        Zs = gaussian_filter(Z, sigma / cell, mode="nearest")
        w = np.zeros(X.shape)
        if keep is not None:
            # 사업지 레벨로 다시 만든 대지 안은 고르게 펴지 않는다 (단·옹벽 높이가 그대로 판이 되게)
            import shapely
            d = shapely.distance(keep, shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip(1.0 - d / 6.0, 0.0, 1.0)
        if keep_w is not None:
            # 계단식 대지(건물 터·논)도 평평한 단과 옹벽이 뭉개지지 않게
            w = np.maximum(w, keep_w(X, Y))
        Z = w * Z + (1 - w) * Zs if w.any() else Zs
    base = math.floor(Z.min() / interval) * interval
    top = math.floor(Z.max() / interval) * interval
    gen = contourpy.contour_generator(X, Y, Z, fill_type="OuterOffset")
    regions = {base: clip}
    edge_w = 2 * smooth + cell
    edge_band = clip.difference(clip.buffer(-edge_w, join_style="mitre"))
    lv = base + interval
    while lv <= top + 1e-6:
        pts, offs = gen.filled(lv, np.inf)
        polys = []
        for P, O in zip(pts, offs):
            rings = [P[O[i]:O[i + 1]] for i in range(len(O) - 1)]
            if len(rings[0]) >= 4:
                polys.append(Polygon(rings[0], [r for r in rings[1:] if len(r) >= 4]))
        g = unary_union([make_valid(p) for p in polys]).intersection(clip)
        raw = g
        # 격자 계단 제거 + 레이저로 자르기 어려운 가는 부분·작은 조각 제거
        g = g.buffer(smooth, join_style="round").buffer(-2 * smooth, join_style="round") \
             .buffer(smooth, join_style="round").intersection(clip)
        # 모델 테두리 가까운 점은 다듬지 않고, 테두리 띠는 다듬기 전 윤곽으로 되돌린다.
        # (판이 테두리를 따라 길게 닿으면 다듬기가 모델 모서리를 큰 삼각형으로 깎아 내던 것)
        g = smooth_poly(g.simplify(cell * 0.5), chaikin_n, bbox, tol=edge_w).intersection(clip)
        g = unary_union([g, raw.intersection(edge_band)]).intersection(clip)
        g = unary_union([p for p in as_polys(g) if p.area >= min_area])
        if not g.is_empty:
            # 윗판은 반드시 아랫판 안에 있어야 쌓을 수 있다
            g = g.intersection(regions[max(regions)])
            # 겹친 판의 교집합에 선·점이 섞이면(GeometryCollection) 경계를 못 구한다 → 면만 남긴다
            ps = [p for p in as_polys(g) if p.area >= min_area]
            if not ps:
                break
            g = unary_union(ps)
            regions[lv] = g
        lv += interval
    return regions, base


def level_at(regions, pt):
    """점이 올라앉는 가장 높은 판. 윗판은 아랫판 안에 있으므로(겹쳐 쌓임) 이분 탐색으로 찾는다."""
    lv = list(regions)
    lo, hi = 0, len(lv) - 1          # lv[lo] 는 항상 점을 품는다(맨 아래 판 = 모델 전체)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if regions[lv[mid]].contains(pt):
            lo = mid
        else:
            hi = mid - 1
    return lv[lo]


def _dist_to_lines(lines, pts):
    """점마다 선까지 최단거리. 선을 짧은 토막으로 나눠 공간 색인으로 가장 가까운 토막만 재므로,
    긴 등고선(판이 많을 때)에서도 빠르다. shapely.distance(lines, pts) 와 같은 값."""
    import shapely
    segs = []
    for part in shapely.get_parts(lines):
        c = shapely.get_coordinates(part)
        if len(c) >= 2:
            segs.append(np.stack([c[:-1], c[1:]], axis=1))
    if not segs:
        return np.full(len(pts), np.inf)
    segs = shapely.linestrings(np.concatenate(segs))
    tree = shapely.STRtree(segs)
    idx, d = tree.query_nearest(pts, return_distance=True, all_matches=False)
    out = np.full(len(pts), np.inf)
    out[idx[0]] = d
    return out


def slope_heightfield(regions, bbox, cell=1.0, cap=60.0, return_levels=False):
    """계단 사이 높이차를 완만한 경사면으로 이은 높이 격자.

    두 등고선(아래 L, 위 L+간격) 사이의 점은 아래선까지 거리 dL, 위선까지
    거리 dU의 비율로 높이를 정한다: H = L + 간격 * dL / (dL + dU).
    그래서 경사가 두 등고선 사이 전체 폭에 고르게 퍼지고, 등고선 위에서는
    정확히 그 표고가 된다. 위 등고선이 없는 봉우리는 평평하게 둔다.
    반환: (X, Y, H)
    """
    import shapely
    clip = box(*bbox)
    edge = clip.exterior.buffer(0.05)
    xs = np.arange(bbox[0], bbox[2] + cell * 0.5, cell)
    ys = np.arange(bbox[1], bbox[3] + cell * 0.5, cell)
    X, Y = np.meshgrid(xs, ys)
    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    # 모델 가장자리 점은 곡선 다듬기로 윤곽이 살짝 안쪽에 있을 수 있어 조금 들여서 판정
    Xc = np.clip(X, bbox[0] + 0.5, bbox[2] - 0.5)
    Yc = np.clip(Y, bbox[1] + 0.5, bbox[3] - 0.5)
    inside = []
    for L in lv:
        if L == lv[0]:
            inside.append(np.ones(X.shape, bool))
            continue
        # 윗판은 아랫판 안에 있으므로, 아랫판 안의 점만 시험한다(판이 많아도 빠름)
        prev = inside[-1]
        cur = np.zeros(X.shape, bool)
        if prev.any():
            shapely.prepare(regions[L])
            cur[prev] = shapely.intersects_xy(regions[L], Xc[prev], Yc[prev])
        inside.append(cur)
    lines = [regions[L].boundary.difference(edge) for L in lv]
    H = np.full(X.shape, float(lv[0]))
    for i, L in enumerate(lv):
        band = inside[i] & (~inside[i + 1] if i + 1 < len(lv) else True)
        if not band.any():
            continue
        pts = shapely.points(X[band], Y[band])
        dL = _dist_to_lines(lines[i], pts) if i > 0 and not lines[i].is_empty else np.full(len(pts), cap)
        if i + 1 < len(lv) and not lines[i + 1].is_empty:
            dU = _dist_to_lines(lines[i + 1], pts)
        else:
            dU = np.full(len(pts), np.inf)
        dL = np.minimum(np.nan_to_num(dL, nan=cap), cap)
        t = np.where(np.isinf(dU), 0.0, dL / np.maximum(dL + dU, 1e-9))
        H[band] = L + interval * t
    if return_levels:
        B = np.full(X.shape, float(lv[0]))
        for i, L in enumerate(lv):
            B[inside[i]] = L
        return X, Y, H, B
    return X, Y, H


def building_pads(X, Y, H, masses, apron=2.0):
    """건물 밑 대지를 평평한 터(pad)로 고르고, 둘레 apron(m)에서 경사면과 잇는다.

    경사면 위에 건물이 떠 있거나 한쪽이 파묻히지 않도록, 건물 바닥 높이를
    발자국 안 지형의 중앙값으로 정하고 그 높이로 대지를 깎거나 채운다.
    H를 그 자리에서 고치고, 각 건물의 pad 높이를 masses에 기록한다.
    """
    import shapely
    cell = X[0, 1] - X[0, 0]
    x0, y0 = X[0, 0], Y[0, 0]
    pad_mask = np.zeros(H.shape, bool)
    H0 = H.copy()
    # 큰 건물부터 → 작은 부속 건물이 큰 건물 터를 덮어쓰지 않게
    for m in sorted(masses, key=lambda m: -m["poly"].area):
        p = m["poly"]
        bx = p.buffer(apron).bounds
        i0, i1 = max(int((bx[1] - y0) / cell), 0), min(int((bx[3] - y0) / cell) + 2, H.shape[0])
        j0, j1 = max(int((bx[0] - x0) / cell), 0), min(int((bx[2] - x0) / cell) + 2, H.shape[1])
        if i0 >= i1 or j0 >= j1:
            continue
        xs, ys = X[i0:i1, j0:j1], Y[i0:i1, j0:j1]
        pts = shapely.points(xs, ys)
        d = shapely.distance(p, pts)
        inside = d == 0
        sub = H0[i0:i1, j0:j1]
        if inside.any():
            pad = float(np.median(sub[inside]))
        else:
            c = p.representative_point()
            pad = float(sub.ravel()[np.argmin(shapely.distance(c, pts).ravel())])
        already = pad_mask[i0:i1, j0:j1]
        if (already & inside).any():
            # 이미 다른 건물 터 위(붙은 건물·부속동)면 그 높이를 따른다
            pad = float(np.median(H[i0:i1, j0:j1][already & inside]))
        w = np.clip(d / apron, 0, 1)  # 0: 터, 1: 원래 경사
        Hs = H[i0:i1, j0:j1]
        blend = pad * (1 - w) + Hs * w
        ring = ~already & (d < apron)
        Hs[ring] = blend[ring]
        Hs[inside] = pad
        pad_mask[i0:i1, j0:j1] |= inside
        m["pad"] = pad
        if inside.any():
            m["cutfill"] = float(np.abs(sub[inside] - pad).max())
    return H


def surface_sampler(X, Y, H):
    from scipy.interpolate import RegularGridInterpolator
    f = RegularGridInterpolator((Y[:, 0], X[0]), H, bounds_error=False, fill_value=None)
    return lambda xs, ys: f(np.column_stack([ys, xs]))


def _grid_triangulate(p, cell):
    """면을 cell(m) 격자 칸으로 잘라 칸마다 삼각분할한다. 반환: (꼭짓점 (n,2), 삼각형 (m,3)).

    곡면 위에 얹을 때 칸 크기마다 높이를 다시 읽게 하려는 것. 예전에는 테두리를 잘게 나눈 뒤 긴 삼각형을
    max_edge 까지 쪼갰는데, 가는 삼각형이 끝없이 쪼개져 같은 넓이에 면이 수십 배 생겼다(Rhino 저장이 느려짐).
    칸 경계에 생긴 점은 이웃 칸과 똑같아서 합치면 빈틈 없는 한 장이 된다.
    """
    import shapely
    from mapbox_earcut import triangulate_float64
    x0, y0, x1, y1 = p.bounds
    xs = np.arange(math.floor(x0 / cell) * cell, x1, cell)
    ys = np.arange(math.floor(y0 / cell) * cell, y1, cell)
    X, Y = np.meshgrid(xs, ys)
    cells = shapely.box(X.ravel(), Y.ravel(), X.ravel() + cell, Y.ravel() + cell)
    shapely.prepare(p)
    hit = shapely.intersects(p, cells)
    cells = cells[hit]
    inside = shapely.contains_properly(p, cells)
    pieces = list(cells[inside])
    if (~inside).any():
        for g in shapely.intersection(cells[~inside], p):
            pieces += [q for q in as_polys(g) if q.area > 1e-6]
    V, F, n = [], [], 0
    for q in pieces:
        rings = [np.asarray(q.exterior.coords)[:-1, :2]] + [np.asarray(r.coords)[:-1, :2] for r in q.interiors]
        pts = np.vstack(rings)
        if len(pts) < 3:
            continue
        ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
        t = np.asarray(triangulate_float64(pts, ends), dtype=np.int64).reshape(-1, 3)
        if len(t):
            V.append(pts); F.append(t + n); n += len(pts)
    if not F:
        return np.zeros((0, 2)), np.zeros((0, 3), int)
    return np.vstack(V), np.vstack(F)


def drape_area_meshes(areas, hz, origin, max_edge=3.0):
    """길 면을 경사면 위에 얹은 얇은 메시로. 반환: [(Trimesh, kind)]"""
    from .paths_trees import PATH_STYLE
    out = []
    for p, L, kind in areas:
        p = p.simplify(0.05)
        if p.is_empty or p.area < 0.5:
            continue
        try:
            v2, f = _grid_triangulate(p, max_edge)
        except Exception:
            continue
        if len(f) == 0 or len(v2) < 3:
            continue
        v = np.column_stack([v2, np.zeros(len(v2))])
        v[:, 2] = hz(v[:, 0], v[:, 1]) + PATH_STYLE[kind][2]
        v[:, 0] -= origin[0]; v[:, 1] -= origin[1]
        out.append((trimesh.Trimesh(v, f, process=False), kind))
    return out


def drape_area_solids(areas, hz, origin, thickness, max_edge=3.0):
    """길 면을 곡면 대지 위에 얹은 두께 있는 닫힌 솔리드로. 반환: [(Trimesh, kind)]

    바닥은 곡면보다 5cm 아래(틈 없이 묻힘), 윗면은 곡면 + thickness (보도·교통섬은 + 연석 높이).
    """
    from .paths_trees import PATH_STYLE
    out = []
    for p, L, kind in areas:
        p = p.simplify(0.05)
        if p.is_empty or p.area < 0.5:
            continue
        try:
            v2, f = _grid_triangulate(p, max_edge)
        except Exception:
            continue
        if len(f) == 0 or len(v2) < 3:
            continue
        v = np.column_stack([v2, np.zeros(len(v2))])
        # 칸끼리 맞닿은 점을 하나로 합쳐야 옆벽이 정확히 테두리에만 생긴다
        v, inv = np.unique(np.round(v, 6), axis=0, return_inverse=True)
        f = inv.reshape(-1)[f]
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        f = f[np.unique(np.sort(f, axis=1), axis=0, return_index=True)[1]]
        # 윗면이 위를 보게: 삼각분할 전체 방향(넓이 합의 부호)으로 한꺼번에 뒤집는다
        # (한 장씩 판정하면 넓이 0인 가는 삼각형 때문에 방향이 섞인다)
        d = (v[f[:, 1], 0] - v[f[:, 0], 0]) * (v[f[:, 2], 1] - v[f[:, 0], 1]) - \
            (v[f[:, 2], 0] - v[f[:, 0], 0]) * (v[f[:, 1], 1] - v[f[:, 0], 1])
        if d.sum() < 0:
            f = f[:, ::-1]
        g = hz(v[:, 0], v[:, 1])
        curb = PATH_STYLE[kind][2] if kind not in INLAY_KINDS else 0.0
        top, bot = v.copy(), v.copy()
        top[:, 2] = g + thickness + curb
        bot[:, 2] = g - 0.05
        n = len(v)
        # 한 번만 쓰인 모서리 = 테두리 → 옆벽
        e = np.sort(np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
        e_dir = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        border = e_dir[trimesh.grouping.group_rows(e, require_count=1)]
        a, b = border[:, 0], border[:, 1]
        walls = np.vstack([np.column_stack([a, a + n, b]), np.column_stack([b, a + n, b + n])])
        faces = np.vstack([f, f[:, ::-1] + n, walls])
        verts = np.vstack([top, bot])
        verts[:, 0] -= origin[0]; verts[:, 1] -= origin[1]
        m = trimesh.Trimesh(verts, faces, process=False)
        if not (m.is_watertight and m.is_winding_consistent and m.volume > 0):
            m = _drape_solid_tiles(p, hz, origin, g_off=-0.05, t_off=thickness + curb, max_edge=max_edge)
        if m is not None:
            out.append((_with_ground_normals(m, hz, origin), kind))
    return out


def _drape_solid_tiles(p, hz, origin, g_off, t_off, max_edge=3.0, tile=6.0):
    """복잡한 면(겹친 점·가는 틈)은 작은 칸으로 나눠 칸마다 닫힌 솔리드로 만든다."""
    x0, y0, x1, y1 = p.bounds
    parts = []
    for x in np.arange(x0, x1, tile):
        for y in np.arange(y0, y1, tile):
            for q in as_polys(p.intersection(box(x, y, x + tile, y + tile))):
                if q.area < 0.05:
                    continue
                try:
                    m = trimesh.creation.extrude_polygon(q.segmentize(max_edge), 1.0)
                except Exception:
                    continue
                v = m.vertices.copy()
                g = hz(v[:, 0], v[:, 1])
                v[:, 2] = np.where(v[:, 2] > 0.5, g + t_off, g + g_off)
                v[:, 0] -= origin[0]; v[:, 1] -= origin[1]
                parts.append(trimesh.Trimesh(v, m.faces, process=False))
    if not parts:
        return None
    m = trimesh.util.concatenate(parts)
    # 칸끼리 맞닿은 안쪽 벽(같은 자리에 마주 보는 두 면)은 지워 하나의 판으로
    m.merge_vertices()
    key = np.sort(m.faces, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    keep = cnt[inv.reshape(-1)] == 1
    m = trimesh.Trimesh(m.vertices, m.faces[keep], process=False)
    m.remove_unreferenced_vertices()
    return m


def split_inlays(areas):
    """계단판에 끼워 넣을 도로(차도·자전거도로)와 나머지 길로 나눈다."""
    inlay = [(p, L, k) for p, L, k in areas if k in INLAY_KINDS]
    rest = [(p, L, k) for p, L, k in areas if k not in INLAY_KINDS]
    return inlay, rest


def cut_regions(regions, inlay):
    """판마다 그 판 높이에 드러난 도로 자리를 파낸 판 모양. 반환: {L: geometry}"""
    by = defaultdict(list)
    for p, L, _ in inlay:
        by[L].append(p)
    return {L: (g.difference(unary_union(by[L])) if by[L] else g) for L, g in regions.items()}


def drape_line_3d(ls, hz, step=2.0):
    ls = ls.segmentize(step)
    xy = np.asarray(ls.coords)[:, :2]
    return LineString(np.column_stack([xy, hz(xy[:, 0], xy[:, 1]) + 0.03]))


def heightfield_mesh(X, Y, H, bottom, origin):
    """높이 격자를 바닥이 닫힌 솔리드 메시로."""
    ny, nx = H.shape
    idx = np.arange(nx * ny).reshape(ny, nx)
    top = np.column_stack([X.ravel() - origin[0], Y.ravel() - origin[1], H.ravel()])
    bot = top.copy(); bot[:, 2] = np.ravel(bottom) if np.ndim(bottom) else bottom
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = [np.column_stack([a, b, c]), np.column_stack([a, c, d])]
    n = nx * ny
    faces += [np.column_stack([a + n, c + n, b + n]), np.column_stack([a + n, d + n, c + n])]
    ring = np.concatenate([idx[0, :], idx[:, -1][1:], idx[-1, ::-1][1:], idx[::-1, 0][1:]])
    p, q = ring[:-1], ring[1:]
    faces += [np.column_stack([p, p + n, q]), np.column_stack([q, p + n, q + n])]
    m = trimesh.Trimesh(np.vstack([top, bot]), np.vstack(faces), process=True)
    trimesh.repair.fix_normals(m)
    return m


def simplify_solid(m, tol=0.01):
    """닫힌 지형 메시에서 평평하거나 고르게 기운 곳의 잔 삼각형을 합친다 (모양 차이 tol m 이하).

    1m 격자 그대로면 1000m 모델 지형 하나가 꼭짓점 200만 개라 Rhino 저장만 70초, GLB 85MB 가 된다.
    계단판·터처럼 평평한 곳이 많아 꼭짓점이 1/6~1/10 로 준다. 실패하면 원래 메시를 그대로 쓴다.
    """
    if len(m.vertices) < 20000:
        return m
    try:
        import manifold3d as mf
        M = _mf_from_trimesh(m)
        if M.status() != mf.Error.NoError:
            return m
        out = M.simplify(tol).to_mesh()
        v = np.asarray(out.vert_properties, dtype=float)[:, :3]
        s = trimesh.Trimesh(v, np.asarray(out.tri_verts, dtype=np.int64), process=False)
    except Exception:
        return m
    if len(s.faces) == 0 or not s.is_watertight:
        return m
    return s


# ---------------------------------------------------------------- 건물
def building_masses(buildings, regions, clip, floor_height=FLOOR_HEIGHT):
    """건물 매스. 바닥은 발자국 아래 가장 낮은 판 윗면에서 평평하게 끝난다(그보다 깊이 묻지 않음).

    경사지에 걸친 건물은 위쪽 판들을 지나가므로, 레이저 판에서 그 자리를 잘라 낸다
    (through = 건물이 지나가는 판 높이들). 바닥이 평평해야 매스 하나로 잘라 끼울 수 있다.
    """
    lv = list(regions)
    out = []
    for item in buildings:
        pg, layer, n_floors, name = item[:4]
        attrs = {k: v for k, v in (item[4] if len(item) > 4 else {}).items() if str(v).strip()}
        pg = pg.intersection(clip)
        for p in as_polys(pg):
            if p.area < 4:
                continue
            samples = [Point(c) for c in p.exterior.coords[:-1]] + [p.representative_point()]
            lvs = [level_at(regions, s) for s in samples]
            base = min(lvs)
            ground = level_at(regions, p.representative_point())
            source = "data" if n_floors else "estimate"
            floors = n_floors or FALLBACK_FLOORS.get(layer, 2)
            # 무벽건물(캐노피 등)은 낮게
            h = 3.0 if layer == "무벽건물" else floors * floor_height
            out.append(dict(poly=p, layer=layer, base=base, top=ground + h,
                            through=[L for L in lv if base < L <= max(lvs)], height=h,
                            floors=floors, source=source, name=name, attrs=attrs,
                            use=attrs.get("용도") or "용도미상"))
    return out


# ---------------------------------------------------------------- 도로 드레이프
def drape_lines(lines, regions):
    """선형 요소를 각 계단 판 위로 나눠 올린다. 반환: [(LineString, level)]"""
    lv = list(regions)
    merged = unary_union([l for l in lines if not l.is_empty])
    out = []
    for i, L in enumerate(lv):
        band = regions[L]
        if i + 1 < len(lv):
            band = band.difference(regions[lv[i + 1]])
        part = merged.intersection(band)
        for g in getattr(part, "geoms", [part]):
            if isinstance(g, LineString) and g.length > 0.5:
                out.append((g, L))
    return out


# ---------------------------------------------------------------- 출력: Rhino
def _rh_curve(ring, z, origin):
    if ring.has_z:
        pl = r3.Polyline([r3.Point3d(x - origin[0], y - origin[1], zz) for x, y, zz in ring.coords])
        return r3.PolylineCurve(pl)
    pl = r3.Polyline([r3.Point3d(x - origin[0], y - origin[1], z) for x, y in ring.coords])
    return r3.PolylineCurve(pl)


def _rh_extrusion(poly, z0, z1, origin):
    poly = poly.simplify(0.01)
    outer = Polygon(poly.exterior.coords)
    if not outer.exterior.is_ccw:
        outer = Polygon(list(outer.exterior.coords)[::-1])
    ext = r3.Extrusion.Create(_rh_curve(outer.exterior, z0, origin), z1 - z0, True)
    if ext is None:
        return None
    for hole in poly.interiors:
        ext.AddInnerProfile(_rh_curve(hole, z0, origin))
    return ext


def crisp_mesh(m, deg=30.0):
    """렌더링용: 꺾인 모서리(윗면↔옆벽 등)에서 꼭짓점을 나눠, 매끈한 음영이 모서리 너머로 번지지 않게 한다.

    Rhino·three.js 는 꼭짓점 법선을 그 꼭짓점을 쓰는 면들의 평균으로 잡는다. 길 솔리드처럼 얇은 판의
    윗면과 옆벽이 꼭짓점을 같이 쓰면 윗면 가장자리 법선이 옆으로 기울어 '베개' 같은 얼룩이 생긴다.
    겹친 점을 합친 뒤 deg 보다 크게 꺾인 모서리에서만 끊는다(모양은 그대로).
    """
    mm = trimesh.Trimesh(m.vertices, m.faces, process=False)
    mm.merge_vertices()
    if len(mm.faces) == 0:
        return mm
    try:
        return _split_sharp(mm, np.radians(deg))
    except Exception:
        try:
            return trimesh.graph.smooth_shade(mm, angle=np.radians(deg), facet_minarea=None)
        except Exception:
            return mm


def _split_sharp(mm, angle):
    """trimesh.graph.smooth_shade(facet_minarea=None) 와 같은 결과를 한 번의 배열 연산으로.

    완만하게 이어진 면끼리 한 묶음으로 보고, 꼭짓점을 (묶음, 꼭짓점) 짝마다 하나씩 둔다.
    smooth_shade 는 묶음마다 부분 메시를 따로 떠내 붙여서, 묶음이 많은 메시(건물·길)에서 느리다.
    """
    adj = mm.face_adjacency
    if len(adj) == 0:
        return mm.copy()
    ok = adj[mm.face_adjacency_angles < angle]
    labels = trimesh.graph.connected_component_labels(ok, node_count=len(mm.faces))
    nv = len(mm.vertices)
    key = labels.astype(np.int64)[:, None] * nv + mm.faces
    uniq, inv = np.unique(key.reshape(-1), return_inverse=True)
    return trimesh.Trimesh(mm.vertices[uniq % nv], inv.reshape(-1, 3), process=False)


def _with_ground_normals(m, hz, origin, eps=1.5):
    """길 솔리드: 꺾인 모서리에서 나눈 뒤, 윗면·밑면 꼭짓점 법선을 대지 곡면의 기울기에서 바로 잡는다.

    칸으로 나눠 만든 면도 칸 경계에서 법선이 똑같아 음영 이음매가 생기지 않는다. 옆벽은 면 법선 그대로.
    """
    m = crisp_mesh(m)
    vn = np.array(m.vertex_normals, dtype=float)
    x, y = m.vertices[:, 0] + origin[0], m.vertices[:, 1] + origin[1]
    gx = (hz(x + eps, y) - hz(x - eps, y)) / (2 * eps)
    gy = (hz(x, y + eps) - hz(x, y - eps)) / (2 * eps)
    gn = np.column_stack([-gx, -gy, np.ones(len(x))])
    gn /= np.linalg.norm(gn, axis=1)[:, None]
    up, down = vn[:, 2] > 0.5, vn[:, 2] < -0.5
    vn[up] = gn[up]
    vn[down] = -gn[down]
    m.vertex_normals = vn
    m.metadata["fixed_normals"] = True
    return m


_RH_COUNT = [0]      # 이번 3dm 에 넣은 메시 꼭짓점 수(저장 시간 어림용)


def _rh_mesh(m, crisp=True):
    _RH_COUNT[0] += len(m.vertices)
    fixed = m.metadata.get("fixed_normals") and "vertex_normals" in m._cache.cache
    if crisp and not fixed:
        m = crisp_mesh(m)
    rm = r3.Mesh()
    # numpy 한 줄씩 꺼내 float()/int() 로 바꾸면 느리다(꼭짓점 100만 개에 10초 넘게) → 파이썬 목록으로 한 번에 바꿔 넣는다
    add = rm.Vertices.Add
    for x, y, z in np.asarray(m.vertices, dtype=float).tolist():
        add(x, y, z)
    addf = rm.Faces.AddFace
    for a, b, c in np.asarray(m.faces, dtype=np.int64).tolist():
        addf(a, b, c)
    if fixed:
        addn = rm.Normals.Add
        for x, y, z in np.asarray(m.vertex_normals, dtype=float).tolist():
            addn(x, y, z)
    else:
        rm.Normals.ComputeNormals()
    rm.Compact()
    return rm


def _add_site(f, site, interp, origin, log, step=None):
    """model.3dm 에 검토 대지 경계와 정북일조 사선(참고)을 더한다. site = {poly(모델 좌표), review}"""
    from .landlaw import envelope_mesh
    poly, rv = site["poly"], site.get("review") or {}
    xs = np.linspace(poly.bounds[0], poly.bounds[2], 12)
    ys = np.linspace(poly.bounds[1], poly.bounds[3], 12)
    pts = [(x, y) for x in xs for y in ys if poly.contains(Point(x, y))] or [tuple(poly.representative_point().coords[0])]
    ground = float(np.nanmean(interp(np.array(pts))))

    def layer(name, rgb, parent=None):
        L = r3.Layer(); L.Name = name; L.Color = (*rgb, 255)
        if parent is not None:
            L.ParentLayerId = f.Layers[parent].Id
        return f.Layers.Add(L)
    top = layer("대지", (220, 60, 40))
    lb = layer("경계", (220, 60, 40), top)
    for g in as_polys(poly):
        a = r3.ObjectAttributes(); a.LayerIndex = lb; a.Name = f"대지 경계 {rv.get('address', '')}".strip()
        a.SetUserString("면적(㎡)", f"{poly.area:.1f}")
        a.SetUserString("PNU", str(rv.get("pnu") or ""))
        for k in ("zone", "bcr", "far", "area"):
            if rv.get(k) is not None:
                a.SetUserString({"zone": "용도지역", "bcr": "건폐율(%)", "far": "용적률(%)", "area": "대지면적(㎡)"}[k],
                                str(round(rv[k], 1) if isinstance(rv[k], float) else rv[k]))
        f.Objects.AddCurve(_rh_curve(g.exterior, ground + 0.05, origin), a)
    # 경계선을 지형에 눌러 붙인 선: 계단판마다 그 판 윗면 높이에(레이저 SITE 각인과 같은 선), 그리고 매끈한 지형 위에
    regions = site.get("regions")
    n_dr = 0
    if regions:
        ld = layer("경계(계단판 위)", (220, 60, 40), top)
        rings = [r for g in as_polys(poly) for r in [g.exterior, *g.interiors]]
        for ls, L in drape_lines([LineString(r.coords) for r in rings], regions):
            a = r3.ObjectAttributes(); a.LayerIndex = ld; a.Name = f"대지 경계 · 판 EL {L:g}"
            f.Objects.AddCurve(_rh_curve(ls, L + 0.02, origin), a); n_dr += 1
    lt = layer("경계(지형 위)", (220, 60, 40), top)
    for g in as_polys(poly):
        for r in [g.exterior, *g.interiors]:
            seg = LineString(r.coords).segmentize(1.0)
            zz = interp(np.array(seg.coords)[:, :2])
            a = r3.ObjectAttributes(); a.LayerIndex = lt; a.Name = "대지 경계 · 지형 위"
            f.Objects.AddCurve(_rh_curve(LineString([(x, y, z + 0.02) for (x, y, *_), z in zip(seg.coords, zz)]), 0, origin), a)
    log(f"   대지 경계선을 지형에 눌러 붙였습니다 (Rhino 대지::경계(계단판 위) {n_dr}조각 · 대지::경계(지형 위))")
    if step:
        # 대지 안을 사업지 레벨로 다시 그린 등고선 (step m 간격, 각 선은 제 높이에)
        import contourpy
        b = poly.bounds
        xs = np.arange(b[0] - 1, b[2] + 1.5, 0.5); ys = np.arange(b[1] - 1, b[3] + 1.5, 0.5)
        GX, GY = np.meshgrid(xs, ys)
        GZ = interp(np.column_stack([GX.ravel(), GY.ravel()])).reshape(GX.shape)
        gen = contourpy.contour_generator(GX, GY, GZ)
        lc = layer("레벨 등고선", (40, 90, 220), top)
        n = 0
        for z in np.arange(np.ceil(np.nanmin(GZ) / step) * step, np.nanmax(GZ) + 1e-6, step):
            for c in gen.lines(z - 0.01):     # 평평한 단(레벨 = z)에서 선이 부서지지 않게 살짝 아래를 딴다
                if len(c) < 2:
                    continue
                g2 = LineString(c).intersection(poly)
                for part in getattr(g2, "geoms", [g2]):
                    if isinstance(part, LineString) and part.length > 0.5:
                        a = r3.ObjectAttributes(); a.LayerIndex = lc; a.Name = f"EL {z:g}"
                        f.Objects.AddCurve(_rh_curve(part, z, origin), a); n += 1
        log(f"   대지 안 레벨 등고선 {n}줄을 넣었습니다 ({step:g}m 간격, Rhino 대지::레벨 등고선)")
    # 표준 대지 파일의 레벨판·원지형 유지·계획 건물·도로 (선으로, 그 자리 계획 높이에)
    plates, keep, blds, roads = (site.get(k) or [] for k in ("plates", "keep", "buildings", "roads"))
    if site.get("keep_terrain"):
        plates, keep = [], []      # 원래 지형 유지: 계획 레벨판은 넣지 않는다(건물·도로 선은 원래 지형 위에)
    if plates:
        lp = layer("레벨판", (40, 90, 220), top)
        for p, z in plates:
            for g in as_polys(p):
                a = r3.ObjectAttributes(); a.LayerIndex = lp; a.Name = f"레벨판 EL {z:g}"
                a.SetUserString("계획 레벨", f"{z:g}"); a.SetUserString("면적(㎡)", f"{g.area:.1f}")
                f.Objects.AddCurve(_rh_curve(g.exterior, z + 0.05, origin), a)
    if keep:
        lk = layer("원지형 유지", (90, 90, 90), top)
        for p in keep:
            for g in as_polys(p):
                zz = interp(np.array(g.exterior.coords)[:, :2])
                a = r3.ObjectAttributes(); a.LayerIndex = lk; a.Name = "원지형 유지"
                f.Objects.AddCurve(_rh_curve(LineString([(x, y, z + 0.05) for (x, y), z in zip(g.exterior.coords, zz)]), 0, origin), a)
    if blds:
        lbd = layer("계획 건물", (30, 30, 30), top)
        for i, p in enumerate(blds):
            for g in as_polys(p):
                c = g.representative_point()
                z = float(interp(np.array([[c.x, c.y]]))[0])
                a = r3.ObjectAttributes(); a.LayerIndex = lbd; a.Name = f"계획 건물 {i + 1} (EL {z:.1f})"
                f.Objects.AddCurve(_rh_curve(g.exterior, z + 0.1, origin), a)
    if roads:
        lr = layer("계획 도로", (120, 120, 120), top)
        for ls in roads:
            seg = ls.segmentize(2.0)
            zz = interp(np.array(seg.coords)[:, :2])
            a = r3.ObjectAttributes(); a.LayerIndex = lr; a.Name = "계획 도로·램프"
            f.Objects.AddCurve(_rh_curve(LineString([(x, y, z + 0.1) for (x, y, *_), z in zip(seg.coords, zz)]), 0, origin), a)
    if plates or keep or blds or roads:
        log(f"   표준 대지 파일 도형을 넣었습니다 (Rhino 대지:: 레벨판 {len(plates)} · 원지형 유지 {len(keep)} · 계획 건물 {len(blds)} · 계획 도로 {len(roads)})")
    env = None
    if rv.get("sunlight"):
        env = envelope_mesh(poly, ground)
        if env is not None:
            env.apply_translation([-origin[0], -origin[1], 0])
            le = layer("정북일조 사선(참고)", (240, 150, 60), top)
            a = r3.ObjectAttributes(); a.LayerIndex = le; a.Name = "정북일조 사선: 높이 10m 이하 1.5m, 초과 부분 높이의 1/2"
            f.Objects.AddMesh(_rh_mesh(env), a)
    log(f"   대지 경계" + (" + 정북일조 사선을" if env is not None else "를") + f" 넣었습니다 (기준 지표면 EL {ground:.1f})")
    return dict(address=rv.get("address"), pnu=rv.get("pnu"), ground=round(ground, 2), envelope=env is not None)


def _shifted(mesh, origin):
    m = mesh.copy()
    m.apply_translation([-origin[0], -origin[1], 0])
    return m


def _write_estimate(verts, objs):
    """Rhino 저장(f.Write) 어림 초. 실측(2026-11-17, 리눅스): 꼭짓점·개체 수에 거의 비례."""
    return round(verts * WRITE_SEC_PER_VERT + objs * WRITE_SEC_PER_OBJ, 1)


WRITE_SEC_PER_VERT = 30e-6
WRITE_SEC_PER_OBJ = 2e-4
STL_SEC_PER_FACE = 0.25e-6
STL_SEC_PER_FACE_JOIN = 1.4e-6


def write_3dm(path, regions, masses, draped, origin, areas=(), trees=(), edges=(), terrain_mesh=None,
              area_meshes=(), steps_too=False, floor_height=FLOOR_HEIGHT, inlays=(), lights=(), structs=(),
              tree_style="species", roadbits=(), before_write=None, tick=None):
    """Rhino 모델. 레이어는 지형 / 길 / 건물(용도별 › 부위별) / 나무 로 나뉜다.

    inlays: 계단판에 판 두께로 끼워 넣는 도로 [(Polygon, level, kind)] — 계단판은 그 자리를 파낸다.
    area_meshes: 곡면 대지 위 길 솔리드 [(Trimesh, kind)].
    """
    from .building_detail import building_parts
    from .paths_trees import PATH_STYLE
    f = r3.File3dm()
    f.Settings.ModelUnitSystem = r3.UnitSystem.Meters
    layers = {}

    def layer(path, rgb=(200, 200, 200)):
        """'건물::주택::지붕' 같은 경로로 중첩 레이어를 만들고 번호를 돌려준다."""
        if path in layers:
            return layers[path]
        parent, _, name = path.rpartition("::")
        L = r3.Layer(); L.Name = name; L.Color = (*rgb, 255)
        if parent:
            L.ParentLayerId = f.Layers.FindIndex(layer(parent, rgb)).Id
        layers[path] = f.Layers.Add(L)
        return layers[path]

    def attrs(li, name=""):
        a = r3.ObjectAttributes(); a.LayerIndex = li; a.Name = name
        return a

    groups = {}

    def group(name):
        if name not in groups:
            g = r3.Group(); g.Name = name; f.Groups.Add(g)
            groups[name] = len(groups)
        return groups[name]

    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    T = tick or (lambda *a, **k: None)     # 화면 진행 표시: 저장 중에도 어디까지 썼는지 알린다
    _RH_COUNT[0] = 0
    layer("지형", (205, 205, 200)); layer("길", (130, 130, 126)); layer("건물", (245, 245, 245))
    if terrain_mesh is not None:
        T("지형 메시")
        li = layer("지형::경사매스" if steps_too else "지형::곡면대지", (214, 212, 205))
        f.Objects.AddMesh(_rh_mesh(terrain_mesh, crisp=False), attrs(li, "경사 매스(계단판 위)" if steps_too else "곡면 대지"))
        li_ct = layer("지형::등고선", (140, 140, 136))
        for L in lv[1:]:
            for p in as_polys(regions[L]):
                for ring in [p.exterior, *p.interiors]:
                    f.Objects.AddCurve(_rh_curve(ring, L, origin), attrs(li_ct, f"EL {L:g}"))
    cut = cut_regions(regions, inlays)
    li_terr = layer("지형::계단판", (205, 205, 200))
    for i, L in enumerate(lv if (terrain_mesh is None or steps_too) else []):
        T("계단판", i, len(lv))
        bottom = L - interval if i else L - interval * 2
        for p in as_polys(cut[L]):
            ext = _rh_extrusion(p, bottom, L, origin)
            if ext:
                f.Objects.AddExtrusion(ext, attrs(li_terr, f"계단판 EL {L:g}"))
    T("길")
    for p, L, kind in (inlays if (terrain_mesh is None or steps_too) else []):
        name, rgb = PATH_STYLE[kind][:2]
        ext = _rh_extrusion(p, L - interval, L, origin)
        if ext:
            f.Objects.AddExtrusion(ext, attrs(layer(f"길::계단판::{name}", rgb), f"{name} EL {L:g}"))
    for p, L, kind in areas:
        name, rgb, lift, _ = PATH_STYLE[kind]
        ext = _rh_extrusion(p, L, L + lift, origin)
        if ext:
            f.Objects.AddExtrusion(ext, attrs(layer(f"길::계단판::{name}", rgb), name))
    for m, kind in area_meshes:
        name, rgb = PATH_STYLE[kind][:2]
        f.Objects.AddMesh(_rh_mesh(m), attrs(layer(f"길::곡면대지::{name}", rgb), name))
    for ls, L in draped:
        f.Objects.AddCurve(_rh_curve(ls, L + 0.02, origin), attrs(layer("길::도로선", (90, 140, 200))))
    for ls, L in edges:
        f.Objects.AddCurve(_rh_curve(ls, L + 0.02, origin), attrs(layer("길::도로경계선", (120, 120, 116))))

    # 건물: 재료별 레이어(재료::붉은 벽돌, 재료::유리 …, 레이어마다 렌더 재료). 수치지도·대장 정보는 개체 속성(UserText)으로,
    # 같은 건물 부위는 그룹으로(SelGroup), 용도·종류·층수대도 그룹으로 묶는다.
    from .facade import MATERIALS, facade_meshes, material_of
    mats = {}

    def mat_layer(name):
        path = f"재료::{name}"
        if path not in layers:
            rgb, tr_ = MATERIALS.get(name, ((230, 230, 228), 0.0))
            li = layer(path, rgb)
            mt = r3.Material(); mt.Name = name; mt.DiffuseColor = (*rgb, 255); mt.Transparency = tr_
            mats[name] = f.Materials.Add(mt)
            f.Layers[li].RenderMaterialIndex = mats[name]
        return layers[path]
    for bi, m in enumerate(masses):
        if bi % max(1, len(masses) // 25) == 0:
            T("건물·창문", bi, len(masses))
        at = m.get("attrs", {})
        use = m.get("use", "용도미상")
        fc = m.get("facade") or {}
        title = at.get("명칭") or at.get("주기") or m["layer"] or "건물"
        label = f"{title} {m['floors']}F"
        height = round(m["top"] - m["base"], 2)
        band = ("1층" if m["floors"] <= 1 else "2-4층" if m["floors"] <= 4 else
                "5-9층" if m["floors"] <= 9 else "10-19층" if m["floors"] <= 19 else "20층 이상")
        gids = [group(f"건물 {bi + 1:04d} {label}"), group(f"용도: {use}"), group(f"종류: {m['layer'] or '미상'}"),
                group(f"층수: {band}")] + ([group(f"준공: {m['year'] // 10 * 10}년대")] if m.get("year") else [])

        def a_for(li, part_name):
            a = attrs(li, f"{label} {part_name}")
            for k, v in at.items():
                a.SetUserString(k, str(v))
            a.SetUserString("용도", use)
            a.SetUserString("층수(모델)", str(m["floors"]))
            a.SetUserString("높이(m)", str(height))
            a.SetUserString("층수출처", SOURCE_KO.get(m["source"], "추정"))
            a.SetUserString("층고(m)", f"{m['floor_h']:.2f} (대장 높이÷층수)" if m.get("floor_h") else f"{floor_height:.2f} (기본값)")
            a.SetUserString("부위", part_name)
            if fc:
                a.SetUserString("외벽재료", fc["wall"]); a.SetUserString("지붕재료", fc["roof"])
                a.SetUserString("창형식", fc["window"]); a.SetUserString("재료근거", fc["basis"])
                a.SetUserString("지붕근거", fc["roof_basis"])
            for g in gids:
                a.AddToGroup(g)
            return a
        fm = facade_meshes(m, floor_height, origin) if fc.get("windows") else None
        for part in building_parts(m, floor_height):
            role, typ = part[0], part[1]
            if role == "body" and fm:
                for mat, mesh in fm.items():
                    pn = "유리" if mat == "유리" else "지붕면" if mat.startswith("지붕") else "외벽"
                    f.Objects.AddMesh(_rh_mesh(mesh), a_for(mat_layer(mat), pn))
                continue
            if role == "floorline":
                if fm:
                    continue   # 창이 층을 보여 주므로 층선은 생략
                a = a_for(layer("건물::층선", (150, 150, 146)), "층선")
            else:
                mat = material_of(m, role) if fc else None
                li = mat_layer(mat) if mat else layer(f"건물::{use}::{ROLE_KO.get(role, role)}",
                                                      USE_COLORS.get(use, (232, 232, 230)))
                a = a_for(li, ROLE_KO.get(role, role))
            if typ == "ext":
                ext = _rh_extrusion(part[2], part[3], part[4], origin)
                if ext:
                    f.Objects.AddExtrusion(ext, a)
            elif typ == "mesh":
                mm = part[2].copy(); mm.apply_translation([-origin[0], -origin[1], 0])
                f.Objects.AddMesh(_rh_mesh(mm), a)
            else:
                f.Objects.AddCurve(_rh_curve(part[2], 0, origin), a)
    if structs:
        from .structures import STRUCT_RGB
        for mesh, kind, name in structs:
            f.Objects.AddMesh(_rh_mesh(_shifted(mesh, origin)), attrs(layer(f"구조물::{kind}", STRUCT_RGB.get(kind, (176, 174, 168))), name))
    T("도로 디테일")
    for mesh, lay, rgb, name in roadbits:
        f.Objects.AddMesh(_rh_mesh(_shifted(mesh, origin), crisp=False), attrs(layer(lay, rgb), name))
    if trees:
        # 나무는 블록(수관 반지름 0.1m 단위)으로: 숲이 넓어도 파일이 가볍고, 블록 하나를 고치면 같은 크기 나무가 다 바뀐다
        from .paths_trees import TREE_KIND, tree_dims
        li_trunk = layer("나무::줄기", (150, 130, 110))
        li_crown = layer("나무::수관", (214, 205, 180))
        defs = {}

        def tree_def(r):
            if r not in defs:
                cz, tr_, rz = tree_dims(r)
                crown = r3.Sphere(r3.Point3d(0, 0, 0), 1.0).ToNurbsSurface()
                t = r3.Transform(1.0)
                t.M00, t.M11, t.M22, t.M23 = r, r, rz, cz
                crown.Transform(t)
                trunk = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), tr_), cz).ToBrep(True, True)
                idx = f.InstanceDefinitions.Add(f"나무 r{r:.1f}m", f"수관 반지름 {r:.1f}m", "", "", r3.Point3d(0, 0, 0),
                                                (crown, trunk), (attrs(li_crown, "수관"), attrs(li_trunk, "줄기")))
                defs[r] = f.InstanceDefinitions[idx].Id
            return defs[r]
        from .tree_models import FORMS, WOOD, pick
        sdefs = {}

        def species_def(key, parts):
            if key not in sdefs:
                form, rq, var = key
                fname, _, rgb = FORMS[form]
                w, lf = parts
                idx = f.InstanceDefinitions.Add(f"나무 {fname} r{rq:g}m #{var + 1}", f"{fname}, 수관 반지름 {rq:g}m",
                                                "", "", r3.Point3d(0, 0, 0), (_rh_mesh(w), _rh_mesh(lf)),
                                                (attrs(layer("나무::줄기", WOOD), "줄기"),
                                                 attrs(layer(f"나무::수관::{fname}", rgb), "수관")))
                sdefs[key] = f.InstanceDefinitions[idx].Id
            return sdefs[key]
        for ti, (x, y, z, r, kind) in enumerate(trees):
            if ti % max(1, len(trees) // 25) == 0:
                T("나무", ti, len(trees))
            xf = r3.Transform.Translation(x - origin[0], y - origin[1], z)
            if tree_style == "species":
                key, parts, sp = pick(x, y, r, kind)
                a_ = attrs(layer(f"나무::{TREE_KIND[kind]}", (214, 205, 180)), sp or TREE_KIND[kind])
                if sp:
                    a_.SetUserString("수종", sp)
                a_.SetUserString("형태", FORMS[key[0]][0])
                f.Objects.AddInstanceObject(r3.InstanceReference(species_def(key, parts), xf), a_)
                continue
            rq = round(max(r, 0.5) * 10) / 10
            f.Objects.AddInstanceObject(r3.InstanceReference(tree_def(rq), xf),
                                        attrs(layer(f"나무::{TREE_KIND[kind]}", (214, 205, 180)), TREE_KIND[kind]))
    if lights:
        T("가로등")
        from .streetlights import light_parts
        li_pole = layer("가로등::기둥", (90, 90, 92))
        li_lamp = layer("가로등::등기구", (250, 230, 160))
        ldefs = {}
        for x, y, z, h, ang, kind, src in lights:
            key = (kind, round(h, 1))
            if key not in ldefs:
                geo, at = [], []
                for m, part in light_parts(h, kind):
                    geo.append(_rh_mesh(m)); at.append(attrs(li_lamp if part == "등기구" else li_pole, part))
                idx = f.InstanceDefinitions.Add(f"{kind} {h:g}m", f"{kind} 높이 {h:g}m", "", "",
                                                r3.Point3d(0, 0, 0), tuple(geo), tuple(at))
                ldefs[key] = f.InstanceDefinitions[idx].Id
            t = r3.Transform(1.0)
            c, sn = math.cos(ang), math.sin(ang)
            t.M00, t.M01, t.M10, t.M11 = c, -sn, sn, c
            t.M03, t.M13, t.M23 = x - origin[0], y - origin[1], z
            grp = "추정" if src == "추정" else "자료"
            a_ = attrs(layer(f"가로등::{grp}", (240, 200, 90)), f"{kind} ({src})")
            a_.SetUserString("출처", src); a_.SetUserString("높이(m)", f"{h:g}")
            f.Objects.AddInstanceObject(r3.InstanceReference(ldefs[key], t), a_)
    if before_write is not None:
        T("대지 경계")
        before_write(f)      # 대지 경계 등: 다 쓴 파일을 다시 읽고 쓰지 않도록 저장 전에 더한다
    nobj = len(f.Objects)
    T("디스크에 저장", est=_write_estimate(_RH_COUNT[0], nobj))
    if tick:
        # rhino3dm 저장은 끝날 때까지 파이썬 전체를 붙잡아 화면 물음에도 답하지 못한다. 화면이 '저장 중 · 약 N초'를
        # 먼저 받아 가서 스스로 세도록 잠깐 기다린다.
        time.sleep(1.5)
    t0 = time.time()
    f.Write(path, 7)
    if os.environ.get("CONTOUR_TIMING"):
        print(f"3dm Write {time.time() - t0:.1f}s verts {_RH_COUNT[0]} objs {nobj}")


# ---------------------------------------------------------------- 출력: 3D 프린트용 STL
def _mf_from_trimesh(m):
    import manifold3d as mf
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, dtype=np.float32),
                               tri_verts=np.asarray(m.faces, dtype=np.uint32)))


def _mf_extrude(poly, z0, z1, origin):
    """shapely 면 → manifold 솔리드 (z0 ~ z1). 구멍 있는 면도 그대로."""
    import manifold3d as mf
    rings = [np.asarray(poly.exterior.coords)[:-1, :2] - origin[:2]]
    rings += [np.asarray(r.coords)[:-1, :2] - origin[:2] for r in poly.interiors]
    cs = mf.CrossSection([r.astype(np.float64) for r in rings], mf.FillRule.EvenOdd)
    if cs.area() <= 1e-6 or z1 - z0 <= 1e-6:
        return None
    return cs.extrude(float(z1 - z0)).translate((0.0, 0.0, float(z0)))


SITE_GROOVE_W = 1.0     # 대지 경계 홈 폭 (모형 mm)
SITE_GROOVE_D = 0.6     # 지형 면에서 파 들어가는 깊이 (모형 mm)


def print_solid(regions, masses, origin, scale=1000.0, field=None, combo=False, tick=None, site=None):
    """3D 프린트용 한 덩어리 솔리드(물샐틈없는 메시, 단위 mm, 모형 크기 그대로, 왼쪽 아래 바닥 = 0,0,0).

    지형(경사면은 높이 격자, 계단판은 판 돌출)과 건물 매스를 불리언 합집합으로 합친다. 나무·가로등·노면표시·
    길 층은 얇거나 떠 있어 프린트에 맞지 않아 넣지 않는다(Rhino·GLB 에는 그대로 있다).
    field: (X, Y, H, B) 경사면 높이 격자(있으면), combo 면 판 높이 B 와 겹친 윗면 = max(H, B).
    """
    import manifold3d as mf
    T = tick or (lambda *a, **k: None)
    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    mm = 1000.0 / float(scale or 1000.0)                 # 실제 m → 모형 mm
    base_z = lv[0] - interval * 2 if lv else 0.0
    parts = []
    if field is not None:
        X, Y, H = field[:3]
        top = np.maximum(H, field[3]) if combo and len(field) > 3 and field[3] is not None else H
        # 프린트 해상도: 격자 한 칸이 모형에서 0.5mm 이상이 되게 솎는다(노즐보다 잘게 해도 안 보인다)
        cell = float(abs(X[0, 1] - X[0, 0])) if X.shape[1] > 1 else 1.0
        k = max(1, int(np.ceil(0.5 / max(cell * mm, 1e-9))))
        Xs, Ys, Hs = X[::k, ::k], Y[::k, ::k], top[::k, ::k]
        base_z = min(base_z, float(np.nanmin(Hs)) - interval)
        T("STL 지형")
        parts.append(_mf_from_trimesh(heightfield_mesh(Xs, Ys, Hs, base_z, origin)))
    else:
        # 계단판도 높이 격자로(격자 한 칸 = 모형 0.4mm): 판 돌출을 합치면 판끼리 점·모서리로 닿는 곳에서 꼬인 모서리가
        # 생기는데, 높이 격자 솔리드는 언제나 물샐틈없다. 0.4mm 계단 윤곽은 노즐 굵기라 출력에서 거의 안 보인다.
        T("STL 계단판")
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        env = regions[lv[0]].envelope.bounds if lv else (0, 0, 1, 1)
        cell = 0.4 / mm
        nx = max(2, int(np.ceil((env[2] - env[0]) / cell)) + 1)
        ny = max(2, int(np.ceil((env[3] - env[1]) / cell)) + 1)
        tf = from_origin(env[0] - cell / 2, env[3] + cell / 2, cell, cell)
        Hs = np.full((ny, nx), lv[0] if lv else 0.0)
        for L in lv[1:]:
            if not regions[L].is_empty:
                msk = rasterize([(regions[L], 1)], out_shape=(ny, nx), transform=tf, all_touched=False, dtype="uint8")
                Hs[msk == 1] = L
        xs = env[0] + np.arange(nx) * cell
        ys = env[3] - np.arange(ny) * cell
        Xs, Ys = np.meshgrid(xs, ys)
        Xs, Ys, Hs = Xs[::-1], Ys[::-1], Hs[::-1]          # 아래(남)부터
        parts.append(_mf_from_trimesh(heightfield_mesh(Xs, Ys, Hs, base_z, origin)))
    model_box = regions[lv[0]].envelope if lv else None
    if site is not None and not site.is_empty:
        # 대지 경계: 경계선을 폭 1mm 띠로 만들어 지형 면을 따라 0.6mm 깊이 홈으로 판다(건물은 뒤에 합치므로 그대로).
        T("STL 대지 경계선")
        try:
            w, d = SITE_GROOVE_W / mm, SITE_GROOVE_D / mm
            rings = [r for g in as_polys(site) for r in [g.exterior, *g.interiors]]
            band = unary_union([LineString(r.coords).buffer(w / 2, join_style=2) for r in rings])
            if model_box is not None:
                band = band.intersection(model_box)
            top_z = float(np.nanmax(Hs)) + interval
            low = _mf_from_trimesh(heightfield_mesh(Xs, Ys, np.maximum(Hs - d, base_z + d), base_z, origin))
            cut = [pr for p in as_polys(band) if (pr := _mf_extrude(p, base_z + d * 0.5, top_z, origin)) is not None]
            if cut:
                cutter = (mf.Manifold.batch_boolean(cut, mf.OpType.Add) if len(cut) > 1 else cut[0]) - low
                parts[0] = parts[0] - cutter
        except Exception as e:      # 홈 실패해도 모델은 그대로
            print("STL 대지 경계선 건너뜀:", e)
    T("STL 건물", 0, len(masses))
    for b in masses:
        for p in as_polys(b["poly"].buffer(0.05, join_style=2).intersection(model_box)):
            m = _mf_extrude(p, min(b["base"], b["top"]) - interval, b["top"], origin)
            if m is not None:
                parts.append(m)
    T("STL 합치기 (구멍·겹침 없는 한 덩어리로)")
    solid = mf.Manifold.batch_boolean(parts, mf.OpType.Add) if len(parts) > 1 else parts[0]
    try:      # 평평한 판 윗면의 잔 삼각형 줄이기 (모양 차이 0.02mm 이내, 물샐틈없는 상태는 그대로)
        solid = solid.simplify(0.02 / mm)
    except Exception:
        pass
    out = solid.to_mesh()
    v = np.asarray(out.vert_properties)[:, :3].astype(float)
    v[:, 2] -= base_z
    v *= mm
    vmin = v[:, :2].min(axis=0)
    v[:, :2] -= vmin
    f = np.asarray(out.tri_verts).astype(np.int64)
    if os.environ.get("CONTOUR_TIMING"):
        print("STL 검사(떼기 전):", stl_check(v, f))
    v = _unpinch(v, f)
    return trimesh.Trimesh(v, f, process=False)


def stl_check(v, f):
    """슬라이서처럼 STL(float32) 좌표가 같은 점을 한 점으로 보고 모서리를 센다 → (열린, 꼬인, 납작한 삼각형)."""
    _, inv = np.unique(np.asarray(v, dtype=np.float32), axis=0, return_inverse=True)
    F = inv.reshape(-1)[np.asarray(f)]
    flat = int(((F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])).sum())
    E = np.sort(np.r_[F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]], axis=1)
    _, c = np.unique(E, axis=0, return_counts=True)
    return int((c == 1).sum()), int((c > 2).sum()), flat


def _unpinch(v, f, eps=0.01):
    """불리언 결과에서 두 덩어리가 모서리·점 하나로만 닿은 곳(예: 꼭짓점끼리 맞닿은 두 건물의 세로 모서리)은 manifold 안에서는
    서로 다른 점이지만 STL 로 쓰면 같은 좌표가 되어 슬라이서가 '꼬인 모서리(non-manifold edge)'로 본다.
    같은 좌표에 겹친 점들을 각자 붙은 면 쪽으로 eps mm 만큼 떼어 놓는다(모양 차이 0.01mm)."""
    v = np.array(v, dtype=float)
    for _ in range(3):
        key = np.asarray(v, dtype=np.float32)
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        inv = inv.reshape(-1)
        dup = np.where(cnt[inv] > 1)[0]
        if not len(dup):
            break
        cen = v[f].mean(axis=1)
        acc = np.zeros_like(v); num = np.zeros(len(v))
        np.add.at(acc, f.ravel(), np.repeat(cen, 3, axis=0)); np.add.at(num, f.ravel(), 1)
        d = acc[dup] / np.maximum(num[dup], 1)[:, None] - v[dup]
        d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
        v[dup] += d * eps
    return v


# ---------------------------------------------------------------- 출력: STL
def _shift(p, origin):
    return Polygon([(x - origin[0], y - origin[1]) for x, y in p.exterior.coords],
                   [[(x - origin[0], y - origin[1]) for x, y in h.coords] for h in p.interiors])


def write_stl(path, regions, masses, origin, areas=(), trees=(), terrain_mesh=None, area_meshes=(),
              steps_too=False, floor_height=FLOOR_HEIGHT, inlays=(), lights=(), structs=(), tree_style="species",
              roadbits=(), site_mesh=None, tick=None, scale=1000.0, field=None, site_poly=None):
    from .building_detail import building_parts
    from .paths_trees import PATH_STYLE, TREE
    T = tick or (lambda *a, **k: None)
    T("STL 부품 모으기")
    meshes = []
    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    tr = trimesh.transformations.translation_matrix
    with_steps = terrain_mesh is None or steps_too
    cut = cut_regions(regions, inlays)
    for i, L in enumerate(lv if with_steps else []):
        bottom = L - interval if i else L - interval * 2
        for p in as_polys(cut[L]):
            try:
                m = trimesh.creation.extrude_polygon(_shift(p, origin), L - bottom)
            except Exception:
                continue
            m.apply_transform(tr([0, 0, bottom])); meshes.append(m)
    n_step = len(meshes)
    inlay_meshes = defaultdict(list)
    for p, L, kind in (inlays if with_steps else []):
        try:
            m = trimesh.creation.extrude_polygon(_shift(p, origin), interval)
        except Exception:
            continue
        m.apply_transform(tr([0, 0, L - interval])); inlay_meshes[kind].append(m)
    if terrain_mesh is not None:
        meshes.append(terrain_mesh)
    n_terr = len(meshes)
    for b in masses:
        for part in building_parts(b, floor_height):
            try:
                if part[1] == "ext":
                    m = trimesh.creation.extrude_polygon(_shift(part[2], origin), part[4] - part[3])
                    m.apply_transform(tr([0, 0, part[3]]))
                elif part[1] == "mesh":
                    m = part[2].copy(); m.apply_translation([-origin[0], -origin[1], 0])
                else:
                    continue
            except Exception:
                continue
            meshes.append(m)
    meshes += [_shifted(mm, origin) for mm, k, n in structs]
    n_bld = len(meshes)
    path_meshes = defaultdict(list)
    for p, L, kind in areas:
        try:
            m = trimesh.creation.extrude_polygon(_shift(p, origin), PATH_STYLE[kind][2])
        except Exception:
            continue
        m.apply_transform(tr([0, 0, L])); path_meshes[kind].append(m)
    for m, kind in area_meshes:
        path_meshes[kind].append(m)
    tree_meshes = []
    if trees and tree_style != "species":      # 프린트 STL 에는 나무를 넣지 않는다. 단순 나무 미리보기(GLB)용만
        from .paths_trees import tree_dims
        crown0 = trimesh.creation.icosphere(subdivisions=2, radius=1.0)   # 아래에서 (r, r, rz)로 늘여 타원구
        trunk0 = trimesh.creation.cylinder(radius=1.0, height=1.0, sections=8)
        trunk0.apply_translation([0, 0, 0.5])
        for ti, (x, y, z, r, kind) in enumerate(trees):
            if ti % max(1, len(trees) // 20) == 0:
                T("STL 나무", ti, len(trees))
            cz, tr_, rz = tree_dims(r)
            c = crown0.copy(); c.apply_scale([r, r, rz]); c.apply_translation([x - origin[0], y - origin[1], z + cz])
            t = trunk0.copy(); t.apply_scale([tr_, tr_, cz]); t.apply_translation([x - origin[0], y - origin[1], z])
            tree_meshes.append(trimesh.util.concatenate([c, t]))
    # 3D 프린트용 STL: 지형 + 건물을 한 덩어리 솔리드로, 단위 mm·모형 크기 그대로 (v2.0)
    t0 = time.time()
    scene = print_solid(regions, masses, origin, scale, field, steps_too, tick=T, site=site_poly)
    T("STL 저장")
    scene.export(path)
    if os.environ.get("CONTOUR_TIMING"):
        print(f"STL print solid {time.time() - t0:.1f}s faces {len(scene.faces)} watertight {scene.is_watertight}")
    op, nm, fl = stl_check(scene.vertices, scene.faces)
    if op or nm:
        print(f"STL 검사: 열린 모서리 {op}, 꼬인 모서리 {nm}, 납작한 삼각형 {fl}")
    T("미리보기 GLB")
    # 미리보기용 GLB (지형 회색, 건물 흰색)
    terr = trimesh.util.concatenate(meshes[:n_step] if steps_too else meshes[:n_terr])
    terr.visual.face_colors = [178, 178, 172, 255]
    blds = meshes[n_terr:n_bld]
    sc = trimesh.Scene()
    sc.add_geometry(terr, geom_name="terrain_step" if steps_too else "terrain")
    if steps_too and terrain_mesh is not None:
        fill = terrain_mesh.copy(); fill.visual.face_colors = [196, 194, 186, 255]
        sc.add_geometry(fill, geom_name="terrain_slope")
    if blds:
        bm = crisp_mesh(trimesh.util.concatenate(blds)); bm.visual.face_colors = [246, 246, 242, 255]
        sc.add_geometry(bm, geom_name="buildings")
    for kind, ms in inlay_meshes.items():
        pm = trimesh.util.concatenate(ms); pm.visual.face_colors = [*PATH_STYLE[kind][1], 255]
        sc.add_geometry(pm, geom_name=f"inlay_{kind}")
    for kind, ms in path_meshes.items():
        pm = trimesh.util.concatenate(ms)
        if "vertex_normals" not in pm._cache.cache:
            pm = crisp_mesh(pm)
        pm.visual.face_colors = [*PATH_STYLE[kind][1], 255]
        sc.add_geometry(pm, geom_name=f"path_{kind}")
    if trees and tree_style == "species":
        # 미리보기 GLB: 수종 형태별 메시 하나를 여러 자리에 놓는다(인스턴스) — 수만 그루도 가볍다
        from .tree_models import FORMS, WOOD, pick
        made = set()
        for i, (x, y, z, r, kind) in enumerate(trees):
            key, (w, lf), _ = pick(x, y, r, kind)
            gname = "tree_{}_{:g}_{}".format(*key)
            if gname not in made:
                w2, l2 = w.copy(), lf.copy()
                w2.visual.face_colors = [*WOOD, 255]; l2.visual.face_colors = [*FORMS[key[0]][2], 255]
                sc.add_geometry(trimesh.util.concatenate([w2, l2]), geom_name=gname, node_name=f"t{i}",
                                transform=tr([x - origin[0], y - origin[1], z]))
                made.add(gname)
            else:
                sc.graph.update(frame_to=f"t{i}", frame_from=sc.graph.base_frame,
                                matrix=tr([x - origin[0], y - origin[1], z]), geometry=gname)
    elif tree_meshes:
        tm = trimesh.util.concatenate(tree_meshes); tm.visual.face_colors = [222, 214, 190, 255]
        sc.add_geometry(tm, geom_name="trees")
    if lights:   # 가로등은 3D 프린트(STL)에는 너무 가늘어 미리보기(GLB)에만
        from .streetlights import placed_mesh
        lm = trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin) for x, y, z, h, ang, kind, _ in lights])
        lm.visual.face_colors = [96, 96, 100, 255]
        sc.add_geometry(lm, geom_name="streetlights")
    by_rgb = defaultdict(list)       # 도로 디테일(노면표시·신호등·전봇대): 미리보기에만, 색별로 묶어
    for mesh, lay, rgb, name in roadbits:
        by_rgb[tuple(rgb)].append(_shifted(mesh, origin))
    for i, (rgb, ms) in enumerate(by_rgb.items()):
        rm = trimesh.util.concatenate(ms); rm.visual.face_colors = [*rgb, 255]
        sc.add_geometry(rm, geom_name=f"road_detail_{i}")
    if site_mesh is not None:   # 대지 경계 띠: 미리보기(GLB)에만
        sm = site_mesh.copy(); sm.visual.face_colors = [37, 99, 235, 255]
        sc.add_geometry(sm, geom_name="site_boundary")
    sc.export(os.path.splitext(path)[0] + ".glb")
    return scene


def _road_meshes(info, hz, regions, road_thickness):
    """도로 디테일 → [(월드 좌표 메시, 레이어, 색, 이름)]. hz 가 있으면 곡면, 없으면 계단판 높이."""
    from .road_detail import FIX_RGB, PAINT, flat_mesh, placed, pole_parts, signal_parts
    from .paths_trees import PATH_STYLE
    walk = PATH_STYLE["sidewalk"][2]
    if hz is not None:
        def zr(v):                      # 차도 윗면
            return np.asarray(hz(v[:, 0], v[:, 1]), float) + road_thickness

        def zg(v):                      # 땅(주차장) 윗면: 곡면 덮개가 5cm 떠 있음
            return np.asarray(hz(v[:, 0], v[:, 1]), float) + 0.05

        def zp(x, y):                   # 보도 위
            return float(hz([x], [y])[0]) + road_thickness + walk
    else:
        def zr(v):
            return np.full(len(v), level_at(regions, Point(*v.mean(axis=0))) + 0.03)
        zg = zr

        def zp(x, y):
            return level_at(regions, Point(x, y)) + walk
    out = []
    if info.get("transit"):
        from .transit import meshes as transit_meshes
        out += transit_meshes(info["transit"], zp, lambda x, y: float(zr(np.array([[x, y]]))[0]) - 0.03 * (hz is None))
    if info.get("underground"):
        from .underground import meshes as ug_meshes
        ug = info["underground"]
        out += ug_meshes(ug, lambda x, y: float(zg(np.array([[x, y]], float))[0]), ug.get("road"), ug.get("clip"))
    if info.get("islands") is not None:
        from .transit import island_beds
        out += island_beds(info["islands"], zp)
    groups = defaultdict(list)
    for g, kind, src in info["marks"]:
        groups[(kind, src)].append(g)
    for (kind, src), polys in groups.items():
        m = flat_mesh(polys, zg if kind == "주차구획" else zr, lift=0.03)
        if m is not None:
            out.append((m, f"도로시설::노면표시::{kind}::{src}", PAINT.get(kind, (240, 240, 234)), f"{kind}({src})"))
    sig = defaultdict(list)
    for x, y, ang, kind, src, arm in info["signals"]:
        sig[(kind, src)].append(placed(signal_parts(kind, arm), x, y, zp(x, y), ang))
    for (kind, src), ms in sig.items():
        out.append((trimesh.util.concatenate(ms), f"도로시설::신호등::{kind}::{src}", FIX_RGB["신호등"],
                    f"{kind} 신호등 {len(ms)}개({src})"))
    pol = defaultdict(list)
    base = pole_parts()
    for x, y, src in info["poles"]:
        pol[src].append(placed(base, x, y, zp(x, y), math.atan2(y, x) % math.pi))
    for src, ms in pol.items():
        out.append((trimesh.util.concatenate(ms), f"도로시설::전봇대::{src}", FIX_RGB["전봇대"], f"전봇대 {len(ms)}개({src})"))
    return out


# ---------------------------------------------------------------- 출력: 레이저 DXF
def write_laser(out_dir, regions, masses, draped, origin, scale, areas=(), trees=(),
                edges=(), tree_hole=1.0, greens=(), lights=(), site=None):
    """판마다 DXF 한 장. 단위 mm.

    CUT=절단, CUT_ROAD=도로 자리 절단(판에서 파내고, 같은 선으로 도로 조각을 다른 재료에서 잘라 끼움),
    ENGRAVE=선 각인(윗판 위치, 도로경계, 건물 자리),
    RASTER=면 각인(보도·교통섬, HATCH 채움), HATCH_STAIRS=계단 빗금,
    TREE=가로수 꽂을 구멍(지름 tree_hole mm), TREE_GREEN=녹지 나무 구멍, GREEN=녹지 경계 각인,
    LIGHT=가로등 자리 작은 구멍(지름 0.5mm, 핀을 꽂거나 각인).
    """
    from .paths_trees import PATH_STYLE
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        if old.startswith("sheet_") and old.endswith(".dxf"):
            os.remove(os.path.join(out_dir, old))
    k = 1000.0 / scale  # m -> 모형 mm
    lv = list(regions)
    by_level = defaultdict(list)
    through = defaultdict(list)   # 건물이 뚫고 지나가는 판: 그 판에서 발자국 자리를 잘라 낸다
    for m in masses:
        by_level[m.get("level", m["base"])].append(m["poly"])
        b0 = m.get("level", m["base"])   # 레이저 판은 계단 기준 바닥으로
        for L in m.get("through", ()):
            if b0 < L:
                through[L].append(m["poly"])
    roads = defaultdict(list)
    for ls, L in draped:
        roads[L].append(ls)

    area_by = defaultdict(list)
    for p, L, kind in areas:
        area_by[L].append((p, kind))
    green_by = defaultdict(list)
    for p, L, kind in greens:
        green_by[L].append((p, kind))
    edge_by = defaultdict(list)
    for ls, L in edges:
        edge_by[L].append(ls)
    tree_by = defaultdict(list)
    for x, y, z, r, kind in trees:
        tree_by[z].append((x, y, kind))
    light_by = defaultdict(list)
    for x, y, z, *_ in lights:
        light_by[z].append((x, y))

    def tx(coords):
        return [((x - origin[0]) * k, (y - origin[1]) * k) for x, y in coords]
    files = []
    for i, L in enumerate(lv):
        doc = ezdxf.new("R2010"); doc.units = ezdxf.units.MM
        doc.layers.add("CUT", color=1); doc.layers.add("ENGRAVE", color=5)
        doc.layers.add("LABEL", color=3); doc.layers.add("RASTER", color=8)
        doc.layers.add("HATCH_STAIRS", color=4); doc.layers.add("TREE", color=6)
        doc.layers.add("TREE_GREEN", color=3); doc.layers.add("GREEN", color=3); doc.layers.add("GRASS", color=51)
        doc.layers.add("CUT_ROAD", color=30); doc.layers.add("LIGHT", color=2)
        if site is not None:
            doc.layers.add("SITE", color=140)
        msp = doc.modelspace()
        for p in as_polys(regions[L]):
            for ring in [p.exterior, *p.interiors]:
                msp.add_lwpolyline(tx(ring.coords), close=True, dxfattribs={"layer": "CUT"})
        if i + 1 < len(lv):
            for p in as_polys(regions[lv[i + 1]]):
                msp.add_lwpolyline(tx(p.exterior.coords), close=True, dxfattribs={"layer": "ENGRAVE"})
        for ls in roads[L]:
            msp.add_lwpolyline(tx(ls.coords), dxfattribs={"layer": "ENGRAVE"})
        for p in by_level[L]:
            msp.add_lwpolyline(tx(p.exterior.coords), close=True, dxfattribs={"layer": "ENGRAVE"})
        for p in through[L]:
            for q in as_polys(p.intersection(regions[L])):
                if q.area > 0.5:
                    msp.add_lwpolyline(tx(q.exterior.coords), close=True, dxfattribs={"layer": "CUT"})
        for ls in edge_by[L]:
            msp.add_lwpolyline(tx(ls.coords), dxfattribs={"layer": "ENGRAVE"})
        for p, kind in area_by[L]:
            mode = PATH_STYLE[kind][3]
            if kind in INLAY_KINDS:
                # 도로는 이 판에서 잘라 내고, 같은 선으로 다른 재료(회색 판)에서 도로 조각을 잘라 끼운다
                for ring in [p.exterior, *p.interiors]:
                    msp.add_lwpolyline(tx(ring.coords), close=True, dxfattribs={"layer": "CUT_ROAD"})
                continue
            if mode == "outline":
                for ring in [p.exterior, *p.interiors]:
                    msp.add_lwpolyline(tx(ring.coords), close=True, dxfattribs={"layer": "ENGRAVE"})
                continue
            lay = "RASTER" if mode == "raster" else "HATCH_STAIRS"
            h = msp.add_hatch(dxfattribs={"layer": lay})
            if mode == "hatch":
                h.set_pattern_fill("ANSI31", scale=0.4)
            h.paths.add_polyline_path(tx(p.exterior.coords), is_closed=True, flags=1)
            for ring in p.interiors:
                h.paths.add_polyline_path(tx(ring.coords), is_closed=True)
        for x, y, kind in tree_by[L]:
            msp.add_circle(tx([(x, y)])[0], tree_hole / 2,
                           dxfattribs={"layer": "TREE" if kind == "street" else "TREE_GREEN"})
        for x, y in light_by[L]:
            msp.add_circle(tx([(x, y)])[0], 0.25, dxfattribs={"layer": "LIGHT"})
        for p, kind in green_by[L]:
            for ring in [p.exterior, *p.interiors]:
                msp.add_lwpolyline(tx(ring.coords), close=True,
                                   dxfattribs={"layer": "GRASS" if kind == "grass" else "GREEN"})
        if site is not None:
            # 대지 경계: 이 판 윗면에 드러나는 부분만 각인(윗판에 덮이는 곳은 빼고)
            show = regions[L].difference(regions[lv[i + 1]]) if i + 1 < len(lv) else regions[L]
            for sp in as_polys(site):
                for ring in [sp.exterior, *sp.interiors]:
                    seg = ring.intersection(show)
                    for ls in getattr(seg, "geoms", [seg]):
                        if ls.geom_type == "LineString" and ls.length > 0.3:
                            msp.add_lwpolyline(tx(ls.coords), dxfattribs={"layer": "SITE"})
        rp = regions[L].representative_point()
        msp.add_text(f"{i:02d}  EL {L:g}", height=4, dxfattribs={"layer": "LABEL"}).set_placement(tx(rp.coords)[0])
        fn = os.path.join(out_dir, f"sheet_{i:02d}_EL{L:g}.dxf")
        doc.saveas(fn); files.append(fn)
    return files


# ---------------------------------------------------------------- 메인
DEFAULTS = dict(site=None, site_clear=False, site_keep_terrain=False, dem=None, terrain_fit="smooth", breaklines=False, pads=True, parcel_file=None, scale=1000, interval=5, min_area=None, floor_height=FLOOR_HEIGHT, sigma=4.0, smooth=1.5,
                terrain="combo", no_trees=False, ndvi=None, aerial=None, crs_wkt=None, vworld_key=None,
                vworld_domain="", data_key=None, cache_dir=None, tree_spacing=8.0, tree_hole=1.0,
                road_thickness=ROAD_THICKNESS, sketchup=True, green=True, lights=True, light_files=(),
                max_forest_trees=30000, facade=True, facade_radius=None, detail=3,
                structures=True, road_detail=True, road_files=(), seoul_key=None, safemap_key=None,
                seoul_service=None, safemap_layer=None, safemap_light_layer=None, seoul_code=None, hd_dir=None)


def _crs_wkt(a):
    if a.crs_wkt:
        return a.crs_wkt
    if os.path.isdir(a.src):
        from .pipeline import read_crs
        return read_crs(a.src).to_wkt()
    return None


def _green_areas(a, path_areas, masses, bbox, log, pg):
    """숲·조경 녹지 = 수치지도 식생 면 + 항공사진 수관 + 위성 NDVI 를 겹친 것. 반환 {"forest","garden"}"""
    from .vegetation import aerial_green_areas, combine_greens, green_areas, map_green_areas
    pg.step("5.4")
    blocked = unary_union([m["poly"].buffer(1.5) for m in masses] +
                          [g.buffer(0.5) for g in path_areas.values()])
    sources, bare, ae = [], None, None

    def ha(d):
        return (d["forest"].area + d["garden"].area) / 1e4
    try:
        mp = map_green_areas(a.src, bbox)
        bare = mp.pop("bare")
        if ha(mp) > 0:
            sources.append(mp)
            log(f"   수치지도 식생 면: 숲 {mp['forest'].area / 1e4:.2f}ha · 과수원 등 {mp['garden'].area / 1e4:.2f}ha")
    except Exception as e:
        log(f"   (수치지도 식생 면을 읽지 못함: {e})")
    if a.aerial:
        try:
            ae = aerial_green_areas(a.aerial, bbox, preview_path=os.path.join(a.out, "aerial_green.png"))
            log(f"   항공사진 판독: 나무 수관 {ae['canopy'].area / 1e4:.2f}ha(나무 {len(ae['crowns'])}그루 자리) · "
                f"풀·잔디 {ae['grass'].area / 1e4:.2f}ha → aerial_green.png")
            # 사진이 있으면 사진이 기준: 수치지도·위성(10m) 녹지는 사진에서 수관이 보이는 곳 둘레(3m)만 남기고, 풀로 판독된 곳은 뺀다
            keep = ae["canopy"].buffer(3.0)
            sources = [{k: v.intersection(keep).difference(ae["grass"]) for k, v in src.items()} for src in sources]
            sources.append({"forest": ae["forest"], "garden": ae["garden"]})
        except Exception as e:
            ae = None
            log(f"   (항공사진에서 숲을 찾지 못함: {e})")
    if a.ndvi:
        try:
            nd = green_areas(a.ndvi, bbox, Polygon())
            if ae is not None:
                keep = ae["canopy"].buffer(3.0)
                nd = {k: v.intersection(keep).difference(ae["grass"]) for k, v in nd.items()}
            sources.append(nd)
            log(f"   위성 식생지수: 숲 {nd['forest'].area / 1e4:.2f}ha · 조경 {nd['garden'].area / 1e4:.2f}ha")
        except Exception as e:
            log(f"   (위성 녹지 자료를 읽지 못함: {e})")
    if not sources:
        log("   숲을 찾을 자료가 없습니다 (위성영상 받기 실패, 브이월드 키 없음, 수치지도 식생 면 없음)")
        return {"forest": Polygon(), "garden": Polygon()}
    out = combine_greens(sources, blocked, bare)
    if ae is not None:
        out["grass"] = ae["grass"].difference(blocked).difference(unary_union([out["forest"], out["garden"]]))
        zone = unary_union([out["forest"], out["garden"]]).buffer(2.0)
        from shapely import contains_xy, prepare
        prepare(zone); prepare(blocked)
        cr = ae["crowns"]
        if cr:
            P = np.array([(x, y) for x, y, _ in cr])
            ok = contains_xy(zone, P[:, 0], P[:, 1]) & ~contains_xy(blocked, P[:, 0], P[:, 1])
            out["crowns"] = [c for c, k in zip(cr, ok) if k]
    log(f"   나무를 채울 녹지: 숲 {out['forest'].area / 1e4:.2f}ha · 조경 {out['garden'].area / 1e4:.2f}ha"
        + (f" · 나무 없는 풀밭 {out['grass'].area / 1e4:.2f}ha" if out.get("grass") is not None else ""))
    pg.step("5.5")
    return out


def _green_trees(a, geom, regions, avoid, log):
    from .vegetation import FOREST, GARDEN, scatter_trees, spacing_for
    out = []
    if geom.get("crowns"):
        # 항공사진에서 찾은 수관 하나하나에 나무를 세운다(격자로 흩뿌리지 않음)
        from shapely import contains_xy, prepare
        fz = geom["forest"].buffer(2.0)
        prepare(fz)
        cr = sorted(geom["crowns"], key=lambda c: -c[2])[:a.max_forest_trees]
        av = np.asarray([(t[0], t[1]) for t in avoid]) if len(avoid) else None
        if av is not None:
            from scipy.spatial import cKDTree
            d, _ = cKDTree(av).query([(x, y) for x, y, _ in cr])
            cr = [c for c, dd in zip(cr, d) if dd > 2.5]
        P = np.array([(x, y) for x, y, _ in cr]) if cr else np.zeros((0, 2))
        inf = contains_xy(fz, P[:, 0], P[:, 1]) if len(P) else []
        for (x, y, r), f_ in zip(cr, inf):
            out.append((x, y, level_at(regions, Point(x, y)), float(np.clip(r, 1.2, 6.0)), "forest" if f_ else "garden"))
        log(f"   녹지 나무 {len(out)}그루 — 항공사진 수관 자리 그대로 (숲 {sum(t[4] == 'forest' for t in out)} · 조경 {sum(t[4] == 'garden' for t in out)})")
        return out
    for key, cfg in (("forest", FOREST), ("garden", GARDEN)):
        sp = cfg["spacing"]
        if key == "forest":
            sp = spacing_for(geom[key].area, sp, a.max_forest_trees)
            if sp > cfg["spacing"] + 0.05:
                log(f"   숲이 넓어 나무 간격을 {cfg['spacing']:g}m → {sp:.1f}m 로 넓혔습니다 (숲 나무 최대 {a.max_forest_trees}그루)")
        for x, y, r in scatter_trees(geom[key], sp, cfg["r"], seed=1 if key == "forest" else 2,
                                     avoid=avoid + out):
            out.append((x, y, level_at(regions, Point(x, y)), r, key))
    log(f"   녹지 나무 {len(out)}그루 (숲 {sum(t[4] == 'forest' for t in out)} · 조경 {sum(t[4] == 'garden' for t in out)})")
    return out


def _aerial_accuracy(out, found, reg_pts, walk, log, tol=3.0):
    """항공사진으로 찾은 나무를 가로수 대장(정답지)과 맞대 본다 → aerial_accuracy.json/csv.

    찾을 수 있는 곳(보도 둘레 3m)에 있는 대장 나무만 센다. 3m 안에서 1:1로 짝지으면 '맞힘'."""
    import csv
    import json as _json
    from scipy.spatial import cKDTree
    zone = walk.buffer(3.0)
    truth = [p for p in reg_pts if zone.contains(Point(p))]
    det = [(t[0], t[1]) for t in found]
    pairs, used = [], set()
    if truth and det:
        tt = cKDTree(truth)
        cand = []
        for i, d in enumerate(det):
            for j in tt.query_ball_point(d, tol):
                cand.append((math.dist(d, truth[j]), i, j))
        di = set()
        for dd, i, j in sorted(cand):
            if i not in di and j not in used:
                di.add(i); used.add(j); pairs.append((i, j, dd))
    hit = len(pairs)
    res = dict(대장_나무=len(truth), 사진_찾음=len(det), 맞힘=hit,
               놓침=len(truth) - hit, 잘못_잡음=len(det) - hit,
               찾은_비율=round(hit / len(truth), 3) if truth else None,
               맞은_비율=round(hit / len(det), 3) if det else None,
               평균_위치오차_m=round(sum(p[2] for p in pairs) / hit, 2) if hit else None,
               기준=f"대장 나무와 {tol:.0f}m 안에서 1:1로 짝지으면 맞힘. 보도 둘레 3m 안의 대장 나무만 셈.")
    with open(os.path.join(out, "aerial_accuracy.json"), "w", encoding="utf-8") as f:
        _json.dump(res, f, ensure_ascii=False, indent=1)
    hit_i = {i for i, _, _ in pairs}
    hit_j = {j for _, j, _ in pairs}
    with open(os.path.join(out, "aerial_accuracy.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["x", "y", "구분"])
        w.writerows([(round(x, 2), round(y, 2), "맞힘(사진)" if i in hit_i else "잘못 잡음(사진)") for i, (x, y) in enumerate(det)])
        w.writerows([(round(x, 2), round(y, 2), "놓침(대장)") for j, (x, y) in enumerate(truth) if j not in hit_j])
    if truth:
        log(f"   항공사진 정확도(가로수 대장 기준): 대장 {len(truth)}그루 중 {hit}그루 찾음({res['찾은_비율']:.0%}), "
            f"사진에서 잡은 {len(det)}개 중 {len(det) - hit}개는 대장에 없음 → aerial_accuracy.json")
    return res


def _real_street_trees(a, path_areas, masses, regions, bbox, log, pg):
    """가로수 = 가로수 대장(서울시·산림청 실제 위치·수종) + 수치지도 표시 + 항공사진에서 확인된 나머지.

    일정 간격으로 지어내지 않는다. 반환: ([(x, y, z, 수관 반지름, "street")], [(x, y, 수종, 출처)])
    """
    from shapely.ops import nearest_points
    from .aerial import detect_street_trees, mapped_trees, preview
    from .tree_models import remember
    from .tree_registry import crown_r, trees_in
    walk = unary_union([path_areas[k] for k in ("sidewalk", "island") if k in path_areas])
    walk_in = walk.buffer(-0.4) if not walk.is_empty else walk
    known = []   # (x, y, r, 수종, 출처)
    crs_wkt = _crs_wkt(a)
    if crs_wkt:
        reg = trees_in(bbox, crs_wkt)
        for lab in dict.fromkeys(t[4] for t in reg):
            log(f"   {lab}에서 {sum(t[4] == lab for t in reg)}그루")
        for x, y, sp, dbh, lab in reg:
            p = Point(x, y)
            if not walk.is_empty and not walk.contains(p):
                q = nearest_points(walk_in if not walk_in.is_empty else walk, p)[0]
                if q.distance(p) < 3.5:   # 연석에 걸친 좌표는 보도 안으로
                    x, y = q.x, q.y
            known.append((x, y, crown_r(sp, dbh), sp, lab))
            remember(x, y, sp)
    for x, y in mapped_trees(a.src, bbox):
        if all((x - k[0]) ** 2 + (y - k[1]) ** 2 > 9 for k in known):
            known.append((x, y, 2.5, "", "수치지도"))
    pg.step("5.2")
    found = []
    if a.aerial and not walk.is_empty:
        blds = unary_union([m["poly"] for m in masses]) if masses else None
        found, info = detect_street_trees(a.aerial, walk, path_areas.get("road"), blds)
        reg_pts = [(k[0], k[1]) for k in known if k[4] != "수치지도"]
        if reg_pts:
            try:
                _aerial_accuracy(a.out, found, reg_pts, walk, log)
            except Exception as e:
                log(f"   (항공사진 정확도 확인표 생략: {e})")
        # 이미 아는 나무 자리(4m 안)는 빼고 대장에 없는 나무만 보충
        found = [t for t in found if all((t[0] - k[0]) ** 2 + (t[1] - k[1]) ** 2 > 16 for k in known)]
        log(f"   항공사진({info['source']})에서 대장에 없는 가로수 {len(found)}그루 더 확인")
        try:
            preview(os.path.join(a.out, "aerial_trees.png"), info, found, walk, [(k[0], k[1]) for k in known])
        except Exception as e:
            log(f"   (항공사진 확인 그림 생략: {e})")
    elif not known:
        log("   이 범위에는 가로수 대장·수치지도 가로수가 없고 항공사진도 없어 가로수를 넣지 않습니다")
    pg.step("5.3")
    rows = [(x, y, sp, src, r) for x, y, r, sp, src in known] + [(x, y, "", "항공사진", r) for x, y, r in found]
    trees = [(x, y, level_at(regions, Point(x, y)), r, "street") for x, y, r, *_ in known]
    trees += [(x, y, level_at(regions, Point(x, y)), r, "street") for x, y, r in found]
    try:
        import csv
        with open(os.path.join(a.out, "street_trees.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["x", "y", "수종", "출처", "수관반지름(m)"])
            w.writerows([(round(x, 2), round(y, 2), sp, src, round(r, 2)) for x, y, sp, src, r in rows])
    except Exception:
        pass
    return trees


def _pad_parcels(a, bbox, log):
    """계단식 대지에 쓸 연속지적도 필지: 사용자가 준 지적도 파일 → 브이월드 2D데이터(키가 있을 때). 없으면 []."""
    from .pads import jimok, load_parcels
    crs_wkt = a.crs_wkt
    if not crs_wkt and os.path.isdir(a.src):
        try:
            from .pipeline import read_crs
            crs_wkt = read_crs(a.src).to_wkt()
        except Exception:
            crs_wkt = None
    if not crs_wkt:
        return []
    try:
        if a.parcel_file:
            pcl = load_parcels(a.parcel_file, bbox, crs_wkt)
            log(f"   연속지적도 파일에서 필지 {len(pcl):,}개")
            return pcl
        if a.vworld_key:
            from .publicdata import parcels
            cache = a.cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
            b = (bbox[0] - 20, bbox[1] - 20, bbox[2] + 20, bbox[3] + 20)
            pcl = [(g, jimok(j)) for g, _, j in parcels(a.vworld_key, b, crs_wkt, a.vworld_domain, cache)]
            log(f"   브이월드 연속지적도에서 필지 {len(pcl):,}개")
            return pcl
    except Exception as e:
        log(f"   (연속지적도를 받지 못해 필지 없이 계단식 대지를 만듭니다: {type(e).__name__}: {e})")
    return []


def build(src, bbox, out="out", log=print, **kw):
    """수치지도(SHP 폴더 또는 DXF)에서 콘타 모델 전체를 만든다. 반환: 요약 dict."""
    from types import SimpleNamespace
    from .progress import Progress
    pg = kw.pop("progress", None) or Progress()   # 화면의 생성 과정 실황 (없으면 조용히)
    a = SimpleNamespace(**{**DEFAULTS, **kw})
    a.src, a.out, a.bbox = src, out, bbox
    os.makedirs(a.out, exist_ok=True)
    from .tree_models import SPECIES
    SPECIES.clear()
    from .facade import clear_cache
    clear_cache()
    bbox = tuple(a.bbox)
    min_area = a.min_area if a.min_area is not None else (2.0 * a.scale / 1000) ** 2 * 4  # 모형상 약 4mm²
    log("1/6 수치지도 읽는 중")
    pg.step("1.1")
    data = load_shp(a.src, bbox) if os.path.isdir(a.src) else load_dxf(a.src, bbox)
    origin = (bbox[0], bbox[1])
    clip = box(*bbox)
    if pg.live:
        _live_input(pg, data, bbox, origin, a.interval)
    pg.steps("1.2", "1.3")
    log("2/6 등고선 보간, 계단판 만드는 중")
    pg.steps("2.1", "2.2")
    interp = build_interpolator(data["contours"], data["spots"])
    breaks = [b for b in data.get("breaks", ()) if b[0].intersects(clip.buffer(30))] if a.breaklines else []
    if breaks:
        bp = breakline_points(breaks, interp)
        interp = build_interpolator(data["contours"], data["spots"], extra=bp)
        nk = defaultdict(int)
        for b in breaks:
            nk["옹벽" if b[3] > 0.3 else b[1] or "기타"] += 1
        log("   지형 꺾임선: " + " · ".join(f"{k} {v}줄" for k, v in nk.items()) + f" (높이 점 {len(bp[1]):,}개)")
    sigma, smooth, chaikin_n = a.sigma, a.smooth, 3
    if a.terrain_fit == "exact":
        # 등고선 그대로: 절토면 계단·옹벽처럼 등고선 사이가 좁게 꺾이는 곳을 뭉개지 않게 다듬기를 줄인다.
        # 판 윤곽은 레이저로 자를 수 있는 만큼만(축척 1:1000 에서 0.5m = 0.5mm) 다듬는다.
        sigma, smooth, chaikin_n = min(a.sigma, 0.5), min(a.smooth, 0.5), 1
        log("   지형: 등고선 그대로 (다듬기 최소)")
    if a.dem is not None:
        # 수치표고모델이 있으면 그 높이를 쓰고, 자료가 비는 곳만 등고선으로. 격자 자료라 고르게 펴는 정도도 줄인다
        from .dem import merged_interpolator
        interp = merged_interpolator(a.dem, interp)
        sigma = min(a.sigma, 1.0)
    # 계단식 대지: 비탈의 건물 터·논을 평평한 단으로, 단 사이는 옹벽처럼 (수치지도 건물·옹벽·담·길·논)
    pads = None
    if a.pads and (data.get("buildings") or data.get("paddies")):
        from .pads import build_pads
        site_p = (a.site or {}).get("poly")
        uses_site = site_p is not None and not getattr(a, "site_keep_terrain", False) and (
            a.site_clear or a.site.get("levels") or a.site.get("plates") or a.site.get("keep"))
        try:
            bl = [b[0] for b in data["buildings"]]
            barriers = [b[0] for b in data.get("breaks", ())] + list(data.get("walls", ()))
            if not os.path.isdir(a.src):
                barriers += list(data.get("roads", ()))      # DXF: 길은 선뿐이라 선을 막는 선으로
            roads_u = unary_union(data["road_areas"]) if data.get("road_areas") else None
            pcl = _pad_parcels(a, bbox, log)
            pads = build_pads(bl, interp, bbox, barriers=barriers, roads=roads_u, paddies=data.get("paddies", ()),
                              exclude=site_p if uses_site else None, parcels=pcl, log=log)
        except Exception as e:
            log(f"   (계단식 대지를 만들지 못함: {type(e).__name__}: {e})")
            pads = None
        if pads is not None:
            interp = pads.wrap(interp)
    # 사업지 도면의 레벨: 대지 안 지형을 그 레벨로 다시 만들고, 경계 밖 15m 까지 수치지도 지형과 섞는다
    level_site = None
    orig_interp = interp      # 대지 분석(경사·절토·성토)에서 '원래 지형'으로 쓴다
    plates = (a.site or {}).get("plates") or []
    keep_areas = (a.site or {}).get("keep") or []
    if a.site and getattr(a, "site_keep_terrain", False) and (a.site.get("levels") or plates or keep_areas):
        log("   대지 안 원래 지형 유지: 도면의 레벨·레벨판은 지형에 쓰지 않고 경계선만 지형에 눌러 표시합니다")
    elif a.site and (a.site.get("levels") or plates or keep_areas) and a.site.get("poly") is not None \
            and a.site["poly"].intersects(clip):
        from .site import blended, level_interpolator
        lv = a.site.get("levels")
        if plates:
            # 레벨판 테두리를 높이 점으로 더해 판 사이 빈 곳이 판 높이를 잇게 한다
            from .sitefile import plate_points
            lines_, spots_ = lv or ([], [])
            lv = (lines_, list(spots_) + plate_points(plates))
        # 표준 대지 파일은 사용자가 역할을 확인한 자료라 지형과 많이 달라도(깊은 절토) 버리지 않는다
        tol = 1e4 if a.site.get("standard") else 30.0
        sf = level_interpolator(lv, interp, a.site["poly"], log, tol=tol, cover=clip) if lv and (lv[0] or lv[1]) else None
        n_pts = getattr(sf, "count", 0) if sf is not None else 0
        if plates or keep_areas:
            from .sitefile import plan_function
            sf = plan_function(interp, plates, keep_areas, base=sf)
        if sf is not None:
            interp = blended(a.site["poly"], sf, interp)
            level_site = a.site["poly"]
            log(f"   대지 안 지형을 사업지 레벨로 다시 만들었습니다 (레벨 점 {n_pts:,}개"
                + (f" · 레벨판 {len(plates)}개" if plates else "") + (f" · 원지형 유지 {len(keep_areas)}곳" if keep_areas else "") + ")")
    pg.step("2.3")
    if pg.live:
        _live_tin(pg, interp, bbox, origin)
    regions, zmin = terrace_regions(data["contours"], interp, bbox, a.interval, min_area,
                                    smooth=smooth, sigma=sigma, chaikin_n=chaikin_n, keep=level_site,
                                    keep_w=pads.keep_weight if pads is not None else None)
    lv = list(regions)
    pg.step("2.4", x=f"EL {lv[0]:g}부터 {a.interval:g}m마다 {len(lv)}단")
    # 판이 많으면(예: 1m 간격 수백 장) 화면 실황은 최대 30묶음으로 나눠 보낸다.
    # 판마다 3D 부품·평면 그림을 만들면 그림 그리는 시간이 판 수의 제곱으로 늘어난다.
    stride = max(1, math.ceil(len(lv) / 30))
    for i, L in enumerate(lv):
        pg.step("2.5", r=i, n=len(lv), x=f"판 {i + 1} / {len(lv)} · EL {L:g}")
        if pg.live and (i % stride == stride - 1 or i == len(lv) - 1):
            _live_sheet(pg, regions, i - i % stride, bbox, origin, j=i + 1, key=i // stride)
    pg.steps("2.6", "2.7")
    log("3/6 건물·길·가로수 배치 중")
    pg.steps("3.1", "3.2")
    masses = building_masses(data["buildings"], regions, clip, a.floor_height)
    # 사업 대지: 경계를 표시하고, 고르면 대지 위(건물·길·나무·시설물)를 비운다
    site_poly = a.site.get("poly") if a.site else None
    if site_poly is not None and (site_poly.is_empty or not site_poly.intersects(clip)):
        log("   (대지가 모델 범위 밖에 있어 경계를 넣지 않습니다)")
        site_poly = None
    wipe = site_poly if (site_poly is not None and a.site_clear) else None
    cleared = {}
    if wipe is not None:
        from . import site as site_mod
        masses, gone = site_mod.split_masses(masses, wipe)
        cleared["건물"] = len(gone)
        log(f"   대지 위 비우기: 건물 {len(gone)}동을 뺐습니다 (대지 {wipe.area:,.0f}㎡)")
    register = None
    if a.vworld_key and a.data_key and masses:
        # 건축물대장: 실제 높이·층수·준공연도 (키는 사용자 것, 실패해도 수치지도 층수로 계속)
        from .publicdata import enrich
        crs_wkt = a.crs_wkt
        if not crs_wkt and os.path.isdir(a.src):
            from .pipeline import read_crs
            crs_wkt = read_crs(a.src).to_wkt()
        cache = a.cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
        register = enrich(masses, bbox, crs_wkt, a.vworld_key, a.data_key, a.vworld_domain, cache,
                          a.floor_height, log)
    if a.detail <= 1:
        for m in masses:
            m["lod"] = 1
    if a.facade and a.detail >= 2 and masses:
        from .facade import assign
        assign(masses, a.aerial, log)
        cx, cy = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        if a.site and a.site.get("poly") is not None:
            cx, cy = a.site["poly"].centroid.x, a.site["poly"].centroid.y
        r = a.facade_radius
        n = 0
        for m in masses:
            c = m["poly"].centroid
            if (a.detail >= 3 and (r is None or math.hypot(c.x - cx, c.y - cy) <= r)
                    and m.get("layer") not in ("무벽건물", "공사중건물")):
                m["facade"]["windows"] = True; n += 1
        if a.detail >= 3:
            log(f"   창문을 낼 건물 {n}동" + (f" (가운데에서 {r:g}m 안)" if r else ""))
    pg.step("3.3", x=f"건물 {len(masses)}동")
    if pg.live:
        pg.part("mass", _mass_meshes(masses, origin), info=f"건물 {len(masses)}동")
        pg.plan("b", bbox, lambda ax: (_plan_sheets(ax, regions),
                                       fill(ax, [m["poly"] for m in masses], "white", "#333", 0.3, 3)))
    pg.step("3.4")
    areas, edges, trees, greens, lights, structs = [], [], [], [], [], []
    road_info = None
    transit_items = []
    if os.path.isdir(a.src):
        # SHP: 길을 종류별 면으로 표현, 선형 도로는 면이 없는 곳(단지 내 도로경계)만
        from .paths_trees import drape_areas, load_paths
        pg.step("4.1")
        path_areas, edge_lines = load_paths(a.src, bbox)
        if wipe is not None:
            before = sum(g.area for g in path_areas.values())
            path_areas = site_mod.cut_areas(path_areas, wipe)
            edge_lines = site_mod.cut_lines(edge_lines, wipe)
            cleared["길(㎡)"] = round(before - sum(g.area for g in path_areas.values()))
        if a.road_detail and a.detail >= 2 and path_areas.get("road") is not None:
            # 정류장 지붕·지하철 출입구 캐노피는 수치지도에 '무벽건물'로 있다 → 건물에서 빼서 교통시설로
            from .road_detail import _centerlines, _nodes
            from .transit import classify
            try:
                walk_t = unary_union([g for k, g in path_areas.items() if k in ("sidewalk", "island") and g is not None])
                cl = _centerlines(a.src, bbox)
                nd = [p for p, e in _nodes(cl) if len(e) >= 3 and max(cl[i]["w"] for i, _ in e) >= 8]
                transit_items = classify(masses, path_areas["road"], walk_t, nd, log)
                for d in transit_items:
                    d["road"] = path_areas["road"]
            except Exception as e:
                log(f"   (정류장·지하철 출입구를 찾지 못함: {type(e).__name__}: {e})")
        if a.structures:
            from .structures import load_structures
            try:
                structs, bridged = load_structures(a.src, bbox, lambda xy: interp(np.asarray(xy, float)), log)
                if bridged is not None and "road" in path_areas:
                    path_areas["road"] = path_areas["road"].difference(bridged)   # 다리 위 길은 상판이 대신한다
            except Exception as e:
                log(f"   (고가도로·교량을 만들지 못함: {type(e).__name__}: {e})")
            if pg.live and structs:
                pg.part("struct", [(_shifted(mm, origin), (176, 174, 168)) for mm, k, n in structs],
                        info=f"구조물 {len(structs)}개")
        pg.step("4.2")
        areas = drape_areas(path_areas, regions)
        pg.step("4.3", x=f"길 면 {len(areas)}개")
        edges = drape_lines(edge_lines, regions)
        if pg.live:
            pg.plan("r", bbox, lambda ax: (_plan_sheets(ax, regions), _plan_paths(ax, areas)))
        draped = []
        pg.step("5.1")
        if not a.no_trees:
            trees = _real_street_trees(a, path_areas, masses, regions, bbox, log, pg)
            if wipe is not None:
                n0 = len(trees); trees = site_mod.outside(trees, wipe)
                cleared["나무"] = n0 - len(trees)
        if a.green:
            green_geom = _green_areas(a, path_areas, masses, bbox, log, pg)
            if wipe is not None:
                crowns = green_geom.pop("crowns", None)
                green_geom = site_mod.cut_areas(green_geom, wipe, min_area=0.0)
                for k in ("forest", "garden"):
                    green_geom.setdefault(k, Polygon())
                if crowns:
                    green_geom["crowns"] = site_mod.outside(crowns, wipe)
            trees += _green_trees(a, green_geom, regions, trees, log)
            if wipe is not None:
                n0 = len(trees); trees = site_mod.outside(trees, wipe)
                cleared["나무"] = cleared.get("나무", 0) + n0 - len(trees)
            greens = drape_areas({k: v for k, v in green_geom.items() if k in ("forest", "garden", "grass") and v is not None}, regions)
        if a.lights:
            from .streetlights import place_lights
            api_lights = []
            if getattr(a, "safemap_key", None):
                try:
                    from .crosswalk_api import safemap_lights
                    api_lights = safemap_lights(a.safemap_key, bbox, _crs_wkt(a), a.safemap_light_layer, log=log)
                except Exception as e:
                    log(f"   ({e})")
            try:
                blds = unary_union([m["poly"] for m in masses]) if masses else None
                lights = [(x, y, level_at(regions, Point(x, y)), h, ang, kind, src)
                          for x, y, h, ang, kind, src in place_lights(
                              a.src, bbox, _crs_wkt(a), path_areas.get("road"), blds, a.light_files, log=log,
                              extra=api_lights)]
                if wipe is not None:
                    n0 = len(lights); lights = site_mod.outside(lights, wipe)
                    cleared["가로등"] = n0 - len(lights)
            except Exception as e:
                log(f"   (가로등을 넣지 못함: {type(e).__name__}: {e})")
        if a.road_detail and a.detail >= 2:
            from .road_detail import road_details
            from .transit import stops_from_file
            try:
                blds_rd = unary_union([m["poly"] for m in masses]) if masses else None
                plats = [d["poly"].buffer(0.6, join_style=2) for d in transit_items if d["kind"] == "중앙버스정류장"]
                plats = [unary_union(plats).buffer(5.0, join_style=2).buffer(-5.0, join_style=2)] if plats else []
                road_info = road_details(a.src, bbox, _crs_wkt(a), path_areas.get("road"), blds_rd, a.aerial,
                                         a.road_files, [(t[0], t[1]) for t in lights], log,
                                         keep_out=unary_union(plats) if plats else None,
                                         walk=unary_union([g for k, g in path_areas.items()
                                                           if k in ("sidewalk", "island", "stairs") and g is not None]),
                                         islands=path_areas.get("island"),
                                         api=dict(seoul_key=a.seoul_key, safemap_key=a.safemap_key,
                                                  seoul_service=a.seoul_service, safemap_layer=a.safemap_layer,
                                                  seoul_code=a.seoul_code, cache_dir=a.cache_dir),
                                         hd_dir=a.hd_dir)
                transit_items += stops_from_file(road_info.get("busstops") or [], path_areas.get("road"),
                                                 transit_items, log)
                road_info["transit"] = transit_items
                if wipe is not None:
                    road_info["islands_inferred"] = (road_info["islands_inferred"].difference(wipe)
                                                     if road_info.get("islands_inferred") is not None else None)
                inf = road_info.get("islands_inferred")
                isl0 = path_areas.get("island")
                road_info["islands"] = unary_union([g for g in (isl0, inf) if g is not None and not g.is_empty]) \
                    if (inf is not None and not inf.is_empty) else isl0
                try:     # 확인 그림: 노면표시 출처별 색 + 교차로 면 + 추정 교통섬
                    from .road_detail import marks_check
                    marks_check(os.path.join(a.out, "road_marks_check.png"), path_areas.get("road"),
                                unary_union([g for k, g in path_areas.items() if k in ("sidewalk", "stairs") and g is not None]),
                                road_info["marks"], isl0, inf, bbox, core=road_info.get("core"))
                except Exception as e:
                    log(f"   (노면표시 확인 그림을 못 만듦: {e})")
                try:
                    from .underground import collect
                    ug = collect(dict(seoul_key=a.seoul_key, seoul_service=a.seoul_service, seoul_code=a.seoul_code,
                                      cache_dir=a.cache_dir), bbox, _crs_wkt(a), log)
                    if ug:
                        road_info["underground"] = dict(ug, road=path_areas.get("road"), clip=box(*bbox))
                except Exception as e:
                    log(f"   (땅속 시설을 넣지 못함: {type(e).__name__}: {e})")
            except Exception as e:
                log(f"   (도로 디테일을 넣지 못함: {type(e).__name__}: {e})")
        pg.step("5.6", x=f"나무 {len(trees)}그루")
        if pg.live and trees:
            pg.part("tree", low_trees(trees, origin), info=f"나무 {len(trees)}그루")
        if pg.live and lights:
            from .streetlights import placed_mesh
            pg.part("light", [(trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin)
                                                         for x, y, z, h, ang, kind, _ in lights]), (90, 90, 96))],
                    info=f"가로등 {len(lights)}개")
            pg.plan("tr", bbox, lambda ax: (_plan_sheets(ax, regions), _plan_greens(ax, greens),
                                            _plan_paths(ax, areas), _plan_trees(ax, trees)))
    else:
        draped = drape_lines(data["roads"], regions)
    terrain_mesh = skp_terrain = None
    print_field = None           # 3D 프린트 STL 용 높이 격자 (경사면일 때)
    skp_solids = []
    trees_lv, edges_lv, areas_lv, lights_lv = trees, edges, areas, lights  # 레이저 판은 항상 계단 레벨 기준
    # 차도·자전거도로는 계단판 한 장 두께로 끼워 넣고(판은 그만큼 파냄), 나머지 길은 판 위에 얇게
    pg.step("4.4")
    inlays, areas = split_inlays(areas)
    if pg.live and inlays and a.terrain != "slope":
        from .paths_trees import PATH_STYLE
        pg.part("inlay", [(extrude(p, L - a.interval + 0.03, L + 0.03, origin), PATH_STYLE[k][1])
                          for p, L, k in inlays], info=f"차도 조각 {len(inlays)}개")
    area_meshes = []
    if a.terrain in ("slope", "combo"):
        log("4/6 경사면·건물 터 만드는 중")
        pg.step("6.1")
        X, Y, H, B = slope_heightfield(regions, bbox, return_levels=True)
        if breaks and a.dem is None:
            # 꺾임선 둘레(8m 안)는 꺾임선이 들어간 보간면을 그대로, 20m 밖은 원래 방식(등고선 사이를 고르게), 그 사이는 섞는다
            import shapely
            Hb = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
            dist = _dist_to_lines(shapely.multilinestrings([b[0] for b in breaks]),
                                  shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip((20.0 - dist) / 12.0, 0.0, 1.0)
            H = w * Hb + (1 - w) * H
        if a.dem is not None:
            # 경사면도 등고선 사이를 잇는 대신 DEM 높이 그대로 (절토면 계단·옹벽 같은 꺾임이 살아난다)
            H = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
        elif level_site is not None:
            # 대지 안 경사면도 사업지 레벨 그대로, 경계 밖 15m 에서 원래 경사면으로 넘어간다
            import shapely
            Hl = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
            d = shapely.distance(level_site, shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip(1.0 - d / 15.0, 0.0, 1.0)
            H = w * Hl + (1 - w) * H
        if pads is not None:
            # 경사면에도 같은 평평한 단: 터 안은 터 높이, 둘레는 옹벽(거의 수직)·1:1 비탈로 경사면과 잇는다.
            # 대지 레벨을 쓰는 대지 안은 터를 만들지 않았으므로 그대로다.
            H = pads.apply(np.column_stack([X.ravel(), Y.ravel()]), H.ravel()).reshape(X.shape)
        pg.step("6.2")
        H = building_pads(X, Y, H, masses)
        print_field = (X, Y, H, B)
        if pg.live:
            pg.plan("sl", bbox, lambda ax: ax.imshow(H, origin="lower", cmap="terrain",
                                                     extent=(bbox[0], bbox[2], bbox[1], bbox[3])))
        lv = list(regions)
        if a.terrain == "combo":
            # 계단판 윗면에서 경사면까지만 채우는 얹힘 매스 (윗면은 계단과 겹쳐 깜빡이지 않게 5cm 띄움)
            terrain_mesh = heightfield_mesh(X, Y, H + 0.05, np.minimum(B, H), origin)
        else:
            terrain_mesh = heightfield_mesh(X, Y, H, lv[0] - (lv[1] - lv[0]) * 2, origin)
        terrain_mesh = simplify_solid(terrain_mesh)
        hz = surface_sampler(X, Y, H)
        # 곡면 대지 위에는 길 전체를 두께 있는 한 층으로 (보도·교통섬은 연석 높이만큼 더)
        area_meshes = drape_area_solids(areas_lv, hz, origin, a.road_thickness)
        if a.sketchup:
            # 스케치업용은 가볍게: 대지 격자 2m, 길 면 세분 8m
            X2, Y2, H2, B2 = X[::2, ::2], Y[::2, ::2], H[::2, ::2], B[::2, ::2]
            skp_terrain = (heightfield_mesh(X2, Y2, H2 + 0.05, np.minimum(B2, H2), origin) if a.terrain == "combo"
                           else heightfield_mesh(X2, Y2, H2, lv[0] - (lv[1] - lv[0]) * 2, origin))
            skp_terrain = simplify_solid(skp_terrain)
            skp_solids = drape_area_solids(areas_lv, hz, origin, a.road_thickness, max_edge=8.0)
        areas = []
        edges = [(drape_line_3d(ls, hz), L) for ls, L in edges]
        from .paths_trees import PATH_STYLE
        on_walk = a.road_thickness + PATH_STYLE["sidewalk"][2]   # 가로수는 보도 층 위에 선다
        trees = [(x, y, float(hz([x], [y])[0]) + (on_walk if k == "street" else 0.0), r, k)
                 for x, y, z, r, k in trees]
        lights = [(x, y, float(hz([x], [y])[0]) + (on_walk if kind == "가로등" else a.road_thickness), h, ang, kind, src)
                  for x, y, z, h, ang, kind, src in lights]
        pg.step("6.3")
        for m in masses:
            h = m.get("height", m["top"] - level_at(regions, m["poly"].representative_point()))
            pad = m.get("pad")
            if pad is None:
                c = m["poly"].representative_point(); pad = float(hz([c.x], [c.y])[0])
            m["level"] = m["base"]
            # 바닥은 터에 살짝 묻어 틈이 없게, 다만 가장 낮은 판보다 깊이는 묻지 않는다
            m["base"], m["top"] = max(pad - 0.3, m["level"]), pad + h
        if pg.live:
            pg.part("mass", _mass_meshes(masses, origin), info="터 위에 다시 앉힘")
        pg.step("6.4")
        if pg.live:
            s = slice(None, None, 3)   # 실황용은 3m 격자로 가볍게
            Hs, Bs = H[s, s], B[s, s]
            pg.part("slope", heightfield_mesh(X[s, s], Y[s, s], Hs + 0.05,
                                              np.minimum(Bs, Hs) if a.terrain == "combo" else lv[0] - a.interval * 2,
                                              origin),
                    replaces=["sheet_*", "inlay"] if a.terrain == "slope" else [])
        pg.step("4.5")
        if pg.live and areas_lv:
            sol = skp_solids or drape_area_solids(areas_lv, hz, origin, a.road_thickness, max_edge=8.0)
            pg.part("path", [(m, PATH_STYLE[k][1]) for m, k in sol], info=f"길 층 {len(sol)}개")
        pg.step("6.5")
        if pg.live and trees:
            pg.part("tree", low_trees(trees, origin), info="곡면 위로 올림")
        if pg.live and lights:
            from .streetlights import placed_mesh
            pg.part("light", [(trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin)
                                                         for x, y, z, h, ang, kind, _ in lights]), (90, 90, 96))])
    pg.step("4.6")
    if pg.live:
        _live_details(pg, masses, a.floor_height, origin)
    if wipe is not None:
        if road_info:
            cleared["도로시설"] = site_mod.cut_road_info(road_info, wipe)
        log("   대지 위 비우기: " + " · ".join(f"{k} {v:,}" for k, v in cleared.items() if v) if any(cleared.values())
            else "   대지 위 비우기: 대지 안에 뺄 것이 없었습니다")
    site_mesh = None
    if site_poly is not None:
        try:
            from .site import outline_mesh
            if a.terrain in ("slope", "combo"):
                site_mesh = outline_mesh(site_poly.intersection(clip), lambda x, y: float(hz([x], [y])[0]) + a.road_thickness, origin)
            else:
                site_mesh = outline_mesh(site_poly.intersection(clip), lambda x, y: level_at(regions, Point(x, y)), origin)
            if pg.live and site_mesh is not None:
                pg.part("site", [(site_mesh, (37, 99, 235))], info="대지 경계")
        except Exception as e:
            log(f"   (대지 경계 띠를 만들지 못함: {e})")
    roadbits = _road_meshes(road_info, hz if a.terrain in ("slope", "combo") else None, regions,
                            a.road_thickness) if road_info else []
    if pg.live and roadbits:
        pg.part("roadx", [(_shifted(m, origin), rgb) for m, lay, rgb, nm in roadbits],
                info=f"도로 디테일 {len(roadbits)}묶음")
    combo = a.terrain == "combo"
    log("5/6 Rhino(3dm)·STL·레이저 도면 쓰는 중")
    pg.steps("8.1", "8.2")

    def save_tick(kind):
        """저장 단계 세부 진행: 화면 왼쪽 '지금 하는 일' 줄과 진행 막대를 움직인다."""
        def t(label, r=None, n=None, est=None):
            pg.note("sub", k=5, file=kind, x=label, r=r, n=n, est=est)
        return t
    site_summary = None

    def add_site(f):
        nonlocal site_summary
        try:
            site_summary = _add_site(f, dict(a.site, poly=site_poly, regions=regions, keep_terrain=getattr(a, "site_keep_terrain", False)), interp, origin, log,
                                     step=1.0 if level_site is not None else None)
            site_summary.update(area=round(site_poly.area, 1), cleared=cleared if wipe is not None else None)
        except Exception as e:
            log(f"   (대지 경계·일조 사선을 넣지 못함: {e})")
    write_3dm(os.path.join(a.out, "model.3dm"), regions, masses, draped, origin, areas, trees, edges,
              terrain_mesh, area_meshes, combo, a.floor_height, inlays, lights, structs,
              "species" if a.detail >= 2 else "simple", roadbits,
              before_write=add_site if site_poly is not None else None, tick=save_tick("Rhino"))
    pg.step("8.3")
    write_stl(os.path.join(a.out, "model.stl"), regions, masses, origin, areas, trees, terrain_mesh,
              area_meshes, combo, a.floor_height, inlays, lights, structs,
              "species" if a.detail >= 2 else "simple", roadbits, site_mesh=site_mesh, tick=save_tick("STL"),
              scale=a.scale, field=print_field,
              site_poly=site_poly.intersection(clip) if site_poly is not None else None)
    if a.sketchup:
        pg.step("8.4")
        pg.note("sub", k=5, file="SketchUp", x="스케치업 파일")
        from .skp_export import write_sketchup
        site_lines = []
        if site_poly is not None:
            rings = [LineString(r.coords) for g in as_polys(site_poly) for r in [g.exterior, *g.interiors]]
            site_lines = [(f"대지 경계 · 판 EL {L:g}", np.array([(x, y, L + 0.02) for x, y, *_ in ls.coords]))
                          for ls, L in drape_lines(rings, regions)]
        write_sketchup(a.out, regions, masses, origin, skp_terrain, skp_solids, inlays, areas, trees,
                       combo, a.floor_height, lights, structs, "species" if a.detail >= 2 else "simple", roadbits,
                       site_lines=site_lines, log=log)
    pg.steps("9.1", "9.2", "9.3")
    pg.note("explode")
    pg.step("9.4")
    pg.note("sub", k=5, file="레이저", x="레이저 판 DXF")
    sheets = write_laser(os.path.join(a.out, "laser"), regions, masses, draped, origin, a.scale,
                         areas_lv, trees_lv, edges_lv, a.tree_hole, greens, lights_lv,
                         site=site_poly.intersection(clip) if site_poly is not None else None)
    for i, f in enumerate(sheets):
        pg.step("9.5", r=i, n=len(sheets), x=os.path.basename(str(f)))
    summary = dict(levels=list(regions), sheets=len(sheets), buildings=len(masses),
                   road_segments=len(draped) + len(edges), path_areas=len(areas) + len(area_meshes),
                   road_inlays=len(inlays), road_thickness_m=a.road_thickness,
                   building_uses=dict(sorted({u: sum(1 for m in masses if m.get("use") == u)
                                              for u in {m.get("use") for m in masses}}.items(),
                                             key=lambda kv: -kv[1])),
                   building_register=register, site=site_summary,
                   pads=pads.stats if pads is not None else None,
                   buildings_by_decade=dict(sorted({f"{m['year'] // 10 * 10}년대": sum(
                       1 for n in masses if n.get("year", 0) // 10 == m["year"] // 10)
                       for m in masses if m.get("year")}.items())),
                   structures={k: sum(1 for _, k2, _n in structs if k2 == k) for k in dict.fromkeys(s_[1] for s_ in structs)},
                   lights=len(lights), lights_by_source={s_: sum(1 for t in lights if t[6] == s_)
                                                         for s_ in dict.fromkeys(t[6] for t in lights)},
                   trees=len(trees), trees_by_kind={k: sum(1 for t in trees if t[4] == k) for k in ("street", "forest", "garden")},
                   terrain=a.terrain,
                   building_pad_cutfill_max_m=round(max([m.get("cutfill", 0) for m in masses] or [0]), 2),
                   building_pad_cutfill_median_m=round(float(np.median([m.get("cutfill", 0) for m in masses] or [0])), 2),
                   contours=len(data["contours"]),
                   model_size_mm=[(bbox[2] - bbox[0]) * 1000 / a.scale, (bbox[3] - bbox[1]) * 1000 / a.scale])
    if site_poly is not None and getattr(a, "analysis", True):
        # 대지 분석: 원래 지형 경사, 레벨 계획이 있으면 절토·성토 (그림 2장 + 숫자 표)
        try:
            from .analysis import site_analysis
            summary["analysis"] = site_analysis(a.out, bbox, site_poly, orig_interp,
                                                interp if level_site is not None else None,
                                                slope_limit=getattr(a, "slope_limit", 17.5), log=log)
        except Exception as e:  # 분석 실패는 모델에 영향 없음
            log(f"   (대지 분석을 만들지 못했습니다: {type(e).__name__}: {e})")
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    clear_cache()
    try:
        _preview_plan(os.path.join(a.out, "preview_plan.png"), bbox, regions, masses,
                      areas_lv, trees_lv, greens, lights_lv, site=site_poly)
    except Exception as e:  # 미리보기 실패는 모델에 영향 없음
        log(f"   (평면 미리보기 생략: {e})")
    if pg.live and os.path.exists(os.path.join(a.out, "preview_plan.png")):
        import shutil
        name = pg._name("fin", "png")
        shutil.copy(os.path.join(a.out, "preview_plan.png"), os.path.join(pg.live, name))
        pg.note("plan", key="fin", url="/live/" + name)
    pg.note("done")
    log("6/6 완료")
    return summary


# ---------------------------------------------------------------- 생성 과정 실황 (화면용)
def _live_input(pg, data, bbox, origin, interval):
    """받침판 + 제 높이에 띄운 등고선."""
    clip = box(*bbox)
    cs = [(ls.intersection(clip), z) for ls, z in data["contours"]]
    cs = [(g, z) for g, z in cs if not g.is_empty]
    lines = [(l, z) for g, z in cs for l in getattr(g, "geoms", [g]) if isinstance(l, LineString)]
    zs = [z for _, z in lines] or [0.0]
    z0 = math.floor(min(zs) / interval) * interval - 2 * interval
    pg.part("base", extrude(clip, z0 - 1.0, z0, origin), info=f"{bbox[2] - bbox[0]:.0f} × {bbox[3] - bbox[1]:.0f} m")
    pg.part("c3d", ribbon(lines, origin), info=f"등고선 {len(lines)}개")
    pg.plan("c", bbox, lambda ax: [ax.plot(*np.asarray(l.coords).T, color="#555", lw=0.4) for l, _ in lines])


def _mass_meshes(masses, origin):
    out = []
    for m in masses:
        out.append(extrude(m["poly"], m["base"], m["top"], origin))
    return out


def _live_tin(pg, interp, bbox, origin):
    """보간한 지형면을 4m 격자 곡면으로."""
    xs = np.arange(bbox[0], bbox[2] + 2, 4.0)
    ys = np.arange(bbox[1], bbox[3] + 2, 4.0)
    X, Y = np.meshgrid(xs, ys)
    H = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
    ny, nx = H.shape
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    v = np.column_stack([X.ravel() - origin[0], Y.ravel() - origin[1], H.ravel()])
    f = np.vstack([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    pg.part("tin", trimesh.Trimesh(v, f, process=False))
    pg.plan("tin", bbox, lambda ax: ax.imshow(H, origin="lower", cmap="terrain",
                                              extent=(bbox[0], bbox[2], bbox[1], bbox[3])))


def _live_sheet(pg, regions, i, bbox, origin, j=None, key=None):
    """판 i..j-1 (한 묶음)을 화면 실황에 3D 부품 하나·평면 그림 하나로."""
    lv = list(regions)
    j = j or i + 1
    key = i if key is None else key
    iv = lv[1] - lv[0] if len(lv) > 1 else 1.0
    meshes = []
    for k in range(i, j):
        L = lv[k]
        bottom = L - iv if k else L - iv * 2
        meshes += [extrude(p, bottom, L, origin) for p in as_polys(regions[L])]
    g = 0.86 - 0.3 * i / max(1, len(lv) - 1)
    c = tuple(int(255 * v) for v in (g, g, g * 0.97))
    pg.part(f"sheet_{key:02d}", meshes, color=c, replaces=["tin", "c3d"] if i == 0 else [],
            info=f"EL {lv[i]:g}" if j == i + 1 else f"EL {lv[i]:g}~{lv[j - 1]:g} ({j - i}장)")

    def draw(ax):
        _plan_sheets(ax, {k: regions[k] for k in lv[:i]}, n=len(lv))
        fill(ax, [regions[lv[k]] for k in range(i, j)], "#f08a3c", "#9a3f06", 0.4, 2)
    pg.plan(f"sheet_{key:02d}", bbox, draw,
            label=f"판 {i + 1} 영역" if j == i + 1 else f"판 {i + 1}~{j} 영역 (EL {lv[i]:g}~{lv[j - 1]:g})")


def _live_details(pg, masses, floor_height, origin):
    from .building_detail import building_parts
    pg.step("7.1")
    out = []
    for b in masses:
        for part in building_parts(b, floor_height):
            if part[1] == "ext":
                out.append(extrude(part[2], part[3], part[4], origin))
            elif part[1] == "mesh":
                m = part[2].copy(); m.apply_translation([-origin[0], -origin[1], 0]); out.append(m)
    pg.steps("7.2", "7.3", "7.4", "7.5", "7.6")
    pg.part("detail", out, replaces=["mass"], info="지붕·옥탑·파라펫")


def _plan_sheets(ax, regions, n=None):
    lv = list(regions)
    n = n or len(lv)
    step = max(1, math.ceil(len(lv) / 40))   # 판이 많으면 그림에는 40장 정도만(겹쳐 보여 차이 없음)
    for i, L in enumerate(lv):
        if i % step and i != len(lv) - 1:
            continue
        g = 0.93 - 0.35 * i / max(1, n - 1)
        fill(ax, [regions[L]], (g, g, g * 0.97), "#aaa", 0.2, 1)


def _plan_paths(ax, areas):
    from .paths_trees import PATH_STYLE
    for p, L, kind in areas:
        fill(ax, [p], np.array(PATH_STYLE[kind][1]) / 255, z=2)


def _plan_greens(ax, greens):
    for p, L, kind in greens:
        fill(ax, [p], {"forest": "#b9d7a8", "grass": "#eef3cf"}.get(kind, "#dcebc8"), z=2)


def _plan_trees(ax, trees):
    t = np.array([(x, y, r) for x, y, z, r, k in trees])
    ax.scatter(t[:, 0], t[:, 1], s=(t[:, 2] * 0.5) ** 2, c="#5b7f4a", alpha=0.8, linewidths=0, zorder=4)


def _preview_plan(path, bbox, regions, masses, areas, trees, greens, lights=(), site=None):
    """평면 미리보기 PNG: 계단 높이(회색 단계), 길, 녹지, 건물, 나무."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.plotting import plot_polygon
    from .paths_trees import PATH_STYLE
    fig, ax = plt.subplots(figsize=(10, 10 * (bbox[3] - bbox[1]) / (bbox[2] - bbox[0])), dpi=120)
    lv = list(regions)
    for i, L in enumerate(lv):
        g = 0.93 - 0.35 * i / max(1, len(lv) - 1)
        for p in as_polys(regions[L]):
            plot_polygon(p, ax=ax, add_points=False, facecolor=(g, g, g * 0.97), edgecolor="#999", linewidth=0.3)
    for p, L, kind in greens:
        plot_polygon(p, ax=ax, add_points=False,
                     facecolor={"forest": "#b9d7a8", "grass": "#eef3cf"}.get(kind, "#dcebc8"), edgecolor="none")
    for p, L, kind in areas:
        c = np.array(PATH_STYLE[kind][1]) / 255
        plot_polygon(p, ax=ax, add_points=False, facecolor=c, edgecolor="none")
    for m in masses:
        plot_polygon(m["poly"], ax=ax, add_points=False, facecolor="white", edgecolor="#444", linewidth=0.3)
    if trees:
        t = np.array([(x, y, r) for x, y, z, r, k in trees])
        ax.scatter(t[:, 0], t[:, 1], s=(t[:, 2] * 1.2) ** 2, c="#5b7f4a", alpha=0.75, linewidths=0)
    if lights:
        q = np.array([(x, y) for x, y, *_ in lights])
        ax.scatter(q[:, 0], q[:, 1], s=4, c="#e0a020", marker="o", linewidths=0, zorder=5)
    if site is not None:
        for p in as_polys(site):
            plot_polygon(p, ax=ax, add_points=False, facecolor=(0.145, 0.388, 0.922, 0.06), edgecolor="#2563eb",
                         linewidth=1.6, zorder=8)
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3]); ax.set_aspect("equal"); ax.set_axis_off()
    fig.savefig(path, bbox_inches="tight"); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="수치지도 DXF 파일 또는 수치지도 2.0 SHP 폴더")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, help="xmin ymin xmax ymax (m)")
    ap.add_argument("--scale", type=float, default=1000, help="모형 축척 분모 (1:scale)")
    ap.add_argument("--interval", type=float, default=5, help="계단 간격(m), 원본 등고선 간격의 배수")
    ap.add_argument("--min-area", type=float, default=None, help="버릴 작은 조각 면적(m²)")
    ap.add_argument("--floor-height", type=float, default=FLOOR_HEIGHT)
    ap.add_argument("--sigma", type=float, default=4.0, help="지형 고르기 정도(m). 0이면 원자료 그대로")
    ap.add_argument("--smooth", type=float, default=1.5, help="등고선 곡선 다듬기 반경(m)")
    ap.add_argument("--terrain", choices=["combo", "step", "slope"], default="combo")
    ap.add_argument("--no-trees", action="store_true", help="가로수 생략")
    ap.add_argument("--ndvi", help="식생지수 파일. 주면 녹지를 나무로 채움")
    ap.add_argument("--tree-spacing", type=float, default=8.0)
    ap.add_argument("--tree-hole", type=float, default=1.0)
    ap.add_argument("--hd-dir", help="정밀도로지도 zip 들을 모아 둔 폴더(노면표시를 이것으로 그림)")
    ap.add_argument("--out", default="out")
    a = vars(ap.parse_args())
    src, bbox, out = a.pop("src"), tuple(a.pop("bbox")), a.pop("out")
    print(json.dumps(build(src, bbox, out, **a), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

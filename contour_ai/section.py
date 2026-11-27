"""지형 단면 (v2.2): 지도에 그은 선을 따라 자른 단면을 SVG(일러스트레이터)와 DXF(CAD)로.

모델을 만들 때 단면 재료(section_data.npz: 지형 높이 격자 + 건물·물·길 면)를 결과 폴더에 남겨 두고,
화면에서 선을 그으면 그 재료로 바로 단면을 만든다 (모델을 다시 만들 필요 없음).

그림 구성 (보는 사람은 선의 오른쪽에 서서 왼쪽을 바라본다):
- 잘린 지형: 검정 굵은 선 + 아래 회색 채움
- 뒤쪽 지형(깊이 안): 연한 회색 선
- 잘린 건물: 검정 채움 / 뒤쪽 건물(깊이 안): 흰 채움 + 회색 윤곽 (가까울수록 진하게)
- 물: 잘린 자리에 파란 선 대신 진회색 굵은 선 (무채색 규칙)
- 높이 눈금(EL)·거리 눈금(m)·축척
"""
import json
import math
import os

import numpy as np
from shapely import wkb
from shapely.geometry import LineString, Point


DATA_NAME = "section_data.npz"


def save_section_data(out_dir, st, cell=None):
    """모델 단계 값에서 단면 재료를 저장. 지형 높이는 모델과 같은 표면(st.hz, 없으면 st.interp)."""
    x0, y0, x1, y1 = st.bbox
    side = max(x1 - x0, y1 - y0)
    cell = cell or (1.0 if side <= 400 else 2.0)
    xs = np.arange(x0, x1 + cell * 0.5, cell)
    ys = np.arange(y0, y1 + cell * 0.5, cell)
    X, Y = np.meshgrid(xs, ys)
    hz = getattr(st, "hz", None)
    if hz is not None:
        H = np.asarray(hz(X.ravel(), Y.ravel()), float).reshape(X.shape)
    else:
        H = np.asarray(st.interp(np.column_stack([X.ravel(), Y.ravel()])), float).reshape(X.shape)
    blds = [dict(w=wkb.dumps(m["poly"], hex=True), b=float(m.get("base", 0)), t=float(m.get("top", 0)), f=int(m["floors"]))
            for m in st.masses]
    water = [wkb.dumps(g, hex=True) for g in st.data.get("water_areas", ())]
    pa = getattr(st, "path_areas", None) or {}
    roads = [wkb.dumps(g, hex=True) for k, g in pa.items() if k in ("road", "bike") and g is not None and not g.is_empty]
    meta = dict(bbox=list(st.bbox), scale=int(st.a.scale or 1000), blds=blds, water=water, roads=roads)
    np.savez_compressed(os.path.join(out_dir, DATA_NAME), H=H.astype(np.float32), x0=x0, y0=y0, cell=cell,
                        meta=np.frombuffer(json.dumps(meta).encode("utf-8"), np.uint8))


def load_section_data(out_dir):
    z = np.load(os.path.join(out_dir, DATA_NAME))
    meta = json.loads(bytes(z["meta"]).decode("utf-8"))
    H, x0, y0, cell = z["H"], float(z["x0"]), float(z["y0"]), float(z["cell"])
    from scipy.interpolate import RegularGridInterpolator
    ys = y0 + np.arange(H.shape[0]) * cell
    xs = x0 + np.arange(H.shape[1]) * cell
    f = RegularGridInterpolator((ys, xs), H, bounds_error=False, fill_value=np.nan)
    hz = lambda x, y: f(np.column_stack([np.asarray(y, float), np.asarray(x, float)]))  # noqa: E731
    blds = [(wkb.loads(b["w"], hex=True), b["b"], b["t"], b["f"]) for b in meta["blds"]]
    water = [wkb.loads(w, hex=True) for w in meta["water"]]
    roads = [wkb.loads(w, hex=True) for w in meta["roads"]]
    return dict(hz=hz, blds=blds, water=water, roads=roads, bbox=meta["bbox"], scale=meta["scale"])


def compute(D, a, b, depth=200.0, step=1.0):
    """단면 계산. a→b 선, 보는 쪽은 오른쪽(뒤쪽 = 왼쪽 depth m 까지). 반환 dict (거리 t, 높이 z 로)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    L = float(np.hypot(*(b - a)))
    if L < 10:
        raise ValueError("단면선이 너무 짧아요. 10m 이상으로 그어 주세요.")
    u = (b - a) / L
    n = np.array([-u[1], u[0]])                 # 왼쪽(뒤쪽) 방향
    t = np.arange(0.0, L + step * 0.5, step)
    P = a[None, :] + t[:, None] * u[None, :]
    ground = D["hz"](P[:, 0], P[:, 1])
    if np.all(np.isnan(ground)):
        raise ValueError("단면선이 모델 범위 밖에 있어요. 모델 안쪽에 그어 주세요.")
    # 뒤쪽 지형: 깊이를 여러 줄로 나눠 각 줄의 높이 → 위로 드러나는 윤곽(최댓값)
    back = []
    if depth > 0:
        offs = np.linspace(depth / 6, depth, 6)
        for o in offs:
            Q = P + o * n[None, :]
            back.append((float(o), D["hz"](Q[:, 0], Q[:, 1])))
    line = LineString([tuple(a), tuple(b)])
    region = line.buffer(depth, single_sided=True) if depth > 0 else None   # 왼쪽 띠
    cut_b, back_b = [], []
    for poly, base, top, fl in D["blds"]:
        if poly.intersects(line):
            for seg in _segs(poly.intersection(line)):
                t0, t1 = sorted(line.project(Point(c)) for c in (seg.coords[0], seg.coords[-1]))
                g = float(np.nanmin(D["hz"](*np.array([[c[0] for c in seg.coords], [c[1] for c in seg.coords]]))))
                cut_b.append((t0, t1, min(base, g), top, fl))
        elif region is not None and poly.intersects(region):
            pts = np.asarray(poly.exterior.coords)[:, :2]
            ts = (pts - a) @ u
            ds = (pts - a) @ n
            if ds.max() <= 0:
                continue
            back_b.append((float(max(0.0, ts.min())), float(min(L, ts.max())), base, top, float(max(0.0, ds.min())), fl))
    water = []
    for w in D["water"]:
        for seg in _segs(w.intersection(line)):
            t0, t1 = sorted(line.project(Point(c)) for c in (seg.coords[0], seg.coords[-1]))
            water.append((t0, t1))
    return dict(L=L, t=t, ground=ground, back=back, cut=cut_b, behind=sorted(back_b, key=lambda x: -x[4]),
                water=water, a=a.tolist(), b=b.tolist(), depth=depth)


def _segs(g):
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    return [x for x in getattr(g, "geoms", []) if x.geom_type == "LineString"]


def _nice(x):
    e = 10 ** math.floor(math.log10(max(x, 1e-9)))
    return max(m * e for m in (1, 2, 5) if m * e <= x)


def write_svg(path, S, scale=1000, title="단면 A–A'"):
    k = 1000.0 / scale                      # m → mm
    t, gz = S["t"], S["ground"]
    ok = ~np.isnan(gz)
    zs = [gz[ok]] + [bz[~np.isnan(bz)] for _, bz in S["back"]] + [np.array([c[3] for c in S["cut"] + S["behind"]] or [np.nan])]
    zmax = float(np.nanmax(np.concatenate(zs)))
    zmin = float(np.nanmin(gz[ok]))
    base = math.floor((zmin - max(5.0, (zmax - zmin) * 0.15)) / 5) * 5
    top = zmax + max(5.0, (zmax - zmin) * 0.1)
    padL, padR, padT, padB = 18.0, 8.0, 14.0, 16.0        # mm
    W = S["L"] * k + padL + padR
    Hh = (top - base) * k + padT + padB
    X = lambda tt: padL + tt * k                         # noqa: E731
    Y = lambda z: padT + (top - z) * k                   # noqa: E731
    sw = max(0.12, S["L"] * k / 900)
    fs = max(2.0, min(4.0, S["L"] * k / 90))
    o = [f'<?xml version="1.0" encoding="UTF-8"?>',
         f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
         f'width="{W:.1f}mm" height="{Hh:.1f}mm" viewBox="0 0 {W:.2f} {Hh:.2f}">',
         '<style>text{font-family:Pretendard,"Noto Sans KR",sans-serif;fill:#111}</style>',
         f'<rect width="{W:.2f}" height="{Hh:.2f}" fill="#fff"/>']

    def G(gid, label, body):
        if body:
            o.append(f'<g id="{gid}" inkscape:groupmode="layer" inkscape:label="{label}">'); o.extend(body); o.append("</g>")

    def runs(z):
        """nan 으로 끊긴 구간마다 점 목록"""
        out, cur = [], []
        for tt, zz in zip(t, z):
            if np.isnan(zz):
                if len(cur) > 1:
                    out.append(cur)
                cur = []
            else:
                cur.append((tt, float(zz)))
        if len(cur) > 1:
            out.append(cur)
        return out
    # 뒤쪽 지형 (먼 것부터 연하게)
    body = []
    for i, (off, bz) in enumerate(reversed(S["back"])):
        g = int(215 - 60 * i / max(1, len(S["back"]) - 1))
        for r in runs(bz):
            body.append(f'<polyline points="{" ".join(f"{X(a):.2f},{Y(b):.2f}" for a, b in r)}" fill="none" '
                        f'stroke="rgb({g},{g},{g})" stroke-width="{sw * 0.7:.3f}"/>')
    G("back_terrain", "뒤쪽 지형", body)
    # 뒤쪽 건물 (먼 것부터)
    dep = max(1.0, S["depth"])
    body = []
    for t0, t1, b0, b1, d, fl in S["behind"]:
        g = int(150 + 80 * min(1.0, d / dep))
        body.append(f'<rect x="{X(t0):.2f}" y="{Y(b1):.2f}" width="{max(0.1, (t1 - t0) * k):.2f}" height="{max(0.1, (b1 - b0) * k):.2f}" '
                    f'fill="#fff" stroke="rgb({g},{g},{g})" stroke-width="{sw * 0.7:.3f}"/>')
    G("back_buildings", "뒤쪽 건물", body)
    # 잘린 지형
    body = []
    for r in runs(gz):
        pts = " ".join(f"{X(a):.2f},{Y(b):.2f}" for a, b in r)
        body.append(f'<polygon points="{X(r[0][0]):.2f},{Y(base):.2f} {pts} {X(r[-1][0]):.2f},{Y(base):.2f}" fill="#d9d9d9" stroke="none"/>')
        body.append(f'<polyline points="{pts}" fill="none" stroke="#111" stroke-width="{sw * 2:.3f}" stroke-linejoin="round"/>')
    G("cut_terrain", "잘린 지형", body)
    G("water", "물", [f'<line x1="{X(t0):.2f}" y1="{Y(float(np.nanmin(gz[(t >= t0) & (t <= t1)])) if ((t >= t0) & (t <= t1)).any() else base) + sw:.2f}" '
                     f'x2="{X(t1):.2f}" y2="{Y(float(np.nanmin(gz[(t >= t0) & (t <= t1)])) if ((t >= t0) & (t <= t1)).any() else base) + sw:.2f}" '
                     f'stroke="#555" stroke-width="{sw * 3:.3f}"/>' for t0, t1 in S["water"]])
    G("cut_buildings", "잘린 건물", [f'<rect x="{X(t0):.2f}" y="{Y(b1):.2f}" width="{max(0.1, (t1 - t0) * k):.2f}" '
                                   f'height="{max(0.1, (b1 - b0) * k):.2f}" fill="#111"><title>{fl}층</title></rect>'
                                   for t0, t1, b0, b1, fl in S["cut"]])
    # 눈금: 높이(왼쪽), 거리(아래)
    zstep = _nice((top - base) / 6)
    body = [f'<line x1="{X(0):.2f}" y1="{Y(base):.2f}" x2="{X(S["L"]):.2f}" y2="{Y(base):.2f}" stroke="#111" stroke-width="{sw:.3f}"/>',
            f'<line x1="{X(0) - 2:.2f}" y1="{Y(base):.2f}" x2="{X(0) - 2:.2f}" y2="{Y(top):.2f}" stroke="#111" stroke-width="{sw:.3f}"/>']
    z = math.ceil(base / zstep) * zstep
    while z <= top:
        body.append(f'<line x1="{X(0) - 3:.2f}" y1="{Y(z):.2f}" x2="{X(0) - 2:.2f}" y2="{Y(z):.2f}" stroke="#111" stroke-width="{sw:.3f}"/>')
        body.append(f'<line x1="{X(0):.2f}" y1="{Y(z):.2f}" x2="{X(S["L"]):.2f}" y2="{Y(z):.2f}" stroke="#ececec" stroke-width="{sw * 0.5:.3f}"/>')
        body.append(f'<text x="{X(0) - 4:.2f}" y="{Y(z):.2f}" font-size="{fs * 0.8:.2f}" text-anchor="end" dominant-baseline="middle">EL {z:g}</text>')
        z += zstep
    tstep = _nice(S["L"] / 8)
    tt = 0.0
    while tt <= S["L"] + 1e-6:
        body.append(f'<line x1="{X(tt):.2f}" y1="{Y(base):.2f}" x2="{X(tt):.2f}" y2="{Y(base) + 1.5:.2f}" stroke="#111" stroke-width="{sw:.3f}"/>')
        body.append(f'<text x="{X(tt):.2f}" y="{Y(base) + 1.5 + fs:.2f}" font-size="{fs * 0.8:.2f}" text-anchor="middle">{tt:g}</text>')
        tt += tstep
    body.append(f'<text x="{X(0):.2f}" y="{padT - fs:.2f}" font-size="{fs * 1.2:.2f}" font-weight="600">{title}</text>')
    body.append(f'<text x="{X(S["L"]):.2f}" y="{padT - fs:.2f}" font-size="{fs * 0.85:.2f}" text-anchor="end">'
                f'길이 {S["L"]:.0f} m · 뒤쪽 깊이 {S["depth"]:.0f} m · 1:{scale} · 높이 과장 없음</text>')
    body.append(f'<text x="{X(0):.2f}" y="{Y(base) + 1.5 + fs * 2.2:.2f}" font-size="{fs * 0.8:.2f}">A</text>')
    body.append(f'<text x="{X(S["L"]):.2f}" y="{Y(base) + 1.5 + fs * 2.2:.2f}" font-size="{fs * 0.8:.2f}" text-anchor="end">A\'</text>')
    G("dims", "치수·글자", body)
    o.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(o))
    return path


def write_dxf(path, S):
    """CAD 용: x = 거리(m), y = 높이(m) 그대로. 레이어는 SVG 와 같은 이름."""
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.M
    msp = doc.modelspace()
    for name, c in (("잘린 지형", 7), ("뒤쪽 지형", 8), ("잘린 건물", 7), ("뒤쪽 건물", 8), ("물", 5), ("치수·글자", 7)):
        doc.layers.add(name, color=c)
    t, gz = S["t"], S["ground"]

    def runs(z):
        out, cur = [], []
        for a, b in zip(t, z):
            if np.isnan(b):
                if len(cur) > 1:
                    out.append(cur)
                cur = []
            else:
                cur.append((float(a), float(b)))
        if len(cur) > 1:
            out.append(cur)
        return out
    for r in runs(gz):
        msp.add_lwpolyline(r, dxfattribs={"layer": "잘린 지형", "lineweight": 50})
    for _, bz in S["back"]:
        for r in runs(bz):
            msp.add_lwpolyline(r, dxfattribs={"layer": "뒤쪽 지형"})
    for t0, t1, b0, b1, fl in S["cut"]:
        pl = [(t0, b0), (t1, b0), (t1, b1), (t0, b1)]
        msp.add_lwpolyline(pl, close=True, dxfattribs={"layer": "잘린 건물"})
        h = msp.add_hatch(color=7, dxfattribs={"layer": "잘린 건물"}); h.paths.add_polyline_path(pl, is_closed=True)
    for t0, t1, b0, b1, d, fl in S["behind"]:
        msp.add_lwpolyline([(t0, b0), (t1, b0), (t1, b1), (t0, b1)], close=True, dxfattribs={"layer": "뒤쪽 건물"})
    for t0, t1 in S["water"]:
        msp.add_line((t0, float(np.nanmin(gz)) - 0.2), (t1, float(np.nanmin(gz)) - 0.2), dxfattribs={"layer": "물"})
    zmin = float(np.nanmin(gz))
    msp.add_text(f"단면 A-A'  길이 {S['L']:.0f}m  뒤쪽 깊이 {S['depth']:.0f}m", height=max(1.0, S["L"] / 120),
                 dxfattribs={"layer": "치수·글자"}).set_placement((0, float(np.nanmax(gz)) + 10))
    msp.add_aligned_dim(p1=(0, zmin - 8), p2=(S["L"], zmin - 8), distance=-3, dxfattribs={"layer": "치수·글자"}).render()
    doc.saveas(path)
    return path


def make_section(out_dir, a, b, depth=200.0):
    """결과 폴더에 section_NN.svg / .dxf 를 만든다. 반환 (svg 이름, dxf 이름, 요약)."""
    D = load_section_data(out_dir)
    depth = float(min(1000.0, max(0.0, depth)))
    S = compute(D, a, b, depth)
    n = 1
    while os.path.exists(os.path.join(out_dir, f"section_{n:02d}.svg")):
        n += 1
    title = f"단면 {n}"
    sv = write_svg(os.path.join(out_dir, f"section_{n:02d}.svg"), S, D["scale"], title=title)
    dx = write_dxf(os.path.join(out_dir, f"section_{n:02d}.dxf"), S)
    return os.path.basename(sv), os.path.basename(dx), dict(length=round(S["L"], 1), depth=depth,
                                                           cut_buildings=len(S["cut"]), back_buildings=len(S["behind"]))

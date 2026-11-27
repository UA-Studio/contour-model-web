"""길(차도·보도·계단·교통섬·자전거도로) 면 표현과 가로수 배치."""
import os
from collections import defaultdict

import numpy as np
from shapely import make_valid
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points, unary_union

# kind: (이름, Rhino 색, 지면 위 두께 m, 레이저 표현)
PATH_STYLE = {
    "road":     ("차도",     (118, 118, 115), 0.03, "outline"),
    "sidewalk": ("보도",     (228, 226, 220), 0.15, "raster"),
    "island":   ("교통섬",   (228, 226, 220), 0.15, "raster"),
    "stairs":   ("계단",     (190, 186, 176), 0.15, "hatch"),
    "bike":     ("자전거도로", (196, 176, 168), 0.05, "outline"),
}
PATH_ORDER = ["stairs", "island", "sidewalk", "bike", "road"]  # 앞쪽이 위에 놓임

TREE = dict(trunk_r=0.2, crown_z=6.0, crown_r=2.5)
TREE_KIND = {"street": "가로수", "forest": "숲", "garden": "조경수"}


CROWN_ELONG = 1.6   # 수관 세로 반지름 / 가로 반지름 (위로 길쭉한 타원구)


def tree_dims(r):
    """수관 가로 반지름 r(m) → (수관 중심 높이, 줄기 반지름, 수관 세로 반지름).

    수관은 위로 길쭉한 타원구. 가로수 r=2.5 → 가지 밑 2.6m, 중심 6.6m, 꼭대기 10.6m.
    """
    rz = CROWN_ELONG * r
    return 1.8 + 0.3 * r + rz, 0.08 * r, rz


def _read(folder, name):
    import shapefile
    from shapely.geometry import shape
    fn = os.path.join(folder, name)
    if not os.path.exists(fn + ".shp"):
        return []
    r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
    names = [f[0] for f in r.fields[1:]]
    return [(shape(sr.shape.__geo_interface__), dict(zip(names, sr.record)))
            for sr in r.iterShapeRecords()]


def load_paths(folder, bbox):
    """SHP 폴더에서 길 종류별 면과 도로경계선을 읽는다."""
    clip = box(*bbox)
    raw = defaultdict(list)

    def add(kind, g):
        g = make_valid(g)
        if g.intersects(clip):
            raw[kind].append(g.intersection(clip))
    for g, _ in _read(folder, "N3A_A0010000"):
        add("road", g)
    for g, _ in _read(folder, "N3A_A0070000"):          # 교량은 차도로
        add("road", g)
    for g, _ in _read(folder, "N3A_A0033320"):
        add("sidewalk", g)
    for g, rec in _read(folder, "N3A_A0053326"):
        if rec.get("구조") == "교통섬":
            add("island", g)
    for g, rec in _read(folder, "N3A_C0390000"):
        if rec.get("구조") == "계단":
            add("stairs", g)
    for g, rec in _read(folder, "N3L_A0033330"):
        w = float(rec.get("폭") or 0) or 2.0
        add("bike", g.buffer(w / 2, cap_style="flat"))
    # 위에 놓이는 것부터 빼 가며 겹침 정리
    areas, taken = {}, None
    for kind in PATH_ORDER:
        g = unary_union(raw[kind]) if raw[kind] else None
        if g is None or g.is_empty:
            continue
        if taken is not None:
            g = g.difference(taken)
        areas[kind] = g.buffer(0)
        taken = g if taken is None else unary_union([taken, g])
    # 단지 내 도로처럼 면이 없는 길은 도로경계선만 각인
    edges = [g.intersection(clip) for g, _ in _read(folder, "N3L_A0010000") if g.intersects(clip)]
    return areas, edges


def drape_areas(areas, regions, min_area=0.5):
    """길 면을 계단판별로 나눈다. 반환: [(Polygon, level, kind)]"""
    from .geom_util import as_polys
    lv = list(regions)
    bands = []
    for i, L in enumerate(lv):
        band = regions[L]
        if i + 1 < len(lv):
            band = band.difference(regions[lv[i + 1]])
        bands.append((L, band))
    out = []
    for kind, g in areas.items():
        for L, band in bands:
            for p in as_polys(g.intersection(band)):
                if p.area >= min_area:
                    out.append((p, L, kind))
    return out


def street_trees(areas, buildings, regions, spacing=8.0, inset=0.8,
                 min_width=2.0, clearance=2.0):
    """보도를 따라 가로수 위치를 만든다. 반환: [(x, y, z)]

    차도에 닿은 보도 가장자리를 따라 spacing 간격으로 놓고, 보도 안쪽으로
    inset 만큼 들인다. 폭이 min_width보다 좁은 보도와 건물 가까운 곳은 뺀다.
    """
    from .geom_util import as_polys
    from .terrain import level_at
    walk = unary_union([areas[k] for k in ("sidewalk", "island") if k in areas])
    if walk.is_empty:
        return []
    road = areas.get("road")
    road_zone = road.buffer(0.6) if road is not None else None
    blocked = unary_union([b.buffer(clearance) for b in buildings]) if buildings else None
    picked = []
    for sw in as_polys(walk):
        inner = sw.buffer(-min(inset, min_width / 2))
        if inner.is_empty or sw.buffer(-min_width / 2).is_empty:
            continue
        edge = sw.exterior.intersection(road_zone) if road_zone is not None else None
        lines = [g for g in getattr(edge, "geoms", [edge]) if isinstance(g, LineString)] if edge else []
        if sum(l.length for l in lines) < 0.3 * sw.exterior.length:
            # 차도와 붙지 않은 보도(단지 안 산책로 등)는 중심 쪽 선을 따라
            lines = [inner.exterior if hasattr(inner, "exterior") else
                     max(as_polys(inner), key=lambda p: p.area).exterior]
        for ln in lines:
            if ln.length < spacing * 0.6:
                continue
            n = max(1, int(ln.length // spacing))
            for d in np.linspace(ln.length / (n + 1) * 0.5, ln.length - ln.length / (n + 1) * 0.5, n):
                p = ln.interpolate(d)
                q = nearest_points(inner, p)[0]
                if blocked is not None and blocked.contains(q):
                    continue
                picked.append(q)
    # 너무 가까운 나무 정리
    r = spacing * 0.6
    grid, out = defaultdict(list), []
    for p in picked:
        cx, cy = int(p.x // r), int(p.y // r)
        near = [q for i in (-1, 0, 1) for j in (-1, 0, 1) for q in grid[(cx + i, cy + j)]]
        if any(p.distance(q) < r for q in near):
            continue
        grid[(cx, cy)].append(p)
        out.append((p.x, p.y, level_at(regions, p)))
    return out

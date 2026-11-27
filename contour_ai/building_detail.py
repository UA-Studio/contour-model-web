"""건물 디테일: 수치지도의 종류·층수·면적·형태로 건물마다 매스를 다르게 만든다.

수치지도에는 외곽선과 층수만 있으므로, 실제 형태에 가깝도록 다음 규칙을 쓴다.
- 일반주택(2층 이하)·가건물·온실: 처마 위 경사 지붕(모임지붕, 외곽선 모양 그대로)
- 3층 이상 일반주택·연립·주택외건물: 평지붕 + 파라펫 + 계단실 옥탑
- 아파트: 평지붕 + 파라펫 + 동 길이에 따라 엘리베이터·계단 코어 옥탑 여러 개
- 무벽건물(주차장 지붕·캐노피 등): 얇은 지붕판 + 모서리 기둥
- 공사중건물: 골조만(층마다 얇은 슬래브)
- 모든 2층 이상 건물: 층 구분선(Rhino 곡선)

반환하는 부품: (역할, 형식, 자료)
  ("ext", Polygon, z0, z1)  → 수직 돌출 매스
  ("mesh", Trimesh)         → 경사 지붕 등 (월드 좌표)
  ("curve", LineString(3D)) → 층 구분선
"""
import math

import numpy as np
import trimesh
from scipy.spatial import Delaunay
from shapely.ops import voronoi_diagram
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.prepared import prep

# 파라펫(옥상 난간벽) — 건축법 시행령 제40조①: 옥상광장·2층 이상 노대 주위에는 높이 1.2m 이상의 난간.
# 주택건설기준 등에 관한 규정 제18조: 난간 높이는 바닥 마감면부터 1.2m 이상. 평지붕 옥상 전부에 같은 규격을 쓴다.
# 두께는 법 규정이 없어 철근콘크리트 파라펫에 흔한 0.2m.
PARAPET_H = 1.2       # 옥상 바닥 마감면(ROOF_DECK 위)부터
PARAPET_T = 0.2
ROOF_PITCH = math.radians(27)
ROOF_MAX = 3.2
CANOPY_H = 3.0
CANOPY_T = 0.3


def _polys(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def _axis_frame(poly):
    """외곽선의 최소 회전 사각형: (중심, 긴축 단위벡터, 긴변, 짧은변)."""
    r = poly.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    L, W = np.linalg.norm(e1), np.linalg.norm(e2)
    u = e1 / max(L, 1e-9)
    return np.asarray(r.centroid.coords[0]), u, L, W


def _oriented_box(center, u, length, width):
    v = np.array([-u[1], u[0]])
    hl, hw = length / 2, width / 2
    pts = [center + u * a + v * b for a, b in [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]]
    return Polygon(pts)


def hip_roof(poly, z_eave, pitch=ROOF_PITCH, max_h=ROOF_MAX, step=0.6):
    """외곽선 모양 그대로의 모임지붕 메시.

    지붕 높이 = 처마선까지의 거리 × tan(경사). 용마루(중심선)는 외곽선의
    보로노이 꼭짓점에서 뽑아 넣어 능선이 또렷하게 나오게 한다.
    """
    poly = poly.simplify(0.15).buffer(0)
    if poly.is_empty or poly.area < 4:
        return None
    if not isinstance(poly, Polygon):
        poly = max(_polys(poly), key=lambda p: p.area)
    ring = poly.exterior.segmentize(step)
    bpts = np.asarray(ring.coords)[:-1]
    for h in poly.interiors:
        bpts = np.vstack([bpts, np.asarray(h.segmentize(step).coords)[:-1]])
    import shapely
    vd = voronoi_diagram(MultiPoint(bpts), edges=True)
    pp = prep(poly.buffer(-0.05))
    mpts = [c for c in shapely.get_coordinates(vd) if pp.contains(Point(c))]
    pts = np.vstack([bpts, np.asarray(mpts)]) if mpts else bpts
    pts = np.unique(np.round(pts, 3), axis=0)
    if len(pts) < 3:
        return None
    tri = Delaunay(pts)
    cen = pts[tri.simplices].mean(axis=1)
    keep = [i for i, c in enumerate(cen) if pp.contains(Point(c))]
    if not keep:
        return None
    faces = tri.simplices[keep]
    d = shapely.distance(poly.boundary, shapely.points(pts))
    z = z_eave + np.minimum(d * math.tan(pitch), max_h)
    v = np.column_stack([pts, z])
    m = trimesh.Trimesh(v, faces, process=True)
    m.fix_normals()
    if m.face_normals[:, 2].mean() < 0:
        m.invert()
    return m


def floor_frame(m, floor_height=3.3):
    """(1층 바닥 높이, 층고). 건축물대장 실제 높이가 있으면 층고 = 실제 높이 ÷ 지상층수 —
    같은 층수라도 건물마다 층고가 다르다. 없으면 기본 층고. 바닥은 땅 높이(평평한 밑면이 더 깊어도)."""
    base, top = float(m["base"]), float(m["top"])
    h = float(m.get("height") or (top - base))
    ground = max(base, top - h)
    fh = m.get("floor_h")
    if fh:
        return ground, float(fh)
    return base + 0.3, floor_height


ROOF_DECK = 0.06   # 옥상 바닥 마감 두께(m)


def tier_boxes(m):
    """층별개요 셋백 → [(평면, z0, z1, 층수)] (없으면 None). 높이는 지금의 바닥·꼭대기로 나눈다."""
    t = m.get("tiers")
    if not t:
        return None
    base, top = float(m["base"]), float(m["top"])
    h = float(m.get("height") or (top - base))
    g0 = max(base, top - h)                      # 땅 높이(밑면이 더 깊게 박혀 있어도 층은 땅에서부터)
    out = []
    for p, k0, k1, n in t:
        z0 = base if k0 == 0 else g0 + (top - g0) * k0 / n
        out.append((p, z0, g0 + (top - g0) * k1 / n, k1 - k0))
    return out


_BP_CACHE = {}   # 같은 건물 부품을 3dm·GLB·STL·스케치업 쓰기가 네 번 만들던 것 → 한 번만 (부품은 읽기만 할 것)
_BP_KEYS = ("base", "top", "floors", "layer", "lod", "flat_roof", "floor_h", "height", "rooftop_area", "tiers", "pitched")


def clear_cache():
    _BP_CACHE.clear()


def building_parts(m, floor_height=3.3):
    """건물 하나의 부품 목록. 같은 건물·같은 값이면 앞서 만든 목록을 돌려준다(돌려받은 것을 고치지 말 것)."""
    key = (id(m), float(floor_height), id(m.get("poly"))) + tuple(repr(m.get(k)) for k in _BP_KEYS)
    hit = _BP_CACHE.get(key)
    if hit is not None and hit[0] is m:
        return hit[1]
    out = _building_parts(m, floor_height)
    _BP_CACHE[key] = (m, out)
    return out


def _building_parts(m, floor_height=3.3):
    poly, base, top = m["poly"], m["base"], m["top"]
    kind, floors = m["layer"], int(m["floors"])
    parts = []
    area = poly.area
    if area < 3 or m.get("lod", 2) <= 1:
        return [("body", "ext", poly, base, top)]

    # ---- 무벽건물: 지붕판 + 기둥
    if kind == "무벽건물":
        z1 = top
        parts.append(("canopy", "ext", poly, z1 - CANOPY_T, z1))
        c, u, L, W = _axis_frame(poly)
        inset = poly.buffer(-0.4)
        if not inset.is_empty and L > 2.5:
            v = np.array([-u[1], u[0]])
            nL = max(2, int(L // 6) + 1)
            for a in np.linspace(-L / 2 + 0.6, L / 2 - 0.6, nL):
                for b in (-W / 2 + 0.6, W / 2 - 0.6) if W > 3 else (0,):
                    p = Point(c + u * a + v * b)
                    if inset.contains(p):
                        parts.append(("column", "ext", p.buffer(0.18, quad_segs=2), base, z1 - CANOPY_T))
        return parts

    # ---- 공사중: 층마다 슬래브 + 코어
    if kind == "공사중건물":
        for k in range(floors + 1):
            z = base + 0.3 + k * floor_height
            parts.append(("frame", "ext", poly, z, z + 0.25))
        c, u, L, W = _axis_frame(poly)
        core = _oriented_box(c, u, min(6, L * 0.4), min(5, W * 0.5)).intersection(poly)
        for p in _polys(core):
            parts.append(("frame", "ext", p, base, base + 0.3 + floors * floor_height))
        return parts

    pitched = (kind in ("가건물", "온실") or (kind == "일반주택" and floors <= 2 and area < 400)
               or (m.get("pitched") and area < 800)) and not m.get("flat_roof")
    tb = tier_boxes(m) if not pitched else None
    if tb:
        for p, z0, z1, _ in tb:
            parts.append(("body", "ext", p, z0, z1))
        # 아래 단의 테라스 난간
        for i, (p, z0, z1, _) in enumerate(tb[:-1]):
            inner = p.buffer(-PARAPET_T, join_style="mitre")
            for q in _polys((inner if not inner.is_empty else p).difference(tb[i + 1][0])):
                if q.area > 0.5:
                    parts.append(("roof", "ext", q, z1, z1 + ROOF_DECK))
            ring = (p.difference(inner) if not inner.is_empty else p).difference(tb[i + 1][0])
            for q in _polys(ring):
                if q.area > 0.05:
                    parts.append(("parapet", "ext", q, z1, z1 + ROOF_DECK + PARAPET_H))
        poly = tb[-1][0]
    else:
        parts.append(("body", "ext", poly, base, top))

    if pitched:
        roof = hip_roof(poly, top, pitch=math.radians(15) if kind in ("가건물", "온실") else ROOF_PITCH,
                        max_h=1.5 if kind in ("가건물", "온실") else ROOF_MAX)
        if roof is not None:
            parts.append(("roof", "mesh", roof))
    else:
        # 평지붕 + 파라펫
        inner = poly.buffer(-PARAPET_T, join_style="mitre")
        ring = poly.difference(inner) if not inner.is_empty else poly
        # 옥상 바닥(지붕 재료: 사진이 초록이면 녹색 방수) — 벽 재료와 따로 보이게 얇은 판
        for p in _polys(inner if not inner.is_empty else poly):
            if p.area > 0.5:
                parts.append(("roof", "ext", p, top, top + ROOF_DECK))
        for p in _polys(ring):
            if p.area > 0.05:
                parts.append(("parapet", "ext", p, top, top + ROOF_DECK + PARAPET_H))
        # 옥탑
        c, u, L, W = _axis_frame(poly)
        if kind == "아파트" and floors >= 5:
            n = max(1, int(round(L / 22)))
            for a in np.linspace(-L / 2 + L / (2 * n), L / 2 - L / (2 * n), n):
                b = _oriented_box(c + u * a, u, min(7.0, L / n * 0.45), min(W * 0.55, 8.0))
                b = b.intersection(inner if not inner.is_empty else poly)
                for p in _polys(b):
                    if p.area > 6:
                        parts.append(("penthouse", "ext", p, top, top + 3.6))
                        # 물탱크·기계실 단
                        t = p.buffer(-0.8)
                        for q in _polys(t):
                            parts.append(("penthouse", "ext", q, top + 3.6, top + 5.2))
                            parts.append(("roof", "ext", q, top + 5.2, top + 5.2 + ROOF_DECK))
                        for q in _polys(p.difference(t) if not t.is_empty else p):
                            parts.append(("roof", "ext", q, top + 3.6, top + 3.6 + ROOF_DECK))
        elif floors >= 3 and area > 60:
            # 계단실 옥탑: 긴축 한쪽 끝
            a = L / 2 - min(3.0, L * 0.2)
            ra = m.get("rooftop_area") or 0
            if 4 < ra < area * 0.6:    # 층별개요의 옥탑 면적
                k = math.sqrt(ra / (3.5 * 4.5))
                b = _oriented_box(c + u * a, u, min(3.5 * k, L * 0.6), min(4.5 * k, W * 0.8))
            else:
                b = _oriented_box(c + u * a, u, min(3.5, L * 0.35), min(4.5, W * 0.6))
            b = b.intersection(inner if not inner.is_empty else poly)
            for p in _polys(b):
                if p.area > 4:
                    parts.append(("penthouse", "ext", p, top, top + 3.0))
                    parts.append(("roof", "ext", p, top + 3.0, top + 3.0 + ROOF_DECK))   # 옥탑 지붕도 같은 지붕 재료
    # 층 구분선
    if floors >= 2 and kind != "무벽건물":
        g0, fh_ = floor_frame(m, floor_height)
        for k in range(1, floors):
            z = g0 + k * fh_
            if z >= top - 0.2:
                break
            fp = poly
            for p, z0, z1, _ in (tb or ()):
                if z0 <= z < z1:
                    fp = p
            for ring in [fp.exterior]:
                xy = np.asarray(ring.coords)
                parts.append(("floorline", "curve", LineString(np.column_stack([xy, np.full(len(xy), z)]))))
    return parts


ROLE_STYLE = {
    "body":      ("Buildings",            (245, 245, 245)),
    "roof":      ("Buildings_roof",       (232, 226, 214)),
    "parapet":   ("Buildings_parapet",    (240, 240, 238)),
    "penthouse": ("Buildings_rooftop",    (236, 236, 233)),
    "canopy":    ("Buildings_canopy",     (226, 226, 222)),
    "column":    ("Buildings_canopy",     (226, 226, 222)),
    "frame":     ("Buildings_construction", (210, 210, 205)),
    "floorline": ("Buildings_floorlines", (150, 150, 146)),
}

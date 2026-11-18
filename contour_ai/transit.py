"""버스 정류장·지하철 출입구·교통섬 승강장.

수치지도에는 정류장·출입구 레이어가 없다. 하지만 정류장 지붕과 지하철 출입구 캐노피는
건물 레이어에 '무벽건물'(벽 없는 지붕)로 그려져 있고, 보도나 차도 위에 놓인다. 모양과 자리로 가른다.
  - 연석에서 1.5m 넘게 떨어진 차도 한가운데 무벽건물, 길쭉함     → 중앙버스정류장 (승강장 + 긴 지붕)
  - 보도 위, 폭 0.9~2.4m · 길이 2.5m 이상 · 차도와 4m 안 · 연석과 나란함 → 버스 정류장 쉘터
  - 보도 위, 넓이 12~70㎡ · 폭 2.4~6.5m · 길이/폭 1.6 이상     → 지하철 출입구
그 밖의 작은 무벽건물(가판대 등)은 그대로 건물로 둔다.

버스 정류장 자료 파일(서울시 버스정류소 위치정보, 국토교통부 전국 버스정류장 위치정보: 위도·경도)이 있으면
무벽건물이 없는 정류장 자리에도 표지판 + 표준 쉘터를 세운다.
지하철 출입구는 공개된 좌표 자료가 없어(서울시는 리프트 위치만) 수치지도 무벽건물로만 찾는다.
"""
import math

import numpy as np
from shapely.geometry import LineString, Point, Polygon

FRAME = (120, 124, 128)
GLASS = (176, 196, 204)
ROOF = (214, 216, 214)
PLATFORM = (206, 202, 192)
DARK = (58, 58, 62)
SIGN = (40, 92, 160)      # 정류장 표지·지하철 표지 (파란 계열)
METRO = (232, 156, 40)    # 지하철 표지 판(주황)


def _rect(poly):
    """최소 회전 사각형 → (중심, 긴 축 u, 짧은 축 v, 길이, 폭)"""
    c = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    L, W = float(np.linalg.norm(e1)), float(np.linalg.norm(e2))
    return c.mean(axis=0), e1 / max(L, 1e-9), e2 / max(W, 1e-9), L, W


def classify(masses, road, walk, nodes=(), log=print):
    """무벽건물을 골라 masses 에서 빼고 정류장·출입구로. 반환 [dict(kind, poly, c, u, v, L, W, ...)]"""
    from shapely import prepare
    if road is None or road.is_empty:
        return []
    prepare(road)
    if walk is not None and not walk.is_empty:
        prepare(walk)
    nodes = np.asarray(nodes, float).reshape(-1, 2)
    out, keep = [], []
    for m in masses:
        if m.get("layer") != "무벽건물":
            keep.append(m)
            continue
        p = m["poly"]
        a = p.area
        c, u, v, L, W = _rect(p)
        on_road = p.intersection(road).area / max(a, 1e-6)
        on_walk = p.intersection(walk).area / max(a, 1e-6) if walk is not None and not walk.is_empty else 0.0
        kind = None
        inner = on_road > 0.6 and p.distance(road.boundary) > 1.5    # 연석에서 떨어진 차도 한가운데
        if inner and 0.8 <= W <= 6.5 and L >= 3 and L / W >= 2.0:
            kind = "중앙버스정류장"
        elif on_walk + on_road > 0.6:
            dist = p.distance(road.boundary) if on_walk > on_road else 0.0
            if 0.9 <= W <= 2.4 and 2.5 <= L <= 14 and dist < 4.0 and _parallel(road, c, u):
                kind = "버스정류장"
            elif 12 <= a <= 70 and 2.4 <= W <= 6.5 and L / W >= 1.6:
                kind = "지하철출입구"
        if kind is None:
            keep.append(m)
            continue
        d = dict(kind=kind, poly=p, c=c, u=u, v=v, L=L, W=W, src="수치지도 무벽건물")
        if kind == "버스정류장":
            # 등판(유리 벽)은 차도에서 먼 쪽 → v 를 차도 반대쪽으로
            if road.distance(Point(*(c + v * W))) < road.distance(Point(*(c - v * W))):
                d["v"] = -v
        if kind == "지하철출입구":
            # 계단은 역(보통 교차로 밑) 쪽으로 내려간다 → 들어가는 입구는 교차로에서 먼 끝. u 가 입구 쪽을 향하게
            if len(nodes):
                k = np.argmin(np.hypot(*(nodes - c).T))
                if np.dot(c - nodes[k], u) < 0:
                    d["u"] = -u
        out.append(d)
    masses[:] = keep
    cnt = {}
    for d in out:
        cnt[d["kind"]] = cnt.get(d["kind"], 0) + 1
    if out:
        log("   무벽건물에서 " + ", ".join(f"{k} {v}곳" for k, v in cnt.items()) + " 찾음")
    return out


def _parallel(road, c, u, tol=30):
    """긴 축이 가장 가까운 연석과 나란한지."""
    from shapely.ops import nearest_points
    q = nearest_points(road.boundary, Point(*c))[0]
    b = road.boundary
    s = b.project(q)
    a1, a2 = b.interpolate(max(s - 1.5, 0)), b.interpolate(min(s + 1.5, b.length))
    t = np.array([a2.x - a1.x, a2.y - a1.y])
    n = np.linalg.norm(t)
    if n < 1e-6:
        return True
    ang = math.degrees(math.acos(min(1.0, abs(float(np.dot(t / n, u))))))
    return ang < tol


def stops_from_file(points, road, existing, log=print):
    """자료 파일 정류장 점 → 무벽건물로 이미 찾은 곳(12m 안)은 빼고 표준 쉘터. 연석 쪽으로 붙인다."""
    from shapely.ops import nearest_points
    out = []
    ex = np.array([d["c"] for d in existing]) if existing else np.zeros((0, 2))
    for x, y, name in points:
        if len(ex) and np.min(np.hypot(*(ex - (x, y)).T)) < 12:
            continue
        if road is None or road.is_empty:
            continue
        q = nearest_points(road.boundary, Point(x, y))[0]
        b = road.boundary
        s = b.project(q)
        a1, a2 = b.interpolate(max(s - 2, 0)), b.interpolate(min(s + 2, b.length))
        u = np.array([a2.x - a1.x, a2.y - a1.y])
        u /= np.linalg.norm(u) or 1.0
        v = np.array([-u[1], u[0]])
        if road.contains(Point(*(np.array([q.x, q.y]) + v * 1.0))):
            v = -v                                   # v 는 차도 반대쪽(보도 안쪽)
        c = np.array([q.x, q.y]) + v * 1.6          # 연석에서 0.8m 떨어진 쉘터 중심
        out.append(dict(kind="버스정류장", poly=None, c=c, u=u, v=v, L=4.0, W=1.6, src="자료", name=name))
    if out:
        log(f"   정류장 자료에서 무벽건물이 없는 정류장 {len(out)}곳에 표준 쉘터")
    return out


# ---------------------------------------------------------------- 모양 (원점 = 바닥 중심, x=u, y=v)
def _box(cx, cy, cz, sx, sy, sz):
    import trimesh
    b = trimesh.creation.box([sx, sy, sz])
    b.apply_translation([cx, cy, cz])
    return b


def bus_shelter(L, W, median=False):
    """반환 {색: [메시]}. 등판은 +y 쪽(차도 반대), 중앙정류장은 양쪽 열림."""
    h = 2.7
    parts = {FRAME: [], GLASS: [], ROOF: [], SIGN: []}
    parts[ROOF].append(_box(0, 0, h, L, W, 0.12))
    n = max(2, int(L // 4) + 1)
    for x in np.linspace(-L / 2 + 0.2, L / 2 - 0.2, n):
        ys = (0.0,) if median else (W / 2 - 0.15,)
        for y in ys:
            parts[FRAME].append(_box(x, y, h / 2, 0.1, 0.1, h))
    if not median:
        parts[GLASS].append(_box(0, W / 2 - 0.15, 1.3, L - 0.4, 0.03, 2.0))          # 등판 유리
        parts[GLASS].append(_box(-L / 2 + 0.2, W / 2 - 0.75, 1.3, 0.03, 1.1, 2.0))  # 옆 유리
        parts[FRAME].append(_box(0, W / 2 - 0.45, 0.45, min(L * 0.6, 3.0), 0.4, 0.06))   # 의자
        parts[SIGN].append(_box(L / 2 - 0.3, W / 2 - 0.15, 1.6, 0.9, 0.08, 1.4))   # 노선 안내판
    else:
        parts[FRAME].append(_box(0, 0, 0.45, L * 0.5, 0.4, 0.06))
        parts[SIGN].append(_box(-L / 2 + 0.6, 0, 2.2, 0.08, 1.0, 0.6))
    return parts


def stop_sign():
    """정류장 표지판 기둥 (연석 쪽 끝)."""
    return {FRAME: [_box(0, 0, 1.4, 0.08, 0.08, 2.8)], SIGN: [_box(0, 0, 2.55, 0.5, 0.05, 0.5)]}


def subway_entrance(L, W):
    """지하철 출입구: 유리 캐노피, +x 끝이 들어가는 쪽(열림). 안은 어두운 계단 자리 + 표지 기둥."""
    h = 2.9
    parts = {FRAME: [], GLASS: [], ROOF: [], DARK: [], METRO: []}
    parts[ROOF].append(_box(0, 0, h, L, W, 0.15))
    for x in (-L / 2 + 0.1, L / 2 - 0.1):
        for y in (-W / 2 + 0.1, W / 2 - 0.1):
            parts[FRAME].append(_box(x, y, h / 2, 0.12, 0.12, h))
    for y in (-W / 2 + 0.1, W / 2 - 0.1):                             # 양 옆 유리 벽 + 난간
        parts[GLASS].append(_box(0, y, 1.5, L - 0.2, 0.03, 2.6))
    parts[GLASS].append(_box(-L / 2 + 0.1, 0, 1.5, 0.03, W - 0.2, 2.6))   # 막힌 끝
    # 계단 자리: 어두운 바닥 (입구에서 안쪽으로 계단이 내려가는 듯 단 몇 개)
    steps = 6
    run = (L - 0.6) / steps
    for k in range(steps):
        x0 = L / 2 - 0.3 - (k + 1) * run
        parts[DARK].append(_box(x0 + run / 2, 0, 0.03 - k * 0.02, run, W - 0.5, 0.04))
    parts[METRO].append(_box(L / 2 + 0.4, W / 2 + 0.2, 1.4, 0.35, 0.35, 2.8))       # 표지 기둥
    return parts


def platform(poly_or_rect, road, h=0.2):
    """중앙정류장 승강장: 지붕 자리를 조금 넓혀 차도 안으로. 반환 (Polygon, 높이)"""
    p = poly_or_rect.buffer(0.6, join_style=2).intersection(road)
    return p, h


def meshes(items, zfun, road_z):
    """정류장·출입구 → [(월드 메시, 레이어, 색, 이름)]. zfun(x, y)=보도 높이, road_z(x, y)=차도 높이."""
    import trimesh
    from collections import defaultdict
    out = []
    by = defaultdict(list)
    plats = []
    # 중앙정류장: 이어진 지붕 조각들을 한 승강장으로 (6m 안의 틈은 메움)
    med = [d for d in items if d["kind"] == "중앙버스정류장"]
    if med:
        from shapely.ops import unary_union
        road = med[0].get("road")
        merged = unary_union([d["poly"].buffer(0.6, join_style=2) for d in med]).buffer(5.0, join_style=2).buffer(-5.0, join_style=2)
        if road is not None:
            merged = merged.intersection(road)
        for q in getattr(merged, "geoms", [merged]):
            if q.geom_type != "Polygon" or q.area < 2:
                continue
            c_ = q.representative_point()
            try:
                pm = trimesh.creation.extrude_polygon(q, 0.25)
                pm.apply_translation([0, 0, road_z(c_.x, c_.y) - 0.05])
                plats.append(pm)
            except Exception:
                pass
    for d in items:
        c, u, v, L, W = d["c"], d["u"], d["v"], d["L"], d["W"]
        T = np.eye(4)
        T[:2, 0], T[:2, 1], T[:2, 3] = u, v, c
        if d["kind"] == "중앙버스정류장":
            pz, ph = road_z(*c), 0.2
            T[2, 3] = pz + ph
            parts = bus_shelter(L, W, median=True)
        elif d["kind"] == "버스정류장":
            T[2, 3] = zfun(*c)
            parts = bus_shelter(L, W)
            # 표지판: 연석 쪽(-v) 앞 끝
            s = c - v * (W / 2 + 0.5) + u * (L / 2)
            T2 = T.copy(); T2[:2, 3] = s; T2[2, 3] = zfun(*s)
            for col, ms in stop_sign().items():
                for mm in ms:
                    m2 = mm.copy(); m2.apply_transform(T2)
                    by[(d["kind"], d["src"], col)].append(m2)
        else:
            T[2, 3] = zfun(*c)
            parts = subway_entrance(L, W)
        for col, ms in parts.items():
            for mm in ms:
                m2 = mm.copy(); m2.apply_transform(T)
                by[(d["kind"], d["src"], col)].append(m2)
    names = {FRAME: "기둥·틀", GLASS: "유리", ROOF: "지붕", SIGN: "표지", METRO: "표지", DARK: "계단"}
    for (kind, src, col), ms in by.items():
        out.append((trimesh.util.concatenate(ms), f"교통시설::{kind}::{names[col]}", col,
                    f"{kind} {names[col]} ({src})"))
    if plats:
        out.append((trimesh.util.concatenate(plats), "교통시설::중앙버스정류장::승강장", PLATFORM, "중앙버스정류장 승강장"))
    return out


def island_beds(islands, zfun, min_area=30.0):
    """교통섬: 수치지도 교통섬 면은 이미 연석 높이로 올라가 있다. 30㎡ 넘는 섬에는 가장자리 0.8m 안쪽에
    낮은 화단(0.35m)을 얹는다 — 서울 교통섬은 대개 화단이나 관목이 있다(추정, 레이어로 따로)."""
    import trimesh
    out = []
    if islands is None or islands.is_empty:
        return out
    beds = []
    for p in getattr(islands, "geoms", [islands]):
        if p.geom_type != "Polygon" or p.area < min_area:
            continue
        b = p.buffer(-0.8, join_style=2)
        for q in getattr(b, "geoms", [b]):
            if q.geom_type != "Polygon" or q.area < 6:
                continue
            c = q.representative_point()
            try:
                m = trimesh.creation.extrude_polygon(q.simplify(0.2), 0.4)
                m.apply_translation([0, 0, zfun(c.x, c.y) - 0.05])
                beds.append(m)
            except Exception:
                pass
    if beds:
        out.append((trimesh.util.concatenate(beds), "교통시설::교통섬::화단(추정)", (150, 172, 120), f"교통섬 화단 {len(beds)}곳"))
    return out

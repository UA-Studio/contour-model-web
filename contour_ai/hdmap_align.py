"""정밀도로지도(국토지리정보원) 노면표시를 수치지도 차도에 맞추는 보정.

두 자료는 측량 시기·방법이 달라 수십 cm~1m 쯤 어긋날 수 있다. 그대로 쓰면 횡단보도 끝이 보도 안으로 파고들거나,
차선이 연석 밖으로 나가 보인다. 그래서 아래 순서로 맞춘다.

  0) 좌표계 판별 — 경위도(4326)·UTM-K(5179)·중부원점(5186) 를 좌표 범위로 알아보고 수치지도 좌표계로 바꾼다.
  1) 전체 맞춤 — 정밀도로지도의 차도 가장자리선(없으면 바깥 차선)과 횡단보도 끝을 수치지도 연석·보도에
     겹치도록 이동(dx, dy)과 작은 회전을 한 번에 찾는다(튀는 점에 덜 흔들리는 최소제곱).
  2) 구역 맞춤 — 60m 칸마다 남은 어긋남을 다시 재서(최대 ±1m) 부드럽게 이어 붙인 변위장으로 고친다.
     (측량 구간마다 어긋남이 조금씩 다른 경우)
  3) 표시별 끝 맞춤 — 횡단보도: 자기 방향 그대로 양 끝을 보도·교통섬 가장자리까지 늘이거나 자름.
     정지선: 차도 안으로 자르고, 연석까지 1m 안 모자라면 늘임. 차선: 연석 0.3m 안쪽으로 자르고 1m 안 되는 토막은 버림.
     화살표: 차도 밖으로 나가면 가장 가까운 차로 가운데로 옮기고, 못 옮기면 버림.
  4) 변경 의심 — 항공사진 줄무늬로 찾은 횡단보도와 비교: 사진엔 있는데 지도엔 없으면 '새로 생김?',
     지도엔 있는데 사진에 또렷한 차도만 보이면 '지워짐?' 으로 표시(자동으로 지우지는 않음).

입력 형식(파일 구조를 보기 전이라 읽기와 분리): features = [dict(kind=종류, geom=shapely, attrs=dict)]
종류: '차도경계' '차선' '중앙선' '정지선' '횡단보도' '화살표' '안전지대' '기타노면'
"""
import math

import numpy as np
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import nearest_points, transform as shp_transform, unary_union

LINE_KINDS = ("차도경계", "차선", "중앙선", "정지선")
EDGE_OFFSET = 0.25          # 차도 가장자리선은 연석에서 약 0.25m 안쪽에 칠한다


# ---------------------------------------------------------------- 0) 좌표계
def guess_crs(bounds):
    """좌표 범위로 좌표계를 짐작. 반환 EPSG 번호 또는 None"""
    x0, y0, x1, y1 = bounds
    if 124 <= x0 <= 132 and 33 <= y0 <= 39:
        return 4326
    if 700000 <= x0 <= 1400000 and 1400000 <= y0 <= 2200000:
        return 5179
    if 0 <= x0 <= 700000 and 0 <= y0 <= 800000:
        return 5186            # 수치지도와 같은 중부원점(동·서부원점이면 x 범위가 다름 → 사용자 지정)
    return None


def to_target(features, src_epsg, dst_epsg=5186, log=print):
    if src_epsg in (None, dst_epsg):
        return features
    from pyproj import Transformer
    tr = Transformer.from_crs(src_epsg, dst_epsg, always_xy=True)
    log(f"   정밀도로지도 좌표계 EPSG:{src_epsg} → EPSG:{dst_epsg}")
    return [dict(f, geom=shp_transform(tr.transform, f["geom"])) for f in features]


# ---------------------------------------------------------------- 공통
def _samples(geom, step=2.0):
    """선·면 둘레에서 step 간격 점들"""
    out = []
    for g in getattr(geom, "geoms", [geom]):
        ln = g.exterior if g.geom_type == "Polygon" else g
        if ln.length < 1e-6:
            continue
        for s in np.arange(0, ln.length, step):
            p = ln.interpolate(s)
            out.append((p.x, p.y))
    return np.array(out) if out else np.zeros((0, 2))


def _crosswalk_ends(poly):
    """횡단보도 띠의 두 짧은 변 가운데(= 건너는 양 끝)"""
    r = poly.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e = [np.linalg.norm(c[(i + 1) % 4] - c[i]) for i in range(4)]
    i = int(np.argmax([e[0], e[1]]))           # 긴 변 = 건너는 방향
    # 긴 변 i 와 i+2 의 양 끝 짧은 변 중점
    a = (c[(i + 1) % 4] + c[(i + 2) % 4]) / 2
    b = (c[(i + 3) % 4] + c[i]) / 2
    return a, b


def _apply(features, fn):
    return [dict(f, geom=shp_transform(fn, f["geom"])) for f in features]


# ---------------------------------------------------------------- 1) 전체 맞춤
def _residual_pts(features):
    """맞춤에 쓸 점: (점들, 목표 거리, 기준 종류). 기준 'curb' = 차도 경계선과의 거리, 'walk' = 보도 경계"""
    # 길가장자리선(505)과 가장자리 황색 주정차금지선(515)은 둘 다 연석 바로 안쪽에 칠한다
    edge = [f["geom"] for f in features if f["kind"] in ("차도경계", "주정차금지선")]
    pts = []
    if edge:
        for g in edge:
            P = _samples(g, 2.0)
            pts += [(p, 0.0, "curb") for p in P]      # 기준선 자체를 0.25m 안쪽으로 둠
    for f in features:
        if f["kind"] == "횡단보도" and f["geom"].geom_type == "Polygon":
            for e in _crosswalk_ends(f["geom"]):
                pts.append((e, 0.0, "walk"))
    return pts


def _fit(pts, curb, walk, center, iters=12, log=print, init=(0.0, 0.0, 0.0), cut=3.0):
    """ICP 식: 각 점의 가장 가까운 기준선 점으로 끌어당기는 강체 변환(회전+이동)을 반복해서 찾는다."""
    if len(pts) < 6:
        return 0.0, 0.0, 0.0, None
    P0 = np.array([p for p, _, _ in pts])
    off = np.array([o for _, o, _ in pts])
    ref = [curb if k == "curb" else walk for _, _, k in pts]
    th, dx, dy = init
    for _ in range(iters):
        c, s = math.cos(th), math.sin(th)
        P = (P0 - center) @ np.array([[c, s], [-s, c]]) + center + [dx, dy]
        Q, W = [], []
        for p, o, rb in zip(P, off, ref):
            if rb is None or rb.is_empty:
                Q.append(p); W.append(0.0); continue
            q = np.asarray(nearest_points(rb, Point(*p))[0].coords[0])
            d = p - q
            L = np.linalg.norm(d)
            # 차도 가장자리선은 연석에서 0.25m 안쪽에 있어야 하므로 그 자리를 목표로
            tgt = q + (d / L * o if L > 1e-6 and o else 0.0)
            r = np.linalg.norm(p - tgt)
            Q.append(tgt)
            W.append(1.0 / max(r, 0.3) if r < cut else 0.0)     # cut(기본 3m) 넘게 먼 점(다른 길·골목)은 빼고, 먼 점일수록 덜 믿음
        Q, W = np.array(Q), np.array(W)
        if W.sum() < 1e-6:
            break
        # 가중 프로크루스테스(회전+이동)
        mp = (P * W[:, None]).sum(0) / W.sum()
        mq = (Q * W[:, None]).sum(0) / W.sum()
        H = ((P - mp) * W[:, None]).T @ (Q - mq)
        U, _, Vt = np.linalg.svd(H)
        Rm = Vt.T @ U.T
        if np.linalg.det(Rm) < 0:
            Vt[1] *= -1
            Rm = Vt.T @ U.T
        dth = math.atan2(Rm[1, 0], Rm[0, 0])
        t = mq - mp
        # 누적: 새 변환 = (dth, t) ∘ (th, dx, dy)
        th += dth
        new_c = (np.array([dx, dy]) + center - mp) @ Rm.T + mp + t - center
        dx, dy = float(new_c[0]), float(new_c[1])
        if abs(dth) < 1e-5 and np.linalg.norm(t) < 0.005:
            break
    c, s = math.cos(th), math.sin(th)
    P = (P0 - center) @ np.array([[c, s], [-s, c]]) + center + [dx, dy]
    res = []
    for p, o, rb in zip(P, off, ref):
        if rb is not None and not rb.is_empty:
            res.append(abs(rb.distance(Point(*p)) - o))
    return th, dx, dy, (np.median(res) if res else None)


def _inner_pts(features, step=4.0):
    """차선·중앙선 점: 연석과의 거리는 모르지만 '차도 안에 있어야 한다'는 조건으로 거친 맞춤을 돕는다."""
    out = []
    for f in features:
        if f["kind"] in ("차선", "중앙선", "정지선"):
            out += list(_samples(f["geom"], step))
    return np.array(out) if out else np.zeros((0, 2))


def _coarse(pts, curb, walk, center, reach=12.0, log=print, inner=None, road=None):
    """ICP 는 처음 어긋남이 2m 를 넘으면 엉뚱한 연석에 붙는다. 그래서 먼저 거리 래스터 위에서
    이동 ±reach m(0.5m 간격) × 회전 ±1.5°(0.5° 간격)를 모두 대 보고 가장 잘 겹치는 자리를 고른다."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy.ndimage import distance_transform_edt
    if len(pts) + (0 if inner is None else len(inner)) < 6:
        return 0.0, 0.0, 0.0, 0.0
    P = np.array([p for p, _, _ in pts]) if pts else np.zeros((0, 2))
    kind = np.array([0 if k == "curb" else 1 for _, _, k in pts], dtype=int)
    if len(P) > 1500:
        sel = np.linspace(0, len(P) - 1, 1500).astype(int)
        P, kind = P[sel], kind[sel]
    if inner is not None and len(inner) and road is not None:
        I = inner if len(inner) <= 1500 else inner[np.linspace(0, len(inner) - 1, 1500).astype(int)]
        P = np.vstack([P, I]) if len(P) else I
        kind = np.concatenate([kind, np.full(len(I), 2, dtype=int)])
    res = 0.25
    x0, y0 = P.min(0) - reach - 5
    x1, y1 = P.max(0) + reach + 5
    nx, ny = int((x1 - x0) / res) + 1, int((y1 - y0) / res) + 1
    tf = from_origin(x0, y1, res, res)
    D = []
    for ref in (curb, walk):
        if ref is None or ref.is_empty:
            D.append(np.full((ny, nx), 99.0)); continue
        m = rasterize([(ref, 1)], out_shape=(ny, nx), transform=tf, all_touched=True, fill=0)
        D.append(distance_transform_edt(m == 0) * res)
    M = None
    if road is not None and (kind == 2).any():
        M = rasterize([(road.buffer(-0.3), 1)], out_shape=(ny, nx), transform=tf, fill=0).astype(bool)
    best = (-1e9, 0.0, 0.0, 0.0)
    shifts = np.arange(-reach, reach + 1e-6, 0.5)
    SX, SY = np.meshgrid(shifts, shifts)
    SX, SY = SX.ravel(), SY.ravel()
    for deg in np.arange(-1.5, 1.51, 0.5):
        th = math.radians(deg)
        c, s_ = math.cos(th), math.sin(th)
        Q = (P - center) @ np.array([[c, s_], [-s_, c]]) + center
        sc = np.zeros(len(SX))
        for ch in range(0, len(Q), 300):
            q = Q[ch:ch + 300]
            k = kind[ch:ch + 300]
            col = ((q[:, 0][None, :] + SX[:, None] - x0) / res).astype(int).clip(0, nx - 1)
            row = ((y1 - (q[:, 1][None, :] + SY[:, None])) / res).astype(int).clip(0, ny - 1)
            d = np.where(k[None, :] == 0, D[0][row, col], D[1][row, col])
            v = np.exp(-(d / 0.5) ** 2)
            if M is not None:
                v = np.where(k[None, :] == 2, np.where(M[row, col], 0.5, -0.5), v)   # 차선 점: 차도 안 +, 밖 −
            sc += v.sum(1)
        i = int(sc.argmax())
        if sc[i] > best[0]:
            best = (float(sc[i]), th, float(SX[i]), float(SY[i]))
    sc, th, dx, dy = best
    log(f"   거친 맞춤: 이동 {dx:+.1f},{dy:+.1f}m · 회전 {math.degrees(th):+.1f}° (겹친 점 비율 {sc / len(P):.0%})")
    return th, dx, dy, sc / len(P)


def _rigid(th, dx, dy, center):
    c, s = math.cos(th), math.sin(th)
    cx, cy = center

    def f(x, y, z=None):
        x = np.asarray(x) - cx
        y = np.asarray(y) - cy
        return x * c - y * s + cx + dx, x * s + y * c + cy + dy
    return f


# ---------------------------------------------------------------- 2) 구역 맞춤
def _local_field(features, curb, walk, cell=60.0, max_shift=1.0, log=print):
    """칸마다 남은 이동량 → 역거리 가중 변위장. 칸에 점이 적으면 0."""
    pts = _residual_pts(features)
    if len(pts) < 20:
        return None
    P = np.array([p for p, _, _ in pts])
    x0, y0 = P.min(0)
    nodes = []
    ix = np.floor((P[:, 0] - x0) / cell).astype(int)
    iy = np.floor((P[:, 1] - y0) / cell).astype(int)
    for key in set(zip(ix, iy)):
        sel = [k for k in range(len(pts)) if (ix[k], iy[k]) == key]
        if len(sel) < 12:
            continue
        sub = [pts[k] for k in sel]
        ctr = P[sel].mean(0)
        th, dx, dy, med = _fit(sub, curb, walk, ctr, iters=4, log=log)
        v = np.array([dx, dy])
        n = np.linalg.norm(v)
        if n > max_shift:                       # 1m 넘게 고쳐야 한다면 그 칸 자료가 이상한 것 → 믿지 않음
            continue
        nodes.append((ctr, v))
    if not nodes:
        return None
    C = np.array([c for c, _ in nodes])
    Vv = np.array([v for _, v in nodes])

    def f(x, y, z=None):
        x = np.atleast_1d(np.asarray(x, float))
        y = np.atleast_1d(np.asarray(y, float))
        d2 = (x[:, None] - C[None, :, 0]) ** 2 + (y[:, None] - C[None, :, 1]) ** 2
        w = 1.0 / (d2 + (cell / 2) ** 2)
        w /= w.sum(1, keepdims=True)
        # 칸 중심에서 멀면(자료 없는 곳) 0 으로 잦아들게
        fall = np.exp(-np.sqrt(d2.min(1)) / (2 * cell))
        u = (w @ Vv) * fall[:, None]
        return x + u[:, 0], y + u[:, 1]
    log(f"   구역 맞춤: {len(nodes)}칸, 칸별 추가 이동 최대 {np.linalg.norm(Vv, axis=1).max():.2f}m")
    return f


# ---------------------------------------------------------------- 3) 표시별 끝 맞춤
def _fix_crosswalk(poly, road, islands=None, max_ext=0.8):
    """자기 방향 그대로 양 끝만 연석에 맞춘다.
    - 연석 밖으로 나간 끝은 자르고, 연석에 0.8m 안쪽으로 못 미친 끝은 늘인다(그보다 짧은 건 원래 설계로 보고 둠).
    - 교통섬을 지나는 횡단보도는 한 덩어리로 두고 섬 위 부분만 뺀다."""
    r = poly.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e0, e1 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e0) >= np.linalg.norm(e1):
        along, across = e0, e1
    else:
        along, across = e1, e0
    L, W = np.linalg.norm(along), np.linalg.norm(across)
    u = along / L                                     # 건너는 방향
    ctr = np.asarray(r.centroid.coords[0])
    ln = LineString([ctr - u * (L / 2 + max_ext), ctr + u * (L / 2 + max_ext)]).intersection(road)
    segs = [g for g in getattr(ln, "geoms", [ln]) if g.geom_type == "LineString" and g.length > 0.5]
    if not segs:
        return poly, "차도 밖"
    seg = min(segs, key=lambda g: g.distance(Point(*ctr)))
    if seg.distance(Point(*ctr)) > 1.0:
        return poly, "차도 밖"
    t = [float((np.asarray(q) - ctr) @ u) for q in seg.coords]
    a, b = min(t), max(t)
    a0, b0 = -L / 2, L / 2
    # 끝마다: 연석보다 나갔으면 자르고(a>a0), 못 미쳤으면 max_ext 안에서만 늘임
    a = max(a, a0 - max_ext) if a < a0 else a
    b = min(b, b0 + max_ext) if b > b0 else b
    v = np.array([-u[1], u[0]]) * W / 2
    new = Polygon([ctr + u * a - v, ctr + u * b - v, ctr + u * b + v, ctr + u * a + v])
    if islands is not None and not islands.is_empty:
        new = new.difference(islands)
    note = None
    if abs((b - a) - L) > 0.3:
        note = f"길이 {L:.1f}→{b - a:.1f}m"
    return new, note


def _fix_line(g, road_in, min_len=1.0):
    x = g.intersection(road_in)
    parts = [p for p in getattr(x, "geoms", [x]) if p.geom_type == "LineString" and p.length >= min_len]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiLineString(parts)


def _fix_stop(g, road, ext=1.0):
    """정지선: 차도 밖으로 나간 부분은 자르고, 연석에 1m 안쪽으로 못 미친 끝만 연석까지 늘인다.
    (중앙선 쪽 끝은 연석과 멀어서 그대로 둔다 — 반대 차로로 넘어가지 않게)"""
    if g.geom_type != "LineString":
        return g.intersection(road)
    p0, p1 = np.asarray(g.coords[0]), np.asarray(g.coords[-1])
    u = (p1 - p0) / (np.linalg.norm(p1 - p0) or 1)
    curb = road.boundary
    q0 = p0 - u * ext if curb.distance(Point(*p0)) < ext else p0
    q1 = p1 + u * ext if curb.distance(Point(*p1)) < ext else p1
    long_ = LineString([q0, q1]).intersection(road)
    mid = Point(*(p0 + p1) / 2)
    for q in getattr(long_, "geoms", [long_]):
        if q.geom_type == "LineString" and q.distance(mid) < 0.2:
            return q
    return g.intersection(road)


def _fix_arrow(poly, drive, lanes):
    if drive.buffer(-0.1).contains(poly):
        return poly, None
    c = poly.centroid
    if lanes:
        # 가장 가까운 차선 두 줄 사이 가운데로
        near = sorted(lanes, key=lambda l: l.distance(c))[:2]
        if len(near) == 2:
            a = nearest_points(near[0], c)[0]
            b = nearest_points(near[1], c)[0]
            tgt = Point((a.x + b.x) / 2, (a.y + b.y) / 2)
            dx, dy = tgt.x - c.x, tgt.y - c.y
            if math.hypot(dx, dy) < 2.0:
                moved = shp_transform(lambda x, y, z=None: (np.asarray(x) + dx, np.asarray(y) + dy), poly)
                if drive.buffer(-0.05).contains(moved):
                    return moved, f"차로 가운데로 {math.hypot(dx, dy):.2f}m 이동"
    return None, "차도 밖이라 뺌"


# ---------------------------------------------------------------- 4) 변경 의심(항공사진)
def change_flags(cw_polys, aerial_zebras, drive, near=1.0):
    """aerial_zebras: [Polygon] (marking_assets.find_zebras 의 band).
    서로 겹치는지(1m 여유)로 짝을 짓는다 — 거리만 보면 교차로 모서리의 옆 횡단보도와 잘못 짝지어짐."""
    def hit(a, b):
        return a.intersection(b.buffer(near)).area > 0.3 * min(a.area, b.area)
    flags = []
    for z in aerial_zebras:
        if not any(hit(z, p) for p in cw_polys):
            flags.append(("새로 생김?", z.centroid, "사진엔 횡단보도가 보이는데 정밀도로지도엔 없음"))
    for p in cw_polys:
        if aerial_zebras and not any(hit(p, z) for z in aerial_zebras):
            flags.append(("지워짐?", p.centroid, "정밀도로지도엔 있는데 사진에서 줄무늬를 못 찾음(가림일 수도)"))
    return flags


# ---------------------------------------------------------------- 전체
def align(features, road, walk, islands=None, aerial_zebras=None, src_epsg="auto", dst_epsg=5186, log=print):
    """반환 dict(features=고친 표시, report=dict, flags=[...])"""
    rep = {}
    if not features:
        return dict(features=[], report={"표시 수": 0}, flags=[])
    # 0) 좌표계
    if src_epsg == "auto":
        b = unary_union([f["geom"] for f in features]).bounds
        src_epsg = guess_crs(b)
        rep["좌표계(추정)"] = f"EPSG:{src_epsg}" if src_epsg else "알 수 없음"
    features = to_target(features, src_epsg, dst_epsg, log)
    curb = road.buffer(-EDGE_OFFSET).boundary      # 차도 가장자리선이 있어야 할 자리(연석 0.25m 안쪽)
    walk_b = walk.boundary if walk is not None and not walk.is_empty else None
    drive = road.difference(islands) if islands is not None and not islands.is_empty else road
    # 1) 전체 맞춤
    pts = _residual_pts(features)
    center = np.array([p for p, _, _ in pts]).mean(0) if pts else np.array(road.centroid.coords[0])
    before = None
    if pts:
        before = float(np.median([abs((curb if k == "curb" else walk_b).distance(Point(*p)) - o)
                                  for p, o, k in pts if (curb if k == "curb" else walk_b) is not None]))
    inner = _inner_pts(features)
    if len(pts) < 6 and len(inner):
        center = inner.mean(0)
    th0, dx0, dy0, _ = _coarse(pts, curb, walk_b, center, log=log, inner=inner, road=road)
    th, dx, dy, _ = _fit(pts, curb, walk_b, center, log=log, init=(th0, dx0, dy0), cut=3.0)
    th, dx, dy, after = _fit(pts, curb, walk_b, center, log=log, init=(th, dx, dy), cut=1.0)
    if len(pts) < 6:                       # 연석 기준 점이 없으면 거친 맞춤(차선이 차도 안에 들도록)만
        th, dx, dy, after = th0, dx0, dy0, None
    shift = math.hypot(dx, dy)
    rep["전체 이동"] = f"{dx:+.2f}, {dy:+.2f} m (거리 {shift:.2f} m), 회전 {math.degrees(th):+.3f}°"
    rep["맞춤 전 어긋남(중앙값)"] = f"{before:.2f} m" if before is not None else "-"
    rin = road.buffer(-0.3)

    def inside_frac(t_, x_, y_):
        if not len(inner):
            return None
        c_, s_ = math.cos(t_), math.sin(t_)
        Q = (inner - center) @ np.array([[c_, s_], [-s_, c_]]) + center + [x_, y_]
        return float(np.mean([rin.contains(Point(*q)) for q in Q]))
    # 연석 기준 점이 기준선에서 얼마나 떨어져 있는지(진단용) — 다음에 자료를 볼 때 원인을 알 수 있게 남긴다
    if pts:
        dd = np.array([(curb if k == "curb" else walk_b).distance(Point(*p)) for p, o, k in pts
                       if (curb if k == "curb" else walk_b) is not None])
        if len(dd):
            q = np.percentile(dd, [25, 50, 75])
            log(f"   (진단) 맞춤 전 가장자리선~연석 거리: 25% {q[0]:.2f} · 50% {q[1]:.2f} · 75% {q[2]:.2f} m, 1m 안 {np.mean(dd < 1):.0%}")
    ok = after is not None and after < 0.35
    f_fit, f_raw = inside_frac(th, dx, dy), inside_frac(0.0, 0.0, 0.0)
    if f_fit is not None:
        rep["차선 점이 차도 안에 든 비율(맞춘 뒤/원래)"] = f"{f_fit:.0%} / {f_raw:.0%}"
    if after is None and f_fit is not None:
        ok = f_fit >= 0.85
    if shift > 5.0:
        rep["경고"] = f"{shift:.1f}m 나 어긋나 있었음: 좌표계(측지계) 설정을 확인하세요"
        log("   ⚠ " + rep["경고"])
    if not ok:
        # 같은 기관(국토지리정보원) 자료라 원래 자리가 대개 맞다. 맞춤을 믿기 어렵지만 원래 자리에서 차선이 차도 안에
        # 잘 들어오면 이동 없이 그대로 쓴다. 원래 자리도 차도와 안 맞으면(다른 동네 자료 등) 그때만 버린다.
        if f_raw is not None and f_raw >= 0.7:
            log(f"   연석과 딱 맞는 자리는 못 찾았지만 원래 자리에서 차선 {f_raw:.0%}가 차도 안에 있어 이동 없이 그대로 씀")
            rep["맞춤"] = "이동 없이 원래 자리 사용"
            th, dx, dy, shift = 0.0, 0.0, 0.0, 0.0
        else:
            rep["경고"] = "차도와 맞는 자리를 못 찾음: 정밀도로지도를 쓰지 않음"
            log("   ⚠ " + rep["경고"])
            rep["표시 수"] = len(features)
            return dict(features=features, report=rep, flags=[], failed=True)
    if ok:
        features = _apply(features, _rigid(th, dx, dy, center))
        log(f"   정밀도로지도 전체 맞춤: 이동 {dx:+.2f},{dy:+.2f}m · 회전 {math.degrees(th):+.3f}°")
    # 2) 구역 맞춤 (전체 맞춤을 믿을 수 있을 때만)
    fld = _local_field(features, curb, walk_b, log=log) if ok else None
    if fld is not None:
        features = _apply(features, fld)
    pts2 = _residual_pts(features)
    if pts2:
        rep["맞춤 후 어긋남(중앙값)"] = "%.2f m" % float(np.median(
            [abs((curb if k == "curb" else walk_b).distance(Point(*p)) - o)
             for p, o, k in pts2 if (curb if k == "curb" else walk_b) is not None]))
    # 3) 표시별 끝 맞춤
    road_in = road.buffer(-0.3)
    lanes = [f["geom"] for f in features if f["kind"] in ("차선", "중앙선")]
    out, notes = [], {}
    for f in features:
        k, g = f["kind"], f["geom"]
        if k == "횡단보도" and g.geom_type == "Polygon":
            g2, note = _fix_crosswalk(g, road, islands)
        elif k in ("차선", "중앙선", "차도경계", "주정차금지선"):
            g2 = _fix_line(g, road_in if k not in ("차도경계", "주정차금지선") else road.buffer(-0.05))
            note = None if g2 is not None else "연석 밖이라 뺌"
        elif k == "정지선":
            g2, note = _fix_stop(g, drive), None
        elif k == "화살표":
            g2, note = _fix_arrow(g, drive, lanes)
        else:
            g2, note = g.intersection(road), None
        if note:
            notes[k + " · " + note.split(" ")[0]] = notes.get(k + " · " + note.split(" ")[0], 0) + 1
        if g2 is not None and not g2.is_empty:
            out.append(dict(f, geom=g2))
    rep["표시별 손본 것"] = notes
    rep["표시 수"] = len(out)
    # 4) 변경 의심
    flags = change_flags([f["geom"] for f in out if f["kind"] == "횡단보도"], aerial_zebras or [], drive)
    rep["변경 의심"] = len(flags)
    for k, v in rep.items():
        log(f"   {k}: {v}")
    return dict(features=out, report=rep, flags=flags, failed=False)

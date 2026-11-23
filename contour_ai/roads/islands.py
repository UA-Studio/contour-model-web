"""교통섬: 점·항공사진·정밀도로지도·횡단보도 사이에서 찾기."""
import math
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union



def islands_at_points(pts, chains, road, walk, size=(2.4, 7.0), join=15.0):
    """횡단보도가 차도 한가운데에서 끝나는 점들 → 교통섬. 15m 안의 점끼리 한 섬(점들의 볼록 껍질 + 1.2m),
    점이 하나뿐이면 둥근 끝 막대(긴 쪽 = 길 방향)로 추정."""
    if not pts:
        return Polygon()
    from shapely.geometry import MultiPoint
    road_in = road.buffer(-1.0) if road is not None else None
    groups = []
    for p in pts:
        for g in groups:
            if any(math.dist(p, q) < join for q in g):
                g.append(p)
                break
        else:
            groups.append([p])
    out = []
    for g in groups:
        cx_, cy_ = np.mean([p[0] for p in g]), np.mean([p[1] for p in g])
        q = Point(cx_, cy_)
        nrm = None
        for ln in sorted(chains, key=lambda l: l.distance(q)):
            if ln.distance(q) < 8.0:
                c = np.asarray(ln.coords)[:, :2]
                d = c[-1] - c[0]
                nrm = d / (np.linalg.norm(d) or 1.0)
                break
        if nrm is None:
            continue
        u = np.array([-nrm[1], nrm[0]])
        P = np.array(g) - np.array([cx_, cy_])
        t = P @ nrm
        across = max(size[0], float(t.max() - t.min()) + 0.6)          # 건너는 방향 폭 = 양쪽 횡단보도 끝 사이
        along = max(size[1], across + 5.0, float((P @ u).max() - (P @ u).min()) + 4.0)  # 길 방향 길이(섬 머리·꼬리)
        c0 = np.array([cx_, cy_]) + nrm * float(t.max() + t.min()) / 2
        r = across / 2
        shp = LineString([c0 - u * (along / 2 - r), c0 + u * (along / 2 - r)]).buffer(r, 12) if along > across else \
            Point(*c0).buffer(r, 12)
        if road_in is not None:
            shp = shp.intersection(road_in)
        if not shp.is_empty and shp.area > 4:
            out.append(shp)
    return unary_union(out) if out else Polygon()


def islands_from_aerial(aerial_path, road, walk, min_area=12.0):
    """항공사진에서 차도 안 화단(나무·풀)이 보이는 덩어리 → 교통섬·중앙 화단. 보도에 붙은 것(가로수 수관이
    차도 위로 뻗은 것)은 뺀다. 둘레 0.6m 를 연석으로 붙인다."""
    from rasterio import features
    from rasterio.transform import from_origin
    from ..aerial import load
    from ..vegetation import canopy_mask
    rgb, x0, y1, cell, _ = load(aerial_path)
    tree, info = canopy_mask(rgb, cell)
    green = tree | info["grass"]
    if not green.any():
        return Polygon()
    from shapely.geometry import shape
    tr = from_origin(x0, y1, cell, cell)
    polys = [shape(g) for g, v in features.shapes(green.astype(np.uint8), mask=green, transform=tr) if v == 1]
    road_in = road.buffer(-0.8)
    out = []
    for p in polys:
        p = p.buffer(0.3).buffer(-0.3)
        if p.is_empty or p.area < min_area:
            continue
        inside = p.intersection(road_in)
        if inside.area < 0.8 * p.area:            # 대부분 차도 안이어야
            continue
        if walk is not None and not walk.is_empty and walk.distance(p) < 1.2:
            continue                               # 보도에 붙은 초록 = 가로수 수관
        r = p.minimum_rotated_rectangle
        if r.area > 0 and p.area / r.area < 0.35:  # 너무 들쭉날쭉(차 지붕·그림자 조각)
            continue
        out.append(p.buffer(0.6, join_style=1).simplify(0.4).intersection(road_in))
    return unary_union(out) if out else Polygon()


def hd_islands(fx, road, known=None, log=print, gap=2.5, walk=None):
    """정밀도로지도 가장자리선(길가장자리선 505·주정차금지선 515)이 차도 안에서 둘러싼 곳 = 교통섬.
    선은 횡단보도 자리에서 끊겨 있으므로 gap(m) 만큼 부풀려 이어 붙인 뒤, 안쪽 구멍을 다시 그만큼 키운다.
    가장자리선은 연석 0.25m 안쪽에 칠하므로 마지막에 0.25m 줄인다."""
    from shapely.geometry import Polygon as _P
    edges = [f["geom"] for f in fx if f["kind"] in ("차도경계", "주정차금지선")]
    if not edges:
        return None
    U = unary_union([e.buffer(gap, cap_style=2) for e in edges])
    # 수치지도 차도 면이 섬 자리를 구멍으로 비워 둔 경우도 있으므로, 작은 구멍(1500㎡ 미만)은 차도로 친다
    holes = [_P(r) for p in getattr(road, "geoms", [road]) if p.geom_type == "Polygon" for r in p.interiors]
    road_plus = unary_union([road] + [h for h in holes if h.area < 1500.0])
    outer = unary_union([p.exterior for p in getattr(road, "geoms", [road]) if p.geom_type == "Polygon"])
    out = []
    for pg in getattr(U, "geoms", [U]):
        if pg.geom_type != "Polygon":
            continue
        for ring in pg.interiors:
            P = _P(ring).buffer(gap - 0.25, join_style=2)
            if P.is_empty or not (15.0 <= P.area <= 3000.0):
                continue
            if P.intersection(road_plus).area < 0.8 * P.area:     # 차도 밖(건물 블록 등)
                continue
            if known is not None and not known.is_empty and P.intersection(known).area > 0.3 * P.area:
                continue
            if walk is not None and not walk.is_empty and P.intersection(walk).area > 0.3 * P.area:
                continue                                           # 보도가 든 곳 = 길로 둘러싸인 건물 블록
            if outer.distance(P) < 1.0:
                continue                                           # 바깥 연석에 붙은 조각(섬과 연석 사이 틈 등)
            out.append(P.buffer(0.4).buffer(-0.4))
    n_edge = len(out)
    have = unary_union([g for g in [known] + out if g is not None and not g.is_empty] or [_P()])
    cw = [f["geom"] for f in fx if f["kind"] == "횡단보도" and f["geom"].geom_type == "Polygon"]
    out += crosswalk_islands(cw, road, walk, have)
    if not out:
        return None
    isl = unary_union(out)
    log(f"   정밀도로지도 교통섬: 가장자리선으로 {n_edge}곳, 횡단보도 끝으로 {len(out) - n_edge}곳 ({isl.area:.0f}㎡)")
    return isl


def crosswalk_islands(cws, road, walk, have=None, off=2.0, join=14.0):
    """횡단보도 끝이 보도에 닿지 않고 차도 한가운데에서 끝나면 그 자리에 교통섬이 있다.
    가까운 끝(14m 안)끼리 묶어 그 끝 변들을 감싼 모양을 섬으로 만든다(두 횡단보도가 만나는 세모 섬, 한 길을
    두 번에 건너는 가운데 섬). 끝 변 바깥으로 1.5m 더 나간 자리까지가 섬이다."""
    from shapely.geometry import LineString as _L, Polygon as _P
    holes = [_P(r) for p in getattr(road, "geoms", [road]) if p.geom_type == "Polygon" for r in p.interiors]
    road_f = unary_union([road] + [h for h in holes if h.area < 1500.0]).buffer(0.5)   # 섬 자리가 구멍인 차도도
    ends = []
    for k, pg in enumerate(cws):
        r = pg.minimum_rotated_rectangle
        c = np.asarray(r.exterior.coords)[:4]
        e = [(c[0], c[1]), (c[1], c[2]), (c[2], c[3]), (c[3], c[0])]
        L = [np.linalg.norm(b - a) for a, b in e]
        i = int(np.argmin(L))                       # 짧은 변 = 끝 (줄무늬 길이 쪽)
        for a, b in (e[i], e[(i + 2) % 4]):
            m = (a + b) / 2
            out_dir = m - np.asarray(r.centroid.coords[0])
            out_dir /= (np.linalg.norm(out_dir) or 1)
            if walk is not None and not walk.is_empty and walk.distance(Point(*m)) < off:
                continue                            # 보도에 닿은 끝
            if not road_f.contains(Point(*m)):
                continue
            ends.append((k, a + out_dir * 1.5, b + out_dir * 1.5, a, b, m))
    # 끝끼리 묶기
    par = list(range(len(ends)))

    def fd(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            if ends[i][0] != ends[j][0] and np.linalg.norm(ends[i][5] - ends[j][5]) < join:
                par[fd(i)] = fd(j)
    grp = {}
    for i in range(len(ends)):
        grp.setdefault(fd(i), []).append(ends[i])
    out = []
    for g in grp.values():
        if len({e[0] for e in g}) < 2:
            continue                                # 다른 횡단보도 끝과 짝이 없으면 판단 보류
        pts = [p for e in g for p in (e[1], e[2], e[3], e[4])]
        P = _P(pts).convex_hull if len(pts) >= 3 else None
        if P is None or P.is_empty or P.geom_type != "Polygon":
            continue
        P = P.intersection(road_f)
        for cw in cws:
            P = P.difference(cw)
        P = P.buffer(-0.2).buffer(0.2)
        if P.is_empty or P.area < 6:
            continue
        if have is not None and not have.is_empty and P.intersection(have.buffer(1.0)).area > 0.3 * P.area:
            continue
        out.append(max(getattr(P, "geoms", [P]), key=lambda q: q.area))
    return out

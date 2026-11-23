"""교차로 면(코어)과 횡단보도 위치 다듬기·이어 붙이기."""
import math
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from .centerlines import _approach_dir, _crossing, _nodes



# ---------------------------------------------------------------- 교차로 면·API 횡단보도 다듬기
def junction_cores(lines, road, join=25.0):
    """큰 교차로의 '교차로 면'(차선·화살표·중앙선을 긋지 않는 곳).

    수치지도 중심선은 큰 교차로 안에서 여러 점으로 갈라지므로, 3갈래 이상 점을 30m 안끼리 한 교차로로 묶고
    점마다 (가장 넓은 길 반폭 + 1.5m) 원을 그려 볼록 껍질 → 차도와 겹친 부분."""
    if road is None or road.is_empty or not lines:
        return Polygon()
    def is_junction(e):          # estimate_marks 와 같은 기준: 큰길끼리, 또는 4차로 이상 큰길 + 폭 8m 넘는 길
        big = [k for k in e if lines[k[0]]["n"] >= 2]
        wide = [k for k in e if lines[k[0]]["n"] >= 4]
        side = [k for k in e if lines[k[0]]["n"] < 4 and lines[k[0]]["w"] >= 8]
        return len(e) >= 3 and (len(big) >= 3 or (wide and side))
    nodes = [(p, e) for p, e in _nodes(lines) if is_junction(e)]
    if not nodes:
        return Polygon()
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    P = np.array([p for p, _ in nodes])
    pairs = cKDTree(P).query_pairs(join, output_type="ndarray")
    from scipy.sparse import coo_matrix
    A = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(P), len(P))) if len(pairs) else \
        coo_matrix((len(P), len(P)))
    _, lab = connected_components(A, directed=False)
    cores = []
    for g in np.unique(lab):
        mem = np.nonzero(lab == g)[0]
        circ, pts = [], []
        for k in mem:
            p, ends = nodes[k]
            ws = sorted((lines[i]["w"] for i, _ in ends), reverse=True)
            R = (ws[1] if len(ws) > 1 else ws[0]) / 2 + 1.5      # 두 번째로 넓은 길의 반폭 = 넓은 길이 교차로를 지나는 폭
            circ.append(Point(*p).buffer(R, 16))
            # 길마다 교차로에서 바깥으로 가며 차도 폭이 그 길 폭으로 좁아지는 곳(진입로 입구)을 찾아 그 단면을 꼭짓점으로
            for i, e in ends:
                d = lines[i]
                p0, u = _approach_dir(d["line"], e)
                sec = _mouth(p0, u, d["w"], road, R, min(80.0, d["line"].length + R))
                if sec is not None:
                    pts += sec
        hull = unary_union(circ + ([Polygon(np.asarray(pts)).convex_hull] if len(pts) >= 3 else [])).convex_hull
        if len(pts) >= 3:
            from shapely.geometry import MultiPoint
            hull = unary_union([MultiPoint([tuple(q) for q in pts]).convex_hull] + circ).convex_hull
        g_ = hull.intersection(road).buffer(0.6).buffer(-0.6)   # 차도 면 이음새 틈 메우기
        cores += [Polygon(q.exterior) for q in getattr(g_, "geoms", [g_]) if q.geom_type == "Polygon"]   # 안쪽 구멍(섬·틈)도 교차로
    return unary_union(cores)


def _mouth(p0, u, w, road, t0, t1, step=1.5):
    """교차로 점 p0 에서 길 방향 u 로 t0~t1 를 가며, 차도 단면 폭이 그 길 폭(1.25배 + 2m) 안으로 줄어드는 첫 자리의
    단면 양 끝 [a, b]. 단면 양 끝은 길 폭 범위로 자른다(옆 골목으로 새지 않게)."""
    nrm = np.array([u[1], -u[0]])
    lim = 1.25 * w + 2.0
    for t in np.arange(t0, t1, step):
        c = p0 + u * t
        ln = LineString([c - nrm * 45, c + nrm * 45]).intersection(road)
        segs = []
        for g in getattr(ln, "geoms", [ln]):
            if g.geom_type == "LineString" and g.length > 0.2:
                q = np.asarray(g.coords)[:, :2]
                tt = sorted(float((x - c) @ nrm) for x in q)
                segs.append((tt[0], tt[-1]))
        hit = next(((a, b) for a, b in segs if a - 0.5 <= 0 <= b + 0.5), None)
        if hit is None:
            continue
        if hit[1] - hit[0] <= lim:
            return [c + nrm * hit[0], c + nrm * hit[1]]
    return None


def chain_links(links, walk, road, tol=1.0):
    """도보 네트워크 횡단보도 링크를 이어 붙여 '보도에서 보도까지' 한 줄로. 차도 한가운데에서 이어지는
    점(보도에서 1.5m 넘게 떨어짐)은 그 자리에 교통섬이 있다는 뜻 → 반환 (줄 목록, 섬 자리 점 목록)."""
    from shapely.ops import linemerge
    segs = [ln for ln in links if ln is not None and not ln.is_empty and ln.length > 0.5]
    if not segs:
        return [], []
    merged = linemerge(unary_union(segs))
    chains = [g for g in getattr(merged, "geoms", [merged]) if g.geom_type == "LineString"]
    isl_pts = []
    if walk is not None and not walk.is_empty:
        # 횡단보도 링크 끝이 보도가 아닌 차도 한가운데(보도에서 1.5m 넘게)에서 끝나면 그 자리는 교통섬
        # (섬 위 보행 링크는 횡단보도가 아니라 받지 않으므로 끝이 섬 위에 떠 보인다).
        # 단, 두 링크가 같은 점에서 곧게 이어지기만 하면(단순히 나뉜 선) 섬이 아니다
        ends = []
        for ln in segs:
            c = np.asarray(ln.coords)[:, :2]
            for p, q in ((c[0], c[1]), (c[-1], c[-2])):
                v = q - p
                ends.append((p, v / (np.linalg.norm(v) or 1.0)))
        from scipy.spatial import cKDTree
        P = np.array([e[0] for e in ends])
        tr = cKDTree(P)
        for k, (p, v) in enumerate(ends):
            q = Point(*p)
            # 연석에서 3m 넘게 안쪽이어야 섬(방향이 조금 어긋난 링크 끝이 연석 바로 옆 차도에 떨어진 것은 아님)
            if walk.distance(q) <= 3.0:
                continue
            if road is not None and not road.buffer(0.5).contains(q):
                continue
            if road is not None and road.boundary.distance(q) < 3.0 and walk.distance(q) < 5.0:
                continue
            mates = [j for j in tr.query_ball_point(p, tol) if j != k]
            if len(mates) == 1 and float(np.dot(v, ends[mates[0]][1])) < -0.96:
                continue                                  # 곧게 이어지는 한 선
            isl_pts.append((float(p[0]), float(p[1])))
        # 섬에는 양쪽에서 건너온 횡단보도 끝이 둘 이상 닿는다: 15m 안에 짝이 없는 외톨이 끝은 링크 오차로 본다
        isl_pts = [p for p in isl_pts if any(0.5 < math.dist(p, q) < 15.0 for q in isl_pts)]
        # 섬에서 나뉜 두 조각(거의 일직선)은 한 횡단보도로 잇는다 — 보도 끝에서 끝까지 맞추려면 전체 길이가 필요
        joined, used = [], set()
        for a in range(len(chains)):
            if a in used:
                continue
            ca = np.asarray(chains[a].coords)[:, :2]
            best = None
            for b in range(a + 1, len(chains)):
                if b in used:
                    continue
                cb = np.asarray(chains[b].coords)[:, :2]
                da, db = ca[-1] - ca[0], cb[-1] - cb[0]
                if abs(float(da @ db)) < 0.9 * np.linalg.norm(da) * np.linalg.norm(db):
                    continue
                for ia in (0, -1):
                    for ib in (0, -1):
                        ea, eb = ca[ia], cb[ib]
                        dd = float(np.linalg.norm(ea - eb))
                        if dd < 15.0 and any(math.dist(tuple(ea), q) < 1.0 for q in isl_pts) and (best is None or dd < best[0]):
                            fa = ca[-1] if ia == 0 else ca[0]
                            fb = cb[-1] if ib == 0 else cb[0]
                            best = (dd, b, LineString([fa, fb]))
            if best is not None:
                used.update({a, best[1]})
                joined.append(best[2])
            else:
                joined.append(chains[a])
        chains = joined
    return chains, isl_pts


def _road_dir_at(c, nrm, lines, near=35.0):
    """c 근처 중심선 중 건너는 방향 nrm 에 가장 직각인 길의 방향 u (없으면 nrm 에 직각)."""
    best, bu = None, None
    pc = Point(*c)
    for d in lines:
        ln = d["line"]
        dist = ln.distance(pc)
        if dist > d["w"] / 2 + 3.0:          # 건널목 가운데는 그 길의 차도 띠 안에 있어야
            continue
        s = ln.project(pc)
        a, b = ln.interpolate(max(s - 3, 0)), ln.interpolate(min(s + 3, ln.length))
        u = np.array([b.x - a.x, b.y - a.y])
        if np.linalg.norm(u) < 1e-6:
            continue
        u /= np.linalg.norm(u)
        score = abs(float(u @ nrm)) + dist / 200.0      # 0 이면 완전히 직각
        if best is None or score < best:
            best, bu = score, u
    if bu is None or best > 0.6:                       # 직각에 가까운 길이 없다(약 53° 넘게 비스듬)
        return np.array([-nrm[1], nrm[0]]), False
    return bu, True


def snap_crossings(chains, lines, road, walk, core=None, log=print, width=4.0):
    """API 횡단보도 줄 → 규격 띠. 위치(가운데)만 믿고, 방향은 그 자리 길에 직각, 길이는 보도 끝에서 끝까지.
    교차로 한가운데에 떠 있거나(교차로 면 안쪽 4m), 길에 맞출 수 없을 만큼 비스듬한 줄은 버린다.
    반환 [(띠, u, nrm, e0, e1)]"""
    out, dropped = [], 0
    inner = core.buffer(-4.0) if core is not None and not core.is_empty else None
    for ln in chains:
        c = np.asarray(ln.coords)[:, :2]
        d = c[-1] - c[0]
        L = float(np.linalg.norm(d))
        if L < 2:
            continue
        nrm = d / L
        mid = np.asarray(ln.interpolate(0.5, normalized=True).coords[0][:2])
        if inner is not None and inner.contains(Point(*mid)):
            dropped += 1
            continue
        u, ok = _road_dir_at(mid, nrm, lines)
        hit = _crossing(mid, u, road, walk, width, slide=(0, 1, -1, 2, -2, 3, -3, 4.5, -4.5), max_len=L + 12) \
            if (ok and walk is not None and road is not None) else None
        if hit is not None:
            band, (e0, e1), n2 = hit
            # 맞춘 띠가 원래 자리에서 많이 밀렸거나(6m) 건너는 방향이 35° 넘게 바뀌면 다른 건널목에 붙은 것 → 버림
            moved = band.centroid.distance(Point(*mid))
            if (inner is not None and inner.contains(band.centroid)) or moved > 6.0 or abs(float(n2 @ nrm)) < 0.82:
                dropped += 1
                continue
            out.append((band, u, n2, e0, e1))
            continue
        # 맞출 수 없으면: 길에 거의 직각(20° 안)이고 두 끝이 보도에 닿을 때만 줄 그대로
        if ok and abs(float(u @ nrm)) < 0.34 and walk is not None and \
                walk.distance(Point(*c[0])) < 2.0 and walk.distance(Point(*c[-1])) < 2.0:
            v = np.array([-nrm[1], nrm[0]])
            band = Polygon([c[0] - v * width / 2, c[-1] - v * width / 2, c[-1] + v * width / 2, c[0] + v * width / 2])
            out.append((band, v, nrm, c[0], c[-1]))
        else:
            dropped += 1
    if dropped:
        log(f"   API 횡단보도 {dropped}개는 길에 맞지 않거나 교차로 한가운데라 뺌")
    return out


def dedupe_bands(bands, gap=6.0, ang=0.42):
    """같은 횡단보도를 여러 출처가 그린 것: 가운데가 gap 안이고 방향이 약 25° 안이면 앞의 것(우선 출처)만."""
    keep = []
    for b in bands:
        cb = np.asarray(b[0].centroid.coords[0])
        dup = False
        for k in keep:
            ck = np.asarray(k[0].centroid.coords[0])
            if np.linalg.norm(cb - ck) < gap and abs(float(np.dot(b[2], k[2]))) > math.cos(ang):
                dup = True
                break
            if b[0].intersection(k[0]).area > 0.25 * min(b[0].area, k[0].area):
                dup = True
                break
        if not dup:
            keep.append(b)
    return keep

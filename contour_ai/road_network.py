"""길 뼈대: 도로중심선, 교차로(마디), 진입로 방향, 차도 면 모양, 교차로 팔(연석 사이 단면), 길을 따라 까는 띠.

노면표시·횡단보도·신호등이 모두 이 뼈대를 씁니다.
"""
import math
import os

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from .road_sources import _f


LANE_W = 3.2


# ---------------------------------------------------------------- 도로 중심선 그래프
def _centerlines(folder, bbox):
    import shapefile
    from shapely.geometry import shape
    fn = os.path.join(folder, "N3L_A0020000")
    if not os.path.exists(fn + ".shp"):
        return []
    clip = box(*bbox).buffer(30)
    r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
    out = []
    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict()
        n = int(_f(rec.get("차로수"), 1))
        w = _f(rec.get("도로폭"), 0.0)
        med = str(rec.get("분리대유무") or "")
        one = str(rec.get("일방통행") or "")
        try:
            g = shape(sr.shape.__geo_interface__)
        except Exception:
            continue
        if not g.intersects(clip):
            continue
        for ln in getattr(g, "geoms", [g]):
            if ln.geom_type == "LineString" and ln.length > 1:
                out.append(dict(line=ln, n=max(1, n), w=w or LANE_W * max(1, n) + 1.0,
                                median=("유" in med or med.upper() in ("Y", "1", "TRUE")),
                                oneway=("유" in one or one.upper() in ("Y", "1", "TRUE"))))
    r.close()
    return out


def _nodes(lines, tol=1.0):
    """중심선 끝점을 모아 교차점 [(점, [(선 번호, 끝 0|1)])]."""
    from scipy.spatial import cKDTree
    ends = []
    for i, d in enumerate(lines):
        c = d["line"].coords
        ends += [(c[0][:2], i, 0), (c[-1][:2], i, 1)]
    if not ends:
        return []
    P = np.array([e[0] for e in ends])
    tr = cKDTree(P)
    seen = np.zeros(len(P), bool)
    out = []
    for k in range(len(P)):
        if seen[k]:
            continue
        grp = tr.query_ball_point(P[k], tol)
        for g in grp:
            seen[g] = True
        out.append((P[grp].mean(axis=0), [(ends[g][1], ends[g][2]) for g in grp]))
    return out


def _approach_dir(line, end):
    """교차점에서 길 쪽으로 향하는 단위 벡터."""
    c = np.asarray(line.coords)[:, :2]
    if end == 1:
        c = c[::-1]
    p0 = c[0]
    q = np.asarray(line.interpolate(min(6.0, line.length * 0.5)).coords[0][:2]) if end == 0 else \
        np.asarray(line.interpolate(max(line.length - 6.0, line.length * 0.5)).coords[0][:2])
    d = q - p0
    return p0, d / (np.linalg.norm(d) or 1.0)


def _carriage(ln, road, samples=5):
    """중심선 가운데 60% 에서 직각 단면으로 잰 차도 폭의 중앙값, 단면 가운데가 중심선에 가까운지, 가운데가 비었는지(분리대).
    반환 (폭 | None, 가운데?, 분리대 틈?)"""
    if road is None or road.is_empty or ln.length < 4:
        return None, True, False
    spans, gaps, mids = [], 0, []
    for f in np.linspace(0.2, 0.8, samples):
        s = ln.length * f
        a, b = ln.interpolate(max(s - 1, 0)), ln.interpolate(min(s + 1, ln.length))
        u = np.array([b.x - a.x, b.y - a.y])
        if np.linalg.norm(u) < 1e-6:
            continue
        u /= np.linalg.norm(u)
        nrm = np.array([u[1], -u[0]])
        c = np.asarray(ln.interpolate(s).coords[0][:2])
        x = LineString([c - nrm * 30, c + nrm * 30]).intersection(road)
        segs = []
        for g in getattr(x, "geoms", [x]):
            if g.geom_type == "LineString" and g.length > 0.3:
                q = np.asarray(g.coords)[:, :2]
                t = sorted(float((v - c) @ nrm) for v in q)
                segs.append((t[0], t[-1]))
        segs.sort()
        k = next((i for i, (t0, t1) in enumerate(segs) if t0 - 0.3 <= 0 <= t1 + 0.3), None)
        if k is not None:
            spans.append(segs[k][1] - segs[k][0])
            mids.append((segs[k][1] + segs[k][0]) / 2)
            continue
        # 중심선이 차도 밖(분리대 위): 양옆 가까운 차도 두 쪽을 합쳐 잰다
        lo = [sg for sg in segs if sg[1] < 0 and -sg[1] < 4]
        hi = [sg for sg in segs if sg[0] > 0 and sg[0] < 4]
        if lo and hi:
            spans.append((lo[-1][1] - lo[-1][0]) + (hi[0][1] - hi[0][0]))
            mids.append((lo[-1][0] + hi[0][1]) / 2)
            gaps += 1
    if not spans:
        return None, 0.0, False
    # 가운데 어긋남: 중심선이 차도 가운데에서 벗어난 거리(왼쪽 +). 오른쪽 법선 기준이라 부호를 바꾼다
    mid = float(np.median(mids)) if mids else 0.0
    return float(np.median(spans)), (-mid if abs(mid) < 3.0 else 0.0), gaps >= max(1, len(spans) // 2)


def _split_dual(lines, road):
    """한 차도 면 안에 방향별 중심선이 두 줄 나란히 그려진 길(상·하행 분리 도로): 둘 다 전체 폭으로 차선을 그으면
    겹친다. 나란한 이웃 선이 단면 안에 있으면 이 선은 자기 쪽 절반만 맡는 일방 도로로 고친다."""
    from shapely.strtree import STRtree
    L = [d["line"] for d in lines]
    if len(L) < 2:
        return
    tr = STRtree(L)
    for i, d in enumerate(lines):
        span, shift, _ = d.get("_c") or (None, 0.0, False)
        ln = d["line"]
        if not span or ln.length < 10:
            continue
        s = ln.length / 2
        a, b = ln.interpolate(s - 2), ln.interpolate(s + 2)
        u = np.array([b.x - a.x, b.y - a.y]); u /= np.linalg.norm(u) or 1.0
        nrm = np.array([u[1], -u[0]])                 # 오른쪽
        c = np.asarray(ln.interpolate(s).coords[0][:2])
        half = span / 2
        lo, hi = -shift - half, -shift + half          # 오른쪽 법선 기준 차도 끝 (shift 는 왼쪽 + 라 부호 반대)
        cut = LineString([c + nrm * (lo + 0.5), c + nrm * (hi - 0.5)])
        best = None
        for j in tr.query(cut):
            j = int(j)
            if j == i:
                continue
            o = L[j]
            x = o.intersection(cut)
            if x.is_empty or x.geom_type != "Point":
                continue
            so = o.project(x)
            pa, pb = o.interpolate(max(so - 2, 0)), o.interpolate(min(so + 2, o.length))
            v = np.array([pb.x - pa.x, pb.y - pa.y]); v /= np.linalg.norm(v) or 1.0
            if abs(float(u @ v)) < 0.9:               # 나란하지 않음(가로지르는 길)
                continue
            t = float((np.array([x.x, x.y]) - c) @ nrm)
            if abs(t) > 2.0 and (best is None or abs(t) < abs(best)):
                best = t
        if best is None:
            continue
        mid = best / 2                                 # 두 선 사이 가운데가 경계
        if best > 0:      # 이웃이 오른쪽 → 이 선은 왼쪽 절반 [lo', mid] 를 맡음 (오른쪽 법선 기준 lo~mid)
            a0, a1 = lo, mid
        else:
            a0, a1 = mid, hi
        w = a1 - a0
        if w < 3.0:
            continue
        d["_c"] = (w, -(a0 + a1) / 2, False)
        d["n"] = max(1, int(round(w / 3.3)))
        d["lw"] = min(max(w / d["n"], 2.75), 3.6)
        d["oneway"] = True
        d["dual"] = True


def _strip(p, d, along0, along1, off0, off1):
    """p 에서 d 방향 along0~along1, 오른쪽 법선 off0~off1 인 사각형."""
    nrm = np.array([d[1], -d[0]])
    a, b = p + d * along0, p + d * along1
    return Polygon([a + nrm * off0, b + nrm * off0, b + nrm * off1, a + nrm * off1])


def _offset_strips(line, off, width, dash=None):
    """중심선에서 off 만큼 옆(+는 진행 방향 왼쪽)으로 평행한 폭 width 띠. dash=(칠, 빈) 이면 점선."""
    try:
        ln = line.offset_curve(off) if abs(off) > 1e-6 else line
    except Exception:
        return []
    out = []
    for seg in getattr(ln, "geoms", [ln]):
        if seg.geom_type != "LineString" or seg.length < 1:
            continue
        if dash is None:
            out.append(seg.buffer(width / 2, cap_style=2))
            continue
        on, gap = dash
        s = 0.0
        while s + on <= seg.length:
            from shapely.ops import substring
            out.append(substring(seg, s, s + on).buffer(width / 2, cap_style=2))
            s += on + gap
    return out


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


def _section(c, nrm, road, reach=40.0):
    """c 를 지나는 nrm 방향 차도 단면 (t0, t1) — c 기준 거리, c 를 포함하는 조각. 없으면 None."""
    x = LineString([c - nrm * reach, c + nrm * reach]).intersection(road)
    for g in getattr(x, "geoms", [x]):
        if g.geom_type == "LineString" and g.length > 0.3:
            q = np.asarray(g.coords)[:, :2]
            t = sorted(float((v - c) @ nrm) for v in q)
            if t[0] - 0.3 <= 0 <= t[-1] + 0.3:
                return t[0], t[-1]
    return None


def _arm_end(d, e, R, junction):
    """연석 틀을 어디까지 잴지: 중심선 반대 끝도 교차로면 그 사이 절반까지(양쪽 진입로가 나눠 맡음), 아니면 입구에서 70m."""
    c = d["line"].coords
    other = np.asarray(c[-1] if e == 0 else c[0])[:2]
    if any(np.linalg.norm(np.asarray(jp) - other) < 3.0 for jp, _, _ in junction):
        return max(R + 20.0, d["line"].length / 2 + 2.0)
    return R + 70.0


def _ransac_line(S, T, tol=0.8, min_span=15.0, min_frac=0.3, k_ref=None, k_tol=0.08):
    """(S,T) 점들에 가장 많은 점이 0.8m 안에 드는 직선. 빈 값(nan) 무시.
    반환 (계수, 맞는 점 표시) 또는 None (맞는 점이 15m 안 되거나 30% 미만)"""
    ok0 = np.isfinite(T)
    idx = np.flatnonzero(ok0)
    if len(idx) < 4:
        return None
    best = None
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            i, j = idx[a], idx[b]
            if S[j] - S[i] < 6:
                continue
            k = (T[j] - T[i]) / (S[j] - S[i])
            if abs(k) > 0.4 or (k_ref is not None and abs(k - k_ref) > k_tol):
                continue
            res = np.abs(T[i] + k * (S - S[i]) - T)
            inl = ok0 & (res < tol)
            sc = inl.sum()
            if best is None or sc > best[0]:
                best = (sc, inl)
    if best is None:
        return None
    inl = best[1]
    for _ in range(2):
        cf = np.polyfit(S[inl], T[inl], 1)
        inl = ok0 & (np.abs(np.polyval(cf, S) - T) < tol)
        if inl.sum() < 4:
            return None
    if S[inl].max() - S[inl].min() < min_span or inl.sum() < min_frac * ok0.sum():
        return None
    return np.polyfit(S[inl], T[inl], 1), inl



def _curb_pair(S, T0, T1):
    """양쪽 연석 직선. 두 쪽이 나란하지 않으면(0.2 넘게) 점이 적은 쪽은 옆길·합류로 꺾인 것으로 보고
    다른 쪽과 나란한 직선을 다시 찾는다."""
    lf, rf = _ransac_line(S, T0), _ransac_line(S, T1)
    if lf is not None and rf is not None and abs(lf[0][0] - rf[0][0]) > 0.2:
        if lf[1].sum() >= rf[1].sum():
            rf = _ransac_line(S, T1, k_ref=lf[0][0]) or rf
        else:
            lf = _ransac_line(S, T0, k_ref=rf[0][0]) or lf
    return lf, rf


def arm_frame(p0, u, w, road, s0, s_max=70.0, step=2.0, stop=None, s_end=None):
    """교차로 진입로 하나의 '연석 틀'.

    중심선 방향(u)에서 시작해, 입구(s0)부터 바깥으로 2m마다 차도 단면을 재고 양쪽 연석 점을 모은다.
    두 연석 점들의 가운데 선으로 진입로 방향을 다시 잡아(두 번) 중심선이 비스듬해도 연석에 맞춘다.
    골목 입구처럼 한쪽 연석이 갑자기 튀는 곳은 이웃 값(중앙값)으로 메운다.
    반환 dict(u, p=기준점, s=[...], L=[왼쪽 연석 t], R=[오른쪽 연석 t]) — t 는 오른쪽 법선(u 의 오른쪽) 기준. 못 재면 None."""
    u = np.asarray(u, float) / (np.linalg.norm(u) or 1.0)
    p0 = np.asarray(p0, float)
    for it in range(3):
        nrm = np.array([u[1], -u[0]])
        S, T0, T1 = [], [], []
        miss = 0
        left = False                                   # 출발한 교차로 면을 벗어났는지
        for s in np.arange(s0, min(s0 + s_max, s_end or 1e9), step):
            c = p0 + u * s
            if stop is not None:
                inside = stop.contains(Point(*c))
                if inside and left and s > s0 + 6:
                    break                              # 다음 교차로에 닿음
                left = left or not inside
            sec = _section(c, nrm, road)
            if sec is None or sec[1] - sec[0] > 2.2 * w + 6:
                if S:                                  # 입구 앞(교차로 안)에서 못 잰 것은 세지 않는다
                    miss += 1
                    if miss >= 4:
                        break
                continue
            miss = 0
            # 단면 끝이 잴 수 있는 끝(40m)에 닿음 = 그쪽 연석을 못 찾음(교차로 덩어리·광장) → 빈 값
            S.append(s)
            T0.append(sec[0] if sec[0] > -39.5 else np.nan)
            T1.append(sec[1] if sec[1] < 39.5 else np.nan)
        if len(S) < 5:
            return None
        S, T0, T1 = np.array(S), np.array(T0), np.array(T1)
        lf, rf = _curb_pair(S, T0, T1)
        if lf is None or rf is None:
            return None
        # 가운데 선의 기울기로 방향 보정 (두 연석 직선의 평균 기울기)
        k = (lf[0][0] + rf[0][0]) / 2
        if abs(k) < 0.02 or it == 2:                 # 마지막엔 돌리지 않음: 잰 틀과 방향이 같아야 함
            break
        ang = math.atan(k)
        if abs(ang) > math.radians(20):            # 연석이 중심선과 20° 넘게 어긋남 = 다른 길로 갈라지는 곳
            return None
        cu, sn = math.cos(ang), math.sin(ang)
        u = u * cu + nrm * sn
        u /= np.linalg.norm(u)
    # 연석 선: 각 쪽을 직선으로 맞춘다(RANSAC). 모서리 둥근 곳·골목 입구·버스 베이·합류처럼 직선에서 0.8m 넘게
    # 벗어난 점은 빼고, 맞는 점이 15m 넘게 이어져야 쓴다
    # 길이 끝까지 맞지 않으면(멀리서 좁아지거나 갈라짐) 입구 쪽 40m 만으로 다시 맞춘다
    for lim in (None, 40.0):
        if lim is not None:
            keep = S <= S[0] + lim
            if keep.sum() < 6:
                return None
            S, T0, T1 = S[keep], T0[keep], T1[keep]
            lf, rf = _curb_pair(S, T0, T1)
            if lf is None or rf is None:
                return None
        Ls, Rs = np.polyval(lf[0], S), np.polyval(rf[0], S)
        W = Rs - Ls
        both = lf[1] & rf[1]
        Wi = W[both] if both.sum() >= 3 else W
        # 교차로 입구 쪽이 넓어지는 나팔꼴(차로가 늘어남)까지는 받아들이되, 합류·분기처럼 크게 벌어지면 버린다
        if Wi.min() >= 4.0 and Wi.max() / Wi.min() <= 1.6 and abs(lf[0][0] - rf[0][0]) <= 0.2:
            break
    else:
        return None                                  # 두 연석이 나란하지 않음(합류·분기) → 연석 틀을 믿지 않음
    return dict(u=u, nrm=np.array([u[1], -u[0]]), p=p0, s=S, L=Ls, R=Rs)

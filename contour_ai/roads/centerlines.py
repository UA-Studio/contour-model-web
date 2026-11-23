"""수치지도 도로 중심선 그래프: 중심선·교차점·차도 폭·복선 나누기·횡단 위치 찾기."""
import os
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from .style import LANE_W
from .data_files import _f



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


def _crossing(c, u, road, walk, width=4.0, slide=tuple(np.arange(0, 26, 1.5)), reach=45.0, taken=(), max_len=None):
    """c 근처에서 차도를 가로질러 양 끝이 보도·교통섬에 닿는 횡단보도 띠를 찾는다.
    u = 길 방향(교차로에서 멀어지는 쪽). 교통섬을 지나면 섬 건너 차도까지 이어 한 줄로.
    닿는 자리가 없으면 u 쪽으로 조금씩 밀어 다시 본다. 반환 (띠 Polygon, 끝점 2개, 가로 방향) 또는 None"""
    if walk is None or walk.is_empty:
        return None
    nrm = np.array([u[1], -u[0]])
    for sh in slide:
        cc = np.asarray(c, float) + u * sh
        res = []
        for off in (-width / 2 + 0.3, 0.0, width / 2 - 0.3):   # 띠의 앞·가운데·뒤 세 줄이 모두 닿아야 함
            p = cc + u * off
            seg = _span(p, nrm, road, walk, reach)
            if seg is None:
                break
            res.append(seg)
        if len(res) < 3:
            continue
        a, b = max(r[0] for r in res), min(r[1] for r in res)   # 세 줄 중 안쪽 끝(띠가 보도에 파고들지 않게)
        if b - a < 3.0 or (max_len and b - a > max_len):   # 너무 길면 교차로 안쪽으로 샌 것 → 더 바깥에서
            continue
        band = Polygon([cc - u * width / 2 + nrm * a, cc + u * width / 2 + nrm * a,
                        cc + u * width / 2 + nrm * b, cc - u * width / 2 + nrm * b])
        if any(band.intersection(t).area > 0.1 * band.area for t in taken):
            continue
        return band, (cc + nrm * a, cc + nrm * b), nrm
    return None


def _span(p, nrm, road, walk, reach):
    """p 에서 nrm 양쪽으로 차도를 건너 보도·교통섬에 닿는 [a, b] (p 기준 거리). 교통섬은 건너뛴다."""
    line = LineString([p - nrm * reach, p + nrm * reach])
    inter = line.intersection(road)
    segs = []
    for g in getattr(inter, "geoms", [inter]):
        if g.geom_type == "LineString" and g.length > 0.2:
            c = np.asarray(g.coords)
            t = sorted(float(np.dot(q[:2] - p, nrm)) for q in c)
            segs.append((t[0], t[-1]))
    if not segs:
        return None
    segs.sort()
    k = next((i for i, (s0, s1) in enumerate(segs) if s0 - 0.3 <= 0 <= s1 + 0.3), None)
    if k is None:
        return None

    def lands(t):          # 이 자리 바로 바깥이 보도·교통섬인가
        q = Point(*(p + nrm * t))
        return walk.distance(q) < 0.8
    lo, hi = k, k
    # 아래쪽: 끝이 교통섬이고 섬 건너 바로 차도가 이어지면 계속
    while True:
        a = segs[lo][0]
        if not lands(a - 0.3):
            return None
        if lo > 0 and a - segs[lo - 1][1] < 25 and _all_walk(p, nrm, segs[lo - 1][1], a, walk) and _is_island(p, nrm, a - 0.5, walk):
            lo -= 1
            continue
        break
    while True:
        b = segs[hi][1]
        if not lands(b + 0.3):
            return None
        if hi + 1 < len(segs) and segs[hi + 1][0] - b < 25 and _all_walk(p, nrm, b, segs[hi + 1][0], walk) and _is_island(p, nrm, b + 0.5, walk):
            hi += 1
            continue
        break
    return segs[lo][0], segs[hi][1]


ISLANDS = {"geom": None}


def _is_island(p, nrm, t, walk):
    isl = ISLANDS["geom"]
    return isl is not None and not isl.is_empty and isl.distance(Point(*(p + nrm * t))) < 0.8


def _all_walk(p, nrm, t0, t1, walk):
    seg = LineString([p + nrm * t0, p + nrm * t1])
    return seg.length < 0.5 or seg.difference(walk.buffer(0.6)).length < 0.5

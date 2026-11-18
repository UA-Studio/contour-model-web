"""도로 디테일: 노면표시(차선·중앙선·정지선·횡단보도), 신호등, 전봇대, 주차 구획.

인도는 이미 수치지도 보도 면(N3A_A0033320)을 연석 높이만큼 올려 만든다.

출처 (앞쪽이 우선)
- 노면표시
  1. 항공사진(브이월드 영상·정사영상)이 있으면 차도 안의 흰색·노란색 페인트를 찾아 그대로 면으로 (레이어 '...::항공사진')
  2. 횡단보도 자료 파일 (전국횡단보도표준데이터: 위도·경도·횡단보도폭·연장)
  3. 추정: 수치지도 도로중심선(N3L_A0020000)의 차로수·분리대유무로 중앙선(노랑)·차선(흰 점선),
     차로 2개 이상 길끼리 만나는 교차로마다 횡단보도와 정지선
- 신호등
  1. 신호등 자료 파일 (전국신호등표준데이터: 위도·경도·신호등구분)
  2. 추정: 횡단보도 양 끝에 보행 신호등, 교차로 진입로마다 건너편 오른쪽 모서리에 차량 신호등(팔 달린 기둥)
- 전봇대
  1. 위치 자료 파일 (위도·경도 칸, 파일 이름이나 칸에 '전주'·'전봇대')
  2. 추정: 폭 12m 미만 생활도로에만 한쪽 30m 간격 (큰길은 서울 대부분 지중화) — 보안등 5m 안에서는 생략
     한전 전주 좌표는 공개 자료가 없다(전주번호만 공개).
- 주차장: 수치지도 주차장 면(N3A_C0430000) 안에 2.5×5.0m 구획 + 6m 통로 줄을 긴 축 방향으로 깐다(추정).

반환은 2D 도형과 점이고, 높이는 contour_model 이 붙인다.
"""
import glob
import math
import os

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

LANE_W = 3.2
PAINT = {"중앙선": (232, 190, 64), "차선": (240, 240, 234), "정지선": (240, 240, 234), "화살표": (240, 240, 234),
         "유도선": (240, 240, 234), "안전지대": (240, 240, 234),
         "횡단보도": (240, 240, 234), "주차구획": (240, 240, 234), "노면표시": (240, 240, 234),
         "노면표시(노랑)": (232, 190, 64), "주정차금지선": (232, 190, 64), "가장자리선": (240, 240, 234),
         "버스전용차선": (40, 96, 196), "노면문자": (240, 240, 234)}
FIX_RGB = {"신호등": (70, 72, 76), "전봇대": (172, 170, 162)}


# ---------------------------------------------------------------- 자료 파일
def _kind_of_table(path, cols):
    c = " ".join(map(str, cols)) + " " + os.path.basename(path)
    if "정류소" in c or "정류장" in c:
        return "busstop"
    if "횡단보도" in c:
        return "crosswalk"
    if "신호등" in c or "신호기" in c:
        return "signal"
    if "전주" in c or "전봇대" in c:
        return "pole"
    return None


def read_files(files, bbox, crs_wkt, log=print):
    """자료 파일들 → {"crosswalk": [(x, y, 폭, 연장)], "signal": [(x, y, 종류)], "pole": [(x, y)]}"""
    from pyproj import Transformer
    from .streetlights import _lonlat_cols, read_table
    out = {"crosswalk": [], "signal": [], "pole": [], "busstop": []}
    if not files or not crs_wkt:
        return out
    tf = Transformer.from_crs("EPSG:4326", crs_wkt, always_xy=True)
    for f in files:
        try:
            df = read_table(f)
        except Exception as e:
            log(f"   도로시설 자료를 읽지 못함 ({os.path.basename(f)}): {e}")
            continue
        kind = _kind_of_table(f, df.columns)
        lat, lon = _lonlat_cols(df.columns)
        if lat is None or lon is None:     # 서울시 정류소 자료처럼 X좌표·Y좌표 칸에 경도·위도가 든 경우
            xc = next((c for c in df.columns if str(c).strip().upper() in ("X좌표", "X", "GPS_X", "XCODE")), None)
            yc = next((c for c in df.columns if str(c).strip().upper() in ("Y좌표", "Y", "GPS_Y", "YCODE")), None)
            if xc is not None and yc is not None:
                try:
                    if 124 < float(df[xc].astype(float).median()) < 132:
                        lon, lat = xc, yc
                except (TypeError, ValueError):
                    pass
        if not kind or lat is None or lon is None:
            log(f"   {os.path.basename(f)}: 횡단보도·신호등·전주 자료인지, 위도·경도 칸이 있는지 알 수 없어 건너뜀")
            continue
        la = np.asarray(df[lat], float)
        lo = np.asarray(df[lon], float)
        ok = np.isfinite(la) & np.isfinite(lo) & (la > 33) & (la < 39) & (lo > 124) & (lo < 132)
        x, y = tf.transform(lo[ok], la[ok])
        inb = (x >= bbox[0]) & (x <= bbox[2]) & (y >= bbox[1]) & (y <= bbox[3])
        sub = df[ok][inb]
        x, y = x[inb], y[inb]

        def col(name, d):
            c = next((c for c in df.columns if name in str(c)), None)
            return [d] * len(x) if c is None else list(sub[c])
        if kind == "crosswalk":
            for xi, yi, w, L in zip(x, y, col("횡단보도폭", 4.0), col("횡단보도연장", 0.0)):
                out[kind].append((float(xi), float(yi), _f(w, 4.0), _f(L, 0.0)))
        elif kind == "signal":
            for xi, yi, k in zip(x, y, col("신호등구분", "차량")):
                out[kind].append((float(xi), float(yi), "보행자" if "보행" in str(k) else "차량"))
        elif kind == "busstop":
            nm = next((c for c in df.columns if "정류" in str(c) and "명" in str(c)), None)
            names = list(sub[nm]) if nm is not None else [""] * len(x)
            out[kind] += [(float(xi), float(yi), str(n)) for xi, yi, n in zip(x, y, names)]
        else:
            out[kind] += [(float(xi), float(yi)) for xi, yi in zip(x, y)]
        log(f"   {os.path.basename(f)}: 범위 안 {len(x)}개 ({ {'crosswalk': '횡단보도', 'signal': '신호등', 'pole': '전봇대', 'busstop': '버스 정류장'}[kind]})")
    # 신호등 표준데이터는 등 하나가 한 줄 → 1.5m 안은 기둥 하나로
    out["signal"] = _dedupe(out["signal"], 1.5)
    out["pole"] = _dedupe(out["pole"], 1.0)
    out["busstop"] = _dedupe(out["busstop"], 3.0)
    return out


def _f(v, d):
    try:
        v = float(v)
        return v if math.isfinite(v) and v > 0 else d
    except (TypeError, ValueError):
        return d


def _dedupe(pts, gap):
    if not pts:
        return pts
    from scipy.spatial import cKDTree
    P = np.array([(p[0], p[1]) for p in pts])
    tr = cKDTree(P)
    alive = np.ones(len(P), bool)
    out = []
    for i in range(len(P)):
        if not alive[i]:
            continue
        for j in tr.query_ball_point(P[i], gap):
            if j > i:
                alive[j] = False
        out.append(pts[i])
    return out


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
    from .aerial import load
    from .vegetation import canopy_mask
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


# ---------------------------------------------------------------- 추정: 차선·교차로
def estimate_marks(lines, road, cw_real=(), log=print, walk=None, lane_obs=None, islands=None, real_bands=(), core=None):
    """반환 marks [(Polygon, 종류, 출처)], crossings [(중심 x, y, 방향 벡터, 길이)] (보행 신호등용), approaches"""
    from shapely import prepare
    if road is None or road.is_empty:
        return [], [], []
    road_in = road.buffer(-0.25)
    prepare(road_in)
    # 차로수·도로폭 속성은 보도를 포함하거나 한 방향만 센 경우가 많다 → 차도 면에서 실제 차도 폭을 재서 차로수를 맞춘다
    lines = [dict(d) for d in lines]
    for d in lines:
        c = _carriage(d["line"], road)
        d["_c"] = c
        span = c[0]
        d["n0"] = d["n"]
        if span:
            n = d["n"]
            if not (2.75 <= span / n <= 3.8) and (n >= 2 or span >= 9.0):   # 1차로 골목은 그대로(중앙선 없음)
                n = max(int(round(span / 3.3)), 1 if d.get("oneway") else 2)
            d["n"], d["lw"] = n, min(max(span / n, 2.75), 3.6)
    _split_dual(lines, road)
    nodes = _nodes(lines)
    # 교차로: 3갈래 이상에서 차로 2개 이상 길이 셋 이상
    junction, cut = [], {}
    for p, ends in nodes:
        if len(ends) < 3:
            continue
        big = [e for e in ends if lines[e[0]]["n0"] >= 2]
        half = max(lines[e[0]]["w"] for e in ends) / 2
        R = half + 1.5
        for i, e in ends:
            other = max([lines[j]["w"] for j, _ in ends if j != i] or [0])
            cut[(i, e)] = other / 2 + 1.5
        wide = [e for e in ends if lines[e[0]]["n0"] >= 4]
        side = [e for e in ends if lines[e[0]]["n0"] < 4 and lines[e[0]]["w"] >= 8]
        # 큰길끼리 만나는 곳, 또는 4차로 이상 큰길에 폭 8m 넘는 길이 붙는 곳만 (골목 입구는 신호 교차로가 아님)
        if len(big) >= 3 or (wide and side):
            junction.append((p, ends, R))
    from .marking_assets import SPEC, arrow, island_guides
    lane_obs = lane_obs or {}
    LW, DASH = SPEC["차선_폭"], (SPEC["차선_칠"], SPEC["차선_틈"])
    marks = []
    # 차선: 항공사진에서 읽은 차선 자리가 있으면 그 오프셋에, 없으면 차로수로 추정 — 모양은 규격 에셋
    from shapely.ops import substring
    claimed, claimed_u = [], None
    order = sorted(range(len(lines)), key=lambda i: -((lines[i].get("_c") or (None,))[0] or lines[i]["w"]) * min(lines[i]["line"].length, 200))
    for i in order:
        d = lines[i]
        n, ln = d["n"], d["line"]
        c0, c1 = cut.get((i, 0), 0.0), cut.get((i, 1), 0.0)
        if ln.length - c0 - c1 < 3:
            continue
        ln = substring(ln, c0, ln.length - c1)
        # 차로수·도로폭 속성은 보도를 포함하거나 한 방향만 센 경우가 많다 → 차도 면에서 실제 차도 폭을 재서 맞춘다
        span, shift, gap = d.get("_c") or (None, 0.0, False)
        lw = d.get("lw") or (min(max(d["w"] / n, 2.75), 3.5) if d["w"] else LANE_W)
        if n < 2 and i not in lane_obs:
            continue
        median = d["median"] and gap      # 분리대 '유'라도 차도 면 가운데가 비어 있지 않으면(칠한 중앙선) 노란 겹선
        pieces = []
        src = "추정"
        if i in lane_obs:
            src = "항공사진"
            for off, kind, solid in lane_obs[i]:
                if kind == "중앙선":
                    pieces += [(p, "중앙선") for p in _offset_strips(ln, off, SPEC["중앙선_폭"])]
                else:
                    pieces += [(p, "차선") for p in _offset_strips(ln, off, LW, None if solid else DASH)]
        else:
            # 차로 경계 j = 1..n-1 은 중심선에서 (j - n/2)·차로폭. 양방향이면 가운데 경계(j = n//2)가 중앙선
            # (홀수 차로도 한쪽이 한 차로 많을 뿐 중앙선은 있다), 분리대가 있으면 그 자리는 비우고, 일방통행은 흰 점선만
            mid = n // 2 if not d.get("oneway") else None
            for j in range(1, n):
                off = (j - n / 2) * lw + shift
                if j == mid:
                    if median:
                        continue
                    if d["median"]:      # 물리 분리대 없이 '유'로 적힌 큰길: 노란 겹선(0.5m 사이)
                        pieces += [(p, "중앙선") for p in _offset_strips(ln, off + 0.32, 0.15) + _offset_strips(ln, off - 0.32, 0.15)]
                        continue
                    if n >= 4:
                        pieces += [(p, "중앙선") for p in _offset_strips(ln, off + 0.18, 0.15) + _offset_strips(ln, off - 0.18, 0.15)]
                    else:
                        pieces += [(p, "중앙선") for p in _offset_strips(ln, off, 0.15)]
                else:
                    pieces += [(p, "차선") for p in _offset_strips(ln, off, 0.15, (3.0, 5.0))]
        # 먼저 그은(더 넓고 긴) 길이 맡은 차도 띠 위에는 긋지 않는다: 엇갈린 중심선·합류부에서 차선이 겹치지 않게
        for g, k in pieces:
            if claimed:
                g = g.difference(claimed_u)
            g = g.intersection(road_in)
            if not g.is_empty and g.area > 0.05:
                marks.append((g, k, src))
        span_, shift_, _ = d.get("_c") or (None, 0.0, False)
        band_w = (span_ or d["w"] or LANE_W * n) / 2
        try:
            mine = ln.offset_curve(shift_) if abs(shift_) > 1e-6 else ln
            mine = mine.buffer(band_w + 0.3, cap_style=2)
        except Exception:
            mine = ln.buffer(band_w + 0.3, cap_style=2)
        claimed.append(mine)
        claimed_u = unary_union(claimed)
    # 교차로 진입로: 횡단보도 + 정지선
    from scipy.spatial import cKDTree
    cw_tree = cKDTree([(c[0], c[1]) for c in cw_real]) if cw_real else None
    crossings, approaches = [], []
    bands_done = [b[0] for b in real_bands]
    n_est = 0
    arm_regions, arm_marks = [], []

    def do_arm(fr, d, band):
        """연석 틀 fr 에서 횡단보도 band 바깥쪽으로 정지선·화살표·차선. 성공하면 True."""
        from .marking_assets import SPEC, arrow
        pu, uu, nn = fr["p"], fr["u"], fr["nrm"]
        far = max(float((np.asarray(q) - pu) @ uu) for q in np.asarray(band.exterior.coords)) + 1.0
        S, L, R = fr["s"], fr["L"], fr["R"]
        if S[-1] - far < 8:
            return False
        tail = S >= far - 2
        W = float(np.median((R - L)[tail]))
        n_ = d["n"]
        ow = bool(d.get("oneway"))
        own = (d.get("_c") or (d["w"],))[0] or d["w"]
        if ow and own and W > 1.5 * own:
            # 상·하행 중심선이 따로 있는 길(분리 차로)인데 연석 틀은 길 전체 → 양방향으로 다시 나눈다
            ow, n_ = False, 0
        if not (2.75 <= W / max(n_, 1) <= 3.8):
            n_ = max(int(round(W / 3.25)), 1 if ow else 2)
        if not ow and n_ % 2:
            # 양방향은 짝수 차로로 고르게 나눠 중앙선을 연석 한가운데 둔다 — 짧은 길 양끝 진입로의 중앙선이 어긋나지 않게
            n_ = n_ + 1 if W / (n_ + 1) >= 2.75 else max(n_ - 1, 2)
        gap = bool(d["median"] and (d.get("_c") or (0, 0, False))[2])
        sw = SPEC["정지선_폭"]
        ln_marks, info = arm_lanes(fr, n_, far + sw, S[-1], oneway=ow, median_gap=gap)
        if not info:
            return False
        t_in, l0, t_mid = info
        # 연석 틀 범위(입구 ~ 끝) — 이 안의 중심선 기반 차선은 지운다
        k0 = S >= far - 6
        poly = Polygon([tuple(pu + uu * a + nn * b) for a, b in zip(S[k0], L[k0])] +
                       [tuple(pu + uu * a + nn * b) for a, b in zip(S[k0][::-1], R[k0][::-1])]).buffer(0)
        arm_regions.append(poly)
        arm_marks.extend((g, k, "추정") for g, k in ln_marks)
        stop = Polygon([pu + uu * far + nn * l0, pu + uu * (far + sw) + nn * l0,
                        pu + uu * (far + sw) + nn * (t_mid - 0.3), pu + uu * far + nn * (t_mid - 0.3)])
        stop = stop.intersection(road_in)
        if not stop.is_empty:
            arm_marks.append((stop, "정지선", "추정"))
        s_ar = far + sw + 3.0 + SPEC["화살표_길이"]
        for t in t_in:
            a_ = arrow(pu + uu * s_ar + nn * t, -uu)
            if road_in.contains(a_):
                arm_marks.append((a_, "화살표", "추정"))
        return True
    for p, ends, R in junction:
        j_bands = []
        has_real = bool(cw_real) or bool(real_bands)    # 공공 API·자료·사진 횡단보도가 있으면 그것을 먼저 믿는다
        for i, e in ends:
            d = lines[i]
            if d["n"] < 2:
                continue
            p0, u = _approach_dir(d["line"], e)
            R = cut.get((i, e), R)       # 이 진입로에서 교차로 가장자리까지 = 다른 길 반폭 + 모서리
            half = d["w"] / 2 + 0.5
            approaches.append((p0, u, d["w"], R))
            lw = d.get("lw") or (min(max(d["w"] / max(d["n"], 1), 2.75), 3.5) if d["w"] else LANE_W)
            if has_real:
                # 자료 횡단보도가 이 진입로에 있으면 그 바깥에 정지선·화살표
                nr = np.array([u[1], -u[0]])
                best = None
                # 교차로 면 안쪽(섬과 섬을 잇는) 횡단보도는 진입로 정지선의 기준으로 쓰지 않는다
                core_in = core.buffer(-2.0) if core is not None and not core.is_empty else None
                rb_ok = [b for b in real_bands if core_in is None or not core_in.contains(b[0].centroid)]
                cands = [(c[0], c[1], c[2]) for c in cw_real] + \
                        [(*b[0].centroid.coords[0], 4.0) for b in rb_ok]
                for cx, cy, cw in cands:
                    q = np.array([cx, cy]) - p0
                    al, la = float(q @ u), float(q @ nr)
                    if 0 < al < R + 25 and abs(la) < d["w"] / 2 + 3 and (best is None or al < best[0]):
                        best = (al, cw)
                if best is not None:
                    fr = arm_frame(p0, u, d["w"], road, max(R - 4.0, 2.0), stop=core,
                                   s_end=_arm_end(d, e, R, junction))
                    bb = min((b[0] for b in rb_ok), key=lambda g: g.centroid.distance(Point(*(p0 + u * best[0]))),
                             default=None)
                    if fr is not None and bb is not None and do_arm(fr, d, bb):
                        continue
                    far = best[0] + best[1] / 2 + 1.0
                    marks += _stop_and_arrows(p0, u, far, d, lw, road_in)
                    continue
                # 자료·사진에 이 진입로 횡단보도가 없으면 아래에서 추정
            # 진입로 방향은 중심선이 아니라 양쪽 연석에서(중심선이 비스듬하거나 여러 줄이어도 연석에 맞춘다)
            fr = arm_frame(p0, u, d["w"], road, max(R - 4.0, 2.0), stop=core,
                                   s_end=_arm_end(d, e, R, junction))
            c_try = p0 + u * (R + 2.0)
            if fr is not None:
                u = fr["u"]
                k_ = int(np.argmin(np.abs(fr["s"] - (R + 2.0))))
                c_try = fr["p"] + u * fr["s"][k_] + fr["nrm"] * (fr["L"][k_] + fr["R"][k_]) / 2
            # 횡단보도: 양 끝이 보도·교통섬에 닿는 자리를 찾아 한 줄로 (교통섬은 건너뜀)
            hit = _crossing(c_try, u, road, walk, 4.0, taken=bands_done,
                            max_len=1.4 * d["w"] + 8.0) if walk is not None else None
            if hit is None:
                continue
            band, (e0, e1), nrm = hit
            cb = band.centroid
            if any(t.centroid.distance(cb) < 9.0 for t in bands_done):   # 바로 옆에 이미 횡단보도(같은 건널목)
                continue
            bands_done.append(band)
            j_bands.append(band)
            marks += [(g, k, "추정") for g, k in _zebra(band, u, nrm, road_in)]
            c = (e0 + e1) / 2
            crossings.append((c[0], c[1], nrm, float(np.linalg.norm(e1 - e0)), e0, e1))
            n_est += 1
            if fr is not None and do_arm(fr, d, band):
                continue
            # 정지선: 횡단보도 바깥 1m, 들어오는 차로 쪽(u 기준 오른쪽 법선 = -nrm 쪽 절반)
            far = float(np.dot(np.asarray(band.centroid.coords[0]) - p0, u)) + 2.0 + 1.0
            marks += _stop_and_arrows(p0, u, far, d, lw, road_in)
    # 연석 틀로 다시 그린 진입로: 그 범위의 중심선 기반 차선·화살표·정지선은 지우고 연석 기준 표시로 바꾼다
    if arm_regions:
        az = unary_union(arm_regions).buffer(0.5)
        marks = [(g, k, s_) for g, k, s_ in marks if not (k in ("화살표", "정지선") and g.intersects(az))]
        marks = [(g.difference(az) if (k in ("차선", "중앙선") and s_ == "추정") else g, k, s_) for g, k, s_ in marks]
        # 잘리고 남은 1.5m 안 되는 차선 토막은 지운다(연석 틀 끝 이음매)
        marks = [(g, k, s_) for g, k, s_ in marks
                 if not g.is_empty and g.area > (0.22 if k in ("차선", "중앙선") else 0.05)]
        marks += [(g.intersection(road_in), k, s_) for g, k, s_ in arm_marks]
    # 교차로 면 안과 횡단보도 위(앞뒤 1m)에는 차선·중앙선·화살표를 긋지 않는다
    stop_zone = [b.buffer(1.0, join_style=2) for b in bands_done]
    if core is not None and not core.is_empty:
        # 교차로 면을 그 교차로 횡단보도까지 넓힌다: 횡단보도 안쪽(교차로 쪽)은 모두 교차로
        for cp in getattr(core, "geoms", [core]):
            near = [b for b in bands_done if b.distance(cp) < 15.0]
            g = unary_union([cp] + near).convex_hull.intersection(road) if near else cp
            stop_zone.append(g)
    if stop_zone:
        hz_ = unary_union(stop_zone)
        marks = [(g, k, s_) for g, k, s_ in marks if not (k == "화살표" and g.intersects(hz_))]
        marks = [(g.difference(hz_) if k in ("차선", "중앙선") else g, k, s_) for g, k, s_ in marks]
        marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty and g.area > 0.05]
    # 화살표를 가로지르는 추정 차선 조각(옆 골목 중심선에서 비스듬히 들어온 것)은 지운다
    arrows_ = [g for g, k, _ in marks if k == "화살표"]
    if arrows_:
        az_ = unary_union(arrows_).buffer(0.15)
        marks = [(g, k, s_) for g, k, s_ in marks
                 if not (k == "차선" and s_ == "추정" and g.intersects(az_))]
    # 교통섬: 섬 둘레 1m 안으로 차선이 파고들지 않게 하고, 섬 둘레에 유도선·노즈 빗금(안전지대)
    if islands is not None and not islands.is_empty:
        isl_clear = islands.buffer(1.0)
        marks = [(g.difference(isl_clear) if k in ("차선", "중앙선", "화살표") else g, k, s_) for g, k, s_ in marks]
        marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty and g.area > 0.15]
        guides = island_guides(islands, road_in)
        marks += [(g, k, "추정") for g, k in guides]
    if n_est:
        log(f"   교차로 진입로 {n_est}곳에 횡단보도·정지선 추정")
    return marks, crossings, approaches


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


def arm_lanes(fr, n, s_from, s_to, oneway=False, median_gap=False, solid_len=15.0):
    """연석 틀 안을 n 차로로 고르게 나눈 차선·중앙선 (연석을 따라 굽고 넓어짐).
    t 는 오른쪽 법선 기준: 바깥으로 나가는 차는 오른쪽(t 큰 쪽), 교차로로 들어오는 차는 왼쪽.
    반환 [(Polygon, 종류)], 들어오는 차로 가운데 t 목록(정지선 자리 기준)"""
    from .marking_assets import SPEC
    S, L, R = fr["s"], fr["L"], fr["R"]
    keep = (S >= s_from) & (S <= s_to)
    if keep.sum() < 2 or n < 1:
        return [], []
    S, L, R = S[keep], L[keep], R[keep]
    p, u, nrm = fr["p"], fr["u"], fr["nrm"]
    n_in = n if oneway else n - n // 2           # 들어오는 쪽(왼쪽)에 홀수 차로의 남는 한 차로
    out = []

    def poly_at(frac):
        pts = [p + u * s + nrm * (l + (r - l) * frac) for s, l, r in zip(S, L, R)]
        return LineString(pts)
    for j in range(1, n):
        frac = j / n
        ln = poly_at(frac)
        if not oneway and j == n_in:
            if median_gap:
                continue
            if n >= 4:
                for off in (-0.18, 0.18):
                    out += [(g, "중앙선") for g in _offset_strips(ln, off, 0.15)]
            else:
                out += [(g, "중앙선") for g in _offset_strips(ln, 0.0, 0.15)]
            continue
        # 정지선 쪽 solid_len 은 실선(진로변경 제한), 그 바깥은 3m/5m 점선
        from shapely.ops import substring
        if ln.length > solid_len + 3:
            out += [(g, "차선") for g in _offset_strips(substring(ln, 0, solid_len), 0.0, SPEC["차선_폭"])]
            out += [(g, "차선") for g in _offset_strips(substring(ln, solid_len, ln.length), 0.0, SPEC["차선_폭"],
                                                         (SPEC["차선_칠"], SPEC["차선_틈"]))]
        else:
            out += [(g, "차선") for g in _offset_strips(ln, 0.0, SPEC["차선_폭"])]
    l0, r0 = L[0], R[0]
    lw = (r0 - l0) / n
    t_in = [l0 + (k + 0.5) * lw for k in range(n_in)]
    t_mid = l0 + n_in * lw if not oneway else r0
    return out, (t_in, l0, t_mid)


def _stop_and_arrows(p0, u, far, d, lw, road_in):
    """정지선(규격 폭) + 들어오는 차로마다 진행방향 화살표(정지선 뒤 3m)."""
    from .marking_assets import SPEC, arrow
    out = []
    half = d["w"] / 2 + 0.5
    sw = SPEC["정지선_폭"]
    stop = _strip(p0, u, far, far + sw, -half, 0.0 if not d["median"] else -0.5).intersection(road_in)
    if not stop.is_empty:
        out.append((stop, "정지선", "추정"))
    nr = np.array([u[1], -u[0]])
    n_in = max(1, d["n"] // 2) if not d.get("oneway") else max(1, d["n"])
    tail = far + sw + 3.0 + SPEC["화살표_길이"]
    for k in range(n_in):
        off = -(k + 0.5) * lw
        a = arrow(p0 + u * tail + nr * off, -u)
        if road_in.contains(a):
            out.append((a, "화살표", "추정"))
    return out


def _zebra(band, u, nrm, road_in):
    """띠 → 줄무늬(줄 0.45m · 틈 0.45m, 줄은 길 방향). 차도 밖(교통섬 위)은 칠하지 않음."""
    c = np.asarray(band.exterior.coords)[:4]
    t = [float(np.dot(q, nrm)) for q in c]
    s_ = [float(np.dot(q, u)) for q in c]
    out = []
    from .marking_assets import SPEC
    bw, gap = SPEC["횡단보도_줄폭"], SPEC["횡단보도_간격"]
    for k in np.arange(min(t) + 0.2, max(t) - 0.2, bw + gap):
        bar = Polygon([u * min(s_) + nrm * k, u * max(s_) + nrm * k,
                       u * max(s_) + nrm * (k + bw), u * min(s_) + nrm * (k + bw)]).intersection(band)
        if road_in is not None:
            bar = bar.intersection(road_in)
        if not bar.is_empty and bar.area > 0.2:
            out.append((bar, "횡단보도"))
    return out


def real_crosswalks(cws, lines, road, walk=None, bands_only=False):
    """자료 횡단보도 점 → 줄무늬. 방향은 가장 가까운 중심선.
    bands_only 면 [(띠, u, nrm, e0, e1)] 만 돌려준다(보도 끝~끝으로 맞출 수 있는 것만)."""
    from shapely.strtree import STRtree
    if not cws or not lines:
        return [] if bands_only else ([], [])
    road_in = road.buffer(-0.25) if road is not None else None
    L = [d["line"] for d in lines]
    tr = STRtree(L)
    marks, crossings = [], []
    for x, y, w, length in cws:
        i = tr.nearest(Point(x, y))
        ln = L[i]
        s = ln.project(Point(x, y))
        a, b = ln.interpolate(max(s - 2, 0)), ln.interpolate(min(s + 2, ln.length))
        u = np.array([b.x - a.x, b.y - a.y])
        u /= np.linalg.norm(u) or 1.0
        hit = _crossing((x, y), u, road, walk, w, slide=(0, 1, -1, 2, -2, 3, -3)) if walk is not None else None
        if bands_only:
            if hit is not None:
                marks.append((hit[0], u, hit[2], *hit[1]))
            continue
        if hit is not None:                    # 자료 점 근처에서 양쪽 보도·교통섬에 닿는 자리
            band, (e0, e1), nrm = hit
            marks += _zebra(band, u, nrm, road_in)
            c = (e0 + e1) / 2
            crossings.append((c[0], c[1], nrm, float(np.linalg.norm(e1 - e0)), e0, e1))
            continue
        span = (length / 2 + 0.5) if length else lines[i]["w"] / 2 + 0.5
        p = np.array([x, y]) - u * w / 2
        band = _strip(p, u, 0, w, -span, span)
        if road_in is not None:
            band = band.intersection(road_in)
        for k in np.arange(-span, span, 0.9):
            bar = _strip(p, u, 0, w, k, k + 0.45).intersection(band)
            if not bar.is_empty and bar.area > 0.2:
                marks.append((bar, "횡단보도"))
        crossings.append((x, y, np.array([u[1], -u[0]]), span * 2))
    return marks if bands_only else (marks, crossings)


# ---------------------------------------------------------------- 항공사진 노면표시
def aerial_marks(aerial_path, road, min_area=0.25, log=print):
    """차도 안 밝고 무채색(흰) 또는 노란 화소 → 면. 반환 [(Polygon, 종류)]"""
    from shapely import contains_xy
    from .aerial import load
    rgb, x0, y1, cell, _ = load(aerial_path)
    img = rgb.astype(float)
    R, G, B = img[..., 0], img[..., 1], img[..., 2]
    V = img.max(axis=2)
    S = (V - img.min(axis=2)) / np.maximum(V, 1)
    h, w = V.shape
    xs = x0 + (np.arange(w) + 0.5) * cell
    ys = y1 - (np.arange(h) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    inside = contains_xy(road.buffer(-0.3), X, Y)
    if inside.sum() < 100:
        return []
    # 노면 밝기는 동네마다 달라서 차도 화소의 분포로 문턱을 정한다
    base = np.percentile(V[inside], 60)
    white = inside & (V > max(base + 55, 170)) & (S < 0.18)
    yellow = inside & (R > base + 25) & (G > base + 5) & (B < G - 35) & (S > 0.3)
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape
    tr = from_origin(x0, y1, cell, cell)
    out = []
    for mask, kind in ((white, "노면표시"), (yellow, "노면표시(노랑)")):
        if mask.sum() < 4:
            continue
        for geom, val in shapes(mask.astype(np.uint8), mask=mask, connectivity=8, transform=tr):
            pg = shape(geom).buffer(0).simplify(cell * 0.5)
            if pg.is_empty or pg.area < min_area:
                continue
            # 페인트는 가늘다(차선 0.15, 횡단보도 줄 0.45~0.5, 정지선 0.45m). 흰 차 지붕(폭 1.8m) 같은 덩어리는 버림
            rr = np.asarray(pg.minimum_rotated_rectangle.exterior.coords)
            short = min(np.linalg.norm(rr[1] - rr[0]), np.linalg.norm(rr[2] - rr[1]))
            if short <= 1.0 or pg.area / max(pg.minimum_rotated_rectangle.area, 1e-6) < 0.45:
                out.append((pg, kind))
    log(f"   항공사진에서 노면표시 {len(out)}조각 (흰색 {sum(k == '노면표시' for _, k in out)} · 노랑 {sum(k != '노면표시' for _, k in out)})")
    return out


# ---------------------------------------------------------------- 신호등·전봇대
def estimate_signals(crossings, approaches, curb, buildings=None, road=None):
    """반환 [(x, y, 팔 방향, 종류, "추정", 팔 길이)]"""
    from shapely import contains_xy, prepare
    road_in = None
    if road is not None and not road.is_empty:
        road_in = road.buffer(-0.1)
        prepare(road_in)

    def ok(px, py):                          # 차도 안이나 건물 안에는 세우지 않는다
        return not ((road_in is not None and contains_xy(road_in, px, py))
                    or (blk is not None and contains_xy(blk, px, py)))
    blk = None
    if buildings is not None and not buildings.is_empty:
        blk = buildings.buffer(0.3)
        prepare(blk)
    out = []
    for cr in crossings:                   # 횡단보도 양 끝 보도·교통섬 위
        x, y, nrm, span = cr[:4]
        for sd in (1, -1):
            if len(cr) > 4:                 # 끝점을 알면 그 바로 바깥
                e = cr[4] if sd == -1 else cr[5]
                px, py = e[0] + nrm[0] * sd * 0.6, e[1] + nrm[1] * sd * 0.6
            else:
                d = curb.hit((x, y), (nrm[0] * sd, nrm[1] * sd), span / 2 + 6) or span / 2
                px, py = x + nrm[0] * sd * (d + 0.6), y + nrm[1] * sd * (d + 0.6)
            if ok(px, py):
                out.append((px, py, math.atan2(-nrm[1] * sd, -nrm[0] * sd), "보행자", "추정", 0.0))
    for p0, u, w, R in approaches:          # 차량 신호: 진입로 건너편 오른쪽 모서리, 팔은 차로 위로
        t = -u                               # 진입하는 차의 진행 방향
        right = np.array([t[1], -t[0]])
        c = p0 + t * (R + 1.0)
        d = curb.hit(tuple(c), tuple(right), w / 2 + 8) or w / 2
        px, py = c + right * (d + 0.8)
        if not ok(px, py):
            continue
        out.append((float(px), float(py), math.atan2(-right[1], -right[0]), "차량", "추정", min(max(w / 2 - 1, 2.5), 8.0)))
    return _dedupe(out, 2.5)


def estimate_poles(lines, curb, bbox, buildings=None, lights=(), spacing=30.0, road=None):
    """폭 12m 미만 생활도로 한쪽에 30m 간격. 반환 [(x, y)]"""
    from shapely import contains_xy, prepare
    road_in = None
    if road is not None and not road.is_empty:
        road_in = road.buffer(-0.1)
        prepare(road_in)
    from scipy.spatial import cKDTree
    blk = None
    if buildings is not None and not buildings.is_empty:
        blk = buildings.buffer(0.25)
        prepare(blk)
    lt = cKDTree([(l[0], l[1]) for l in lights]) if lights else None
    out = []
    for d in lines:
        if d["w"] >= 12 or d["n"] >= 3:
            continue
        ln = d["line"]
        n = int(ln.length // spacing)
        if n < 1:
            continue
        start = (ln.length - (n - 1) * spacing) / 2
        for j in range(n):
            s = start + j * spacing
            a, b = ln.interpolate(max(s - 1, 0)), ln.interpolate(min(s + 1, ln.length))
            tx, ty = b.x - a.x, b.y - a.y
            L = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / L, tx / L
            c = ln.interpolate(s)
            for side in (1, -1):
                dd = curb.hit((c.x, c.y), (nx * side, ny * side), d["w"] / 2 + 5)
                if dd is None:
                    continue
                x, y = c.x + nx * side * (dd + 0.35), c.y + ny * side * (dd + 0.35)
                if road_in is not None and contains_xy(road_in, x, y):
                    continue
                if not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                    break
                if blk is not None and contains_xy(blk, x, y):
                    continue
                if lt is not None and lt.query_ball_point((x, y), 5.0):
                    break            # 골목 보안등은 보통 전봇대에 달려 있다 → 그 자리를 전봇대로 본다
                out.append((x, y))
                break
    return _dedupe(out, 8.0)


# ---------------------------------------------------------------- 주차 구획
def parking_lots(folder, bbox, buildings=None):
    import shapefile
    from shapely.geometry import shape
    fn = os.path.join(folder, "N3A_C0430000")
    if not os.path.exists(fn + ".shp"):
        return []
    clip = box(*bbox)
    r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
    out = []
    for sr in r.iterShapeRecords():
        try:
            g = shape(sr.shape.__geo_interface__).buffer(0).intersection(clip)
        except Exception:
            continue
        if buildings is not None and not buildings.is_empty:
            g = g.difference(buildings)      # 주차 건물(입체 주차장)은 건물이 대신
        for p in getattr(g, "geoms", [g]):
            if p.geom_type == "Polygon" and p.area > 30:
                out.append(p)
    r.close()
    return out


def parking_marks(lot, stall=(2.5, 5.0), aisle=6.0, line_w=0.12):
    """긴 축 방향으로 [구획 줄][통로][구획 줄] 반복. 반환 [(Polygon, "주차구획")]"""
    rr = lot.minimum_rotated_rectangle
    c = np.asarray(rr.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
        c0 = c[1]
    else:
        c0 = c[0]
    L1, L2 = np.linalg.norm(e1), np.linalg.norm(e2)
    u, v = e1 / L1, e2 / L2
    # 기준 모서리를 c0 로 잡았을 때 v 가 사각형 안쪽을 향하도록
    if not rr.buffer(0.01).contains(Point(*(c0 + u * L1 / 2 + v * 0.5))):
        v = -v
    inner = lot.buffer(-0.4)
    sw, sl = stall
    out = []
    t = 0.5                          # 가장자리 여유(담·연석)
    rows = []
    while t + sl <= L2 + 1e-6:      # 구획 줄 위치 (v 방향 시작점, 차가 들어오는 쪽 부호)
        rows.append((t, +1))
        if t + sl + aisle + sl <= L2 + 1e-6:
            rows.append((t + sl + aisle, -1))
        t += 2 * sl + aisle
    for t0, _ in rows:
        n = int((L1 - 1.0) // sw)
        off = (L1 - n * sw) / 2
        for k in range(n):
            a = off + k * sw
            q = [c0 + u * a + v * t0, c0 + u * (a + sw) + v * t0,
                 c0 + u * (a + sw) + v * (t0 + sl), c0 + u * a + v * (t0 + sl)]
            cell_ = Polygon(q)
            if not inner.contains(cell_):
                continue
            for p_a, p_b in ((q[0], q[3]), (q[1], q[2])):   # 구획 옆선 두 개
                out.append((LineString([p_a, p_b]).buffer(line_w / 2, cap_style=2), "주차구획"))
    return out


# ---------------------------------------------------------------- 모양
def signal_parts(kind, arm=0.0):
    """원점 바닥, 팔은 +x. 반환 trimesh."""
    import trimesh
    parts = []
    if kind == "보행자":
        h = 3.2
        p = trimesh.creation.cylinder(radius=0.07, height=h, sections=8); p.apply_translation([0, 0, h / 2])
        hd = trimesh.creation.box([0.32, 0.3, 0.75]); hd.apply_translation([0.18, 0, h - 0.45])
        parts += [p, hd]
    else:
        h = 6.2
        p = trimesh.creation.cylinder(radius=0.14, height=h, sections=8); p.apply_translation([0, 0, h / 2])
        parts.append(p)
        if arm > 0:
            a = trimesh.creation.box([arm, 0.14, 0.16]); a.apply_translation([arm / 2, 0, h - 0.4])
            parts.append(a)
            for xx in (arm * 0.55, arm - 0.6):          # 가로형 3·4색 등
                hd = trimesh.creation.box([0.35, 1.25, 0.4])
                hd.apply_translation([xx, 0, h - 0.75])
                parts.append(hd)
        hd = trimesh.creation.box([0.3, 0.3, 0.9]); hd.apply_translation([0.2, 0, 3.2])  # 기둥에 붙은 보조등
        parts.append(hd)
    return trimesh.util.concatenate(parts)


def pole_parts(h=12.0):
    import trimesh
    from .tree_models import _frustum
    p = _frustum((0, 0, 0), (0, 0, h), 0.2, 0.14, sections=8)
    arm = trimesh.creation.box([0.12, 1.8, 0.12]); arm.apply_translation([0, 0, h - 1.0])
    arm2 = trimesh.creation.box([0.12, 1.4, 0.12]); arm2.apply_translation([0, 0, h - 2.4])
    return trimesh.util.concatenate([p, arm, arm2])


def placed(mesh, x, y, z, ang=0.0):
    import trimesh
    m = mesh.copy()
    m.apply_transform(trimesh.transformations.rotation_matrix(ang, [0, 0, 1]))
    m.apply_translation([x, y, z])
    return m


def flat_mesh(polys, zfun, lift=0.03):
    """2D 면들 → 높이를 따라 얹힌 얇은 판 (윗면만). zfun(xy 배열) → 높이."""
    import trimesh
    Vs, Fs, n = [], [], 0
    for g in polys:
        for p in getattr(g, "geoms", [g]):
            if p.geom_type != "Polygon" or p.area < 0.02:
                continue
            try:
                v2, f = trimesh.creation.triangulate_polygon(p, engine="earcut")
            except Exception:
                continue
            if len(f) == 0:
                continue
            z = np.asarray(zfun(v2), float) + lift
            Vs.append(np.column_stack([v2, z])); Fs.append(f + n); n += len(v2)
    if not Vs:
        return None
    return trimesh.Trimesh(np.vstack(Vs), np.vstack(Fs), process=False)


# ---------------------------------------------------------------- 묶음

def aerial_zebras(aerial_path, road, walk=None, log=print):
    """항공사진 줄무늬를 직접 읽은 횡단보도 → [(띠, u, nrm, e0, e1)].

    방향은 도로 중심선이 아니라 줄무늬 자체에서 잰다(오거리의 섬과 섬을 비스듬히 잇는 횡단보도도 그 각도 그대로).
    차·그림자로 일부만 보이면 그 방향 그대로 양 끝 보도·교통섬까지 늘인다. 못 늘이면 보인 만큼만."""
    from shapely import contains_xy
    from scipy.ndimage import median_filter
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape
    from .aerial import load
    from .marking_assets import find_zebras
    rgb, x0, y1, cell, _ = load(aerial_path)
    img = rgb.astype(float)
    V = img.max(axis=2)
    S = (V - img.min(axis=2)) / np.maximum(V, 1)
    h, w = V.shape
    X, Y = np.meshgrid(x0 + (np.arange(w) + 0.5) * cell, y1 - (np.arange(h) + 0.5) * cell)
    near = contains_xy(road.buffer(1.0), X, Y)
    if near.sum() < 100:
        return []
    # 주변(3m)보다 뚜렷이 밝은 무채색·옅은 노랑 = 페인트 (그늘 속 줄무늬도 잡히게 주변 밝기 기준)
    loc = median_filter(V, size=int(round(3.0 / cell)) | 1)
    paint = near & (V - loc > np.maximum(16, 0.18 * loc)) & (S < 0.45)
    tr = from_origin(x0, y1, cell, cell)
    blobs = []
    for con in (4, 8):                       # 비스듬한 줄은 4-연결에서 끊기고, 붙은 줄은 8-연결에서 붙는다 → 둘 다
        for geom, _ in shapes(paint.astype(np.uint8), mask=paint, connectivity=con, transform=tr):
            pg = shape(geom).buffer(0)
            if pg.area >= 0.15:
                blobs.append((pg, "w"))
    out = []
    for z in find_zebras(blobs, log=log):
        u, c = z["u"], np.asarray(z["c"])
        hit = _crossing(c, u, road, walk, min(max(z["width"], 3.0), 6.0), slide=(0.0, 0.5, -0.5)) \
            if walk is not None else None
        if hit is not None:
            band, (e0, e1), nrm = hit
            if np.linalg.norm(e1 - e0) <= z["length"] + 30:      # 보인 줄무늬보다 터무니없이 길면 믿지 않음
                out.append((band, u, nrm, e0, e1))
                continue
        band = z["band"].intersection(road.buffer(0.3))
        if band.is_empty or band.geom_type != "Polygon":
            continue
        nrm = z["nrm"]
        t = [float((np.asarray(q) - c) @ nrm) for q in band.exterior.coords]
        out.append((band, u, nrm, c + nrm * min(t), c + nrm * max(t)))
    return out


CW_RANK = {"항공사진": 0, "자료": 0, "서울도보네트워크": 1, "생활안전지도": 1, "추정": 2}


def tidy_crosswalks(marks, islands, log=print):
    """복잡한 교차로의 횡단보도 정리.
    1) 교통섬(수치지도·추정 모두) 위로는 줄무늬를 긋지 않는다 — 횡단보도는 섬 가장자리에서 끊기고 섬 건너편에서 이어진다.
    2) 두 횡단보도가 겹치면 믿을 만한 쪽(자료·API > 항공사진 > 추정)만 남긴다.
    3) 잘리고 남은 짧은 토막(줄무늬 4개 미만, 약 3.5m 미만)은 지운다 — 섬 너머로 삐져나온 꼬리."""
    cw = [(g, s_) for g, k, s_ in marks if k == "횡단보도"]
    rest = [m for m in marks if m[1] != "횡단보도"]
    if not cw:
        return marks
    cut = islands.buffer(0.3) if islands is not None and not islands.is_empty else None
    pieces = []
    for g, s_ in cw:
        if cut is not None:
            g = g.difference(cut)
        for q in getattr(g, "geoms", [g]):
            if q.geom_type == "Polygon" and q.area > 0.2:
                pieces.append((q, s_))
    # 같은 출처 줄무늬끼리 이웃(간격 0.45m, 0.6m 안)으로 묶어 한 횡단보도 조각으로
    from shapely.strtree import STRtree
    geoms = [q for q, _ in pieces]
    tree = STRtree(geoms)
    parent = list(range(len(pieces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, (q, s_) in enumerate(pieces):
        for j in tree.query(q.buffer(0.7)):
            j = int(j)
            if j != i and pieces[j][1] == s_ and geoms[j].distance(q) < 0.6:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(pieces)):
        groups.setdefault(find(i), []).append(i)
    grp = sorted(groups.values(), key=lambda ix: (CW_RANK.get(pieces[ix[0]][1], 2), -len(ix)))
    taken, out, n_short, n_ovl = None, [], 0, 0
    for ix in grp:
        if len(ix) < 4:
            n_short += 1
            continue
        area = unary_union([geoms[i] for i in ix])
        hull = area.minimum_rotated_rectangle
        if taken is not None and hull.intersects(taken):
            # 더 믿을 만한 횡단보도와 겹침: 겹치는 줄무늬만 빼고, 남은 게 짧으면 통째로 뺀다
            keep = [i for i in ix if not geoms[i].intersects(taken)]
            if len(keep) < 4:
                n_ovl += 1
                continue
            if len(keep) < len(ix):
                n_ovl += 1
            ix = keep
            hull = unary_union([geoms[i] for i in ix]).minimum_rotated_rectangle
        out += [(geoms[i], "횡단보도", pieces[i][1]) for i in ix]
        taken = hull.buffer(0.3) if taken is None else unary_union([taken, hull.buffer(0.3)])
    if n_short or n_ovl:
        log(f"   횡단보도 정리: 섬 위 줄무늬는 끊고, 짧은 토막 {n_short}개 빼고, 겹친 {n_ovl}곳 정리")
    return rest + out



def road_details(folder, bbox, crs_wkt, road, buildings=None, aerial_path=None, files=(), lights=(),
                 log=print, keep_out=None, walk=None, islands=None, api=None, hd_dir=None, dst_epsg=5186):
    """정밀도로지도 폴더(hd_dir)가 있으면 그 범위의 노면표시는 정밀도로지도가 으뜸이고, 항공사진·추정은
    정밀도로지도가 없는 곳(골목 등)만 채운다.
    반환 dict(marks=[(Polygon, 종류, 출처)], signals=[(x, y, 방향, 종류, 출처, 팔)],
                 poles=[(x, y, 출처)], lots=[Polygon])"""
    from .streetlights import _Curb
    lines = _centerlines(folder, bbox)
    data = read_files(files, bbox, crs_wkt, log)
    curb = _Curb(road)
    marks, crossings = [], []
    am = []
    if aerial_path and road is not None and not road.is_empty:
        try:
            am = aerial_marks(aerial_path, road, log=log)
        except Exception as e:
            log(f"   (항공사진 노면표시를 찾지 못함: {type(e).__name__}: {e})")
    ISLANDS["geom"] = islands
    core = junction_cores(lines, road)
    inferred = Polygon()
    # 공공 API 횡단보도(서울 도보 네트워크 → 생활안전지도 지도) — 위치는 가장 믿고, 모양은 그 자리 길에 맞춘다
    api_bands, api_src = [], None
    api = api or {}
    if api.get("seoul_key"):
        try:
            from .crosswalk_api import seoul_crosswalks
            links = seoul_crosswalks(api["seoul_key"], bbox, crs_wkt, api.get("seoul_service"),
                                     api.get("seoul_code"), api.get("cache_dir"), log)
            chains, isl_pts = chain_links(links, walk, road)
            if isl_pts:
                isl_new = islands_at_points(isl_pts, chains, road, walk)
                if islands is not None and not islands.is_empty:
                    isl_new = isl_new.difference(islands.buffer(1.0))
                if not isl_new.is_empty:
                    inferred = unary_union([inferred, isl_new])
                    log(f"   도보 네트워크: 횡단보도가 차도 한가운데에서 이어지는 {len(isl_pts)}곳에 교통섬 추정")
            if not inferred.is_empty:          # 섬을 건너는 횡단보도가 섬에 닿도록 먼저 넣는다
                ISLANDS["geom"] = unary_union([g for g in (islands, inferred) if g is not None and not g.is_empty])
            walk_api = unary_union([walk, inferred]) if (walk is not None and not inferred.is_empty) else walk
            api_bands = snap_crossings(chains, lines, road, walk_api, core, log)
            api_src = "서울도보네트워크"
        except Exception as e:
            log(f"   ({e})")
    if not api_bands and api.get("safemap_key"):
        try:
            from .crosswalk_api import bands_from_polys, safemap_crosswalks
            polys = safemap_crosswalks(api["safemap_key"], bbox, crs_wkt, api.get("safemap_layer"),
                                       api.get("safemap_styles") or "", api.get("cache_dir"), log)
            chains = [LineString([b[3], b[4]]) for b in bands_from_polys(polys)]
            api_bands = snap_crossings(chains, lines, road, walk, core, log)
            api_src = "생활안전지도"
        except Exception as e:
            log(f"   ({e})")
    # 항공사진: 차도 안 화단 = 교통섬
    if aerial_path and road is not None and not road.is_empty:
        try:
            ia = islands_from_aerial(aerial_path, road, walk)
            known = unary_union([g for g in (islands, inferred) if g is not None and not g.is_empty] or [Polygon()])
            ia = unary_union([p for p in getattr(ia, "geoms", [ia]) if p.geom_type == "Polygon"
                              and p.intersection(known.buffer(1.0)).area < 0.3 * p.area] or [Polygon()])
            if not ia.is_empty:
                inferred = unary_union([inferred, ia])
                log(f"   항공사진: 차도 안 화단 {len(getattr(ia, 'geoms', [ia]))}곳을 교통섬으로")
        except Exception as e:
            log(f"   (항공사진에서 교통섬을 찾지 못함: {type(e).__name__}: {e})")
    if not inferred.is_empty:
        islands = unary_union([g for g in (islands, inferred) if g is not None and not g.is_empty])
        ISLANDS["geom"] = islands
        walk = unary_union([walk, inferred]) if walk is not None else inferred
    # 자료 파일 → API → 항공사진 판독 순으로 띠를 모으고, 같은 횡단보도는 하나만
    bands = []
    # 항공사진 줄무늬에서 직접 읽은 횡단보도를 가장 먼저 믿는다 (실제로 칠해진 자리·각도)
    zb = []
    if aerial_path and road is not None and not road.is_empty:
        try:
            zb = aerial_zebras(aerial_path, road, walk, log)
        except Exception as e:
            log(f"   (항공사진 횡단보도 줄무늬를 읽지 못함: {type(e).__name__}: {e})")
    bands += [(b, "항공사진") for b in zb]
    zb_ids = {id(b) for b in zb}
    for x, y, w, length in data["crosswalk"]:
        hit = real_crosswalks([(x, y, w, length)], lines, road, walk, bands_only=True)
        bands += [(b, "자료") for b in hit]
    bands += [(b, api_src) for b in api_bands]
    aer = None
    if am:
        from .marking_assets import read_aerial
        aer = read_aerial(am, lines, log)
        for x, y, w, length in aer["crosswalks"]:
            hit = real_crosswalks([(x, y, w, length)], lines, road, walk, bands_only=True)
            bands += [(b, "항공사진") for b in hit]
    inner = core.buffer(-4.0) if not core.is_empty else None
    # 교차로 한가운데 횡단보도는 버린다 — 단, 사진에 실제로 칠해진 것(섬과 섬을 잇는 횡단보도)은 둔다
    bands = [(b, s_) for b, s_ in bands if inner is None or id(b) in zb_ids or not inner.contains(b[0].centroid)]
    kept = dedupe_bands([b for b, _ in bands])
    srcs = {id(b): s_ for b, s_ in bands}
    road_in_ = road.buffer(-0.25)
    marks, rc = [], []
    for b in kept:
        band, u, nrm, e0, e1 = b
        isl_cut = inferred.buffer(0.3) if not inferred.is_empty else None
        for g, k in _zebra(band, u, nrm, road_in_):
            if isl_cut is not None:
                g = g.difference(isl_cut)
            if not g.is_empty and g.area > 0.2:
                marks.append((g, k, srcs.get(id(b), "자료")))
        c = (np.asarray(e0) + np.asarray(e1)) / 2
        rc.append((c[0], c[1], nrm, float(np.linalg.norm(np.asarray(e1) - np.asarray(e0))), np.asarray(e0), np.asarray(e1)))
    if len(kept) < len(bands):
        log(f"   같은 자리 횡단보도 {len(bands) - len(kept)}개 겹침 정리")
    em, ec, appr = estimate_marks(lines, road, (), log, walk, lane_obs=aer["lanes"] if aer else None,
                                  islands=islands, real_bands=kept, core=core)
    marks += em
    marks = tidy_crosswalks(marks, islands, log)
    # 교통섬 유도선·빗금은 횡단보도·정지선과 겹치지 않게
    cwz = [g for g, k, _ in marks if k in ("횡단보도", "정지선")]
    if cwz:
        cwz = unary_union(cwz).buffer(0.6)
        marks = [((g.difference(cwz) if k in ("유도선", "안전지대") else g), k, s_) for g, k, s_ in marks]
        marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty and g.area > 0.1]
    crossings = rc + ec
    # 신호등
    sig = [(x, y, 0.0, k, "자료", 4.0 if k == "차량" else 0.0) for x, y, k in data["signal"]]
    if not sig:
        sig = estimate_signals(crossings, appr, curb, buildings, road)
    else:
        from shapely.strtree import STRtree
        cl = [d["line"] for d in lines]
        tr = STRtree(cl) if cl else None
        out = []
        for x, y, a, k, s, arm in sig:
            if tr is not None:
                from shapely.ops import nearest_points
                q = nearest_points(cl[tr.nearest(Point(x, y))], Point(x, y))[0]
                a = math.atan2(q.y - y, q.x - x)
            out.append((x, y, a, k, s, arm))
        sig = out
    sig = [s for s in sig if bbox[0] <= s[0] <= bbox[2] and bbox[1] <= s[1] <= bbox[3]]
    # 전봇대
    poles = [(x, y, "자료") for x, y in data["pole"]]
    if not poles:
        poles = [(x, y, "추정") for x, y in estimate_poles(lines, curb, bbox, buildings, lights, road=road)]
    # 정밀도로지도: 덮는 곳은 그것으로 바꾸고, 사진과 비교해 바뀐 곳은 알림
    hd_flags, hd_report = [], None
    if hd_dir != "" and road is not None and not road.is_empty:      # None = 흔한 자리에서 자동으로 찾기, "" = 쓰지 않음
        try:
            marks, sig, hd_flags, hd_report, hd_isl = _apply_hdmap(hd_dir, bbox, road, walk, islands, zb, marks, sig,
                                                                   dst_epsg, log)
            if hd_isl is not None and not hd_isl.is_empty:
                inferred = unary_union([inferred, hd_isl])
                marks = [(g.difference(hd_isl), k, s_) for g, k, s_ in marks]
                marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty and g.area > 0.05]
        except Exception as e:
            log(f"   (정밀도로지도를 쓰지 못함: {type(e).__name__}: {e})")
    # 주차장
    lots = parking_lots(folder, bbox, buildings)
    for lot in lots:
        marks += [(g, k, "추정") for g, k in parking_marks(lot)]
    if keep_out is not None and not keep_out.is_empty:      # 중앙정류장 승강장 밑 페인트는 지운다
        marks = [(g.difference(keep_out), k, s_) for g, k, s_ in marks]
        marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty]
    cnt = {}
    for _, k, s in marks:
        cnt[k] = cnt.get(k, 0) + 1
    log("   노면표시: " + (", ".join(f"{k} {v}" for k, v in cnt.items()) or "없음")
        + f" · 신호등 {len(sig)}개 · 전봇대 {len(poles)}개 · 주차장 {len(lots)}곳")
    return dict(marks=marks, signals=sig, poles=poles, lots=lots, busstops=data["busstop"],
                islands_inferred=inferred, core=core, hd_flags=hd_flags, hd_report=hd_report)


def _apply_hdmap(hd_dir, bbox, road, walk, islands, zebras, marks, sig, dst_epsg, log):
    from . import hdmap_align, hdmap_reader
    feats = hdmap_reader.load(hd_dir, bbox, dst_epsg, log)
    paint_kinds = ("차선", "중앙선", "차도경계", "주정차금지선", "버스전용차선", "정지선", "횡단보도", "화살표", "안전지대", "유도선", "노면문자", "정차금지지대", "자전거횡단도", "기타노면")
    feats = [f for f in feats if f["kind"] in paint_kinds]
    if not any(f["kind"] in paint_kinds for f in feats):
        return marks, sig, [], None, None
    res = hdmap_align.align(feats, road, walk, islands=islands, aerial_zebras=list(zebras or []),
                            src_epsg=hdmap_reader.SRC_EPSG, dst_epsg=dst_epsg, log=log)
    if res.get("failed"):
        return marks, sig, [], res["report"], None
    fx = res["features"]
    hd_isl = hd_islands(fx, road, islands, log, walk=walk)
    paint = hdmap_reader.to_paint([f for f in fx if f["kind"] in paint_kinds], road, log)
    # 정밀도로지도가 덮는 곳 = 그 자료의 표시(가장자리선 빼고) 둘레 8m 안의 차도. 거기의 사진·추정 표시는 뺀다.
    cover = unary_union([f["geom"].buffer(8.0) for f in fx if f["kind"] in paint_kinds if f["kind"] != "차도경계"])
    cover = cover.buffer(15.0).buffer(-15.0).intersection(road)     # 교차로 한가운데(표시 없는 곳)도 덮은 것으로
    keep = [(g, k, s_) for g, k, s_ in marks if g.intersection(cover).area < 0.5 * max(g.area, 1e-9)]
    log(f"   정밀도로지도로 바꿈: 표시 {len(paint)}개 (사진·추정 표시 {len(marks) - len(keep)}개 대신)")
    # 신호등: 정밀도로지도 점은 차도 위 신호등 '머리' 자리라 기둥 자리로 바로 쓰기 어려움 → 지금은 기존 추정 유지
    return keep + paint, sig, res["flags"], res["report"], hd_isl


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


# ---------------------------------------------------------------- 확인 그림
SRC_COLOR = {"정밀도로지도": "#0a8f5a", "서울도보네트워크": "#1f6fd1", "생활안전지도": "#1f6fd1", "자료": "#1f6fd1", "항공사진": "#d1501f",
             "추정": "#222222"}


def marks_check(path, road, walk, marks, islands=None, inferred=None, bbox=None, title="", core=None):
    """노면표시 확인 그림: 출처별 색(파랑 = 공공 API·자료, 주황 = 항공사진, 검정 = 추정),
    중앙선은 노랑, 수치지도 교통섬은 회색, 추정 교통섬은 빨간 테두리."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.plotting import plot_polygon

    def polys(g):
        if g is None or g.is_empty:
            return []
        return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"]
    fig, ax = plt.subplots(figsize=(12, 12), dpi=130)
    clip = box(*bbox) if bbox else None
    for g, fc in ((road, "#8a8a8a"), (walk, "#e4e2dc"), (islands, "#b8b4a8")):
        if g is not None and clip is not None:
            g = g.intersection(clip)
        for p in polys(g):
            plot_polygon(p, ax=ax, add_points=False, facecolor=fc, edgecolor="none")
    for p in polys(core):
        plot_polygon(p, ax=ax, add_points=False, facecolor="none", edgecolor="#2a9d5c", linewidth=1.0, linestyle="--")
    for p in polys(inferred):
        plot_polygon(p, ax=ax, add_points=False, facecolor="#c9c3b3", edgecolor="#e01010", linewidth=1.2)
    for g, k, s in marks:
        fc = "#f2c230" if k in ("중앙선", "주정차금지선") else ("#2860c4" if k == "버스전용차선" else SRC_COLOR.get(s, "#222222"))
        if k in ("유도선", "안전지대"):
            fc = "#ffffff"
        for p in polys(g if clip is None else g.intersection(clip)):
            plot_polygon(p, ax=ax, add_points=False, facecolor=fc, edgecolor="none")
    if bbox:
        ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color="#0a8f5a", label="HD map"), Patch(color="#1f6fd1", label="API / data file"), Patch(color="#d1501f", label="aerial photo"),
                       Patch(color="#222222", label="estimated"), Patch(color="#f2c230", label="center line"),
                       Patch(facecolor="#c9c3b3", edgecolor="#e01010", label="inferred island"),
                       Patch(facecolor="none", edgecolor="#2a9d5c", linestyle="--", label="junction core")],
              loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)

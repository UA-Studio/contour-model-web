"""노면표시: 중앙선·차선·정지선·화살표 추정, 항공사진에서 찾은 페인트, 칠 색.

이 파일만 고치면 차선·정지선·화살표만 바뀝니다 (횡단보도는 road_crosswalks.py).
"""
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from .road_crosswalks import _crossing, _zebra
from .road_network import (_approach_dir, _arm_end, arm_frame, _carriage, LANE_W, _nodes,
                           _offset_strips, _split_dual, _strip)


PAINT = {"중앙선": (232, 190, 64), "차선": (240, 240, 234), "정지선": (240, 240, 234), "화살표": (240, 240, 234),
         "유도선": (240, 240, 234), "안전지대": (240, 240, 234),
         "횡단보도": (240, 240, 234), "주차구획": (240, 240, 234), "노면표시": (240, 240, 234),
         "노면표시(노랑)": (232, 190, 64), "주정차금지선": (232, 190, 64), "가장자리선": (240, 240, 234),
         "버스전용차선": (40, 96, 196), "노면문자": (240, 240, 234)}


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

"""횡단보도: 건널목 띠 모양, 자료 파일·항공사진 횡단보도, 길 방향에 맞추기, 겹친 것 정리.

이 파일만 고치면 횡단보도만 바뀝니다.
"""
import math

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from .road_network import _road_dir_at, _strip


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

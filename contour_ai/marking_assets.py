"""규격 노면표시 에셋과 항공사진 판독.

항공사진의 흰·노란 화소를 그대로 면으로 옮기면 조각나고(차·그림자·마모) 모양이 흐트러진다.
그래서 사진은 '어디에 무엇이 있는가'만 읽고, 모양은 아래 규격 치수 에셋으로 다시 그린다.

치수는 경찰청 「교통노면표시 설치·관리 매뉴얼」의 도시부 일반값을 따른다(지자체마다 조금 다를 수 있음).
"""
import math

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

# ---------------------------------------------------------------- 규격
SPEC = {
    "횡단보도_줄폭": 0.45,       # 45~50cm
    "횡단보도_간격": 0.45,       # 45~80cm (줄 사이)
    "횡단보도_폭": 4.0,          # 건너는 띠의 폭(길 방향), 최소 4m
    "정지선_폭": 0.45,           # 30~60cm
    "차선_폭": 0.15,             # 10~15cm
    "차선_칠": 3.0, "차선_틈": 5.0,   # 도시부 점선 3m 칠 5m 틈
    "중앙선_폭": 0.15, "중앙선_겹간격": 0.15,
    "화살표_길이": 5.0, "화살표_몸통폭": 0.3, "화살표_머리길이": 1.5, "화살표_머리폭": 0.9,
    "유도선_폭": 0.15,           # 교통섬 둘레 도류화 유도선
    "유도선_떨어짐": 0.5,
    "안전지대_줄폭": 0.45, "안전지대_간격": 1.5,
}


def arrow(p, u, kind="직진"):
    """진행방향 화살표: p = 화살표 꼬리, u = 진행 방향. 반환 Polygon."""
    u = np.asarray(u, float) / (np.linalg.norm(u) or 1.0)
    v = np.array([-u[1], u[0]])
    L, sw, hl, hw = SPEC["화살표_길이"], SPEC["화살표_몸통폭"], SPEC["화살표_머리길이"], SPEC["화살표_머리폭"]
    p = np.asarray(p, float)
    s = L - hl
    pts = [p - v * sw / 2, p + u * s - v * sw / 2, p + u * s - v * hw / 2, p + u * L,
           p + u * s + v * hw / 2, p + u * s + v * sw / 2, p + v * sw / 2]
    return Polygon(pts)


def island_guides(islands, road_in, min_area=20.0):
    """교통섬 둘레 도류화 유도선(흰 실선) + 섬 앞 뾰족한 머리(노즈)의 안전지대 빗금.

    반환 [(Polygon, 종류)]. 섬 안·보도 위에는 칠하지 않는다."""
    out = []
    if islands is None or islands.is_empty or road_in is None:
        return out
    off, w = SPEC["유도선_떨어짐"], SPEC["유도선_폭"]
    for isl in getattr(islands, "geoms", [islands]):
        if isl.area < min_area:
            continue
        ring = isl.buffer(off + w, join_style=2).difference(isl.buffer(off, join_style=2))
        g = ring.intersection(road_in)
        if not g.is_empty and g.area > 0.1:
            out.append((g, "유도선"))
        # 노즈: 섬의 가장 뾰족한 꼭짓점 앞 6m 삼각 구역에 45° 빗금
        c = np.asarray(isl.simplify(0.8).exterior.coords)[:-1]     # 둥근 모서리를 꺾인 점으로
        if len(c) < 3:
            continue
        best, ang = None, 7.0
        for k in range(len(c)):
            a, b, d = c[k - 1], c[k], c[(k + 1) % len(c)]
            e1, e2 = a - b, d - b
            n1, n2 = np.linalg.norm(e1), np.linalg.norm(e2)
            if n1 < 1 or n2 < 1:
                continue
            t = math.acos(max(-1, min(1, float(np.dot(e1, e2) / (n1 * n2)))))
            if t < ang:
                ang, best = t, (b, -(e1 / n1 + e2 / n2))
        if best is None or ang > math.radians(70):
            continue
        tip, dirv = best
        dirv = dirv / (np.linalg.norm(dirv) or 1.0)
        side = np.array([-dirv[1], dirv[0]])
        L = 6.0
        half = math.tan(ang / 2) * L + 0.3
        tri = Polygon([tip, tip + dirv * L - side * 0.2, tip + dirv * L + side * 0.2,
                       tip + side * half, tip - side * half]).convex_hull
        zone = tri.difference(isl.buffer(off)).intersection(road_in)
        if zone.is_empty or zone.area < 1:
            continue
        out.append((zone.boundary.buffer(w / 2).intersection(road_in), "유도선"))
        diag = (dirv + side) / math.sqrt(2)
        nd = np.array([-diag[1], diag[0]])
        sw, gap = SPEC["안전지대_줄폭"], SPEC["안전지대_간격"]
        for t in np.arange(-L * 1.5, L * 1.5, sw + gap):
            q = tip + nd * t
            bar = Polygon([q - diag * 20, q + diag * 20, q + diag * 20 + nd * sw, q - diag * 20 + nd * sw])
            g = bar.intersection(zone)
            if not g.is_empty and g.area > 0.1:
                out.append((g, "안전지대"))
    return out


# ---------------------------------------------------------------- 항공사진 판독
def _frame(pg):
    r = pg.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    L, W = float(np.linalg.norm(e1)), float(np.linalg.norm(e2))
    return np.asarray(r.centroid.coords[0]), (e1 / L if L else np.array([1.0, 0.0])), L, W


def read_aerial(blobs, lines, log=print):
    """항공사진 페인트 조각 → 판독 결과.

    blobs: [(Polygon, '노면표시'|'노면표시(노랑)')], lines: 중심선 dict 목록.
    반환 dict(crosswalks=[(x, y, 폭, 길이)], lanes={중심선 번호: [(오프셋, '차선'|'중앙선', 실선?)]},
              stops=[(x, y)])"""
    from shapely.strtree import STRtree
    res = dict(crosswalks=[], lanes={}, stops=[])
    if not blobs or not lines:
        return res
    L_ = [d["line"] for d in lines]
    tree = STRtree(L_)
    info = []
    for pg, kind in blobs:
        c, a, L, W = _frame(pg)
        i = int(tree.nearest(Point(c)))
        ln = L_[i]
        s = ln.project(Point(c))
        p0, p1 = ln.interpolate(max(s - 1.5, 0)), ln.interpolate(min(s + 1.5, ln.length))
        u = np.array([p1.x - p0.x, p1.y - p0.y])
        u /= np.linalg.norm(u) or 1.0
        base = ln.interpolate(s)
        off = float(np.dot(c - np.array([base.x, base.y]), np.array([-u[1], u[0]])))
        cos = abs(float(np.dot(a, u)))
        info.append(dict(c=c, a=a, L=L, W=W, i=i, u=u, off=off, cos=cos, yellow=kind != "노면표시"))
    # 1) 횡단보도: 길 방향과 나란한 짧은 줄이 1.6m 안 간격으로 3개 이상 늘어선 무리
    bars = [k for k, b in enumerate(info) if b["cos"] > 0.8 and 1.2 <= b["L"] <= 7 and b["W"] >= 0.2 and not b["yellow"]]
    used = set()
    if bars:
        geo = [Point(info[k]["c"]).buffer(0.9) for k in bars]
        groups = unary_union(geo)
        for g in getattr(groups, "geoms", [groups]):
            mem = [k for k, gg in zip(bars, geo) if g.intersects(gg.centroid)]
            if len(mem) < 3:
                continue
            P = np.array([info[k]["c"] for k in mem])
            u = info[mem[0]]["u"]
            nrm = np.array([-u[1], u[0]])
            t = P @ nrm
            if t.max() - t.min() < 2.0:
                continue
            ctr = P.mean(axis=0)
            res["crosswalks"].append((float(ctr[0]), float(ctr[1]),
                                      float(np.clip(np.median([info[k]["L"] for k in mem]), 3.0, 6.0)),
                                      float(t.max() - t.min() + 1.0)))
            used.update(mem)
    # 2) 정지선: 길에 거의 수직인 굵은 줄
    for k, b in enumerate(info):
        if k not in used and b["cos"] < 0.35 and b["L"] >= 2.0 and 0.2 <= b["W"] <= 0.9:
            res["stops"].append((float(b["c"][0]), float(b["c"][1])))
            used.add(k)
    # 3) 차선·중앙선: 길과 나란한 가는 줄을 중심선 오프셋별로 모은다(0.5m 칸)
    acc = {}
    for k, b in enumerate(info):
        if k in used or b["cos"] < 0.9 or b["W"] > 0.6 or b["L"] < 1.0:
            continue
        key = (b["i"], round(b["off"] * 2) / 2)
        a = acc.setdefault(key, dict(len=0.0, yel=0.0, longest=0.0, offs=[]))
        a["len"] += b["L"]; a["yel"] += b["L"] * b["yellow"]; a["longest"] = max(a["longest"], b["L"]); a["offs"].append(b["off"])
    for (i, _), a in acc.items():
        if a["len"] < 6.0:                     # 짧은 조각 한두 개는 잡음
            continue
        kind = "중앙선" if a["yel"] > a["len"] / 2 else "차선"
        res["lanes"].setdefault(i, []).append((float(np.median(a["offs"])), kind, a["longest"] > 9.0))
    # 같은 선을 두 칸으로 나눠 센 것 합치기(0.6m 안)
    for i, lst in res["lanes"].items():
        lst.sort()
        merged = []
        for o in lst:
            if merged and abs(o[0] - merged[-1][0]) < 0.6 and o[1] == merged[-1][1]:
                continue
            merged.append(o)
        res["lanes"][i] = merged
    log(f"   항공사진 판독: 횡단보도 {len(res['crosswalks'])}곳, 정지선 {len(res['stops'])}, "
        f"차선이 보이는 길 {len(res['lanes'])}개 → 규격 에셋으로 다시 그림")
    return res


def find_zebras(blobs, log=print, min_bars=4):
    """항공사진 흰 페인트 조각에서 횡단보도를 길 중심선 없이 찾는다.

    오거리처럼 횡단보도가 섬과 섬을 비스듬히 잇는 곳에서는 '길 방향과 나란한 줄'이라는 가정이 틀리므로,
    줄무늬끼리의 관계만 본다: 같은 방향(18° 안)·길이 3배 안의 가는 막대가 막대에 수직 방향으로 0.6~2.2m 간격으로
    4개 이상 늘어서면 횡단보도. 방향·위치·폭·길이는 줄무늬 자체에서 잰다.
    반환 [dict(band=Polygon, u=막대 방향(건너는 방향), nrm=횡단보도가 늘어선 방향, n=막대 수, c=(x,y), width, length)]"""
    bars = []
    for pg, kind in blobs:
        c, a, L, W = _frame(pg)                    # 흰색·노란색(어린이보호구역 횡단보도) 모두
        if 1.0 <= L <= 7.5 and 0.15 <= W <= 1.1 and L >= 2.2 * W:
            bars.append((c, a, L, W))
    # 같은 막대를 두 번 받은 것(연결 방식을 바꿔 두 번 뽑은 조각)은 하나만
    uniq = []
    for b in sorted(bars, key=lambda b: -b[2]):
        if all(np.linalg.norm(b[0] - q[0]) > 0.3 for q in uniq):
            uniq.append(b)
    bars = uniq
    if len(bars) < min_bars:
        return []
    C = np.array([b[0] for b in bars])
    from scipy.spatial import cKDTree
    tr = cKDTree(C)
    par = list(range(len(bars)))

    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    for i, j in tr.query_pairs(2.4):
        (ci, ai, Li, _), (cj, aj, Lj, _) = bars[i], bars[j]
        if abs(float(ai @ aj)) < math.cos(math.radians(18)):   # 그림자·차로 토막 난 막대는 방향이 흔들린다
            continue
        if max(Li, Lj) > 3.0 * min(Li, Lj):
            continue
        d = cj - ci
        along = abs(float(d @ ai))                 # 막대 방향 어긋남(엇갈림)
        across = abs(float(d @ np.array([-ai[1], ai[0]])))
        if 0.5 <= across <= 2.2 and along <= 0.5 * max(Li, Lj):
            par[f(i)] = f(j)
    groups = {}
    for i in range(len(bars)):
        groups.setdefault(f(i), []).append(i)
    # 차·그림자로 끊겨 두 무리로 나뉜 같은 횡단보도는 합친다(같은 방향, 막대 줄 위에서 3m 안)
    gl = [m for m in groups.values() if len(m) >= 3]
    merged = True
    while merged:
        merged = False
        for x in range(len(gl)):
            for y in range(x + 1, len(gl)):
                a1 = np.mean([bars[k][1] * np.sign(bars[k][1] @ bars[gl[x][0]][1]) for k in gl[x]], axis=0)
                a2 = np.mean([bars[k][1] * np.sign(bars[k][1] @ bars[gl[x][0]][1]) for k in gl[y]], axis=0)
                a1 /= np.linalg.norm(a1) or 1.0; a2 /= np.linalg.norm(a2) or 1.0
                if abs(float(a1 @ a2)) < math.cos(math.radians(12)):
                    continue
                d = C[gl[y]][:, None, :] - C[gl[x]][None, :, :]
                along = np.abs(d @ a1); across = np.abs(d @ np.array([-a1[1], a1[0]]))
                Lx = max(bars[k][2] for k in gl[x] + gl[y])
                if ((along < Lx + 1.0) & (across < 1.5)).any() or ((along < 2.0) & (across < 3.0)).any():
                    gl[x] = gl[x] + gl[y]; del gl[y]; merged = True
                    break
            if merged:
                break
    out = []
    for mem in gl:
        if len(mem) < min_bars:
            continue
        A = np.array([bars[k][1] for k in mem])
        A *= np.sign(A @ A[0])[:, None]
        u = A.mean(axis=0); u /= np.linalg.norm(u)
        nrm = np.array([-u[1], u[0]])
        P = C[mem]
        t, s = P @ nrm, P @ u
        Lm = float(np.median([bars[k][2] for k in mem]))
        ctr = u * float(np.median(s)) + nrm * float((t.max() + t.min()) / 2)
        half_n = (t.max() - t.min()) / 2 + 0.3
        band = Polygon([tuple(ctr + u * a_ + nrm * b_) for a_, b_ in
                        ((-Lm / 2, -half_n), (Lm / 2, -half_n), (Lm / 2, half_n), (-Lm / 2, half_n))])
        if 2 * half_n < 2.5:
            continue
        out.append(dict(band=band, u=u, nrm=nrm, n=len(mem), c=(float(ctr[0]), float(ctr[1])),
                        width=Lm, length=float(2 * half_n)))
    log(f"   항공사진 줄무늬로 찾은 횡단보도 {len(out)}곳 (막대 {sum(o['n'] for o in out)}개)")
    return out

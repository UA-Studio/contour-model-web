"""건물 용도·매스에 맞는 창 짜기 (v2.4).

예전(v2.3)에는 건물마다 한 가지 창을 같은 간격으로 모든 면·모든 층에 찍었다(일률적인 격자).
여기서는 면마다 '어떤 면인지'(앞·뒤·옆, 이웃 건물에 막힌 면)를 보고 건물 유형별로 창을 짠다.

  다세대·다가구 빌라   한 층 안에 거실 큰 창·방 창·주방 작은 창·욕실 작은 불투명 창·확장 발코니가 섞임,
                       층마다 조금씩 다름, 계단실 세로창(층 사이 높이), 이웃에 막힌 옆면은 작은 창만
  4~6층 아파트·사무소  기둥 사이 가로 띠창(창살 촘촘히)
  판상형 고층 아파트   앞(남쪽) 긴 면은 세대마다 거실·침실 발코니 전면 유리, 뒷면은 작은 창 + 계단·승강기
                       코어 줄, 옆(박공) 면은 앞쪽 모서리 가까이 작은 창 한 줄
  학교                 교실 쪽(남쪽) 긴 띠창 + 교실 칸마다 기둥, 복도 쪽은 높은 창대의 작은 창, 끝면 계단창
  1층 가로변 상가      이웃에 막히지 않은 면만 큰 유리, 일부 칸은 셔터 내림, 유리 위 간판 판(글자 없음)

창 하나 = 벽 판의 구멍(s0, s1, z0, z1) + 종류(kind). 종류에 따라 유리(틴트·불투명), 창틀·창살, 창턱,
셔터, 간판을 사각 면(삼각형으로 쪼개지 않음)으로 만든다 → trim().
같은 건물은 늘 같은 결과(건물마다 정해진 난수).
"""
import numpy as np

# 창 종류
WIN, BATH, STAIR, BAND, BALC, SHOP, SHUTTER, CURTAIN = ("win", "bath", "stair", "band", "balc", "shop",
                                                         "shutter", "curtain")

GLASS_TINTS = ["유리", "유리 짙은 청회", "유리 녹색 틴트", "유리 밝은 반사"]
FRAMES = ["창틀 백색", "창틀 은색 알루미늄", "창틀 진회색", "창틀 흑색"]
SIGNS = ["간판 남색", "간판 적갈", "간판 짙은 녹색", "간판 회백", "간판 진회색", "간판 황토"]

# 재료(색, 투명도) — facade.MATERIALS 에 더해진다
MATERIALS = {
    "유리 짙은 청회": ((78, 98, 114), 0.45),
    "유리 녹색 틴트": ((106, 146, 136), 0.5),
    "유리 밝은 반사": ((168, 184, 196), 0.35),
    "유리 불투명": ((214, 220, 222), 0.15),
    "유리 상가": ((150, 172, 182), 0.7),
    "창틀 백색": ((236, 236, 232), 0.0),
    "창틀 은색 알루미늄": ((178, 182, 186), 0.0),
    "창틀 진회색": ((72, 74, 78), 0.0),
    "창틀 흑색": ((40, 40, 42), 0.0),
    "창턱": ((198, 196, 190), 0.0),
    "셔터": ((150, 152, 150), 0.0),
    "간판 남색": ((44, 62, 102), 0.0),
    "간판 적갈": ((128, 58, 46), 0.0),
    "간판 짙은 녹색": ((46, 88, 64), 0.0),
    "간판 회백": ((222, 220, 212), 0.0),
    "간판 진회색": ((66, 68, 72), 0.0),
    "간판 황토": ((184, 140, 62), 0.0),
}

# 부위 이름(Rhino 레이어): 재료 → 부위
def part_of(mat):
    if mat.startswith("유리"):
        return "유리"
    if mat.startswith("창틀"):
        return "창틀"
    if mat == "창턱":
        return "창턱"
    if mat == "셔터":
        return "셔터"
    if mat.startswith("간판"):
        return "간판"
    return "창"


# ---------------------------------------------------------------- 건물마다 정하는 것
def pick_trim(style, year, rng):
    """건물 하나의 창틀 색·기본 유리 틴트·창살 간격."""
    y = year or 2000
    if style in ("villa", "punch"):
        fw = [("창틀 백색", 5), ("창틀 은색 알루미늄", 4), ("창틀 진회색", 1)] if y < 2012 else \
            [("창틀 진회색", 3), ("창틀 은색 알루미늄", 2), ("창틀 백색", 2), ("창틀 흑색", 1)]
        gw = [("유리", 4), ("유리 짙은 청회", 2), ("유리 녹색 틴트", 2), ("유리 밝은 반사", 1)]
    elif style in ("balcony", "apt"):
        fw = [("창틀 백색", 4), ("창틀 은색 알루미늄", 3)] + ([("창틀 진회색", 2)] if y >= 2010 else [])
        gw = [("유리", 3), ("유리 짙은 청회", 2), ("유리 밝은 반사", 2), ("유리 녹색 틴트", 1)]
    elif style == "school":
        fw = [("창틀 백색", 3), ("창틀 은색 알루미늄", 3)]
        gw = [("유리", 3), ("유리 녹색 틴트", 2), ("유리 밝은 반사", 1)]
    elif style == "curtain":
        fw = [("창틀 진회색", 3), ("창틀 은색 알루미늄", 2), ("창틀 흑색", 1)]
        gw = [("유리 짙은 청회", 3), ("유리 밝은 반사", 2), ("유리 녹색 틴트", 1)]
    else:
        fw = [("창틀 은색 알루미늄", 3), ("창틀 진회색", 2), ("창틀 백색", 2)]
        gw = [("유리", 3), ("유리 짙은 청회", 2), ("유리 녹색 틴트", 1), ("유리 밝은 반사", 1)]
    frame = _pick(rng, fw)
    glass = _pick(rng, gw)
    alt = _pick(rng, [(g, 1) for g in GLASS_TINTS if g != glass])
    return dict(frame=frame, glass=glass, glass_alt=alt, mullion=float(rng.uniform(1.1, 1.5)),
                seed=int(rng.integers(0, 2 ** 31 - 1)))


def _pick(rng, opts):
    names, w = zip(*opts)
    w = np.asarray(w, float)
    return names[int(rng.choice(len(names), p=w / w.sum()))]


# ---------------------------------------------------------------- 면 성격
def blocked(A, t, nrm, L, nbr, dist=2.2):
    """이 면 바로 앞(dist m)이 이웃 건물이면 True: 세 점 중 둘 이상이 이웃 평면 안."""
    if not nbr or L < 1.0:
        return False
    import shapely
    pts = [A + t * (L * f) + nrm * dist for f in (0.2, 0.5, 0.8)]
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    hit = np.zeros(3, bool)
    for p in nbr:
        hit |= shapely.contains_xy(p, xs, ys)
        if hit.sum() >= 2:
            return True
    return False


def edge_roles(edges):
    """edges [(A, t, nrm, L)] → 역할 목록: 'front'(남쪽 긴 면) / 'back'(반대 긴 면) / 'end'(짧은 면)."""
    if not edges:
        return []
    Lmax = max(e[3] for e in edges)
    south = [-e[2][1] for e in edges]
    longs = [i for i, e in enumerate(edges) if e[3] >= 0.6 * Lmax]
    front = max(longs, key=lambda i: south[i]) if longs else None
    out = []
    for i, e in enumerate(edges):
        if i == front:
            out.append("front")
        elif i in longs:
            fn = edges[front][2]
            out.append("back" if float(np.dot(fn, e[2])) < -0.5 else "front" if float(np.dot(fn, e[2])) > 0.5
                       else "end")
        else:
            out.append("end")
    return out


# ---------------------------------------------------------------- 한 면 창 배치
def _bays(rng, L, tpls, gap=(0.6, 1.2), margin=0.5, reserve=None):
    """면 길이 L 에 창 칸을 차례로 채운다. tpls = [(이름, 가중)], 반환 [(가운데, 폭배율, 이름)].
    reserve=(a, b) 는 비워 둘 구간(계단실)."""
    out, s = [], margin
    for _ in range(64):
        name = _pick(rng, tpls)
        w = TPL[name][0] * float(rng.uniform(0.93, 1.07))
        if s + w + margin > L:
            # 남은 자리에 들어가는 가장 작은 창으로 한 번 더
            small = [(n, k) for n, k in tpls if TPL[n][0] + margin + s <= L]
            if not small:
                break
            name = small[-1][0]; w = TPL[name][0]
        if reserve and s < reserve[1] and s + w > reserve[0]:
            s = reserve[1] + 0.3
            continue
        out.append([s + w / 2, w, name])
        s += w + float(rng.uniform(*gap))
    if out:                                       # 남는 길이를 양쪽에 나눠 가운데로 (계단실이 없을 때만)
        end = out[-1][0] + out[-1][1] / 2
        shift = (L - margin - end) / 2 if not reserve else 0.0
        for b in out:
            b[0] += shift
    return out


# 창 틀: 이름 → (폭, 높이, 창대 높이, 종류)
TPL = {
    "거실창": (2.1, 1.5, 0.9, WIN),
    "방창": (1.5, 1.2, 0.9, WIN),
    "주방창": (1.0, 1.0, 1.1, WIN),
    "욕실창": (0.6, 0.6, 1.6, BATH),
    "발코니": (2.6, None, 0.15, BALC),     # 높이 = 층고 - 0.5
    "뒷창": (1.6, 1.2, 0.9, WIN),
    "복도창": (1.8, 1.0, 1.25, WIN),
    "계단창": (0.9, 1.1, None, STAIR),
}
SWAP = {"거실창": "방창", "방창": ("거실창", "주방창"), "주방창": "방창", "발코니": "거실창"}


def _win(c, w, name, zf, fh, top):
    _w, h, sill, kind = TPL[name]
    if kind == BALC:
        z0, z1 = zf + sill, zf + fh - 0.35
    else:
        z0 = zf + sill
        z1 = min(z0 + h, zf + fh - 0.25)
    z1 = min(z1, top - 0.4)
    if z1 - z0 < 0.4:
        return None
    return (c - w / 2, c + w / 2, z0, z1, kind)


def _stair(c, zf, fh, top, w=0.9, h=1.1):
    """계단참 높이(층 중간)의 계단실 창."""
    z0 = zf + 0.5 * fh + 0.15
    z1 = min(z0 + h, top - 0.4)
    if z1 - z0 < 0.5:
        return None
    return (c - w / 2, c + w / 2, z0, z1, STAIR)


def plan_edge(style, role, blind, L, floors, fh, g0, zwall, top, shop, rng, stair_at=None):
    """한 면의 창 목록 [(s0, s1, z0, z1, kind)] 와 간판 [(s0, s1, z0, z1)].
    style: villa·ribbon·balcony·apt·school (그 밖은 None → 예전 방식). shop = 상가 층 번호 집합."""
    ops, signs = [], []
    if L < 2.0:
        return ops, signs
    reserve = None
    if stair_at is not None:
        reserve = (stair_at - 0.75, stair_at + 0.75)

    # 층마다 같은 자리(위아래로 쌓임)에 오는 칸 배치를 면마다 한 번 정한다
    if style == "villa":
        if blind:
            bays = _bays(rng, L, [("욕실창", 3), ("주방창", 2), ("방창", 1)], gap=(1.4, 3.0), margin=0.8,
                         reserve=reserve)
        else:
            bays = _bays(rng, L, [("거실창", 3), ("방창", 3), ("주방창", 2), ("욕실창", 1), ("발코니", 1)],
                         reserve=reserve)
    elif style in ("balcony", "apt"):
        U = float(rng.uniform(7.5, 9.5))
        bays = []
        if blind:
            bays = _bays(rng, L, [("욕실창", 1), ("주방창", 1)], gap=(2.5, 5.0), margin=1.0)
        elif role == "front" or (style == "apt" and role != "back" and L >= 6):
            n = max(1, int(L // U))
            U = L / n
            for j in range(n):
                s = j * U
                w1 = 0.55 * (U - 1.0)
                w2 = U - 1.0 - w1
                bays.append([s + 0.3 + w1 / 2, w1, "발코니"])
                if w2 > 1.2:
                    bays.append([s + 0.3 + w1 + 0.4 + w2 / 2, w2, "발코니"])
        elif role == "back":
            n = max(1, int(L // U))
            U = L / n
            for j in range(n):
                s = j * U
                if j % 2 == 1:                    # 두 세대마다 계단·승강기 코어 → 층 중간 높이 작은 창
                    bays.append([s, 1.0, "계단창"])
                bays.append([s + 0.35 * U, 1.6 * float(rng.uniform(0.9, 1.1)), "뒷창"])
                bays.append([s + 0.72 * U, 0.6, "욕실창"])
                if U > 8.2 and j % 2 == 1:          # 다음 칸 경계에 코어가 없는 세대만 (코어 창과 붙지 않게)
                    bays.append([s + 0.88 * U, 1.0, "주방창"])
        else:                                     # 옆(박공) 면: 앞쪽 모서리 가까이 작은 창 한 줄
            bays = [[min(L - 1.0, 1.4), 1.0, "주방창"]]
            if L > 10:
                bays.append([L - 1.4, 0.6, "욕실창"])
    elif style == "school":
        if role == "front" and not blind:
            P = float(rng.uniform(8.1, 9.0))      # 교실 한 칸
            n = max(1, int(round(L / P)))
            P = L / n
            bays = [[(j + 0.5) * P, P - 0.6, "교실띠"] for j in range(n)]
        elif role == "back":
            n = max(1, int((L - 1.0) // 3.3))
            sp = (L - 1.0) / max(n, 1)
            bays = [[0.5 + (j + 0.5) * sp, 1.8, "복도창"] for j in range(n)]
        else:
            bays = [[L / 2, 1.2, "계단창"]]
    elif style == "ribbon":
        if blind:
            bays = _bays(rng, L, [("방창", 1)], gap=(2.0, 3.5), margin=1.0)
        else:
            P = float(rng.uniform(6.0, 8.0))      # 기둥 간격
            n = max(1, int(round(L / P)))
            P = L / n
            bays = [[(j + 0.5) * P, P - 0.7, "띠창"] for j in range(n)]
    else:
        return None, None

    for k in range(floors):
        zf = g0 + k * fh
        if zf + 1.2 > top:
            break
        if zf < zwall - 0.01:
            continue
        if k in shop:
            if blind:
                continue                          # 이웃에 막힌 면은 상가 유리 없음
            n = max(1, int(round((L - 0.6) / float(rng.uniform(4.0, 6.0)))))
            bay = (L - 0.6) / n
            z0, z1 = zf + 0.2, min(zf + 2.8, zf + fh - 0.3, top - 0.3)
            if z1 - z0 < 1.0:
                continue
            if zf + fh - z1 < 0.6 and zf + fh - 0.85 - z0 >= 1.9:     # 간판 자리: 유리 위를 조금 비운다
                z1 = zf + fh - 0.85
            for j in range(n):
                a, b = 0.3 + j * bay + 0.2, 0.3 + (j + 1) * bay - 0.2
                if b - a < 0.8:
                    continue
                ops.append((a, b, z0, z1, SHUTTER if rng.random() < 0.22 else SHOP))
                sz1 = min(z1 + 0.85, zf + fh - 0.1, top - 0.3)
                if sz1 - z1 > 0.4:
                    signs.append((a + 0.05, b - 0.05, z1 + 0.1, sz1))
            continue
        for c, w, name in bays:
            if name == "계단창":
                o = _stair(c, zf, fh, top, w=w)
            elif name == "교실띠":
                z0 = zf + 0.85
                z1 = min(zf + fh - 0.45, top - 0.4)
                o = (c - w / 2, c + w / 2, z0, z1, BAND) if z1 - z0 > 0.8 else None
            elif name == "띠창":
                z0 = zf + 0.9
                z1 = min(z0 + 1.5, zf + fh - 0.25, top - 0.4)
                o = (c - w / 2, c + w / 2, z0, z1, BAND) if z1 - z0 > 0.6 else None
            else:
                nm = name
                if style == "villa" and rng.random() < 0.15 and nm in SWAP:     # 층마다 조금씩 다른 창
                    sw = SWAP[nm]
                    nm = sw if isinstance(sw, str) else sw[int(rng.integers(0, len(sw)))]
                    if TPL[nm][0] > w + 0.3:
                        nm = name
                ww = min(w, TPL[nm][0] * 1.07) if nm != name else w
                if TPL[nm][3] == BALC and blind:
                    nm, ww = "방창", min(w, 1.5)
                o = _win(c, ww, nm, zf, fh, top)
            if o:
                ops.append(o)
        if stair_at is not None:
            o = _stair(stair_at, zf, fh, top)
            if o:
                ops.append(o)
    return ops, signs


# ---------------------------------------------------------------- 창틀·창턱·셔터·간판·유리 면
def _unit_glass(spec, i, c):
    """세대(창 칸)마다 유리 틴트를 조금 다르게: 약 15% 는 다른 틴트."""
    h = (spec["seed"] * 31 + i * 7919 + int(round(c * 10)) * 104729) % 1000
    return spec["glass_alt"] if h < 150 else spec["glass"]


def quad_arrays(quads):
    """사각형 목록 → (꼭짓점 (n,3), 사각 면 (m,4)). 같은 평면에서 맞닿는 꼭짓점은 하나로 합친다
    (창틀 테두리 네 조각이 꼭짓점을 나눠 씀 → 파일이 작아짐). 방향이 다른 면끼리는 나누지 않아 모서리가 뭉개지지 않는다."""
    Q = np.asarray(quads, float).reshape(-1, 4, 3)
    n = np.cross(Q[:, 1] - Q[:, 0], Q[:, 3] - Q[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    P = Q.reshape(-1, 3)
    N = np.repeat(n, 4, axis=0)
    key = np.hstack([np.round(P * 1000), np.round(N * 50)]).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return P[first], inv.reshape(-1, 4)


def trim(lay, spec, depth, fine=True):
    """trim 결과를 단(lay)에 담아 두고 다시 쓴다 (Rhino·메시가 같은 것을 씀)."""
    key = ("_trim", bool(fine), float(depth))
    hit = lay.get(key)
    if hit is None:
        hit = lay[key] = _trim(lay, spec, depth, fine)
    return hit


def _trim(lay, spec, depth, fine=True):
    """한 단(lay)의 창 부위 → {재료: [사각형(점 4개, 월드 좌표)]}. 모두 사각 면, 삼각형 없음.
    fine=False: 유리(틴트·불투명)·셔터·간판만 (창틀·창살·창턱 없이, 먼 건물용)."""
    out = {}
    if not spec:
        spec = dict(frame="창틀 은색 알루미늄", glass="유리", glass_alt="유리", mullion=1.3, seed=0)
    fr = spec["frame"]
    mull = spec["mullion"]
    fw, mw = 0.05, 0.05                    # 창틀 폭, 창살 폭
    fd = depth - 0.04                       # 창틀 앞면 깊이(유리보다 4cm 앞)

    raw = {}            # 재료 → [(면 번호, s,z,d × 4)] — 좌표 변환은 마지막에 numpy 로 한꺼번에

    def add(mat, q):
        raw.setdefault(mat, []).append((cur[0], *q))
    cur = [0]

    signs = lay.get("signs") or {}
    for ei, (A, t, nrm, L, ops) in enumerate(lay["edges"]):
        cur[0] = ei

        def P(s, z, d):
            return (s, z, d)

        def rect(mat, s0, s1, z0, z1, d):
            add(mat, [P(s0, z0, d), P(s1, z0, d), P(s1, z1, d), P(s0, z1, d)])

        def box_front(mat, s0, s1, z0, z1, d, bottom=True):
            """벽 앞으로 튀어나온 판: 앞면·윗면(·아랫면) (d<0 = 바깥)."""
            rect(mat, s0, s1, z0, z1, d)
            add(mat, [P(s0, z1, d), P(s1, z1, d), P(s1, z1, 0), P(s0, z1, 0)])
            if bottom:
                add(mat, [P(s0, z0, 0), P(s1, z0, 0), P(s1, z0, d), P(s0, z0, d)])

        for o in ops:
            s0, s1, z0, z1 = o[:4]
            kind = o[4] if len(o) > 4 else WIN
            c = (s0 + s1) / 2
            if kind == SHUTTER:
                rect("셔터", s0, s1, z0, z1, depth * 0.5)
                continue
            glass = ("유리 불투명" if kind == BATH else "유리 상가" if kind == SHOP else _unit_glass(spec, ei, c))
            if kind == BALC and z1 - z0 > 1.6:          # 확장 발코니: 아래 1m 는 불투명(난간), 위는 유리
                rect("유리 불투명", s0, s1, z0, z0 + 1.0, depth)
                rect(glass, s0, s1, z0 + 1.0, z1, depth)
            else:
                rect(glass, s0, s1, z0, z1, depth)
            if kind == CURTAIN or not fine:
                continue
            # 창틀(테두리)
            rect(fr, s0, s1, z0, z0 + fw, fd)
            rect(fr, s0, s1, z1 - fw, z1, fd)
            rect(fr, s0, s0 + fw, z0 + fw, z1 - fw, fd)
            rect(fr, s1 - fw, s1, z0 + fw, z1 - fw, fd)
            # 창살: 종류마다
            w = s1 - s0
            if kind in (BAND, BALC, SHOP):
                pitch = 2.5 if kind == SHOP else mull
                n = max(1, int(round(w / pitch)))
                for j in range(1, n):
                    x = s0 + w * j / n
                    rect(fr, x - mw / 2, x + mw / 2, z0 + fw, z1 - fw, fd)
                if kind == BALC and z1 - z0 > 1.6:
                    rect(fr, s0 + fw, s1 - fw, z0 + 1.0 - mw / 2, z0 + 1.0 + mw / 2, fd)
                if kind == SHOP and z1 - z0 > 2.4:
                    rect(fr, s0 + fw, s1 - fw, z1 - 0.5 - mw / 2, z1 - 0.5 + mw / 2, fd)
            elif kind == WIN and w > 1.2:              # 미서기창: 가운데 창살
                rect(fr, c - mw / 2, c + mw / 2, z0 + fw, z1 - fw, fd)
            # 창턱: 벽 밖으로 5cm, 두께 4cm
            if kind in (WIN, BATH, STAIR, BAND):
                box_front("창턱", s0 - 0.05, s1 + 0.05, z0 - 0.04, z0, -0.05, bottom=False)
        for i, (s0, s1, z0, z1) in enumerate(signs.get(ei, [])):
            mat = SIGNS[(spec["seed"] + ei * 13 + i * 7) % len(SIGNS)]
            box_front(mat, s0, s1, z0, z1, -0.1)
    if not raw:
        return out
    E = lay["edges"]
    A = np.array([e[0] for e in E], float); T = np.array([e[1] for e in E], float)
    N = np.array([e[2] for e in E], float)
    for mat, rows in raw.items():
        r = np.array([(row[0], *[v for p_ in row[1:] for v in p_]) for row in rows], float)
        ei = r[:, 0].astype(int)
        szd = r[:, 1:].reshape(-1, 4, 3)
        xy = A[ei][:, None, :] + szd[:, :, :1] * T[ei][:, None, :] - szd[:, :, 2:3] * N[ei][:, None, :]
        out[mat] = np.concatenate([xy, szd[:, :, 1:2]], axis=2)
    return out

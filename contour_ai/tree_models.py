"""수종별 가벼운 나무 모형.

공개 나무 모델(Kenney·Quaternius 같은 CC0 저폴리 묶음, Poly Haven 스캔 모델)을 검토했지만
한국 가로수 수종(은행·벚·느티·이팝·버즘·메타세쿼이아 …) 구분이 없거나 한 그루 수십만 면이라
콘타 모형에 맞지 않았다. 그래서 수종마다 '알아볼 수 있는 실루엣'만 살린 형태를 직접 만든다.

- 줄기·큰 가지: 5각 원뿔대 몇 개
- 수관: 흔들린 정이십면체(80면) 덩어리 여러 개를 수종 실루엣대로 배치
- 한 그루 200~700면. 같은 (형태, 크기 0.5m 단위, 변형 3가지)는 블록 하나로 재사용해 수만 그루도 가볍다.

형태 → 수종 묶음은 FORM_OF. 모르는 수종은 이름에 든 말로 침엽/활엽을 가려 정한다.
"""
import hashlib
import math

import numpy as np
import trimesh

# 형태: (이름, 높이/수관반지름 비, 수관 색)
FORMS = {
    "ginkgo":   ("은행나무형(원뿔~달걀)", 4.4, (196, 204, 150)),
    "cherry":   ("벚나무형(넓게 퍼진 우산)", 2.4, (190, 198, 160)),
    "zelkova":  ("느티나무형(V자 꽃병)", 2.7, (176, 192, 150)),
    "round":    ("둥근형(이팝·회화·단풍)", 3.0, (184, 198, 160)),
    "plane":    ("버즘나무형(크고 넓은 타원)", 3.6, (170, 186, 146)),
    "cone":     ("원뿔형(메타세쿼이아·침엽)", 8.0, (150, 170, 136)),
    "pine":     ("소나무형(굽은 줄기·납작한 잎 뭉치)", 4.6, (140, 160, 128)),
    "oak":      ("참나무형(숲)", 3.6, (164, 182, 140)),
    "column":   ("기둥형(포플러)", 7.0, (176, 194, 150)),
    "weeping":  ("늘어진형(버들)", 2.6, (186, 202, 156)),
}
WOOD = (122, 106, 90)

FORM_OF = {
    "ginkgo": ("은행", "튜울립", "튤립", "백합나무", "목백합", "대왕참나무", "루브라", "계수나무"),
    "cherry": ("벚", "왕벚", "자귀", "배롱", "백일홍", "산딸", "꽃사과", "매화", "매실", "살구", "꽃복숭아", "만첩"),
    "zelkova": ("느티", "팽나무", "느릅", "회화"),
    "plane": ("버즘", "플라타너스", "칠엽수", "오동", "가죽나무", "밤나무", "호도", "녹나무", "구실"),
    "cone": ("메타세쿼이아", "낙우송", "히말라야", "개잎갈", "잎갈", "전나무", "젓나무", "삼나무", "편백",
             "잣나무", "스트로브", "가문비", "주목", "측백", "향나무", "비자", "침엽"),
    "pine": ("소나무", "곰솔", "해송", "반송", "금강송"),
    "column": ("포플러", "미루나무", "양버들"),
    "weeping": ("수양버들", "능수버들", "버드나무"),
    "round": ("이팝", "단풍", "복자기", "모감주", "칠자화", "산수유", "목련", "후박", "먼나무", "감나무",
              "때죽", "마가목", "팥배", "층층", "물푸레", "아까시", "아카시", "가시나무", "동백", "광나무"),
}


def form_of(species, kind="street", x=0.0, y=0.0):
    """수종 이름(또는 숲/조경 구분)으로 형태를 고른다."""
    s = species or ""
    for form, words in FORM_OF.items():
        if any(w in s for w in words):
            return form
    h = int(hashlib.md5(f"{x:.1f},{y:.1f}".encode()).hexdigest()[:6], 16)
    if kind == "forest":           # 우리 산: 참나무류 + 소나무 섞임
        return ("oak", "oak", "pine", "oak", "pine")[h % 5]
    if kind == "garden":           # 단지 조경: 여러 형태 섞임
        return ("round", "cherry", "pine", "round", "zelkova", "cone")[h % 6]
    return "round"


def variant_of(x, y, n=3):
    return int(hashlib.md5(f"v{x:.1f},{y:.1f}".encode()).hexdigest()[:6], 16) % n


# ---------------------------------------------------------------- 기본 조각
def _frustum(p0, p1, r0, r1, sections=5):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    L = np.linalg.norm(d)
    if L < 1e-6:
        return None
    z = d / L
    a = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(z, a); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    ang = np.linspace(0, 2 * np.pi, sections, endpoint=False)
    ring = np.cos(ang)[:, None] * x + np.sin(ang)[:, None] * y
    V = np.vstack([p0 + ring * r0, p1 + ring * r1, p1])
    F = []
    for i in range(sections):
        j = (i + 1) % sections
        F += [[i, j, sections + j], [i, sections + j, sections + i], [sections + i, sections + j, 2 * sections]]
    return trimesh.Trimesh(V, F, process=False)


_ICO = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
_ICO0 = trimesh.creation.icosphere(subdivisions=0, radius=1.0)


def _blob(c, rx, ry, rz, rng, jitter=0.14, coarse=False):
    base = _ICO0 if coarse else _ICO
    v = base.vertices * (1 + rng.uniform(-jitter, jitter, (len(base.vertices), 1)))
    v = v * [rx, ry, rz] + np.asarray(c, float)
    return trimesh.Trimesh(v, base.faces.copy(), process=False)


def _cone(c, r, h, rng, sections=7):
    m = trimesh.creation.cone(radius=r, height=h, sections=sections)
    v = m.vertices.copy()
    v[:, :2] *= 1 + rng.uniform(-0.08, 0.08, (len(v), 1))
    return trimesh.Trimesh(v + np.asarray(c, float), m.faces, process=False)


def _ring_pts(n, rad, z, rng, phase=None):
    ph = rng.uniform(0, 2 * np.pi) if phase is None else phase
    a = ph + np.arange(n) * 2 * np.pi / n + rng.uniform(-0.3, 0.3, n)
    return np.column_stack([np.cos(a) * rad, np.sin(a) * rad, np.full(n, z)])


# ---------------------------------------------------------------- 형태
# 뼈대 방식: 줄기(굽음·기울기) → 큰 가지(수종별 각도) → 잎 덩어리.
# 잎 덩어리는 수종마다 정한 수관 윤곽(높이별 반지름) 안쪽 껍질에 놓고, 가장 가까운 큰 가지에서 잔가지로 잇는다.
def _prof(form, t):
    t = np.clip(t, 0, 1)
    if form == "ginkgo":      # 아래가 넓은 원뿔~달걀형, 꼭대기 뾰족
        return np.clip((1 - t) ** 0.85 * np.minimum(1, (t + 0.25) / 0.4), 0.08, 1)
    if form == "cherry":      # 낮고 넓게 퍼진 우산, 윗면 완만
        return np.clip(np.sqrt(np.maximum(0, 1 - ((t - 0.3) / 0.72) ** 2)), 0.1, 1)
    if form == "zelkova":     # 아래 좁고 위로 퍼지는 V, 꼭대기 둥근 돔
        return np.where(t < 0.68, 0.38 + 0.62 * (t / 0.68) ** 0.7,
                        np.sqrt(np.maximum(0.02, 1 - ((t - 0.68) / 0.34) ** 2)))
    if form == "column":      # 좁은 기둥
        return np.clip(np.sqrt(np.maximum(0, 1 - (2 * t - 1) ** 2)) ** 0.7, 0.12, 1)
    if form == "weeping":
        return np.clip(np.sqrt(np.maximum(0, 1 - ((t - 0.25) / 0.78) ** 2)), 0.15, 1)
    # round, plane, oak: 달걀형 (아래쪽이 조금 더 넓게)
    return np.clip(np.sqrt(np.maximum(0, 1 - ((t - 0.45) / 0.57) ** 2)), 0.12, 1)


# 수종별 뼈대 값: 수관 시작 높이(전체 높이 비), 큰 가지 수, 잎 덩어리 수, 덩어리 크기(r 비), 납작함,
#               큰 가지 앙각(도), 중심 줄기가 꼭대기까지 가는지, 줄기 갈라지는 높이, 기울기
SPEC = {
    "ginkgo":  dict(base=0.18, mains=7, clusters=13, size=0.46, flat=0.9, elev=48, leader=True, split=None, lean=0.02,
                    shell=(0.5, 0.82)),
    "cherry":  dict(base=0.42, mains=5, clusters=11, size=0.44, flat=0.72, elev=36, leader=False, split=0.3, lean=0.06,
                    shell=(0.55, 0.85)),
    "zelkova": dict(base=0.3, mains=6, clusters=14, size=0.38, flat=0.7, elev=55, leader=False, split=0.22, lean=0.03),
    "round":   dict(base=0.38, mains=4, clusters=10, size=0.42, flat=0.8, elev=45, leader=False, split=0.36, lean=0.04),
    "plane":   dict(base=0.32, mains=5, clusters=14, size=0.38, flat=0.75, elev=42, leader=True, split=None, lean=0.04),
    "oak":     dict(base=0.42, mains=5, clusters=11, size=0.42, flat=0.75, elev=40, leader=False, split=0.45, lean=0.08),
    "column":  dict(base=0.12, mains=6, clusters=15, size=0.5, flat=1.4, elev=75, leader=True, split=None, lean=0.0),
    "weeping": dict(base=0.5, mains=5, clusters=9, size=0.45, flat=0.7, elev=40, leader=False, split=0.4, lean=0.05),
}


def _trunk(H, top, lean, rng, wiggle=0.0):
    """줄기 중심선 점들 (굽음 포함)."""
    d = rng.uniform(0, 2 * np.pi)
    lx, ly = math.cos(d), math.sin(d)
    pts = []
    for t in np.linspace(0, 1, 5):
        z = top * t
        off = lean * H * t ** 1.4 + wiggle * H * math.sin(t * math.pi * 1.6) * 0.5
        side = wiggle * H * 0.35 * math.sin(t * math.pi * 2.3)
        pts.append((lx * off - ly * side, ly * off + lx * side, z))
    return np.array(pts)


def _along(pts, z):
    """줄기 중심선에서 높이 z 의 점."""
    zs = pts[:, 2]
    z = np.clip(z, zs[0], zs[-1])
    i = min(np.searchsorted(zs, z), len(zs) - 1)
    if i == 0:
        return pts[0]
    a = (z - zs[i - 1]) / max(zs[i] - zs[i - 1], 1e-6)
    return pts[i - 1] * (1 - a) + pts[i] * a


def _generic(form, r, rng):
    sp = SPEC[form]
    H = FORMS[form][1] * r
    tr = max(0.09, 0.08 * r)
    z0 = H * sp["base"]
    top_z = H * (0.93 if sp["leader"] else sp["split"])
    stem = _trunk(H, top_z, sp["lean"], rng)
    wood = [_frustum(stem[i], stem[i + 1], tr * (1 - 0.55 * i / 4), tr * (1 - 0.55 * (i + 1) / 4))
            for i in range(len(stem) - 1)]
    cx, cy = stem[-1][0], stem[-1][1]

    def rad(z):
        return r * _prof(form, (z - z0) / (H - z0))
    # 큰 가지
    mains = []
    az0 = rng.uniform(0, 2 * np.pi)
    for k in range(sp["mains"]):
        az = az0 + k * 2 * np.pi / sp["mains"] + rng.uniform(-0.35, 0.35)
        if sp["leader"]:
            za = z0 + (top_z - z0) * (k + rng.uniform(0.2, 0.8)) / sp["mains"] * 0.85
        else:
            za = top_z
        a0 = _along(stem, za)
        el = math.radians(sp["elev"] + rng.uniform(-10, 10))
        reach = rad(za + 0.3 * (H - za)) * rng.uniform(0.6, 0.85)
        dz = reach * math.tan(el)
        end = a0 + [math.cos(az) * reach, math.sin(az) * reach, min(dz, H * 0.95 - za)]
        rad_end = rad(end[2])
        hdist = math.hypot(end[0] - cx, end[1] - cy)
        if hdist > rad_end * 0.85:   # 수관 밖으로 나가지 않게
            s_ = rad_end * 0.85 / max(hdist, 1e-6)
            end[:2] = [cx + (end[0] - cx) * s_, cy + (end[1] - cy) * s_]
        mains.append((a0, end, az))
        wood.append(_frustum(a0, end, tr * 0.5, tr * 0.18))
    # 잎 덩어리: 윤곽 껍질 위
    leaf = []
    n = sp["clusters"]
    zs = z0 + (H - z0) * (np.arange(n) + rng.uniform(0.15, 0.85, n)) / n
    for i, z in enumerate(zs):
        az = az0 + i * 2.39996 + rng.uniform(-0.3, 0.3)     # 황금각으로 고르게 돌림
        lo, hi = sp.get("shell", (0.45, 0.72))
        rr = rad(z) * rng.uniform(lo, hi)
        c = np.array([cx + math.cos(az) * rr, cy + math.sin(az) * rr, z])
        size = r * sp["size"] * rng.uniform(0.85, 1.15) * (0.75 + 0.35 * _prof(form, (z - z0) / (H - z0)))
        leaf.append(_blob(c, size, size, size * sp["flat"], rng))
        # 가장 가까운 큰 가지에서 잔가지
        m = min(mains, key=lambda mm: abs((mm[2] - az + np.pi) % (2 * np.pi) - np.pi))
        wood.append(_frustum(m[0] * 0.4 + m[1] * 0.6, c, tr * 0.22, tr * 0.08, sections=4))
    # 꼭대기 덩어리 (원뿔형 끝)
    tsz = r * sp["size"] * (0.7 if form in ("ginkgo", "column") else 0.9)
    leaf.append(_blob((cx, cy, H - tsz * sp["flat"] * 0.9), tsz, tsz, tsz * sp["flat"], rng))
    # 안쪽 채움 덩어리 (속이 비어 보이지 않게)
    for z in np.linspace(z0 + (H - z0) * 0.3, z0 + (H - z0) * 0.7, 2):
        s2 = rad(z) * 0.55
        leaf.append(_blob((cx, cy, z), s2, s2, s2 * sp["flat"], rng))
    if form == "weeping":   # 돔 가장자리에서 땅 가까이까지 늘어진 가지 커튼: 가늘고 긴 덩어리를 촘촘히 겹침
        for ring, (n, rr0, zc, ln) in enumerate(((18, 0.9, 0.36, 0.27), (10, 0.6, 0.42, 0.22))):
            for k in range(n):
                az = az0 + (k + 0.5 * ring) * 2 * np.pi / n + rng.uniform(-0.1, 0.1)
                rr = r * rr0 * rng.uniform(0.94, 1.04)
                c = (cx + math.cos(az) * rr, cy + math.sin(az) * rr, H * zc * rng.uniform(0.9, 1.1))
                w_ = r * rng.uniform(0.13, 0.17)
                leaf.append(_blob(c, w_, w_, H * ln * rng.uniform(0.85, 1.1), rng, jitter=0.1, coarse=True))
    return wood, leaf


def _pine(r, rng):
    """소나무(적송): 기울고 굽은 긴 줄기, 아래 가지는 떨어지고 위쪽 1/3 에만 수관.
    가지는 거의 수평으로 뻗고 끝이 살짝 들리며, 끝마다 구름처럼 도톰하고 납작한 잎 뭉치(작은 덩어리 2~3개).
    꼭대기는 둥글넓적한 뭉치가 겹쳐 불규칙한 우산 모양."""
    H = FORMS["pine"][1] * r
    tr = max(0.1, 0.085 * r)
    stem = _trunk(H, H * 0.88, 0.12, rng, wiggle=0.06)
    wood = [_frustum(stem[i], stem[i + 1], tr * (1 - 0.5 * i / 4), tr * (1 - 0.5 * (i + 1) / 4))
            for i in range(len(stem) - 1)]
    leaf = []
    top = stem[-1]

    def cloud(c, w, rng, n=3):
        """도톰한 구름 뭉치: 납작한 덩어리 n 개를 옆으로 겹침."""
        d = rng.uniform(0, 2 * np.pi)
        for j in range(n):
            o = (j - (n - 1) / 2) * w * 0.55
            cc = np.asarray(c) + [math.cos(d) * o, math.sin(d) * o, rng.uniform(-0.1, 0.12) * w]
            s_ = w * rng.uniform(0.6, 0.78)
            leaf.append(_blob(cc, s_, s_ * 0.9, s_ * 0.55, rng, jitter=0.16))
    nb = 7
    zs = np.sort(rng.uniform(0.58, 0.84, nb)) * H
    az0 = rng.uniform(0, 2 * np.pi)
    for k, z in enumerate(zs):
        a0 = _along(stem, z)
        t = (z / H - 0.6) / 0.24
        az = az0 + k * 2.39996 + rng.uniform(-0.3, 0.3)
        reach = r * rng.uniform(0.75, 1.05) * (1.0 - 0.3 * t)
        el = math.radians(rng.uniform(0, 18))
        mid = a0 + [math.cos(az) * reach * 0.5, math.sin(az) * reach * 0.5, math.tan(el) * reach * 0.4]
        end = a0 + [math.cos(az) * reach, math.sin(az) * reach, math.tan(el) * reach + reach * 0.1]
        wood += [_frustum(a0, mid, tr * 0.42, tr * 0.26, sections=4), _frustum(mid, end, tr * 0.26, tr * 0.1, sections=4)]
        cloud(end + [0, 0, r * 0.12], r * rng.uniform(0.5, 0.6), rng, n=3 if reach > r * 0.75 else 2)
    # 꼭대기: 둥글넓적한 뭉치 2개 + 가운데 하나
    for k in range(2):
        az = az0 + 1.0 + k * math.pi
        c = top + [math.cos(az) * r * 0.4, math.sin(az) * r * 0.4, rng.uniform(-0.1, 0.25) * r]
        cloud(c, r * rng.uniform(0.55, 0.65), rng, n=2)
    s_ = r * 0.5
    leaf.append(_blob(top + [0, 0, r * 0.35], s_, s_ * 0.9, s_ * 0.6, rng, jitter=0.16))
    return wood, leaf


def _cone_tree(r, rng):
    """메타세쿼이아·침엽수: 곧은 줄기, 아래가 넓은 좁은 원뿔. 층마다 살짝 어긋난 원뿔."""
    H = FORMS["cone"][1] * r
    tr = max(0.08, 0.07 * r)
    wood = [_frustum((0, 0, 0), (0, 0, H * 0.97), tr, tr * 0.2)]
    leaf = []
    z0 = H * 0.12
    tiers = 6
    for k in range(tiers):
        t0 = k / tiers
        zb = z0 + (H - z0) * t0 * 0.92
        rk = r * (1 - t0 * 0.88)
        hk = (H - zb) if k == tiers - 1 else (H - z0) * 0.26
        off = rng.uniform(-0.06, 0.06, 2) * r
        leaf.append(_cone((off[0], off[1], zb), rk, hk, rng, sections=8))
    return wood, leaf


def build(form, r, variant=0):
    """원점 바닥에 선 나무. 반환 (줄기 메시, 수관 메시)."""
    rng = np.random.default_rng(int(hashlib.md5(f"{form}{r:.2f}{variant}".encode()).hexdigest()[:8], 16))
    if form == "pine":
        wood, leaf = _pine(r, rng)
    elif form == "cone":
        wood, leaf = _cone_tree(r, rng)
    else:
        wood, leaf = _generic(form, r, rng)
    w = trimesh.util.concatenate([m for m in wood if m is not None])
    f = trimesh.util.concatenate(leaf)
    return w, f


_CACHE = {}


def model(form, r, variant=0):
    """(form, r 0.5m 단위, 변형) 별로 한 번만 만든다."""
    rq = max(0.5, round(r * 2) / 2)
    key = (form, rq, variant)
    if key not in _CACHE:
        _CACHE[key] = build(form, rq, variant)
    return key, _CACHE[key]


# 가로수 수종을 나무 좌표에 붙여 두는 곳 (출력 때 형태를 고르는 데 씀)
SPECIES = {}


def remember(x, y, species):
    if species:
        SPECIES[(round(x, 1), round(y, 1))] = species


def species_at(x, y):
    return SPECIES.get((round(x, 1), round(y, 1)), "")


def pick(x, y, r, kind):
    """나무 하나 → (블록 키, (줄기, 수관), 수종 이름)."""
    sp = species_at(x, y) if kind == "street" else ""
    form = form_of(sp, kind, x, y)
    key, parts = model(form, r, variant_of(x, y))
    return key, parts, sp

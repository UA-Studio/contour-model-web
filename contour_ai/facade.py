"""건물 입면: 창문을 뚫고 재료별 레이어로 나눈다.

브이월드 3D 건물 자료(입면 사진)는 2019년에 Open API가 닫혔고 '공개제한' 정보라 내려받아 쓸 수 없다.
그래서 쓸 수 있는 공개 자료로 정한다.

벽 재료·창 형식: 건축물대장 구조(조적·철근콘크리트·철골·목구조) + 주용도 + 사용승인 연도,
               대장이 없으면 수치지도 종류·용도·층수로 추정.
지붕 재료:     항공사진이 있으면 지붕 한가운데 색을 재료 팔레트에 견줘서, 없으면 종류로 추정.
창문:          층마다 입면 변을 따라 창 형식에 맞는 개구부를 내고(벽에 실제 구멍), 개구부 둘레(창 깊이)와
               안쪽 유리면을 만든다.

m["facade"] = {"wall": 재료, "roof": 재료, "window": 형식, "basis": 근거}
"""
import math

import numpy as np
import trimesh
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

# 재료: 이름 → (색, 투명도)
MATERIALS = {
    "붉은 벽돌": ((150, 78, 58), 0.0),
    "갈색 치장벽돌": ((126, 96, 78), 0.0),
    "백색 도장": ((232, 230, 222), 0.0),
    "회색 노출콘크리트": ((170, 168, 162), 0.0),
    "석재 패널": ((206, 198, 184), 0.0),
    "금속 패널": ((176, 180, 184), 0.0),
    "목재": ((158, 118, 82), 0.0),
    "베이지 석재": ((214, 200, 176), 0.0),
    "진회색 금속 패널": ((88, 90, 94), 0.0),
    "흑색 벽돌": ((72, 66, 64), 0.0),
    "유리": ((120, 150, 168), 0.55),
    "유리 커튼월 스팬드럴": ((70, 86, 98), 0.0),
    "지붕 녹색 방수": ((92, 138, 108), 0.0),
    "지붕 청색 방수·슬레이트": ((84, 112, 148), 0.0),
    "지붕 적갈 기와": ((146, 82, 62), 0.0),
    "지붕 회색 콘크리트": ((150, 150, 146), 0.0),
    "지붕 밝은 금속·백색": ((214, 214, 208), 0.0),
    "지붕 흑색 슁글·아스팔트": ((64, 64, 66), 0.0),
}
ROOF_PALETTE = [k for k in MATERIALS if k.startswith("지붕 ")]

# 창 형식: (기준 간격, 창 폭, 창 높이, 창대 높이)
WINDOW = {
    "punch": (3.2, 1.4, 1.4, 0.9),        # 주택·다세대: 낱창
    "apt": (3.6, 2.2, 1.5, 0.9),          # 아파트: 큰 낱창
    "ribbon": (4.5, 4.1, 1.6, 0.9),       # 사무·학교: 띠창 (4.5m마다 0.4m 기둥)
    "curtain": (1.5, 1.38, None, 0.0),    # 커튼월: 층마다 유리 + 스팬드럴
    "small": (4.0, 1.0, 1.0, 1.2),        # 창고·공장: 드문 작은 창
    "studio": (2.4, 0.9, 1.2, 1.0),       # 도시형생활주택·원룸: 작은 창 촘촘히 (v2.3)
    "balcony": (4.2, 3.2, 2.1, 0.15),     # 판상형 아파트: 발코니 큰 창 (v2.3)
    "bigpunch": (3.6, 2.4, 1.8, 0.6),     # 근생 꼬마빌딩: 큰 낱창 (v2.3)
    "tall": (4.0, 1.3, 2.8, 1.0),         # 종교·문화: 키 큰 창 (v2.3)
}
TYPE_KO = {"punch": "낱창", "apt": "타워형 아파트", "ribbon": "띠창", "curtain": "커튼월", "small": "공장·창고",
           "studio": "도시형생활주택", "balcony": "판상형 아파트", "bigpunch": "근생 꼬마빌딩", "tall": "종교·문화"}
DEPTH = 0.18          # 창 깊이(벽면에서 유리까지)


# ---------------------------------------------------------------- 재료·창 형식 정하기
def _year(m):
    y = m.get("year")
    if y:
        return int(y)
    s = str(m.get("attrs", {}).get("대장_사용승인일") or "")[:4]
    return int(s) if s.isdigit() else None


def choose(m):
    """벽 재료·창 형식과 그 근거."""
    at = m.get("attrs", {})
    kind, use, fl = m.get("layer") or "", m.get("use") or "", int(m.get("floors") or 1)
    strc = at.get("대장_구조", "")
    purp = at.get("대장_주용도", "") or use
    y = _year(m)
    basis = []
    if strc:
        basis.append(f"대장 구조 {strc}")
    if y:
        basis.append(f"{y}년")
    office = any(w in purp for w in ("업무", "판매", "문화", "의료", "숙박", "방송"))
    school = any(w in purp for w in ("교육", "연구", "노유자"))
    housing = "주택" in purp or kind in ("일반주택", "연립주택", "아파트")
    factory = any(w in purp for w in ("공장", "창고", "자동차", "위험물", "동물", "식물")) or kind in ("가건물", "온실")
    if kind == "아파트" or "공동주택" in purp and fl >= 6:
        wall, win = "백색 도장", "apt"
    elif "조적" in strc or "벽돌" in strc:
        wall, win = "붉은 벽돌", "punch"
    elif "목" in strc:
        wall, win = "목재", "punch"
    elif "철골" in strc or "steel" in strc.lower():
        if office or fl >= 6:
            wall, win = "유리 커튼월 스팬드럴", "curtain"
        elif factory:
            wall, win = "금속 패널", "small"
        else:
            wall, win = "금속 패널", "ribbon"
    elif factory:
        wall, win = "금속 패널", "small"
    elif office and fl >= 8:
        wall, win = ("유리 커튼월 스팬드럴", "curtain") if (y or 2000) >= 1995 else ("석재 패널", "ribbon")
    elif office or school:
        wall, win = ("석재 패널" if office else "회색 노출콘크리트"), "ribbon"
    elif housing or use in ("주택", "근린생활시설"):
        # 다가구·다세대: 90년대까지 붉은 벽돌, 2000년대 이후 갈색 치장벽돌·도장
        if y is None:
            wall = "붉은 벽돌" if fl <= 4 else "백색 도장"
        else:
            wall = "붉은 벽돌" if y < 1995 else "갈색 치장벽돌" if y < 2012 else "백색 도장"
        win = "punch"
    else:
        wall, win = "회색 노출콘크리트", "punch"
    if not strc:
        basis.append("수치지도 종류·용도로 추정")
    wall, win, extra = refine(m, wall, win, purp, strc, y, fl, housing, office)
    basis += extra
    return wall, win, ", ".join(basis)


def _rng(m):
    """건물마다 늘 같은 난수: 필지·동 이름이 있으면 그것으로, 없으면 평면 중심(0.5m 단위)으로."""
    import zlib
    at = m.get("attrs", {})
    key = at.get("PNU") or at.get("대장_건물명", "") + at.get("대장_동명칭", "")
    c = m["poly"].representative_point() if m.get("poly") is not None else None
    key = f"{key}|{round(c.x * 2) if c else 0}|{round(c.y * 2) if c else 0}"
    return np.random.default_rng(zlib.crc32(key.encode("utf-8")))


def _pick(rng, opts):
    names, w = zip(*opts)
    w = np.asarray(w, float)
    return names[int(rng.choice(len(names), p=w / w.sum()))]


def refine(m, wall, win, purp, strc, y, fl, housing, office):
    """v2.3 입면 유형 늘리기: 대장 기타용도·세대수·지붕·층별 용도와 연대별 흔한 재료 고르기.
    연대별 재료 비율은 흔히 보이는 경향을 어림한 것(통계 아님). 같은 건물은 늘 같은 결과."""
    at = m.get("attrs", {})
    etc = at.get("대장_기타용도", "")
    roofk = at.get("대장_지붕", "") + at.get("대장_기타지붕", "")
    hh = int(float(at.get("대장_세대수") or 0) or 0)
    rng = _rng(m)
    extra = []
    area = m["poly"].area if m.get("poly") is not None else 0
    txt = purp + " " + etc
    kind = m.get("layer") or ""
    # 지붕: 대장에 기와·슬레이트면 경사 지붕
    if any(w in roofk for w in ("기와", "슬레이트", "경사", "아스팔트슁글", "슁글")) and fl <= 4:
        m["pitched"] = True
        extra.append(f"대장 지붕 {roofk.strip()}")
    # 한옥: 목구조 + 기와 + 2층 이하
    if "목" in strc and fl <= 2 and ("기와" in roofk or (y and y < 1970)):
        m["pitched"] = True
        return "목재", "punch", extra + ["한옥 추정"]
    if "종교" in txt:
        return _pick(rng, [("석재 패널", 2), ("붉은 벽돌", 2), ("백색 도장", 1)]), "tall", extra
    if kind == "아파트" or ("공동주택" in purp and "아파트" in txt):
        if fl >= 6:
            ctr = m["poly"].minimum_rotated_rectangle if area else None
            if ctr is not None and not ctr.is_empty:
                xy = np.asarray(ctr.exterior.coords)
                e = sorted(np.hypot(*np.diff(xy[:3], axis=0).T))
                slab = e[-1] > 2.2 * max(e[0], 1.0)
            else:
                slab = True
            w = _pick(rng, [("백색 도장", 4), ("베이지 석재", 2), ("회색 노출콘크리트", 1)]) if (y or 2000) < 2010 \
                else _pick(rng, [("백색 도장", 2), ("베이지 석재", 2), ("석재 패널", 2), ("진회색 금속 패널", 1)])
            return w, ("balcony" if slab else "apt"), extra
    studio = ("도시형" in txt or "원룸" in txt or "고시원" in txt or "오피스텔" in txt
              or (housing and hh >= 6 and area * max(fl - 1, 1) / hh < 40))
    if studio and fl >= 3:
        w = _pick(rng, [("백색 도장", 3), ("흑색 벽돌", 1), ("진회색 금속 패널", 1), ("베이지 석재", 1)])
        return w, "studio", extra
    if housing and win == "punch" and wall in ("붉은 벽돌", "갈색 치장벽돌", "백색 도장") and "조적" not in strc:
        if y and 1995 <= y < 2012:
            wall = _pick(rng, [("갈색 치장벽돌", 5), ("붉은 벽돌", 2), ("석재 패널", 2), ("베이지 석재", 1)])
        elif y and y >= 2012:
            wall = _pick(rng, [("백색 도장", 4), ("베이지 석재", 2), ("흑색 벽돌", 1), ("진회색 금속 패널", 1),
                               ("회색 노출콘크리트", 1)])
    near = any(w in purp for w in ("근린", "판매"))
    if near and win in ("punch", "ribbon") and 2 <= fl <= 8 and (y or 0) >= 2000:
        wall = _pick(rng, [("석재 패널", 3), ("진회색 금속 패널", 2), ("백색 도장", 2), ("흑색 벽돌", 1)])
        win = "bigpunch"
    elif office and win == "ribbon":
        wall = _pick(rng, [("석재 패널", 3), ("베이지 석재", 1), ("금속 패널", 2)])
    return wall, win, extra


def tone_variant(wall, rng):
    """같은 재료라도 건물마다 밝기를 조금 달리 (3단계). 유리·커튼월은 그대로."""
    if wall.startswith("유리"):
        return wall
    k = int(rng.integers(0, 3))
    if k == 0:
        return wall
    rgb, tr = MATERIALS[wall]
    f = (0.93, 1.06)[k - 1]
    name = f"{wall} 톤{k}"
    if name not in MATERIALS:
        MATERIALS[name] = (tuple(int(min(255, v * f)) for v in rgb), tr)
    return name


def floor_uses(rows, pk=None):
    """층별개요 줄 → {지상층번호: '용도 기타용도'} (주건축물)."""
    out = {}
    for r in rows:
        if pk and r.get("mgmBldrgstPk") and str(r.get("mgmBldrgstPk")) != str(pk):
            continue
        if "부속" in str(r.get("mainAtchGbCdNm") or "") or "지하" in str(r.get("flrGbCdNm") or ""):
            continue
        if "옥탑" in str(r.get("flrGbCdNm") or "") + str(r.get("flrNoNm") or ""):
            continue
        try:
            k = int(float(r.get("flrNo") or 0))
        except (TypeError, ValueError):
            continue
        if k >= 1:
            u = f"{r.get('mainPurpsCdNm') or ''} {r.get('etcPurps') or ''}".strip()
            out[k] = (out.get(k, "") + " " + u).strip()
    return out


def _lab(rgb):
    c = np.asarray(rgb, float) / 255
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


PAL_LAB = None


def roof_from_color(rgb):
    global PAL_LAB
    if PAL_LAB is None:
        PAL_LAB = _lab([MATERIALS[k][0] for k in ROOF_PALETTE])
    d = np.linalg.norm(PAL_LAB - _lab(rgb), axis=1)
    return ROOF_PALETTE[int(np.argmin(d))]


def roof_colors(masses, aerial_path):
    """항공사진에서 건물마다 지붕 가운데(가장자리 0.8m 안쪽) 색의 중앙값. 반환 {i: (r,g,b)}"""
    import shapely
    from .aerial import load
    rgb, x0, y1, cell, _ = load(aerial_path)
    h, w = rgb.shape[:2]
    out = {}
    for i, m in enumerate(masses):
        # 가장자리 1.5m 안쪽만: 항공사진은 높은 건물이 옆으로 기울어 찍혀(기복 변위) 테두리에 벽이 섞인다
        p = m["poly"].buffer(-1.5)
        if p.is_empty or p.area < 4:
            p = m["poly"].buffer(-0.8)
        if p.is_empty or p.area < 2:
            p = m["poly"]
        bx0, by0, bx1, by1 = p.bounds
        c0, c1 = int((bx0 - x0) / cell), int(math.ceil((bx1 - x0) / cell))
        r0, r1 = int((y1 - by1) / cell), int(math.ceil((y1 - by0) / cell))
        c0, r0, c1, r1 = max(c0, 0), max(r0, 0), min(c1, w), min(r1, h)
        if c1 <= c0 or r1 <= r0:
            continue
        xs = x0 + (np.arange(c0, c1) + 0.5) * cell
        ys = y1 - (np.arange(r0, r1) + 0.5) * cell
        X, Y = np.meshgrid(xs, ys)
        inside = shapely.contains_xy(p, X, Y)
        if inside.sum() < 6:
            continue
        px = rgb[r0:r1, c0:c1][inside]
        if px.max() == 0:
            continue
        out[i] = dominant_color(px)
    return out


def dominant_color(px, k=3, iters=8):
    """지붕 화소에서 가장 넓게 칠해진 색. 그늘(어두운 쪽 25%)·반사(가장 밝은 3%)를 빼고 Lab 색공간에서
    세 무리로 나눈 뒤, 가장 큰 무리의 중앙값 — 옥탑·물탱크·실외기·그늘이 섞여도 지붕 바닥색이 나온다."""
    px = np.asarray(px, float).reshape(-1, 3)
    lab = _lab(px)
    L = lab[:, 0]
    keep = (L >= np.percentile(L, 25)) & (L <= np.percentile(L, 97))
    if keep.sum() >= 12:
        px, lab = px[keep], lab[keep]
    if len(px) < 3 * k:
        return tuple(int(v) for v in np.median(px, axis=0))
    c = np.percentile(lab, np.linspace(15, 85, k), axis=0)
    for _ in range(iters):
        lbl = np.argmin(((lab[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        c = np.array([lab[lbl == j].mean(axis=0) if (lbl == j).any() else c[j] for j in range(k)])
    big = np.bincount(lbl, minlength=k).argmax()
    return tuple(int(v) for v in np.median(px[lbl == big], axis=0))


def photo_roof_material(rgb, kind):
    """사진에서 읽은 지붕색을 그대로 쓰는 재료(16단계로 묶어 재료 수가 너무 늘지 않게). 이름은 가까운 지붕 종류 + 색 번호."""
    q = tuple(int(min(255, round(v / 16) * 16)) for v in rgb)
    base = kind.replace("지붕 ", "").split("·")[0]
    name = f"지붕 사진색 {base} {q[0]:02X}{q[1]:02X}{q[2]:02X}"
    if name not in MATERIALS:
        MATERIALS[name] = (q, 0.0)
    return name


def assign(masses, aerial_path=None, log=print):
    """건물마다 m["facade"] 를 채운다."""
    colors = {}
    if aerial_path:
        try:
            colors = roof_colors(masses, aerial_path)
        except Exception as e:
            log(f"   (항공사진 지붕 색을 읽지 못함: {e})")
    count = {}
    for i, m in enumerate(masses):
        wall, win, basis = choose(m)
        if i in colors:
            kind = roof_from_color(colors[i])
            rbasis = f"항공사진 지붕색 RGB{colors[i]} ≈ {kind}"
            if kind == "지붕 녹색 방수":
                # 사진에서 초록 지붕 = 녹색 우레탄 방수 평지붕(옥상). 박공·모임지붕으로 만들지 않는다
                m["flat_roof"] = True
                rbasis += " → 초록 평지붕"
            # 재료 색은 팔레트 대표색이 아니라 사진에서 읽은 색 그대로(옥탑 지붕도 같은 재료)
            roof = photo_roof_material(colors[i], kind)
        else:
            pitched = m.get("layer") in ("가건물", "온실") or (m.get("layer") == "일반주택" and int(m.get("floors") or 1) <= 2)
            roof = "지붕 적갈 기와" if pitched else "지붕 녹색 방수"
            rbasis = "종류로 추정"
        rng = _rng(m)
        jit = (float(rng.uniform(0.9, 1.1)), float(rng.uniform(0.85, 1.1)), float(rng.uniform(-0.1, 0.1)))
        fu = m.get("floor_uses") or {}
        shop_floors = sorted(k - 1 for k, u in fu.items() if k <= 3 and any(w in u for w in ("근린", "판매", "음식", "소매")))
        pil = bool(fu.get(1)) and any(w in fu[1] for w in ("주차", "필로티")) and int(m.get("floors") or 1) >= 3
        m["facade"] = dict(wall=tone_variant(wall, rng), roof=roof, window=win, basis=basis, roof_basis=rbasis,
                           type=TYPE_KO.get(win, win), jitter=jit, pilotis=pil,
                           shop_floors=shop_floors if fu else None)
        m.setdefault("attrs", {})["입면_유형"] = TYPE_KO.get(win, win) + (" · 필로티" if pil else "")
        count[wall] = count.get(wall, 0) + 1
    top = ", ".join(f"{k} {v}" for k, v in sorted(count.items(), key=lambda kv: -kv[1])[:5])
    log(f"   입면 재료: {top}" + (f" · 지붕색 항공사진 {len(colors)}동" if colors else ""))


# ---------------------------------------------------------------- 창이 뚫린 벽
def _openings(L, z_floor, fh, top, style, floor_index, shop, jit=None):
    """한 변 길이 L, 층 바닥 z_floor 에서 (s0, s1, z0, z1) 개구부들. shop = 상가 유리를 낼 층 번호(0부터) 집합."""
    out = []
    if floor_index in shop:
        # 1층 상가: 큰 유리 (5m마다 0.4m 기둥)
        # 층고가 2.8m 보다 낮으면 위층 창과 겹치지 않게 그 층 안(천장 밑 0.3m)에서 끝낸다
        z0, z1 = z_floor + 0.25, min(z_floor + 2.8, z_floor + fh - 0.3, top - 0.3)
        n = max(1, int(round((L - 0.6) / 5.0)))
        bay = (L - 0.6) / n
        for k in range(n):
            out.append((0.3 + k * bay + 0.2, 0.3 + (k + 1) * bay - 0.2, z0, z1))
        return [o for o in out if o[1] - o[0] > 0.6 and z1 - z0 > 1.0]
    bay, ww, wh, sill = WINDOW[style]
    if jit and style != "curtain":
        bay, ww, sill = bay * jit[0], ww * jit[1], max(0.1, sill + jit[2])
        wh = min(wh, fh - sill - 0.35)
    if style == "curtain":
        z0, z1 = z_floor + 0.1, min(z_floor + fh - 0.9, top - 0.3)
    else:
        z0, z1 = z_floor + sill, min(z_floor + sill + wh, z_floor + fh - 0.1, top - 0.4)
    if z1 - z0 < 0.6:
        return []
    usable = L - (0.3 if style == "curtain" else 0.8)
    n = int(usable // bay)
    if n < 1:
        if L >= 2.2 and style != "curtain":
            w = min(ww, L - 1.2)
            return [((L - w) / 2, (L + w) / 2, z0, z1)] if w > 0.5 else []
        return []
    bay = usable / n
    w = min(ww, bay - (0.12 if style == "curtain" else 0.4))
    s_start = (L - usable) / 2
    for k in range(n):
        c = s_start + (k + 0.5) * bay
        out.append((c - w / 2, c + w / 2, z0, z1))
    return out


def _tri2d(poly):
    """(s, z) 평면 다각형 → 삼각형 (반시계로 맞춤)."""
    v, f = trimesh.creation.triangulate_polygon(poly)
    if len(f) == 0:
        return v, f
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    f = np.where((cross < 0)[:, None], f[:, ::-1], f)
    return v, f


class _Acc:
    def __init__(self):
        self.V, self.F, self.n = [], [], 0

    def add(self, V, F):
        if len(F):
            self.V.append(np.asarray(V, float)); self.F.append(np.asarray(F) + self.n); self.n += len(V)

    def quad(self, a, b, c, d):   # 반시계 a→b→c→d 가 바깥을 본다
        self.add([a, b, c, d], [[0, 1, 2], [0, 2, 3]])

    def mesh(self):
        if not self.F:
            return None
        return trimesh.Trimesh(np.vstack(self.V), np.vstack(self.F), process=False)


_FM_CACHE = {}   # 건물 하나의 창문 메시는 3dm·GLB·스케치업 쓰기에서 똑같이 쓰인다 → 한 번만 만든다


def clear_cache():
    _FM_CACHE.clear()


def facade_meshes(m, floor_height=3.3, origin=(0.0, 0.0)):
    """본체 대신 쓸 메시: {재료: Trimesh}. 같은 건물·같은 높이면 앞서 만든 것을 돌려준다(읽기만 할 것)."""
    key = (id(m), float(floor_height), tuple(origin), m.get("base"), m.get("top"), m.get("floors"))
    hit = _FM_CACHE.get(key)
    if hit is not None and hit[0] is m:
        return hit[1]
    out = _facade_meshes(m, floor_height, origin)
    _FM_CACHE[key] = (m, out)
    return out


def layout(m, floor_height=3.3):
    """입면 짜임: 벽 한 변마다 '온전한 한 면'(직사각형 + 창 구멍)으로. 메시·Rhino 솔리드가 같이 쓴다.
    반환 [tier dict]: wall, roof(재료), poly, base, zwall(벽 시작 높이), top,
                      edges=[(A, t, nrm, L, [(s0, s1, z0, z1) 창])], cols=[(x, y)] 필로티 기둥."""
    from .building_detail import floor_frame, tier_boxes
    tb = tier_boxes(m)
    if tb:
        out, off = [], 0
        for p, z0, z1, nf in tb:
            g0, _ = floor_frame(m, floor_height)
            sub = dict(m, poly=p, base=z0, top=z1, floors=nf, tiers=None, height=z1 - max(z0, g0),
                       floor_h=(z1 - max(z0, g0)) / nf if m.get("floor_h") else None, floor_offset=off)
            off += nf
            out += layout(sub, floor_height)
        return out
    fc = m.get("facade") or {}
    wall_mat, roof_mat, style = fc.get("wall", "백색 도장"), fc.get("roof", "지붕 녹색 방수"), fc.get("window", "punch")
    poly = m["poly"].simplify(0.15)
    if poly.is_empty or poly.geom_type != "Polygon":
        return []
    poly = orient(poly, 1.0)
    base, top = float(m["base"]), float(m["top"])
    floors = max(1, int(m.get("floors") or 1))
    g0, fh = floor_frame(m, floor_height)
    if not m.get("floor_h"):
        fh = min(floor_height, max(2.4, (top - base - 0.3) / floors))
    off = int(m.get("floor_offset") or 0)
    sf = fc.get("shop_floors")
    if sf is None:
        shop0 = (m.get("use") == "근린생활시설" or "근린" in m.get("attrs", {}).get("대장_주용도", "")) \
            and style in ("punch", "ribbon", "bigpunch", "studio")
        sf = [0] if shop0 else []
    shop = {k - off for k in sf if style != "curtain"}
    jit = fc.get("jitter")
    zwall = base
    if fc.get("pilotis") and off == 0 and floors >= 3:      # 필로티: 1층은 벽 없이 기둥만
        zwall = g0 + fh
        shop = set()
    edges = []
    for ring in [poly.exterior, *poly.interiors]:
        c = np.asarray(ring.coords)[:, :2]
        for i in range(len(c) - 1):
            A, B = c[i], c[i + 1]
            L = float(np.hypot(*(B - A)))
            if L < 0.05:
                continue
            t = (B - A) / L
            nrm = np.array([t[1], -t[0]])            # 바깥쪽
            ops = []
            if L >= 2.0 and top - base > 2.6:
                for k in range(floors):
                    zf = g0 + k * fh
                    if zf + 1.2 > top:
                        break
                    if zf < zwall - 0.01:
                        continue
                    ops += _openings(L, zf, fh, top, style, k, shop, jit)
            ops = [o for o in ops if o[1] - o[0] > 0.2 and o[3] - o[2] > 0.2
                   and o[0] > 0.02 and o[1] < L - 0.02 and o[2] > zwall + 0.02 and o[3] < top - 0.02]
            edges.append((A, t, nrm, L, _apart(ops)))
    cols = []
    if zwall > base:
        from shapely.geometry import Point
        ring = np.asarray(poly.exterior.coords)[:-1, :2]
        inner = poly.buffer(-0.35, join_style=2)
        for i in range(len(ring)):
            A, B = ring[i], ring[(i + 1) % len(ring)]
            n = max(1, int(round(float(np.hypot(*(B - A))) / 6.0)))
            for j in range(n):
                q = A + (B - A) * j / n
                if not inner.is_empty:
                    pp = inner.exterior.interpolate(inner.exterior.project(Point(q)))
                    q = np.array([pp.x, pp.y])
                if all(np.hypot(*(q - c_)) > 1.5 for c_ in cols):
                    cols.append(q)
    return [dict(wall=wall_mat, roof=roof_mat, poly=poly, base=base, zwall=zwall, top=top, edges=edges, cols=cols)]


def _apart(ops, gap=0.08):
    """창 구멍끼리 겹치거나 닿으면 Rhino 가 벽 판(구멍 뚫린 면)을 그리지 못해 벽이 사라지고
    창 테두리·지붕·파라펫만 공중에 떠 보인다 → 앞 구멍과 gap 안으로 붙는 구멍은 버린다."""
    kept = []
    for o in ops:
        if all(o[1] + gap <= k[0] or k[1] + gap <= o[0] or o[3] + gap <= k[2] or k[3] + gap <= o[2] for k in kept):
            kept.append(o)
    return kept


def _rects(L, z0, z1, holes):
    """[0,L]×[z0,z1] 에서 구멍을 뺀 부분을 큰 직사각형 몇 개로 (가는 삼각형 없이). 반환 [(s0, s1, za, zb)]"""
    xs = sorted({0.0, L, *[h[0] for h in holes], *[h[1] for h in holes]})
    zs = sorted({z0, z1, *[h[2] for h in holes], *[h[3] for h in holes]})
    zs = [z for z in zs if z0 <= z <= z1]
    rows = []
    for a, b in zip(zs[:-1], zs[1:]):
        zm = (a + b) / 2
        runs, cur = [], None
        for x0, x1 in zip(xs[:-1], xs[1:]):
            xm = (x0 + x1) / 2
            hole = any(h[0] <= xm <= h[1] and h[2] <= zm <= h[3] for h in holes)
            if hole:
                if cur:
                    runs.append(cur); cur = None
            else:
                cur = (cur[0], x1) if cur else (x0, x1)
        if cur:
            runs.append(cur)
        rows.append((a, b, runs))
    out, open_ = [], {}
    for a, b, runs in rows:                   # 위아래 같은 가로 구간은 하나로 잇는다
        nxt = {}
        for r in runs:
            if r in open_:
                nxt[r] = (open_.pop(r)[0], b)
            else:
                nxt[r] = (a, b)
        for r, (za, zb) in open_.items():
            out.append((r[0], r[1], za, zb))
        open_ = nxt
    for r, (za, zb) in open_.items():
        out.append((r[0], r[1], za, zb))
    return out


def _facade_meshes(m, floor_height=3.3, origin=(0.0, 0.0)):
    """본체 대신 쓸 메시: {재료: Trimesh} — 벽은 큰 직사각형 조각(가는 삼각형 없음), 창 둘레, 유리, 지붕 덮개, 바닥."""
    out = {}
    ox, oy = origin
    for lay in layout(m, floor_height):
        wall, glass, roof = _Acc(), _Acc(), _Acc()
        zwall, top = lay["zwall"], lay["top"]
        for A, t, nrm, L, ops in lay["edges"]:
            def pt(s, z, d=0.0):
                q = A + s * t - nrm * d
                return [q[0] - ox, q[1] - oy, z]
            for s0, s1, za, zb in _rects(L, zwall, top, ops):
                wall.quad(pt(s0, za), pt(s1, za), pt(s1, zb), pt(s0, zb))
            d = DEPTH
            for s0, s1, z0, z1 in ops:
                wall.quad(pt(s0, z0), pt(s0, z0, d), pt(s0, z1, d), pt(s0, z1))
                wall.quad(pt(s1, z1), pt(s1, z1, d), pt(s1, z0, d), pt(s1, z0))
                wall.quad(pt(s1, z0), pt(s1, z0, d), pt(s0, z0, d), pt(s0, z0))
                wall.quad(pt(s0, z1), pt(s0, z1, d), pt(s1, z1, d), pt(s1, z1))
                glass.quad(pt(s0, z0, d), pt(s1, z0, d), pt(s1, z1, d), pt(s0, z1, d))
        v, f = trimesh.creation.triangulate_polygon(lay["poly"])
        roof.add(np.column_stack([v[:, 0] - ox, v[:, 1] - oy, np.full(len(v), top)]), f)
        wall.add(np.column_stack([v[:, 0] - ox, v[:, 1] - oy, np.full(len(v), zwall)]), f[:, ::-1])
        for q in lay["cols"]:
            b = trimesh.creation.box([0.45, 0.45, zwall - lay["base"]])
            b.apply_translation([q[0] - ox, q[1] - oy, (lay["base"] + zwall) / 2])
            wall.add(b.vertices, b.faces)
        for mat, acc in ((lay["wall"], wall), ("유리", glass), (lay["roof"], roof)):
            mm = acc.mesh()
            if mm is not None:
                out[mat] = mm if mat not in out else trimesh.util.concatenate([out[mat], mm])
    return out or None


def material_of(m, role):
    """building_parts 의 부위 → 재료 이름."""
    fc = m.get("facade") or {}
    wall = fc.get("wall", "백색 도장")
    if role == "roof":
        return fc.get("roof", "지붕 적갈 기와")
    if role in ("canopy", "column", "frame"):
        return "회색 노출콘크리트" if role != "canopy" else "금속 패널"
    if role in ("parapet", "penthouse"):
        return "백색 도장" if wall.startswith("유리") else wall
    return wall

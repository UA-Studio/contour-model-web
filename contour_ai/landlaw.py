"""대지 법규 검토: 주소 → 필지 → 토지이용계획·토지특성 → 1차 법규 검토.

자료 (토지이음에 나오는 것과 같은 국가 자료를 브이월드 API로 받는다):
- 주소 → 좌표: 브이월드 지오코더 (req/address, getcoord)
- 좌표 → 필지 경계·PNU: 브이월드 2D데이터 연속지적도 (LP_PA_CBND_BUBUN)
- 토지이용계획(용도지역·지구·구역, 저촉 여부): 브이월드 국가중점 API getLandUseAttr
- 토지특성(지목·면적·도로접면·지형·공시지가): 브이월드 국가중점 API getLandCharacteristics
  ※ 브이월드 인증키에 '지오코더', '토지이용계획정보', '토지특성정보' 활용 API를 추가해 두어야 한다.

검토 내용은 법령과 서울시 조례의 일반 기준을 자동으로 맞춰 본 1차 검토다. 지구단위계획·정비계획·
개별 조례·도시계획시설 결정이 있으면 그것이 우선하므로, 결과의 '확인할 것'을 꼭 확인해야 한다.
"""
import json
import math
import os
import re
import urllib.parse

from .publicdata import ApiError, _get, vworld_features

GEOCODER = "https://api.vworld.kr/req/address"
NED = "https://api.vworld.kr/ned/data/"

# ---------------------------------------------------------------- 기준표
# 국토의 계획 및 이용에 관한 법률 시행령 제84조(건폐율)·제85조(용적률): 용도지역별 상한(%)
NATIONAL = {
    "제1종전용주거지역": (50, 100), "제2종전용주거지역": (50, 150),
    "제1종일반주거지역": (60, 200), "제2종일반주거지역": (60, 250), "제3종일반주거지역": (50, 300),
    "준주거지역": (70, 500),
    "중심상업지역": (90, 1500), "일반상업지역": (80, 1300), "근린상업지역": (70, 900), "유통상업지역": (80, 1100),
    "전용공업지역": (70, 300), "일반공업지역": (70, 350), "준공업지역": (70, 400),
    "보전녹지지역": (20, 80), "생산녹지지역": (20, 100), "자연녹지지역": (20, 100),
    "보전관리지역": (20, 80), "생산관리지역": (20, 80), "계획관리지역": (40, 100),
    "농림지역": (20, 80), "자연환경보전지역": (20, 80),
}
# 서울특별시 도시계획 조례 제54조(건폐율)·제55조(용적률) 일반 기준(%)
# 상업지역은 사대문 안(역사도심)에서 더 낮다: 중심 800, 일반 600, 근린·유통 500
SEOUL = {
    "제1종전용주거지역": (50, 100), "제2종전용주거지역": (40, 120),
    "제1종일반주거지역": (60, 150), "제2종일반주거지역": (60, 200), "제3종일반주거지역": (50, 250),
    "준주거지역": (60, 400),
    "중심상업지역": (60, 1000), "일반상업지역": (60, 800), "근린상업지역": (60, 600), "유통상업지역": (60, 600),
    "전용공업지역": (60, 200), "일반공업지역": (60, 200), "준공업지역": (60, 400),
    "보전녹지지역": (20, 50), "생산녹지지역": (20, 50), "자연녹지지역": (20, 50),
}
SUNLIGHT_ZONES = ("전용주거지역", "일반주거지역")   # 건축법 제61조 제1항: 정북방향 일조 사선

# 지역·지구·구역 이름에 이 말이 들어 있으면 확인할 것
WATCH = [
    ("지구단위계획", "지구단위계획 결정사항(건폐율·용적률·높이·용도·배치)이 일반 기준보다 우선합니다. 결정도서를 확인하세요."),
    ("고도지구", "도시관리계획으로 정한 최고 높이 제한이 있습니다."),
    ("경관지구", "높이·층수·형태·색채 제한이 있을 수 있습니다(시 조례·경관계획)."),
    ("방화지구", "주요구조부·외벽을 내화구조로 해야 합니다(건축법 제51조)."),
    ("방재지구", "재해 저감 대책(건축 제한)이 있을 수 있습니다."),
    ("역사문화환경", "국가유산 주변 현상변경 허용기준(높이·앙각)을 확인하세요."),
    ("문화재", "국가유산 주변 현상변경 허용기준(높이·앙각)을 확인하세요."),
    ("국가유산", "국가유산 주변 현상변경 허용기준(높이·앙각)을 확인하세요."),
    ("정비구역", "정비계획(재개발·재건축)이 우선합니다."),
    ("재정비촉진", "재정비촉진계획이 우선합니다."),
    ("개발제한구역", "개발제한구역: 원칙적으로 건축이 제한됩니다."),
    ("비행안전", "비행안전구역 높이 제한이 있습니다(군사기지법)."),
    ("비행장", "비행장 주변 높이 제한을 확인하세요."),
    ("대공방어", "대공방어협조구역: 일정 높이 이상은 군 협의가 필요합니다."),
    ("군사시설", "군사기지·시설 보호구역: 군 협의가 필요할 수 있습니다."),
    ("교육환경보호", "교육환경보호구역: 일부 용도(유흥·숙박 등)가 제한됩니다."),
    ("상수원", "상수원보호구역: 건축·용도 제한이 큽니다."),
    ("하천", "하천구역·하천 접도 제한을 확인하세요."),
    ("도로", "도시계획시설(도로)에 저촉되면 그 부분에는 건축이 제한됩니다."),
    ("광로", "도시계획시설(도로): 저촉이면 그 부분 건축 제한, 접합이면 접도 조건으로 참고."),
    ("대로", "도시계획시설(도로): 저촉이면 그 부분 건축 제한, 접합이면 접도 조건으로 참고."),
    ("중로", "도시계획시설(도로): 저촉이면 그 부분 건축 제한, 접합이면 접도 조건으로 참고."),
    ("소로", "도시계획시설(도로): 저촉이면 그 부분 건축 제한, 접합이면 접도 조건으로 참고."),
    ("공원", "도시계획시설(공원)에 저촉되면 건축이 제한됩니다."),
    ("가축사육제한", "축사 등 가축 사육 시설 제한(참고)."),
    ("토지거래", "토지거래허가구역: 매매에 허가가 필요합니다(참고)."),
    ("과밀억제", "수도권 과밀억제권역: 과밀부담금 등(참고)."),
]
CONFLICT = {"1": "포함", "2": "저촉", "3": "접합"}


# ---------------------------------------------------------------- 조회
def _vw(url, q, key, domain):
    q = dict(q, key=key)
    if domain:
        q["domain"] = domain
    raw = _get(url + "?" + urllib.parse.urlencode(q))
    try:
        return json.loads(raw)
    except Exception:
        raise ApiError("브이월드 응답을 읽지 못했습니다: " + " ".join(raw[:200].decode("utf-8", "replace").split()))


def geocode(address, key, domain=""):
    """주소 → (경도, 위도, 정제된 주소). 지번 주소를 먼저, 안 되면 도로명으로."""
    last = None
    for typ in ("parcel", "road"):
        res = _vw(GEOCODER, dict(service="address", request="getcoord", version="2.0", crs="epsg:4326",
                                 address=address, type=typ, format="json", refine="true", simple="false"),
                  key, domain).get("response", {})
        st = res.get("status")
        if st == "OK":
            p = res["result"]["point"]
            refined = (res.get("refined") or {}).get("text") or address
            return float(p["x"]), float(p["y"]), refined
        if st == "ERROR":
            err = res.get("error", {})
            raise ApiError(f"브이월드 지오코더: {err.get('code', '')} {err.get('text', '')}".strip())
        last = st
    raise ApiError(f"주소를 찾지 못했습니다: {address} ({last})")


def parcel_at(lon, lat, key, domain=""):
    """좌표가 든 필지. 반환: (GeoJSON geometry(경위도), pnu, 지번주소)"""
    d = 0.00002
    feats = vworld_features(key, "LP_PA_CBND_BUBUN", (lon - d, lat - d, lon + d, lat + d), domain, size=50, max_pages=1)
    from shapely.geometry import Point, shape
    pt = Point(lon, lat)
    best = None
    for f in feats:
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if g.contains(pt):
            best = f
            break
        if best is None or g.distance(pt) < shape(best["geometry"]).distance(pt):
            best = f
    if not best:
        raise ApiError("이 위치의 필지(연속지적도)를 찾지 못했습니다.")
    p = best.get("properties", {})
    return best["geometry"], str(p.get("pnu") or ""), p.get("addr") or p.get("jibun") or ""


def _ned(op, list_key, pnu, key, domain):
    res = _vw(NED + op, dict(pnu=pnu, format="json", numOfRows=1000, pageNo=1), key, domain)
    body = res.get(list_key) or {}
    if not body:
        err = res.get("response", {}) or res
        msg = json.dumps(err, ensure_ascii=False)[:200]
        raise ApiError(f"브이월드 {op}: 결과가 없습니다 ({msg}). 인증키에 이 API가 추가돼 있는지 확인하세요.")
    rows = body.get("field") or []
    return rows if isinstance(rows, list) else [rows]


def land_use(pnu, key, domain=""):
    """토지이용계획: [{이름, 저촉, 코드, 등록일}]"""
    rows = _ned("getLandUseAttr", "landUses", pnu, key, domain)
    out = []
    for r in rows:
        out.append(dict(name=str(r.get("prposAreaDstrcCodeNm") or "").strip(), code=r.get("prposAreaDstrcCode"),
                        conflict=CONFLICT.get(str(r.get("cnflcAt")), str(r.get("cnflcAtNm") or r.get("cnflcAt") or "")),
                        date=r.get("registDt") or r.get("lastUpdtDt")))
    return [o for o in out if o["name"]]


def land_character(pnu, key, domain=""):
    """토지특성: 가장 최근 연도 한 건. {지목, 면적, 용도지역1·2, 이용상황, 지형높이, 지형형상, 도로접면, 공시지가, 기준연도}"""
    rows = _ned("getLandCharacteristics", "landCharacteristicss", pnu, key, domain)
    if not rows:
        return {}
    r = max(rows, key=lambda x: str(x.get("stdrYear") or ""))

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return {"지목": r.get("lndcgrCodeNm"), "면적": num(r.get("lndpclAr")), "용도지역1": r.get("prposArea1Nm"),
            "용도지역2": r.get("prposArea2Nm"), "이용상황": r.get("ladUseSittnNm"), "지형높이": r.get("tpgrphHgCodeNm"),
            "지형형상": r.get("tpgrphFrmCodeNm"), "도로접면": r.get("roadSideCodeNm"),
            "공시지가": num(r.get("pblntfPclnd")), "기준연도": r.get("stdrYear"), "주소": r.get("ldCodeNm")}


def lookup(address, key, domain="", cache_dir=None):
    """주소 하나의 원자료 전부. 캐시가 있으면 다시 쓴다."""
    fn = None
    if cache_dir:
        d = os.path.join(cache_dir, "landlaw")
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, re.sub(r"[^\w가-힣-]+", "_", address)[:80] + ".json")
        if os.path.exists(fn):
            return json.load(open(fn, encoding="utf-8"))
    lon, lat, refined = geocode(address, key, domain)
    geom, pnu, jibun = parcel_at(lon, lat, key, domain)
    raw = dict(address=address, refined=refined, lonlat=[lon, lat], pnu=pnu, jibun=jibun, geometry=geom,
               land_use=land_use(pnu, key, domain), character=land_character(pnu, key, domain))
    if fn:
        json.dump(raw, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
    return raw


# ---------------------------------------------------------------- 검토
def _zone(raw):
    names = [u["name"] for u in raw.get("land_use", [])] + \
            [raw.get("character", {}).get(k) or "" for k in ("용도지역1", "용도지역2")]
    for n in names:
        for z in NATIONAL:
            if z in n.replace(" ", ""):
                return z
    return None


def _area(raw):
    a = (raw.get("character") or {}).get("면적")
    if a:
        return a, "토지특성(공부상 면적)"
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        g = shape(raw["geometry"])
        return abs(Geod(ellps="GRS80").geometry_area_perimeter(g)[0]), "연속지적도 경계에서 계산"
    except Exception:
        return None, ""


def review(raw):
    """원자료 → 검토 결과 dict."""
    pnu = raw.get("pnu", "")
    seoul = pnu.startswith("11")
    zone = _zone(raw)
    area, area_src = _area(raw)
    ch = raw.get("character") or {}
    rows, checks = [], []
    bcr = far = None
    if zone:
        nb, nf = NATIONAL[zone]
        if seoul and zone in SEOUL:
            bcr, far = SEOUL[zone]
            basis = "서울특별시 도시계획 조례 제54조·제55조"
        else:
            bcr, far = nb, nf
            basis = "국토계획법 시행령 제84조·제85조 상한 (시·군 조례가 더 낮을 수 있음)"
            checks.append(f"이 지역의 시·군 도시계획 조례에서 {zone} 건폐율·용적률을 확인하세요(법정 상한 {nb}%/{nf}%를 넣어 두었습니다).")
        rows.append(("용도지역", zone, "토지이용계획"))
        rows.append(("건폐율", f"{bcr}% 이하", basis))
        rows.append(("용적률", f"{far}% 이하", basis))
        if seoul and "상업지역" in zone:
            checks.append("사대문 안(역사도심)이면 서울시 조례상 상업지역 용적률이 더 낮습니다(중심 800·일반 600·근린/유통 500%).")
    else:
        checks.append("용도지역을 찾지 못했습니다. 토지이음에서 직접 확인하세요.")
    if area:
        rows.append(("대지면적", f"{area:,.1f} ㎡", area_src))
        if bcr:
            rows.append(("최대 건축면적", f"{area * bcr / 100:,.1f} ㎡", "대지면적 × 건폐율"))
            rows.append(("최대 연면적(용적률 산정용)", f"{area * far / 100:,.1f} ㎡", "대지면적 × 용적률 (지하·주차 등 제외 면적 별도)"))
    sun = bool(zone and any(k in zone for k in SUNLIGHT_ZONES))
    if sun:
        rows.append(("정북방향 일조 사선", "적용", "건축법 제61조, 시행령 제86조: 높이 10m 이하 1.5m 이상, 10m 초과 부분은 높이의 1/2 이상 띄움"))
        checks.append("정북일조는 북쪽 인접 대지 경계선 기준입니다. 북쪽이 도로·공원 등이면 기준선이 달라질 수 있습니다(시행령 제86조).")
    elif zone:
        rows.append(("정북방향 일조 사선", "해당 없음", "전용·일반주거지역이 아님"))
    jm = ch.get("지목")
    if jm:
        rows.append(("지목", jm, "토지특성"))
        if jm not in ("대", "공장용지", "학교용지", "주차장", "주유소용지", "창고용지", "종교용지"):
            checks.append(f"지목이 '{jm}'입니다. 건축하려면 개발행위허가(형질변경)·지목변경이 필요할 수 있습니다.")
    rs = ch.get("도로접면")
    if rs:
        rows.append(("도로접면", rs, "토지특성"))
        if "맹지" in rs:
            checks.append("맹지입니다. 건축법 제44조(대지는 2m 이상 도로에 접해야 함)를 충족하는지 확인하세요.")
    if ch.get("공시지가"):
        rows.append(("개별공시지가", f"{ch['공시지가']:,.0f} 원/㎡ ({ch.get('기준연도', '')})", "토지특성"))
    uses = []
    for u in raw.get("land_use", []):
        note = next((t for k, t in WATCH if k in u["name"]), "")
        uses.append(dict(u, note=note))
        if note and "(참고)" not in note and u["conflict"] in ("포함", "저촉", ""):
            checks.append(f"{u['name']}({u['conflict'] or '해당'}): {note}")
    seen, uniq = set(), []
    for c in checks:
        if c not in seen:
            seen.add(c); uniq.append(c)
    return dict(address=raw.get("refined") or raw.get("address"), pnu=pnu, jibun=raw.get("jibun"), zone=zone,
                bcr=bcr, far=far, area=area, sunlight=sun, rows=rows, land_use=uses, checks=uniq,
                note="자동 1차 검토입니다. 지구단위계획·정비계획·개별 조례·도시계획시설 결정이 있으면 그것이 우선하므로 "
                     "인허가 전에 토지이음(eum.go.kr)과 해당 지자체에서 꼭 확인하세요.")


def to_markdown(r):
    lines = [f"# 대지 법규 검토: {r['address']}", "", f"PNU {r['pnu']} · {r.get('jibun') or ''}", "",
             "| 항목 | 내용 | 근거 |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in r["rows"]]
    lines += ["", "## 토지이용계획", "", "| 지역·지구·구역 | 저촉 | 메모 |", "|---|---|---|"]
    lines += [f"| {u['name']} | {u['conflict']} | {u['note']} |" for u in r["land_use"]]
    if r["checks"]:
        lines += ["", "## 확인할 것", ""] + [f"- {c}" for c in r["checks"]]
    lines += ["", f"> {r['note']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- 3D: 대지 경계·정북일조 사선
def site_polygon(raw, crs_wkt):
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    t = Transformer.from_crs(4326, crs_wkt, always_xy=True)
    return transform(lambda x, y, z=None: t.transform(x, y), shape(raw["geometry"])).buffer(0)


def sunlight_height(poly, cell=1.0, cap=60.0):
    """정북일조 사선으로 허용되는 높이(대지 기준 지표면에서, m)를 격자로.

    점에서 정북(+y)으로 대지 경계선까지 거리 d: d < 1.5 → 0, 그 밖에는 max(10, 2d) (cap 까지).
    반환: (xs, ys, Z) — 대지 밖은 nan.
    """
    import numpy as np
    from shapely.geometry import LineString, Point
    x0, y0, x1, y1 = poly.bounds
    xs = np.arange(x0 + cell / 2, x1, cell)
    ys = np.arange(y0 + cell / 2, y1, cell)
    Z = np.full((len(ys), len(xs)), np.nan)
    for i, x in enumerate(xs):
        col = poly.intersection(LineString([(x, y0 - 1), (x, y1 + 1)]))
        segs = [col] if col.geom_type == "LineString" else [g for g in getattr(col, "geoms", []) if g.geom_type == "LineString"]
        for s in segs:
            ya, yb = sorted((s.coords[0][1], s.coords[-1][1]))
            for j, y in enumerate(ys):
                if ya <= y <= yb:
                    d = yb - y
                    Z[j, i] = 0.0 if d < 1.5 else min(cap, max(10.0, 2 * d))
    return xs, ys, Z


def envelope_mesh(poly, ground, cell=1.0, cap=60.0):
    """정북일조 사선 윗면 메시(모델 좌표, 원점 이동 전). ground: 대지 기준 높이(m)."""
    import numpy as np
    import trimesh
    xs, ys, Z = sunlight_height(poly, cell, cap)
    if not np.isfinite(Z).any():
        return None
    nx = len(xs)
    V = np.column_stack([np.repeat(xs[None, :], len(ys), 0).ravel(), np.repeat(ys[:, None], nx, 1).ravel(),
                         (ground + np.nan_to_num(Z, nan=0.0)).ravel()])
    ok = np.isfinite(Z).ravel()
    F = []
    for j in range(len(ys) - 1):
        for i in range(nx - 1):
            a, b, c, d = j * nx + i, j * nx + i + 1, (j + 1) * nx + i, (j + 1) * nx + i + 1
            if ok[a] and ok[b] and ok[c] and ok[d]:
                F += [[a, b, d], [a, d, c]]
    if not F:
        return None
    m = trimesh.Trimesh(V, np.array(F), process=False)
    m.remove_unreferenced_vertices()
    return m

"""공공데이터 연결: 브이월드 연속지적도 + 국토교통부 건축물대장(건축HUB) 표제부.

수치지도 건물에는 층수만 있고 높이·준공연도가 없다. 모델 범위 안 필지(PNU)를 브이월드에서 받고,
필지마다 건축물대장 표제부를 조회해 수치지도 건물과 짝지은 뒤 실제 높이·층수·지하층·구조·사용승인일·주용도를 붙인다.

인증키는 사용자 것(화면에 넣어 그 PC에만 저장). 응답은 ~/.contour_ai_cache/publicdata 에 저장해 다시 쓴다.
네트워크·키 문제가 있으면 예외 대신 빈 결과와 이유를 돌려주고, 모델은 수치지도 층수로 계속 만든다.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

VWORLD_DATA = "https://api.vworld.kr/req/data"
# 건축HUB(현행) → 예전 주소 순서로 시도
BLD_TITLE = ["https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo",
             "https://apis.data.go.kr/1613000/BldRgstService_v2/getBrTitleInfo"]
UA = {"User-Agent": "contour-ai/1.0"}


class ApiError(Exception):
    pass


def _get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _cache(cache_dir, *parts):
    d = os.path.join(cache_dir, "publicdata")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "_".join(str(p) for p in parts) + ".json")


# ---------------------------------------------------------------- 브이월드 2D 데이터
def vworld_features(key, layer, bbox_ll, domain="", size=1000, max_pages=30):
    """브이월드 2D데이터 API GetFeature. bbox_ll = (경도min, 위도min, 경도max, 위도max). 반환: GeoJSON features"""
    feats = []
    for page in range(1, max_pages + 1):
        q = dict(service="data", request="GetFeature", data=layer, key=key, format="json", size=size, page=page,
                 geometry="true", attribute="true", crs="EPSG:4326",
                 geomFilter="BOX({:.7f},{:.7f},{:.7f},{:.7f})".format(*bbox_ll))
        if domain:
            q["domain"] = domain
        raw = _get(VWORLD_DATA + "?" + urllib.parse.urlencode(q))
        try:
            res = json.loads(raw)["response"]
        except Exception:
            raise ApiError("브이월드 응답을 읽지 못했습니다: " + raw[:200].decode("utf-8", "replace"))
        st = res.get("status")
        if st == "NOT_FOUND":
            break
        if st != "OK":
            err = res.get("error", {})
            raise ApiError(f"브이월드 {layer}: {err.get('code', st)} {err.get('text', '')}".strip())
        fc = res["result"]["featureCollection"]
        feats += fc.get("features", [])
        total = int(res.get("page", {}).get("total", 1) or 1)
        if page >= total:
            break
    return feats


def parcels(key, bbox, crs_wkt, domain="", cache_dir=None):
    """범위 안 연속지적도 필지. 반환: [(shapely 다각형(모델 좌표), pnu, 지번)]"""
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    to_ll = Transformer.from_crs(crs_wkt, 4326, always_xy=True)
    to_m = Transformer.from_crs(4326, crs_wkt, always_xy=True)
    lo, la = to_ll.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    bb = (min(lo), min(la), max(lo), max(la))
    fn = _cache(cache_dir, "parcels", *[int(v) for v in bbox]) if cache_dir else None
    if fn and os.path.exists(fn):
        feats = json.load(open(fn, encoding="utf-8"))
    else:
        feats = vworld_features(key, "LP_PA_CBND_BUBUN", bb, domain)
        if fn:
            json.dump(feats, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
    out = []
    for f in feats:
        p = f.get("properties", {})
        pnu = str(p.get("pnu") or "")
        if len(pnu) != 19:
            continue
        try:
            g = transform(lambda x, y, z=None: to_m.transform(x, y), shape(f["geometry"]))
        except Exception:
            continue
        out.append((g.buffer(0), pnu, p.get("jibun", "")))
    return out


# ---------------------------------------------------------------- 건축물대장 표제부
def _service_key(key):
    # 포털의 '인코딩' 키(이미 %가 들어 있음)는 그대로, '디코딩' 키는 인코딩해서
    return key if "%" in key else urllib.parse.quote(key, safe="")


def _register(key, pnu, op, keep, cache_dir=None):
    """필지(PNU) 하나의 건축물대장 목록(op = getBrTitleInfo, getBrFlrOulnInfo …). 반환: [dict]"""
    fn = _cache(cache_dir, op, pnu) if cache_dir else None
    if op == "getBrTitleInfo" and cache_dir:
        fn = _cache(cache_dir, "title", pnu)
    if fn and os.path.exists(fn):
        return json.load(open(fn, encoding="utf-8"))
    sigungu, bjdong, plat, bun, ji = pnu[0:5], pnu[5:10], pnu[10], pnu[11:15], pnu[15:19]
    plat_gb = "1" if plat == "2" else "0"   # PNU 산=2 → 대장 대지구분 산=1
    items, last = [], None
    for base in BLD_TITLE:
        base = base.rsplit("/", 1)[0] + "/" + op
        items = []
        try:
            for page in range(1, 20):
                q = urllib.parse.urlencode(dict(sigunguCd=sigungu, bjdongCd=bjdong, platGbCd=plat_gb, bun=bun, ji=ji,
                                                numOfRows=100, pageNo=page, _type="json"))
                raw = _get(f"{base}?serviceKey={_service_key(key)}&{q}")
                if raw.lstrip()[:1] == b"<":
                    txt = raw[:400].decode("utf-8", "replace")
                    raise ApiError("건축물대장 오류 응답: " + " ".join(txt.split())[:200])
                body = json.loads(raw)["response"]
                head = body.get("header", {})
                if str(head.get("resultCode", "00")) not in ("00", "0", "000"):
                    raise ApiError(f"건축물대장 {head.get('resultCode')}: {head.get('resultMsg')}")
                b = body.get("body", {})
                it = (b.get("items") or {}) if isinstance(b.get("items"), dict) else {}
                it = it.get("item", [])
                items += it if isinstance(it, list) else [it]
                if len(items) >= int(b.get("totalCount", 0) or 0):
                    break
            last = None
            break
        except ApiError as e:
            last = e
        except Exception as e:
            last = ApiError(f"{type(e).__name__}: {e}")
    if last is not None:
        raise last
    items = [{k: i.get(k) for k in keep} for i in items if isinstance(i, dict)]
    if fn:
        json.dump(items, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
    return items


def title_info(key, pnu, cache_dir=None):
    """표제부: 동마다 높이·층수·구조·사용승인일."""
    return _register(key, pnu, "getBrTitleInfo",
                     ("dongNm", "bldNm", "heit", "grndFlrCnt", "ugrndFlrCnt", "strctCdNm", "useAprDay", "mainPurpsCdNm",
                      "archArea", "totArea", "regstrKindCdNm", "roofCdNm", "mgmBldrgstPk", "mainAtchGbCdNm"), cache_dir)


def floor_info(key, pnu, cache_dir=None):
    """층별개요: 층마다 면적·용도. 위층이 좁아지는(셋백) 건물과 옥탑 크기를 알 수 있다."""
    return _register(key, pnu, "getBrFlrOulnInfo",
                     ("mgmBldrgstPk", "dongNm", "flrGbCdNm", "flrNo", "flrNoNm", "area", "mainPurpsCdNm",
                      "etcPurps", "mainAtchGbCdNm"), cache_dir)


def floor_areas(rows, pk=None):
    """층별개요 줄 → ({지상층번호: 면적}, 옥탑면적). 주건축물만."""
    above, roof = {}, 0.0
    for r in rows:
        if pk and r.get("mgmBldrgstPk") and str(r.get("mgmBldrgstPk")) != str(pk):
            continue
        if "부속" in str(r.get("mainAtchGbCdNm") or ""):
            continue
        gb, nm, a = str(r.get("flrGbCdNm") or ""), str(r.get("flrNoNm") or ""), _num(r.get("area"))
        if a <= 0:
            continue
        if "옥탑" in gb or "옥탑" in nm:
            roof += a
        elif "지하" in gb:
            continue
        else:
            k = int(_num(r.get("flrNo")))
            if k >= 1:
                above[k] = above.get(k, 0.0) + a
    return above, roof


def tiers_from_floors(poly, above):
    """층별 면적 → [(평면, 시작층, 끝층(제외), 전체층수)] — 높이는 그릴 때 건물 바닥·꼭대기로 나눠 정한다. 위층 면적이 1층의 85% 아래로 줄면 북쪽부터 잘라 낸다(정북일조 셋백이 가장 흔함).
    층별 면적이 고르면 None."""
    from shapely.geometry import box
    if len(above) < 2:
        return None
    n = max(above)
    a1 = above.get(1) or max(above.values())
    ratios = [min(1.0, above.get(k, above.get(k - 1, a1)) / a1) for k in range(1, n + 1)]
    if min(ratios) > 0.85 or min(ratios) < 0.15:
        return None
    # 비슷한 비율끼리 묶기
    groups = []
    for k, r in enumerate(ratios):
        r = 1.0 if r > 0.9 else round(r, 2)
        if groups and abs(groups[-1][0] - r) < 0.08:
            groups[-1][1].append(k)
        else:
            groups.append([r, [k]])
    # 아래층보다 넓어지면 아래층 비율로 (평면은 위로 갈수록 줄기만)
    for i in range(1, len(groups)):
        groups[i][0] = min(groups[i][0], groups[i - 1][0])
    minx, miny, maxx, maxy = poly.bounds
    out = []
    for r, ks in groups:
        if r >= 0.999:
            p = poly
        else:
            target, lo, hi = poly.area * r, miny, maxy
            for _ in range(30):
                mid = (lo + hi) / 2
                if poly.intersection(box(minx - 1, miny - 1, maxx + 1, mid)).area < target:
                    lo = mid
                else:
                    hi = mid
            p = poly.intersection(box(minx - 1, miny - 1, maxx + 1, hi))
            if p.geom_type != "Polygon":
                p = max(getattr(p, "geoms", [p]), key=lambda g: g.area)
        out.append((p, ks[0], ks[-1] + 1, n))
    return out if len(out) > 1 else None


# ---------------------------------------------------------------- 수치지도 건물과 짝짓기
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dong_key(s):
    import re
    m = re.search(r"(\d+)\s*동", str(s or ""))
    return m.group(1) if m else None


def match(masses, parcel_list, titles_by_pnu):
    """건물마다 대장 표제부 하나를 짝짓는다. 반환: {mass index: 표제부 dict}

    1. 건물 대표점이 든 필지의 표제부만 후보(총괄표제부 제외)
    2. 동 번호(수치지도 명칭·주기의 '101동' ↔ 대장 동명칭)가 같으면 그것
    3. 아니면 건축면적이 발자국 넓이와 가장 가까운 것(±40% 안)
    """
    import shapely
    from shapely.strtree import STRtree
    polys = [p for p, _, _ in parcel_list]
    if not polys:
        return {}
    tree = STRtree(polys)
    by_parcel = {}
    for i, m in enumerate(masses):
        pt = m["poly"].representative_point()
        hit = [j for j in tree.query(pt) if polys[j].contains(pt)]
        if hit:
            by_parcel.setdefault(parcel_list[hit[0]][1], []).append(i)
    out = {}
    for pnu, idx in by_parcel.items():
        cands = [t for t in titles_by_pnu.get(pnu, []) if "총괄" not in str(t.get("regstrKindCdNm") or "")]
        if not cands:
            continue
        used = set()
        # 동 번호
        for i in idx:
            at = masses[i].get("attrs", {})
            k = _dong_key(at.get("명칭")) or _dong_key(at.get("주기")) or _dong_key(masses[i].get("name"))
            if not k:
                continue
            for c, t in enumerate(cands):
                if c not in used and _dong_key(t.get("dongNm")) == k:
                    out[i] = t; used.add(c); break
        # 하나뿐이면 그대로
        rest = [i for i in idx if i not in out]
        free = [c for c in range(len(cands)) if c not in used]
        if len(rest) == 1 and len(free) == 1:
            out[rest[0]] = cands[free[0]]
            continue
        # 건축면적
        pairs = []
        for i in rest:
            a = masses[i]["poly"].area
            for c in free:
                ar = _num(cands[c].get("archArea"))
                if ar > 0 and abs(ar - a) / max(a, ar) < 0.4:
                    pairs.append((abs(ar - a) / max(a, ar), i, c))
        for _, i, c in sorted(pairs):
            if i not in out and c not in used:
                out[i] = cands[c]; used.add(c)
    return out


def enrich(masses, bbox, crs_wkt, vworld_key, data_key, domain="", cache_dir=None, floor_height=3.3, log=print,
           workers=8):
    """masses 에 건축물대장 정보를 붙이고 높이를 실제 값으로 바꾼다. 반환: 요약 dict"""
    stats = dict(parcels=0, parcels_queried=0, matched=0, height_from_register=0, errors=[])
    try:
        pl = parcels(vworld_key, bbox, crs_wkt, domain, cache_dir)
    except Exception as e:
        stats["errors"].append(f"연속지적도: {e}")
        log(f"   브이월드 연속지적도를 받지 못해 건축물대장은 생략합니다: {e}")
        return stats
    stats["parcels"] = len(pl)
    # 건물이 있는 필지만 조회
    import shapely
    from shapely.strtree import STRtree
    tree = STRtree([p for p, _, _ in pl])
    need = set()
    for m in masses:
        pt = m["poly"].representative_point()
        for j in tree.query(pt):
            if pl[j][0].contains(pt):
                need.add(pl[j][1])
    log(f"   건축물대장 조회: 필지 {len(need)}곳 (연속지적도 {len(pl)}필지)")
    titles, errs = {}, []

    def one(pnu):
        try:
            return pnu, title_info(data_key, pnu, cache_dir), None
        except Exception as e:
            return pnu, [], str(e)
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for pnu, items, err in ex.map(one, sorted(need)):
            for it in items:
                it["_pnu"] = pnu
            titles[pnu] = items
            if err:
                errs.append(err)
            if len(errs) >= 5 and not any(titles.values()):
                break   # 키가 틀린 경우: 계속 두드리지 않는다
    stats["parcels_queried"] = len(titles)
    if errs:
        stats["errors"] += sorted(set(errs))[:3]
        log(f"   건축물대장 조회 오류 {len(errs)}건 (예: {errs[0]})")
    pairs = match(masses, pl, titles)
    for i, t in pairs.items():
        m = masses[i]
        h = _num(t.get("heit"))
        fl = int(_num(t.get("grndFlrCnt")))
        reg = {"대장_동명칭": t.get("dongNm") or "", "대장_건물명": t.get("bldNm") or "",
               "대장_높이(m)": h or "", "대장_지상층수": fl or "", "대장_지하층수": int(_num(t.get("ugrndFlrCnt"))) or "",
               "대장_구조": t.get("strctCdNm") or "", "대장_사용승인일": t.get("useAprDay") or "",
               "대장_주용도": t.get("mainPurpsCdNm") or "", "대장_지붕": t.get("roofCdNm") or "", "대장_주부속": t.get("mainAtchGbCdNm") or ""}
        m.setdefault("attrs", {}).update({k: str(v) for k, v in reg.items() if v != ""})
        if fl:
            m["floors"] = fl
        new_h = h if h >= 2 else (fl * floor_height if fl else 0)
        if new_h and m.get("layer") != "무벽건물":
            ground = m["top"] - m.get("height", m["top"] - m["base"])
            m["height"] = new_h
            m["top"] = ground + new_h
            m["source"] = "register" if h >= 2 else "register_floors"
            if h >= 2 and fl:
                # 실제 높이를 지상층수로 나눈 이 건물의 층고(파라펫 1m 정도를 뺌) → 창·층선이 실제 층 높이에 맞는다
                fh = (h - (1.0 if h / fl > 3.0 else 0.0)) / fl
                m["floor_h"] = round(min(max(fh, 2.3), 6.0), 2)
                m.setdefault("attrs", {})["대장_층고(m)"] = f"{m['floor_h']:.2f}"
            stats["height_from_register"] += 1
        y = str(t.get("useAprDay") or "")[:4]
        if y.isdigit():
            m["year"] = int(y)
    stats["matched"] = len(pairs)
    stats["setback"] = _apply_floors(masses, pairs, data_key, cache_dir, floor_height, log, workers)
    log(f"   건축물대장과 짝지은 건물 {len(pairs)}/{len(masses)}동, 실제 높이 반영 {stats['height_from_register']}동 "
        f"({time.time() - t0:.0f}초)")
    return stats


def _apply_floors(masses, pairs, data_key, cache_dir, floor_height, log, workers=8):
    """층별개요로 셋백(위층이 좁아짐)과 옥탑 면적을 반영. 반환: 셋백 반영 동 수."""
    want = {}
    for i, t in pairs.items():
        m = masses[i]
        if m.get("layer") == "무벽건물" or int(m.get("floors") or 0) < 2 or not t.get("_pnu"):
            continue
        want.setdefault(t["_pnu"], []).append(i)
    if not want:
        return 0
    rows, errs = {}, []

    def one(pnu):
        try:
            return pnu, floor_info(data_key, pnu, cache_dir), None
        except Exception as e:
            return pnu, [], str(e)
    with ThreadPoolExecutor(workers) as ex:
        for pnu, it, err in ex.map(one, sorted(want)):
            rows[pnu] = it
            if err:
                errs.append(err)
    if errs and not any(rows.values()):
        log(f"   층별개요를 받지 못함(셋백 생략): {errs[0]}")
        return 0
    n = 0
    for pnu, idx in want.items():
        for i in idx:
            m, t = masses[i], pairs[i]
            above, roof = floor_areas(rows.get(pnu, []), t.get("mgmBldrgstPk"))
            if roof:
                m["rooftop_area"] = roof
                m.setdefault("attrs", {})["대장_옥탑면적(㎡)"] = f"{roof:.1f}"
            if above:
                m.setdefault("attrs", {})["대장_층별면적(㎡)"] = ", ".join(f"{k}층 {a:.0f}" for k, a in sorted(above.items()))
            tiers = tiers_from_floors(m["poly"], above)
            if tiers:
                m["tiers"] = tiers
                m["attrs"]["셋백"] = "층별개요 면적으로 북쪽부터 줄임(위치는 추정)"
                n += 1
    log(f"   층별개요: 위층이 좁아지는 건물 {n}동 셋백 반영")
    return n


def selftest(bbox, crs_wkt, vworld_key="", data_key="", domain=""):
    """화면의 '연결 시험': 키마다 한 번씩 불러 보고 결과를 문장으로."""
    out = []
    if vworld_key:
        try:
            from .aerial import VWORLD_URL
            import math
            from pyproj import Transformer
            lo, la = Transformer.from_crs(crs_wkt, 4326, always_xy=True).transform((bbox[0] + bbox[2]) / 2,
                                                                                    (bbox[1] + bbox[3]) / 2)
            z = 18
            x = int((lo + 180) / 360 * 2 ** z)
            y = int((1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * 2 ** z)
            url = VWORLD_URL.format(key=vworld_key, z=z, y=y, x=x) + (f"?domain={urllib.parse.quote(domain)}" if domain else "")
            data = _get(url)
            ok = data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"
            out.append(("브이월드 영상지도(항공사진)", ok, f"사진 {len(data) // 1024}KB 받음" if ok
                        else data[:200].decode("utf-8", "replace")))
        except Exception as e:
            out.append(("브이월드 영상지도(항공사진)", False, str(e)))
        try:
            pl = parcels(vworld_key, bbox, crs_wkt, domain)
            out.append(("브이월드 연속지적도", bool(pl), f"필지 {len(pl)}개"))
        except Exception as e:
            pl = []
            out.append(("브이월드 연속지적도", False, str(e)))
        # 대지 법규 검토에 쓰는 API (인증키에 따로 추가해야 함)
        from . import landlaw
        if pl:
            pnu = pl[len(pl) // 2][1]
            for nm, fn in (("브이월드 토지이용계획정보", landlaw.land_use), ("브이월드 토지특성정보", landlaw.land_character)):
                try:
                    r = fn(pnu, vworld_key, domain)
                    out.append((nm, bool(r), f"필지 {pl[len(pl) // 2][2] or pnu}: "
                                + (f"{len(r)}건" if isinstance(r, list) else f"지목 {r.get('지목')}, {r.get('용도지역1')}")))
                except Exception as e:
                    out.append((nm, False, str(e)))
        try:
            c = pl[len(pl) // 2][0].representative_point() if pl else None
            if c is not None:
                from pyproj import Transformer
                lo, la = Transformer.from_crs(crs_wkt, 4326, always_xy=True).transform(c.x, c.y)
                q = dict(service="address", request="getAddress", version="2.0", crs="epsg:4326",
                         point=f"{lo},{la}", type="parcel", format="json", key=vworld_key)
                if domain:
                    q["domain"] = domain
                res = json.loads(_get(landlaw.GEOCODER + "?" + urllib.parse.urlencode(q))).get("response", {})
                ok = res.get("status") == "OK"
                msg = (res.get("result") or [{}])[0].get("text", "") if ok else json.dumps(res.get("error") or res.get("status"), ensure_ascii=False)
                out.append(("브이월드 지오코더(주소)", ok, msg))
        except Exception as e:
            out.append(("브이월드 지오코더(주소)", False, str(e)))
    else:
        pl = []
        out.append(("브이월드", False, "인증키가 비어 있습니다"))
    if data_key:
        pnu = pl[len(pl) // 2][1] if pl else None
        if not pnu:
            out.append(("건축물대장", False, "시험할 필지가 없습니다(브이월드 연속지적도가 먼저 되어야 함)"))
        else:
            try:
                n = 0
                for p in pl[len(pl) // 2:len(pl) // 2 + 15]:
                    n = len(title_info(data_key, p[1]))
                    if n:
                        out.append(("건축물대장", True, f"필지 {p[2] or p[1]}: 표제부 {n}건"))
                        try:
                            fl = floor_info(data_key, p[1])
                            out.append(("건축물대장 층별개요", True, f"같은 필지 층별 {len(fl)}줄 (셋백·옥탑 크기에 씀)"))
                        except Exception as e:
                            out.append(("건축물대장 층별개요", False, f"{e} — 공공데이터포털에서 건축물대장정보 서비스 활용신청에 층별개요가 포함됐는지 확인"))
                        break
                else:
                    out.append(("건축물대장", True, "응답은 정상, 시험한 필지에 건물 없음"))
            except Exception as e:
                out.append(("건축물대장", False, str(e)))
    else:
        out.append(("건축물대장", False, "공공데이터포털 인증키가 비어 있습니다"))
    return [dict(name=a, ok=b, msg=c) for a, b, c in out]

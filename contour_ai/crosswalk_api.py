"""횡단보도 위치를 공공 API에서 자동으로 받는다.

1. 서울 열린데이터광장 「서울시 자치구별 도보 네트워크 공간정보」(OA-21208, WGS84 노드·링크)
   링크 유형이 횡단보도인 선 = 보도에서 보도(또는 교통섬)로 건너는 실제 선. 가장 정확하다.
   서울 전체를 1000줄씩 받아 횡단보도 링크만 캐시(~/.contour_ai_cache/seoul_crosswalk_<서비스>.json)에 남긴다.
2. 행정안전부 생활안전지도 「도로시설(횡단보도)」·「보안등」 WMS (전국, 국토지리정보원 원천)
   범위의 지도 그림(PNG)을 받아 칠해진 화소를 면으로 바꾼다 → 횡단보도 모양 그대로.

API 설명 페이지가 자동 읽기를 막고 있어 서비스 이름·레이어 이름을 확인하지 못했다.
화면에서 직접 적을 수 있고(고급), 비우면 아래 후보를 차례로 시도한다. 실패 이유는 로그와 '연결 시험'에 그대로 나온다.
인증키는 사용자 것(화면에 넣어 그 PC 브라우저에만 저장).
"""
import io
import json
import math
import os
import urllib.parse
import urllib.request

import numpy as np
from shapely.geometry import LineString, Polygon, box, shape
from shapely import wkt as shp_wkt

SEOUL_URL = "http://openapi.seoul.go.kr:8088/{key}/json/{svc}/{s}/{e}/"
SEOUL_SERVICES = ("TbTraficWlkNet", "tbTraficWlkNet", "TbTraficWalkNet")
# 생활안전지도 오픈API2: 서비스마다 주소가 따로 있다 → http://www.safemap.go.kr/openapi2/<서비스ID>_WMS?serviceKey=…
#   (사용자 화면 'My Data' 예시로 확인, 2026-11-03). 매개변수 이름은 serviceKey 만 대소문자 그대로, 나머지는 소문자.
SAFEMAP_BASES = ("http://www.safemap.go.kr/openapi2/{sid}_WMS", "https://www.safemap.go.kr/openapi2/{sid}_WMS",
                 "http://safemap.go.kr/openapi2/{sid}_WMS")
# (서비스ID, layername, styles)
SAFEMAP_LIGHT = ("IF_0102", "A2SM_CMMNPOI_SECULIGHT", "A2SM_CMMNPOI_07")      # 보안등 (확인됨)
# 도로시설(횡단보도): XML 정보조회 서비스가 IF_0097(출력에 좌표 칸이 없음) → 같은 번호의 WMS 를 쓴다.
#   보안등 예시처럼 레이어·스타일 없이 서비스ID만으로 그림이 오는지부터 시도. 레이어 이름을 알면 '고급'에 적는다.
SAFEMAP_CROSSWALK = ("IF_0097", "", "")
SAFEMAP_LAYERS = ()
SAFEMAP_LIGHT_LAYERS = ()
UA = {"User-Agent": "contour-ai/1.0"}


def _get(url, timeout=40):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _cache_dir(cache_dir=None):
    d = cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 1. 서울 도보 네트워크
def _seoul_page(key, svc, s, e):
    raw = _get(SEOUL_URL.format(key=urllib.parse.quote(key), svc=svc, s=s, e=e))
    j = json.loads(raw)
    if svc not in j:
        r = j.get("RESULT") or {}
        raise RuntimeError(f"{svc}: {r.get('CODE', '')} {r.get('MESSAGE', raw[:150].decode('utf-8', 'replace'))}".strip())
    body = j[svc]
    res = body.get("RESULT") or {}
    if res.get("CODE") not in (None, "INFO-000"):
        raise RuntimeError(f"{svc}: {res.get('CODE')} {res.get('MESSAGE', '')}")
    return int(body.get("list_total_count") or 0), body.get("row") or []


CROSS_KEYS = ("CRSWK", "CRSWLK", "CRS_WK", "CROSSWALK", "CRSWALK")
TRUE_VALS = ("1", "Y", "TRUE", "T", "예")


def _is_crosswalk_row(row, code=None):
    """횡단보도 링크인가. ① 사용자가 준 코드(LNKG_TYPE_CD 등 값과 일치) ② 이름에 CRSWK 가 든 칸이 1/Y ③ 값에 '횡단'."""
    if str(row.get("NODE_TYPE") or "").upper() == "NODE":
        return False
    if code:
        return any(str(v).strip() == str(code) for v in row.values() if v is not None)
    for k, v in row.items():
        if any(c in str(k).upper() for c in CROSS_KEYS) and str(v).strip().upper() in TRUE_VALS:
            return True
    return any("횡단" in str(v) for v in row.values() if v is not None)


def _row_wkt(row):
    for v in row.values():
        t = str(v or "").strip().upper()
        if t.startswith(("LINESTRING", "MULTILINESTRING")):
            return str(v)
    return None


# 도보 네트워크 링크의 0/1 표시 칸 → 우리가 쓰는 종류
LINK_FLAGS = {"tunnel": ("TNL",), "subway": ("SBWY_NTW",), "overpass": ("OVRP",), "bridge": ("BRG",)}


def _flag(row, names):
    return any(str(row.get(k, "")).strip().upper() in TRUE_VALS for k in names)


def seoul_links(key, bbox, crs_wkt, service=None, code=None, cache_dir=None, log=print, page=1000,
                max_rows=2_000_000):
    """서울 도보 네트워크 → {종류: [LineString(모델 좌표)]} — crosswalk(횡단보도), tunnel(지하보도),
    subway(지하철 연결통로), overpass(육교), bridge(교량). 서울 전체를 처음 한 번 받아 종류별로 저장해 둔다."""
    from pyproj import Transformer
    from shapely.ops import transform
    svcs = [service] if service else list(SEOUL_SERVICES)
    last = None
    for svc in svcs:
        fn = os.path.join(_cache_dir(cache_dir), f"seoul_links3_{svc}_{code or 'auto'}.json")
        kinds = None
        if os.path.exists(fn):
            try:
                kinds = json.load(open(fn, encoding="utf-8"))
            except Exception:
                kinds = None
        if kinds is None:
            try:
                total, rows = _seoul_page(key, svc, 1, page)
            except Exception as e:
                last = e
                continue
            log(f"   서울 도보 네트워크({svc}) {total:,}줄에서 횡단보도·지하보도·지하철 통로를 고르는 중 (처음 한 번만, 몇 분 걸림)")
            kinds = {"crosswalk": [], **{k: [] for k in LINK_FLAGS}}
            s = 1
            while True:
                for r in rows:
                    if str(r.get("NODE_TYPE") or "").upper() == "NODE":
                        continue
                    w = _row_wkt(r)
                    if not w:
                        continue
                    if _is_crosswalk_row(r, code):
                        kinds["crosswalk"].append(w)
                    for k, names in LINK_FLAGS.items():
                        if _flag(r, names):
                            kinds[k].append(w)
                s += page
                if s > min(total, max_rows):
                    break
                _, rows = _seoul_page(key, svc, s, s + page - 1)
            if any(kinds.values()):     # 비었으면 저장하지 않는다(규칙을 고친 뒤 다시 받도록)
                json.dump(kinds, open(fn, "w", encoding="utf-8"))
        tf = Transformer.from_crs("EPSG:4326", crs_wkt, always_xy=True).transform
        clip = box(*bbox)
        out = {}
        for k, lines in kinds.items():
            out[k] = []
            for w in lines:
                try:
                    g = transform(tf, shp_wkt.loads(w))
                except Exception:
                    continue
                for ln in getattr(g, "geoms", [g]):
                    if ln.intersects(clip) and ln.length > 1:
                        out[k].append(ln)
        log("   서울 도보 네트워크: " + ", ".join(f"{KIND_KO[k]} {len(v)}" for k, v in out.items()))
        return out
    raise RuntimeError(f"서울 도보 네트워크 API를 부르지 못함: {last}")


KIND_KO = {"crosswalk": "횡단보도", "tunnel": "지하보도", "subway": "지하철 통로", "overpass": "육교", "bridge": "교량"}
_LINK_MEMO = {}


def seoul_crosswalks(key, bbox, crs_wkt, service=None, code=None, cache_dir=None, log=print):
    """반환 [LineString(모델 좌표)] — 범위 안 횡단보도 링크. 다른 종류는 _LINK_MEMO 에 남겨 지하 표시에 쓴다."""
    out = seoul_links(key, bbox, crs_wkt, service, code, cache_dir, log)
    _LINK_MEMO[(tuple(bbox), key)] = out
    if not out["crosswalk"]:
        log("   서울 도보 네트워크에서 횡단보도 링크를 찾지 못함 — '연결 시험'의 값 분포를 보고 '고급'에 횡단보도 코드를 적어 주세요")
    return out["crosswalk"]


# ---------------------------------------------------------------- 1-2. 서울 역사마스터 (역 위치·노선)
STATION_SERVICES = ("subwayStationMaster", "SubwayStationMaster")


def seoul_stations(key, bbox, crs_wkt, service=None, cache_dir=None, log=print, margin=150.0):
    """서울시 역사마스터 정보 → [(x, y, 역이름, 노선)] (범위 + margin 안)."""
    from pyproj import Transformer
    svcs = [service] if service else list(STATION_SERVICES)
    last = None
    for svc in svcs:
        fn = os.path.join(_cache_dir(cache_dir), f"seoul_stations_{svc}.json")
        rows = None
        if os.path.exists(fn):
            try:
                rows = json.load(open(fn, encoding="utf-8"))
            except Exception:
                rows = None
        if rows is None:
            try:
                total, rows = _seoul_page(key, svc, 1, 1000)
                s = 1001
                while s <= total:
                    rows += _seoul_page(key, svc, s, s + 999)[1]
                    s += 1000
            except Exception as e:
                last = e
                continue
            if rows:
                json.dump(rows, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
        tf = Transformer.from_crs("EPSG:4326", crs_wkt, always_xy=True).transform
        out = []
        for r in rows:
            ku = {k.upper(): v for k, v in r.items()}
            lat = next((ku[k] for k in ku if k in ("LAT", "YCRD", "Y", "위도") or k.endswith("_LAT")), None)
            lon = next((ku[k] for k in ku if k in ("LOT", "LON", "LNG", "XCRD", "X", "경도") or k.endswith(("_LOT", "_LON"))), None)
            nm = next((ku[k] for k in ku if k in ("BLDN_NM", "STATN_NM", "STATION_NM", "역명")), "") or ""
            ln = next((ku[k] for k in ku if k in ("ROUTE", "LINE_NM", "LINE_NUM", "호선")), "") or ""
            try:
                x, y = tf(float(lon), float(lat))
            except (TypeError, ValueError):
                continue
            if bbox[0] - margin <= x <= bbox[2] + margin and bbox[1] - margin <= y <= bbox[3] + margin:
                out.append((x, y, str(nm), str(ln)))
        log(f"   서울 역사마스터: 범위 둘레 역 {len(out)}곳")
        return out
    raise RuntimeError(f"서울 역사마스터 API를 부르지 못함: {last}")


# ---------------------------------------------------------------- 2. 생활안전지도 WMS
def _spec(text, default=None):
    """'IF_0102|A2SM_CMMNPOI_SECULIGHT|A2SM_CMMNPOI_07' (서비스ID|레이어|스타일). 쉼표로 적어도 된다."""
    if not text:
        return default
    parts = [p.strip() for p in str(text).replace(",", "|").split("|")]
    sid = next((p for p in parts if p.upper().startswith("IF_")), None)
    rest = [p for p in parts if p and p is not sid]
    if not sid and default:
        sid = default[0]
    if not sid:
        raise RuntimeError(f"'{text}': 서비스ID(IF_로 시작)가 필요합니다 — 예) IF_0102|A2SM_CMMNPOI_SECULIGHT|A2SM_CMMNPOI_07")
    return (sid, rest[0] if rest else "", rest[1] if len(rest) > 1 else "")


def _html_reason(raw):
    import re
    t = raw[:3000].decode("utf-8", "replace")
    title = re.search(r"<title>(.*?)</title>", t, re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I))
    words = " ".join(body.split())[:160]
    return f"[{title.group(1).strip() if title else 'HTML'}] {words}"


def _wms_png(key, spec, bbox3857, w, h):
    sid, layer, style = spec
    q = dict(layers=layer, styles=style, service="WMS", request="GetMap", version="1.1.1", srs="EPSG:3857",
             bbox=",".join(f"{v:.2f}" for v in bbox3857), width=w, height=h, format="image/png", transparent="TRUE")
    q = {k: v for k, v in q.items() if v != ""}
    sk = key if "%" in key else urllib.parse.quote(key, safe="")
    last = None
    for base in SAFEMAP_BASES:
        try:
            raw = _get(base.format(sid=sid) + f"?serviceKey={sk}&" + urllib.parse.urlencode(q))
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if raw[:4] == b"\x89PNG" or raw[:3] == b"\xff\xd8\xff":
            return raw
        last = _html_reason(raw) if raw.lstrip()[:1] == b"<" else raw[:200].decode("utf-8", "replace")
    raise RuntimeError(f"{sid} {layer}: {last}")


def _safemap_polys(key, bbox, crs_wkt, specs, cell=0.25, close=2, min_area=3.0):
    """생활안전지도 WMS 그림 → 칠해진 면 [Polygon(모델 좌표)], 쓴 레이어 이름."""
    from PIL import Image
    from pyproj import Transformer
    from rasterio.features import shapes
    from rasterio.transform import from_bounds
    from scipy import ndimage
    from shapely.ops import transform
    to3857 = Transformer.from_crs(crs_wkt, "EPSG:3857", always_xy=True).transform
    back = Transformer.from_crs("EPSG:3857", crs_wkt, always_xy=True).transform
    xs, ys = to3857([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    b3 = (min(xs), min(ys), max(xs), max(ys))
    # 3857 은 위도만큼 늘어나 있다 → 땅 위 cell(m) 에 맞춘 화소 수
    k = 1 / math.cos(math.radians(37.5))
    W = int((b3[2] - b3[0]) / (cell * k))
    H = int((b3[3] - b3[1]) / (cell * k))
    tiles = max(1, math.ceil(max(W, H) / 2048))
    last = None
    for lay in specs:
        try:
            polys = []
            for i in range(tiles):
                for j in range(tiles):
                    tb = (b3[0] + (b3[2] - b3[0]) * i / tiles, b3[1] + (b3[3] - b3[1]) * j / tiles,
                          b3[0] + (b3[2] - b3[0]) * (i + 1) / tiles, b3[1] + (b3[3] - b3[1]) * (j + 1) / tiles)
                    tw, th = max(16, W // tiles), max(16, H // tiles)
                    img = np.asarray(Image.open(io.BytesIO(_wms_png(key, lay, tb, tw, th))).convert("RGBA"))
                    mask = img[..., 3] > 40
                    if close:
                        mask = ndimage.binary_closing(mask, iterations=close)      # 줄무늬 사이를 메워 한 덩어리로
                    tr = from_bounds(*tb, tw, th)
                    for geom, v in shapes(mask.astype(np.uint8), mask=mask, transform=tr):
                        g = transform(back, shape(geom)).buffer(0)
                        if g.area > min_area:
                            polys.append(g.simplify(0.2))
            return polys, lay[1] or lay[0]
        except Exception as e:
            last = e
            continue
    raise RuntimeError(str(last))


def safemap_crosswalks(key, bbox, crs_wkt, layer=None, styles="", cache_dir=None, log=print, cell=0.25):
    """반환 [Polygon(모델 좌표)] — WMS 그림에서 칠해진 횡단보도 면."""
    try:
        polys, lay = _safemap_polys(key, bbox, crs_wkt, [_spec(layer, SAFEMAP_CROSSWALK)], cell)
    except RuntimeError as e:
        raise RuntimeError(f"생활안전지도 횡단보도 WMS를 부르지 못함: {e}")
    log(f"   생활안전지도 횡단보도 지도({lay})에서 {len(polys)}개")
    return polys


def safemap_lights(key, bbox, crs_wkt, layer=None, styles="", log=print, cell=0.5):
    """생활안전지도 보안등 지도 → [(x, y)] 등 위치(기호의 가운데). 붙은 기호는 크기로 나눠 여러 개로 센다."""
    try:
        polys, lay = _safemap_polys(key, bbox, crs_wkt, [_spec(layer, SAFEMAP_LIGHT)] + ([] if layer else [(SAFEMAP_LIGHT[0], "", "")]), cell, close=0, min_area=0.2)
    except RuntimeError as e:
        raise RuntimeError(f"생활안전지도 보안등 WMS를 부르지 못함: {e}")
    if not polys:
        log(f"   생활안전지도 보안등 지도({lay}): 범위 안에 없음")
        return []
    areas = np.array([p.area for p in polys])
    one = float(np.median(areas))            # 기호 하나의 크기
    pts = []
    for p, a in zip(polys, areas):
        n = int(round(a / one)) if one > 0 else 1
        if n <= 1:
            c = p.centroid
            pts.append((c.x, c.y))
        else:                                  # 겹친 기호: 긴 축을 따라 고르게
            r = p.minimum_rotated_rectangle
            c = np.asarray(r.exterior.coords)[:4]
            e = c[1] - c[0] if np.linalg.norm(c[1] - c[0]) > np.linalg.norm(c[2] - c[1]) else c[2] - c[1]
            m = c.mean(axis=0)
            for t in np.linspace(-0.5, 0.5, n + 2)[1:-1]:
                pts.append(tuple(float(v) for v in (m + e * t)))
    log(f"   생활안전지도 보안등 지도({lay})에서 등 {len(pts)}개")
    return pts


# ---------------------------------------------------------------- 띠로 바꾸기
def bands_from_lines(lines, width=4.0):
    """횡단보도 링크(건너는 선) → (띠, u=길 방향, nrm=건너는 방향)."""
    out = []
    for ln in lines:
        c = np.asarray(ln.coords)[:, :2]
        d = c[-1] - c[0]
        L = np.linalg.norm(d)
        if L < 2:
            continue
        nrm = d / L
        u = np.array([-nrm[1], nrm[0]])
        band = Polygon([c[0] - u * width / 2, c[-1] - u * width / 2, c[-1] + u * width / 2, c[0] + u * width / 2])
        out.append((band, u, nrm, c[0], c[-1]))
    return out


def bands_from_polys(polys):
    """지도에서 뽑은 횡단보도 면 → (최소 회전 사각형 띠, u, nrm). 긴 쪽이 건너는 방향."""
    out = []
    for p in polys:
        for q in getattr(p, "geoms", [p]):
            if q.geom_type != "Polygon":
                continue
            r = q.minimum_rotated_rectangle
            c = np.asarray(r.exterior.coords)[:4]
            e1, e2 = c[1] - c[0], c[2] - c[1]
            if np.linalg.norm(e1) < np.linalg.norm(e2):
                e1, e2 = e2, e1
            L, W = np.linalg.norm(e1), np.linalg.norm(e2)
            if L < 3 or W < 1.5 or W > 10:
                continue
            nrm, u = e1 / L, e2 / W
            m = c.mean(axis=0)
            out.append((r, u, nrm, m - nrm * L / 2, m + nrm * L / 2))
    return out


def selftest(bbox, crs_wkt, seoul_key="", safemap_key="", seoul_service=None, safemap_layer=None, code=None,
             safemap_light_layer=None):
    out = []
    if seoul_key:
        svcs = [seoul_service] if seoul_service else list(SEOUL_SERVICES)
        msg = None
        for svc in svcs:
            try:
                total, rows = _seoul_page(seoul_key, svc, 1, 1000)
                links = [r for r in rows if str(r.get("NODE_TYPE") or "").upper() != "NODE"]
                cw = sum(_is_crosswalk_row(r, code) for r in rows)
                keys = ", ".join(rows[0].keys()) if rows else ""
                from collections import Counter
                types = []
                for k in (rows[0].keys() if rows else []):
                    if "TYPE" in k.upper() and k.upper() != "NODE_TYPE" or any(c in k.upper() for c in CROSS_KEYS):
                        cnt = Counter(str(r.get(k)) for r in links).most_common(8)
                        types.append(f"{k}: " + ", ".join(f"{v}×{n}" for v, n in cnt))
                out.append(("서울 도보 네트워크", True, f"{svc}: 전체 {total:,}줄. 칸 {keys}. 앞 1000줄 중 링크 {len(links)}, "
                            f"횡단보도로 본 링크 {cw}. 값 분포 — " + " / ".join(types)))
                msg = None
                break
            except Exception as e:
                msg = str(e)
        if msg:
            out.append(("서울 도보 네트워크", False, msg + " — 열린데이터광장 Open API 탭의 서비스명을 '고급'에 적어 주세요"))
        smsg = None
        for svc in STATION_SERVICES:
            try:
                total, rows = _seoul_page(seoul_key, svc, 1, 3)
                ex = rows[0] if rows else {}
                out.append(("서울 역사마스터(지하철역)", True, f"{svc}: 역 {total:,}곳, 칸 {', '.join(ex.keys())}"))
                smsg = None
                break
            except Exception as e:
                smsg = str(e)
        if smsg:
            out.append(("서울 역사마스터(지하철역)", False, smsg + " — 열린데이터광장에서 「서울시 역사마스터 정보」 Open API 를 확인해 주세요"))
    else:
        out.append(("서울 도보 네트워크", False, "서울 열린데이터광장 인증키가 비어 있습니다(서울 밖이면 필요 없음)"))
    if safemap_key:
        try:
            polys = safemap_crosswalks(safemap_key, bbox, crs_wkt, safemap_layer, log=lambda *a: None, cell=1.0)
            out.append(("생활안전지도 횡단보도", True, f"범위 안 횡단보도 면 {len(polys)}개"))
        except Exception as e:
            out.append(("생활안전지도 횡단보도", False, str(e) + " — 도로시설(횡단보도) WMS 상세 페이지의 layername·styles 를 '고급'에 서비스ID|레이어|스타일로 적어 주세요"))
        try:
            pts = safemap_lights(safemap_key, bbox, crs_wkt, safemap_light_layer, log=lambda *a: None, cell=1.0)
            out.append(("생활안전지도 보안등", True, f"범위 안 보안등 {len(pts)}개"))
        except Exception as e:
            out.append(("생활안전지도 보안등", False, str(e) + " — 인증키(My Data에 보안등이 있는지)와 승인 상태를 확인해 주세요"))
    else:
        out.append(("생활안전지도 횡단보도", False, "생활안전지도 인증키가 비어 있습니다"))
    return out

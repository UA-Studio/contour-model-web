"""버스 정류장 위치 자동 받기 (v2.3).

자료 파일을 올리지 않아도, 공공데이터포털 인증키(건축물대장에 쓰는 것과 같은 키)로 범위 안 정류장을 받는다.
  1. 국토교통부 (TAGO) 버스정류소정보: 좌표 기반 근접 정류소 목록 (반경 약 500m, 서울 밖 대부분 지역)
  2. 서울특별시 정류소정보조회: 좌표 기반 정류소 목록 (서울)
두 곳 모두 공공데이터포털에서 키마다 '활용신청'을 따로 해야 한다. 신청이 안 된 곳은 조용히 건너뛴다.
응답은 cache_dir/publicdata 에 저장해 다시 쓴다. 실패해도 모델은 계속 만든다(빈 목록).

반환: [(x, y, 정류장 이름)]  (모델 좌표)
"""
import json
import math
import os
import urllib.parse

from .publicdata import _cache, _get, _service_key

TAGO = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList"
SEOUL = "http://ws.bus.go.kr/api/rest/stationinfo/getStationByPos"
TAGO_STEP = 700.0       # TAGO 는 점에서 약 500m 안을 돌려준다 → 700m 격자면 빈틈 없음
SEOUL_RADIUS = 800
SEOUL_STEP = 1100.0


def _grid(bbox, step):
    x0, y0, x1, y1 = bbox
    nx = max(1, int(math.ceil((x1 - x0) / step)))
    ny = max(1, int(math.ceil((y1 - y0) / step)))
    for i in range(nx):
        for j in range(ny):
            yield (x0 + (i + 0.5) * (x1 - x0) / nx, y0 + (j + 0.5) * (y1 - y0) / ny)


def _items(obj):
    """TAGO 응답 item 은 하나면 dict, 여럿이면 list, 없으면 '' 로 온다."""
    if not obj:
        return []
    return obj if isinstance(obj, list) else [obj]


def _tago(key, lon, lat):
    q = f"serviceKey={_service_key(key)}&" + urllib.parse.urlencode(
        dict(gpsLati=f"{lat:.6f}", gpsLong=f"{lon:.6f}", numOfRows=200, pageNo=1, _type="json"))
    raw = _get(TAGO + "?" + q, timeout=20)
    res = json.loads(raw)["response"]
    code = str(res.get("header", {}).get("resultCode", ""))
    if code not in ("00", "0", "000"):
        raise RuntimeError(f"TAGO {code} {res.get('header', {}).get('resultMsg', '')}")
    body = res.get("body") or {}
    items = _items((body.get("items") or {}).get("item") if isinstance(body.get("items"), dict) else None)
    return [(float(it["gpslong"]), float(it["gpslati"]), str(it.get("nodenm", "")))
            for it in items if it.get("gpslong") and it.get("gpslati")]


def _seoul(key, lon, lat):
    q = f"serviceKey={_service_key(key)}&" + urllib.parse.urlencode(
        dict(tmX=f"{lon:.6f}", tmY=f"{lat:.6f}", radius=SEOUL_RADIUS, resultType="json"))
    raw = _get(SEOUL + "?" + q, timeout=20)
    res = json.loads(raw)
    hd = res.get("msgHeader", {})
    code = str(hd.get("headerCd", ""))
    if code == "4":                      # 결과 없음
        return []
    if code != "0":
        raise RuntimeError(f"서울 정류소 {code} {hd.get('headerMsg', '')}")
    items = _items((res.get("msgBody") or {}).get("itemList"))
    return [(float(it["gpsX"]), float(it["gpsY"]), str(it.get("stationNm", "")))
            for it in items if it.get("gpsX") and it.get("gpsY")]


def _in_seoul(lon, lat):
    return 126.76 <= lon <= 127.19 and 37.42 <= lat <= 37.70


def fetch(data_key, bbox, crs_wkt, cache_dir=None, log=print):
    """범위 안 버스 정류장. 반환 [(x, y, 이름)]"""
    if not data_key or not crs_wkt:
        return []
    from pyproj import Transformer
    to_ll = Transformer.from_crs(crs_wkt, 4326, always_xy=True)
    to_m = Transformer.from_crs(4326, crs_wkt, always_xy=True)
    fn = _cache(cache_dir, "busstops", *[int(v) for v in bbox]) if cache_dir else None
    if fn and os.path.exists(fn):
        pts = [tuple(p) for p in json.load(open(fn, encoding="utf-8"))]
    else:
        pts, errs, ok = [], {}, 0
        clo, cla = to_ll.transform((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        jobs = [(_tago, TAGO_STEP, "국토교통부")]
        if _in_seoul(clo, cla):
            jobs.append((_seoul, SEOUL_STEP, "서울시"))
        for fn_api, step, label in jobs:
            for x, y in _grid(bbox, step):
                lon, lat = to_ll.transform(x, y)
                try:
                    pts += fn_api(data_key, lon, lat)
                    ok += 1
                except Exception as e:
                    errs[label] = f"{type(e).__name__}: {e}"[:160]
                    break                      # 키 미신청·차단이면 같은 곳을 더 묻지 않는다
        for label, e in errs.items():
            log(f"   ({label} 정류장 API를 쓰지 못함 — 공공데이터포털에서 활용신청이 필요할 수 있음: {e})")
        if fn and ok and not errs:
            json.dump(pts, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
    out, seen = [], []
    for lon, lat, name in pts:
        x, y = to_m.transform(lon, lat)
        if not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
            continue
        if any(math.hypot(x - sx, y - sy) < 3.0 for sx, sy in seen):
            continue
        seen.append((x, y))
        out.append((float(x), float(y), name))
    if out:
        log(f"   공공데이터포털에서 버스 정류장 {len(out)}곳을 받음")
    return out

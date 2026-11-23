"""OpenStreetMap 지하철 자료 (v2.3): 지하철 출입구(번호), 지하철 선로, 지하철역.

수치지도에는 지하철 출입구 레이어가 없고(지붕이 '무벽건물'로 있을 때만 찾음), 공공 자료에도 출입구 좌표가 없다.
OpenStreetMap 에는 서울·수도권 출입구가 번호(ref)와 함께 대부분 그려져 있고, 선로와 역도 있다.
Overpass API 로 범위 안만 받아 cache_dir/osm 에 저장한다.

라이선스: OpenStreetMap 자료는 ODbL. 결과 모델(만든 작품)에는 '© OpenStreetMap contributors' 출처 표시가 필요하다
(사용 안내 파일과 Rhino 레이어 이름에 남긴다). 자료 자체를 묶어 다시 배포하지는 않는다.

환경변수 CONTOUR_OSM=0 이면 받지 않는다(시험·오프라인).

반환 dict(entrances=[(x, y, 번호, 역 이름)], lines=[(LineString, 지하 여부, 노선 이름)], stations=[(x, y, 이름, 노선)])
"""
import json
import os
import urllib.parse
import urllib.request

from shapely.geometry import LineString

ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
UA = {"User-Agent": "contour-ai/2.3 (architectural model generator)"}
CREDIT = "© OpenStreetMap contributors (ODbL)"

QUERY = """[out:json][timeout:50];
(
  node["railway"="subway_entrance"]({s},{w},{n},{e});
  way["railway"="subway"]({s},{w},{n},{e});
  node["railway"="station"]["station"="subway"]({s},{w},{n},{e});
  node["public_transport"="station"]["subway"="yes"]({s},{w},{n},{e});
);
out geom;"""


def _post(url, query, timeout=60):
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _raw(bb, cache_dir=None, log=print):
    s, w, n, e = bb
    fn = None
    if cache_dir:
        d = os.path.join(cache_dir, "osm")
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, "subway_{:.4f}_{:.4f}_{:.4f}_{:.4f}.json".format(s, w, n, e))
        if os.path.exists(fn):
            return json.load(open(fn, encoding="utf-8"))
    q = QUERY.format(s=f"{s:.6f}", w=f"{w:.6f}", n=f"{n:.6f}", e=f"{e:.6f}")
    last = None
    for url in ENDPOINTS:
        try:
            res = json.loads(_post(url, q))
            if fn:
                json.dump(res, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
            return res
        except Exception as ex:
            last = ex
    raise RuntimeError(f"OpenStreetMap(Overpass)에 연결하지 못함: {type(last).__name__}: {last}")


def _underground(tags):
    if tags.get("tunnel") in ("yes", "building_passage", "covered"):
        return True
    try:
        return float(tags.get("layer", "0")) < 0
    except ValueError:
        return False


def fetch(bbox, crs_wkt, cache_dir=None, log=print):
    if os.environ.get("CONTOUR_OSM", "1") == "0" or not crs_wkt:
        return None
    from pyproj import Transformer
    to_ll = Transformer.from_crs(crs_wkt, 4326, always_xy=True)
    to_m = Transformer.from_crs(4326, crs_wkt, always_xy=True)
    lo, la = to_ll.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    bb = (min(la), min(lo), max(la), max(lo))
    res = _raw(bb, cache_dir, log)
    out = dict(entrances=[], lines=[], stations=[])
    seen_st = []
    for el in res.get("elements", []):
        t = el.get("tags", {}) or {}
        if el.get("type") == "node":
            x, y = to_m.transform(el["lon"], el["lat"])
            if not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                continue
            if t.get("railway") == "subway_entrance":
                out["entrances"].append((float(x), float(y), str(t.get("ref", "")), str(t.get("name", ""))))
            else:
                if any(abs(x - sx) < 30 and abs(y - sy) < 30 for sx, sy in seen_st):
                    continue
                seen_st.append((x, y))
                nm = t.get("name:ko") or t.get("name") or ""
                out["stations"].append((float(x), float(y), str(nm), str(t.get("line") or t.get("subway:line") or "")))
        elif el.get("type") == "way" and el.get("geometry"):
            pts = [to_m.transform(g["lon"], g["lat"]) for g in el["geometry"]]
            if len(pts) >= 2:
                out["lines"].append((LineString(pts), _underground(t), str(t.get("name", ""))))
    log(f"   OpenStreetMap: 지하철 출입구 {len(out['entrances'])}, 선로 {len(out['lines'])}, 역 {len(out['stations'])}")
    return out


def entrances_to_items(entrances, road, existing, log=print):
    """OSM 출입구 점 → 이미 무벽건물로 찾은 출입구(15m 안)에는 번호만 붙이고, 없으면 표준 캐노피를 세운다."""
    import numpy as np
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    out, tagged = [], 0
    exist = [d for d in existing if d["kind"] == "지하철출입구"]
    for x, y, ref, name in entrances:
        near = [d for d in exist if np.hypot(*(np.asarray(d["c"]) - (x, y))) < 15]
        if near:
            d = min(near, key=lambda d: np.hypot(*(np.asarray(d["c"]) - (x, y))))
            if ref and not d.get("ref"):
                d["ref"], tagged = ref, tagged + 1
            continue
        if road is None or road.is_empty:
            continue
        q = nearest_points(road.boundary, Point(x, y))[0]
        b = road.boundary
        s = b.project(q)
        a1, a2 = b.interpolate(max(s - 2, 0)), b.interpolate(min(s + 2, b.length))
        u = np.array([a2.x - a1.x, a2.y - a1.y])
        u /= np.linalg.norm(u) or 1.0
        v = np.array([-u[1], u[0]])
        if road.contains(Point(x, y)):
            continue                                         # 차도 한가운데 점은 위치가 어긋난 것
        c = np.array([x, y])
        # 캐노피는 연석과 나란히, 점(입구 자리)이 +u 끝에 오도록 조금 뒤로
        out.append(dict(kind="지하철출입구", poly=None, c=c - u * 2.5, u=u, v=v, L=6.0, W=3.2, src="OSM",
                        ref=ref, name=name))
    if out or tagged:
        log(f"   OSM 지하철 출입구: 새로 {len(out)}곳, 번호만 붙인 곳 {tagged}곳")
    return out

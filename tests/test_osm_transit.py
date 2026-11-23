"""v2.3 OpenStreetMap 지하철 출입구·선로·역 시험 (가짜 Overpass 응답)."""
import json

import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry import LineString, box

import contour_ai.osm_transit as ot
from contour_ai import transit, underground

WKT = CRS.from_epsg(5186).to_wkt()
BBOX = (201300.0, 554600.0, 201800.0, 555100.0)


def _ll(x, y):
    return Transformer.from_crs(WKT, 4326, always_xy=True).transform(x, y)


def _fake(monkeypatch):
    e1, e2, st, out = _ll(201410, 554812), _ll(201600, 554812), _ll(201500, 554850), _ll(205000, 558000)
    line = [_ll(201300, 554850), _ll(201800, 554850)]
    res = {"elements": [
        {"type": "node", "lat": e1[1], "lon": e1[0], "tags": {"railway": "subway_entrance", "ref": "3"}},
        {"type": "node", "lat": e2[1], "lon": e2[0], "tags": {"railway": "subway_entrance", "ref": "4"}},
        {"type": "node", "lat": out[1], "lon": out[0], "tags": {"railway": "subway_entrance", "ref": "9"}},
        {"type": "node", "lat": st[1], "lon": st[0], "tags": {"railway": "station", "station": "subway", "name": "가짜역"}},
        {"type": "way", "tags": {"railway": "subway", "tunnel": "yes"},
         "geometry": [{"lat": p[1], "lon": p[0]} for p in line]}]}
    monkeypatch.setenv("CONTOUR_OSM", "1")
    monkeypatch.setattr(ot, "_post", lambda url, q, timeout=60: json.dumps(res).encode())


def test_범위_안_출입구_선로_역을_읽음(monkeypatch, tmp_path):
    _fake(monkeypatch)
    o = ot.fetch(BBOX, WKT, str(tmp_path))
    assert sorted(e[2] for e in o["entrances"]) == ["3", "4"]
    assert o["stations"][0][2] == "가짜역"
    assert o["lines"][0][1] is True and o["lines"][0][0].length > 400
    monkeypatch.setattr(ot, "_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("다시 물음")))
    assert len(ot.fetch(BBOX, WKT, str(tmp_path))["entrances"]) == 2       # 저장해 둔 응답


def test_끄면_묻지_않음(monkeypatch):
    monkeypatch.setenv("CONTOUR_OSM", "0")
    monkeypatch.setattr(ot, "_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("물음")))
    assert ot.fetch(BBOX, WKT) is None


def test_출입구는_있던_캐노피엔_번호만_없으면_새로(monkeypatch, tmp_path):
    _fake(monkeypatch)
    o = ot.fetch(BBOX, WKT, str(tmp_path))
    road = box(201300, 554790, 201800, 554808)          # 출입구 점은 차도 북쪽 보도 위
    existing = [dict(kind="지하철출입구", c=np.array([201405.0, 554815.0]), u=np.array([1.0, 0]),
                     v=np.array([0, 1.0]), L=6, W=3, src="수치지도 무벽건물")]
    new = ot.entrances_to_items(o["entrances"], road, existing, log=lambda *a: None)
    assert existing[0]["ref"] == "3"
    assert len(new) == 1 and new[0]["ref"] == "4" and new[0]["src"] == "OSM"
    ms = transit.meshes(existing + new, lambda x, y: 10.0, lambda x, y: 9.8)
    names = [n for *_, n in ms]
    assert any("3번" in n for n in names) and any("4번" in n and "OpenStreetMap" in n for n in names)


def test_선로_터널과_역은_선로_방향(monkeypatch, tmp_path):
    _fake(monkeypatch)
    o = ot.fetch(BBOX, WKT, str(tmp_path))
    ug = dict(tunnel=[], subway=[], stations=o["stations"], lines=[ln for ln, u, _ in o["lines"] if u])
    out = underground.meshes(ug, lambda x, y: 20.0)
    layers = [l for _, l, _, _ in out]
    assert "지하::지하철 선로(OSM)" in layers
    plat = [m for m, l, _, _ in out if "승강장" in l][0]
    ext = plat.bounds[1] - plat.bounds[0]
    assert ext[0] > 150 and ext[1] < 20             # 동서로 놓인 선로를 따라 길쭉
    assert plat.bounds[1][2] <= 20 - 13 + 1e-6

"""v2.3 버스 정류장 자동 받기 시험 (네트워크 없이 가짜 응답)."""
import json

from pyproj import CRS, Transformer

import contour_ai.busstop_api as ba

WKT = CRS.from_epsg(5186).to_wkt()
BBOX = (201300.0, 554600.0, 201800.0, 555100.0)      # 가짜 도엽 자리(서울)


def _ll(x, y):
    return Transformer.from_crs(WKT, 4326, always_xy=True).transform(x, y)


def test_두_곳에서_받아_겹친_정류장은_하나로(monkeypatch, tmp_path):
    a = _ll(201400, 554700)
    b = _ll(201700, 555000)
    far = _ll(203500, 557000)                         # 범위 밖

    def fake_get(url, timeout=30):
        if "BusSttnInfoInqireService" in url:
            item = {"gpslati": a[1], "gpslong": a[0], "nodenm": "가짜정류장A"}
            return json.dumps({"response": {"header": {"resultCode": "00"},
                                            "body": {"items": {"item": item}}}}).encode()
        assert "ws.bus.go.kr" in url
        return json.dumps({"msgHeader": {"headerCd": "0"}, "msgBody": {"itemList": [
            {"gpsX": a[0] + 1e-6, "gpsY": a[1], "stationNm": "가짜정류장A"},
            {"gpsX": b[0], "gpsY": b[1], "stationNm": "가짜정류장B"},
            {"gpsX": far[0], "gpsY": far[1], "stationNm": "먼정류장"}]}}).encode()

    monkeypatch.setattr(ba, "_get", fake_get)
    pts = ba.fetch("키", BBOX, WKT, str(tmp_path))
    assert sorted(p[2] for p in pts) == ["가짜정류장A", "가짜정류장B"]
    assert abs(pts[0][0] - 201400) < 1 and abs(pts[0][1] - 554700) < 1
    # 두 번째는 저장해 둔 응답을 씀
    monkeypatch.setattr(ba, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("다시 물음")))
    assert len(ba.fetch("키", BBOX, WKT, str(tmp_path))) == 2


def test_활용신청이_안_된_키면_빈_목록과_안내(monkeypatch, tmp_path):
    def fake_get(url, timeout=30):
        raise OSError("HTTP Error 403: Forbidden")
    monkeypatch.setattr(ba, "_get", fake_get)
    logs = []
    assert ba.fetch("키", BBOX, WKT, str(tmp_path), log=logs.append) == []
    assert any("활용신청" in s for s in logs)
    assert not list((tmp_path / "publicdata").glob("busstops_*"))    # 실패는 저장하지 않음


def test_키가_없으면_묻지_않음(monkeypatch):
    monkeypatch.setattr(ba, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("물음")))
    assert ba.fetch(None, BBOX, WKT) == []

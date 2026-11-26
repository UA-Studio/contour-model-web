"""건축물대장 조회: 답한 주소를 기억해 필지마다 막힌 주소를 다시 두드리지 않는지 (인터넷 없이 가짜 응답)."""
import json

from contour_ai import publicdata as pd


def test_register_remembers_working_endpoint(monkeypatch):
    calls = []

    def fake_get(url, timeout=20):
        calls.append(url)
        if "BldRgstHubService" in url:
            return b"<OpenAPI_ServiceResponse>SERVICE ERROR</OpenAPI_ServiceResponse>"
        return json.dumps({"response": {"header": {"resultCode": "00"},
                                        "body": {"totalCount": 1, "items": {"item": [{"heit": 10}]}}}}).encode()
    monkeypatch.setattr(pd, "_get", fake_get)
    monkeypatch.setattr(pd, "_BASE_OK", {})
    pnu = "1129010700100010000"
    assert pd.title_info("k", pnu)[0]["heit"] == 10
    assert len(calls) == 2                      # 처음엔 새 주소 실패 → 예전 주소
    calls.clear()
    pd.title_info("k", "1129010700100020000")
    assert len(calls) == 1 and "BldRgstService_v2" in calls[0]   # 다음 필지는 답한 주소 하나만


def test_ticker_logs_progress(monkeypatch):
    lines = []
    t = [0.0]
    monkeypatch.setattr(pd.time, "time", lambda: t[0])
    tick = pd._ticker(100, "건축물대장 표제부", lines.append)
    for i in range(50):
        t[0] += 1.0
        tick()
    assert lines and "/ 100필지" in lines[-1]

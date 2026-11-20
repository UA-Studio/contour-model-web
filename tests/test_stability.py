"""사이트 안정화 시험 (2026-11-20): 서버에서 '중간에 멈춤'·버벅임·저장 공간 부족으로 생기던 문제를 다시 만들지 않게.

- 12분 시간 제한에 걸린 작업을 화면이 '멈추기를 누름'으로 보이지 않고 이유를 받아야 한다
- 새로 만들기 시작하면 그 사람의 이전 결과는 지운다 (결과를 오래 남겨 두지 않음)
- 한동안 쓰지 않은 칸은 지우고, 디스크가 모자라면 쉬는 칸부터 지운다 (만드는 중인 칸은 남김)
- 서버는 무거운 라이브러리를 불러오기 전에 접속을 받아야 한다 (켜질 때 14초 '응답 없음')
"""
import importlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTOUR_SERVER", "1")
    monkeypatch.setenv("CONTOUR_DATA", str(tmp_path / "data"))
    import contour_ai.app as m
    m = importlib.reload(m)
    yield m
    monkeypatch.delenv("CONTOUR_SERVER")
    importlib.reload(m)


def _session(app, sid, at=None):
    home = os.path.join(app.DATA, "sessions", sid)
    os.makedirs(os.path.join(home, "_입력"), exist_ok=True)
    s = app.SESSIONS[sid] = app._new_session(sid, home)
    s["at"] = at if at is not None else time.time()
    return s


class _FakeJob:
    def __init__(self, done):
        self.done = done


def test_시간_초과는_멈추기와_다르게_이유가_보임(app):
    """시간 제한으로 멈춘 작업: /status 의 cancelled 는 거짓, error 에 이유 (화면이 '멈췄어요'만 띄우던 것)."""
    job = app.Job.__new__(app.Job)
    job.cancelled = job.timed_out = False
    job.timeout()
    assert job.cancelled and job.timed_out
    src = (ROOT / "contour_ai" / "app.py").read_text(encoding="utf-8")
    assert "cancelled=job.cancelled and not job.timed_out" in src
    html = (ROOT / "contour_ai" / "web" / "index.html").read_text(encoding="utf-8")
    assert "분 안에 끝나지 않아" in html, "시간 초과 안내가 화면에 없음"
    live = (ROOT / "contour_ai" / "web" / "live.js").read_text(encoding="utf-8")
    assert "기록 탭에서 이유를" not in live, "없는 '기록 탭'을 보라는 안내가 남아 있음"


def test_새로_만들면_이전_결과는_지움(app):
    s = _session(app, "a" * 20)
    app._CTX.sess = s
    old = os.path.join(s["home"], "모델_1119_1000")
    new = os.path.join(s["home"], "모델_1120_0900")
    os.makedirs(old)
    open(os.path.join(old, "model.3dm"), "wb").write(b"x" * 1000)
    app._drop_old_results(new)
    assert not os.path.exists(old)
    assert os.path.isdir(s["uploads"]), "올린 수치지도(_입력)는 남아야 함"


def test_쉬는_칸은_지우고_만드는_중인_칸은_남김(app, monkeypatch):
    idle = _session(app, "i" * 20, at=time.time() - 3600 * 5)
    busy = _session(app, "b" * 20, at=time.time() - 3600 * 5)
    busy["state"]["job"] = _FakeJob(done=False)
    me = _session(app, "m" * 20, at=time.time() - 3600 * 9)
    free = iter([100, 100, 100, 99999, 99999, 99999])          # 처음엔 모자라고, 하나 지우면 충분
    monkeypatch.setattr(app, "_free_mb", lambda: next(free, 99999))
    app._make_room(keep_sid=me["sid"])
    assert idle["sid"] not in app.SESSIONS and not os.path.exists(idle["home"])
    assert busy["sid"] in app.SESSIONS and os.path.isdir(busy["home"])
    assert me["sid"] in app.SESSIONS


def test_보관_시간은_1시간(app):
    assert app.LIMITS["keep_minutes"] == 60
    assert "keep_hours" not in app.LIMITS


def test_서버는_무거운_라이브러리_전에_접속을_받음():
    """app 을 불러올 때 numpy·trimesh·rhino3dm 을 같이 불러오지 않아야 켜지자마자 대답한다."""
    code = ("import sys, contour_ai.app; heavy=[m for m in ('trimesh','rhino3dm','rasterio','scipy') if m in sys.modules];"
            "print(','.join(heavy))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "", f"서버가 켜질 때 무거운 라이브러리를 불러옴: {out}"


def test_서버_켜짐_빠름_그리고_디스크_기록(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, CONTOUR_SERVER="1", CONTOUR_DATA=str(tmp_path), PORT=str(port), MPLBACKEND="Agg", PYTHONUTF8="1")
    t0 = time.time()
    p = subprocess.Popen([sys.executable, "-m", "contour_ai", "serve"], cwd=ROOT, env=env)
    try:
        for _ in range(200):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/config", timeout=2) as r:
                    cfg = json.loads(r.read())
                break
            except Exception:
                time.sleep(0.05)
        else:
            pytest.fail("서버가 켜지지 않음")
        took = time.time() - t0
        assert cfg["limits"]["keep_minutes"] == 60
        assert took < 8, f"서버가 대답하기까지 {took:.1f}초"
    finally:
        p.terminate(); p.wait(10)

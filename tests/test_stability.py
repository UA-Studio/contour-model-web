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
    import contour_ai.app_state as st          # 서버 설정·상태는 app_state 에 있다 → 다시 불러와 서버 모드로
    importlib.reload(st)
    m = importlib.reload(m)
    yield m
    monkeypatch.delenv("CONTOUR_SERVER")
    importlib.reload(st)
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
    src = (ROOT / "contour_ai" / "app_http.py").read_text(encoding="utf-8")     # /status 응답
    assert "cancelled=job.cancelled and not job.timed_out" in src
    web = ROOT / "contour_ai" / "web"           # 화면은 index.html + 기능별 ui_*.js 로 나뉘어 있음
    html = "\n".join(p.read_text(encoding="utf-8") for p in [web / "index.html", *sorted(web.glob("ui_*.js"))])
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
    std = os.path.join(s["home"], "대지표준")              # 레이어 정리한 대지 파일: 다시 만들 때 씀
    os.makedirs(std)
    open(os.path.join(std, "계획안_표준.dxf"), "w").write("0")
    app._drop_old_results(new)
    assert not os.path.exists(old)
    assert os.path.isdir(s["uploads"]), "올린 수치지도(_입력)는 남아야 함"
    assert os.path.isfile(os.path.join(std, "계획안_표준.dxf")), "정리해 둔 대지 파일(대지표준)이 지워짐"


def test_쉬는_칸은_지우고_만드는_중인_칸은_남김(app, monkeypatch):
    idle = _session(app, "i" * 20, at=time.time() - 3600 * 5)
    busy = _session(app, "b" * 20, at=time.time() - 3600 * 5)
    busy["state"]["job"] = _FakeJob(done=False)
    me = _session(app, "m" * 20, at=time.time() - 3600 * 9)
    free = iter([100, 100, 100, 99999, 99999, 99999])          # 처음엔 모자라고, 하나 지우면 충분
    from contour_ai import app_sessions                   # 칸 정리는 app_sessions.py
    monkeypatch.setattr(app_sessions, "_free_mb", lambda: next(free, 99999))
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


def test_개발자_열쇠로_하루_횟수_제한_없음(tmp_path):
    """관리자 토큰으로 /dev?key=… 를 한 번 열면 그 브라우저는 하루 5번 제한 없이 만든다 (2026-11-20 사용자 요청)."""
    import urllib.error
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, CONTOUR_SERVER="1", CONTOUR_DATA=str(tmp_path), PORT=str(port), MPLBACKEND="Agg",
               PYTHONUTF8="1", CONTOUR_ADMIN_TOKEN="test-key-123", CONTOUR_RUNS_PER_DAY="0")
    p = subprocess.Popen([sys.executable, "-m", "contour_ai", "serve"], cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(base + "/config", timeout=2)
                break
            except Exception:
                time.sleep(0.05)
        with pytest.raises(urllib.error.HTTPError) as e:
            op.open(base + "/dev?key=wrong", timeout=10)
        assert e.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as e:   # 302 는 따라가지 않게 해서 오류로 잡힘
            op.open(base + "/dev?key=test-key-123", timeout=10)
        assert e.value.code == 302
        ck = e.value.headers.get("Set-Cookie")
        assert ck.startswith("cdev=") and "test-key-123" not in ck
        cookie = ck.split(";")[0]
        req = urllib.request.Request(base + "/config", headers={"Cookie": cookie})
        assert json.loads(urllib.request.urlopen(req, timeout=10).read())["dev"] is True
        assert json.loads(urllib.request.urlopen(base + "/config", timeout=10).read())["dev"] is False
    finally:
        p.terminate(); p.wait(10)


def test_개발자는_횟수_검사를_건너뜀(app):
    s = _session(app, "d" * 20)
    app._CTX.sess = s
    params = dict(hd_dir=None, aerial_file=None, site_file=None, road_files=(), light_files=(), dem_files=None,
                  out=os.path.join(s["home"], "모델"), size=500)
    app.LIMITS["runs_per_day"] = 0
    app._CTX.dev = False
    with pytest.raises(ValueError, match="하루에"):
        app._server_check(dict(params), {"_ip": "1.2.3.4"})
    app._CTX.dev = True
    app._server_check(dict(params), {"_ip": "1.2.3.4"})


def test_지형_메시를_줄여도_모양은_같음():
    """1000m 지형이 1m 격자 그대로면 꼭짓점 200만 개라 5단계(Rhino 저장)만 70초가 걸렸다 (2026-11-20).
    평평한 곳의 잔 삼각형을 합쳐 꼭짓점은 크게 줄이되, 높이 차는 1cm 이하·닫힌 솔리드 그대로여야 한다."""
    import numpy as np
    import trimesh
    from contour_ai.contour_model import heightfield_mesh, simplify_solid
    n = 301
    X, Y = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    H = np.where(X < 150, 50.0, 50.0 + (X - 150) * 0.2) + np.where(Y > 200, 3.0, 0.0)   # 평지·고른 비탈·단
    H = H + 0.5 * np.sin(X / 9.0) * (X > 250)                                            # 굽은 곳
    m = heightfield_mesh(X, Y, H, 40.0, (0.0, 0.0))
    s = simplify_solid(m)
    assert len(s.vertices) < len(m.vertices) / 4, (len(m.vertices), len(s.vertices))
    assert s.is_watertight and abs(s.volume - m.volume) / m.volume < 1e-3
    import matplotlib.tri as mtri
    top = s.faces[s.face_normals[:, 2] > 0.05]
    tri = mtri.Triangulation(s.vertices[:, 0], s.vertices[:, 1], top)
    z = mtri.LinearTriInterpolator(tri, s.vertices[:, 2])(X.ravel(), Y.ravel())
    assert np.ma.count_masked(z) == 0
    assert np.max(np.abs(np.asarray(z) - H.ravel())) <= 0.012

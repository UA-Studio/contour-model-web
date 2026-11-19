"""웹 서버 시험: 공개 서버 모드로 켜서 소개·프로그램·안내 화면이 열리는지."""
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, CONTOUR_SERVER="1", CONTOUR_DATA=str(tmp_path_factory.mktemp("data")),
               PORT=str(port), MPLBACKEND="Agg", PYTHONUTF8="1")
    p = subprocess.Popen([sys.executable, "-m", "contour_ai", "serve"], cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/", timeout=2)
            break
        except Exception:
            if p.poll() is not None:
                pytest.fail("서버가 켜지자마자 꺼짐")
            time.sleep(0.5)
    yield base
    p.terminate()
    p.wait(10)


@pytest.mark.parametrize("path", ["/", "/app", "/legal"])
def test_화면_열림(server, path):
    with urllib.request.urlopen(server + path, timeout=10) as r:
        assert r.status == 200
        body = r.read().decode("utf-8", "replace")
    assert "<html" in body.lower()


def _start(data, port):
    env = dict(os.environ, CONTOUR_SERVER="1", CONTOUR_DATA=str(data), PORT=str(port), MPLBACKEND="Agg", PYTHONUTF8="1")
    p = subprocess.Popen([sys.executable, "-m", "contour_ai", "serve"], cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/", timeout=2)
            return p, base
        except Exception:
            time.sleep(0.5)
    p.terminate()
    pytest.fail("서버가 켜지지 않음")


def _post(base, path, data, headers):
    import json
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), None


def test_서버가_다시_켜져도_넣은_수치지도를_기억함(tmp_path, fake_sheet, fake_site):
    """새 판 배포·쉬었다 깨어남으로 서버가 다시 켜진 뒤에도 대지 파일 '레이어 정리'가 열려야 한다."""
    import json
    from urllib.parse import quote
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p, base = _start(tmp_path, port)
    try:
        st, j, ck = _post(base, "/upload", fake_sheet.read_bytes(), {"X-Filename": quote(fake_sheet.name)})
        assert st == 200, j
        cookie = {"Cookie": ck.split(";")[0]}
        sid = j["id"]
    finally:
        p.terminate(); p.wait(10)
    p, base = _start(tmp_path, port)           # 다시 켜짐 (메모리 비워짐)
    try:
        st, j, _ = _post(base, "/siteupload", fake_site.read_bytes(),
                         {**cookie, "X-Source": quote(sid), "X-Filename": quote(fake_site.name)})
        assert st in (200, 400) and "먼저 수치지도" not in j.get("error", ""), j
        path = j.get("path") or str(next((tmp_path / "sessions").rglob(fake_site.name)))
        st, j, _ = _post(base, "/siteprep", json.dumps({"source": sid, "path": path}).encode(), cookie)
        assert st == 200, j
        assert j.get("layers")
    finally:
        p.terminate(); p.wait(10)

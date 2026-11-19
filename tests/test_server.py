"""웹 서버 시험: 공개 서버 모드로 켜서 소개·프로그램·안내 화면이 열리는지."""
import os
import socket
import subprocess
import sys
import time
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

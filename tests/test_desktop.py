"""내 PC 판(윈도우 다운로드판) 흐름: 서버 모드가 아닌 그대로 켜서
수치지도 올리기 → 대지 DXF 올리기 → 레이어 정리 → 만들기 → 결과 파일·최근 결과 확인.

웹 전용 제한(하루 횟수·자동 삭제·쿠키 칸)이 꺼져 있는지도 본다.

CONTOUR_TEST_BAT 에 '콘타모델 실행.bat' 경로를 주면 (윈도우판 묶음 시험) 저장소 소스 대신
그 .bat 을 더블클릭한 것처럼 cmd 로 실행해서 묶음 안의 파이썬·라이브러리로 시험한다.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def _call(base, path, data=None, headers=None, timeout=300):
    req = urllib.request.Request(base + path, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _start(home, port):
    env = dict(os.environ, CONTOUR_PORT=str(port), CONTOUR_NO_BROWSER="1", MPLBACKEND="Agg", PYTHONUTF8="1",
               HOME=str(home), USERPROFILE=str(home))
    env.pop("CONTOUR_SERVER", None)
    bat = os.environ.get("CONTOUR_TEST_BAT")
    if bat:
        bat = Path(bat).resolve()
        # .bat 끝의 pause 는 입력이 닫혀 있으면 바로 넘어간다
        return subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(bat.parent), env=env,
                                stdin=subprocess.DEVNULL), bat
    return subprocess.Popen([sys.executable, "-m", "contour_ai"], cwd=ROOT, env=env), None


def _stop(p, bat):
    if bat:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()
    try:
        p.wait(20)
    except subprocess.TimeoutExpired:
        p.kill()


def test_내_PC_판_흐름(tmp_path, fake_sheet, fake_site):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    home = tmp_path / "사용자 폴더"          # 한글·띄어쓰기 경로
    home.mkdir()
    p, bat = _start(home, port)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(1200):
            try:
                st, cfg = _call(base, "/config", timeout=2)
                break
            except Exception:
                assert p.poll() is None, "프로그램이 켜지자마자 꺼짐"
                time.sleep(0.1)
        else:
            raise AssertionError("화면 주소가 열리지 않음")
        assert cfg["server"] is False and not cfg.get("limits"), cfg
        if os.environ.get("CONTOUR_EXPECT_VERSION"):
            assert cfg["version"] == os.environ["CONTOUR_EXPECT_VERSION"], cfg
        st, page = _call(base, "/")
        assert st == 200 and b"<html" in page.lower()[:2000]
        # 수치지도 → 대지 → 레이어 정리
        st, src = _call(base, "/upload", fake_sheet.read_bytes(), {"X-Filename": quote(fake_sheet.name)})
        assert st == 200, src
        st, site = _call(base, "/siteupload", fake_site.read_bytes(),
                         {"X-Filename": quote(fake_site.name), "X-Source": quote(src["id"])})
        assert st == 200, site
        path = site.get("path") or site.get("file")
        st, prep = _call(base, "/siteprep", json.dumps({"source": src["id"], "path": path}).encode())
        assert st == 200, prep
        roles = {L["name"]: ("경계" if L["name"] == "대지경계" else L["role"]) for L in prep["layers"]}
        st, saved = _call(base, "/sitesave", json.dumps({"source": src["id"], "path": path, "roles": roles}).encode())
        assert st == 200, saved
        # 만들기 두 번 (내 PC 판은 횟수 제한 없음, 이전 결과도 지우지 않음)
        outs = []
        for i, label in enumerate(("PC시험1", "PC시험2")):
            body = dict(source=src["id"], w=300, h=300, green=False, detail=2, site_file=saved["saved"],
                        sketchup=True, lights=False, road_detail=False, label=label)
            st, j = _call(base, "/run", json.dumps(body).encode())
            assert st == 200, f"{i + 1}번째 만들기 거절: {j}"
            t0 = time.time()
            while True:
                st, r = _call(base, "/status")
                if r.get("done"):
                    break
                assert time.time() - t0 < 900, "모델 만들기가 끝나지 않음"
                time.sleep(1)
            assert not r.get("error"), f"{i + 1}번째 만들기 실패: {r.get('error')}\n" + "\n".join(r.get("lines", [])[-30:])
            outs.append(Path(r["out"]))
            st, files = _call(base, "/files")
            names = {f["name"] for f in files["files"]}
            assert {"model.3dm", "model.stl"} <= names, names
        for o in outs:
            assert (o / "summary.json").is_file() and (o / "model.3dm").is_file(), o
            assert o.parent == home / "콘타모델", o
        st, rec = _call(base, "/recent")
        assert st == 200 and len(rec["items"]) >= 2, rec
    finally:
        _stop(p, bat)

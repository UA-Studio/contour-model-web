"""실제 사이트 사용 흐름 그대로 (2026-11-20 사용자 신고 재현):
개발자 열쇠 → 수치지도 올리기 → 대지 DXF 올리기 → 레이어 정리 → 만들기 → 같은 설정으로 다시 만들기 → 또 한 번.
하루 횟수 1번으로 줄여 개발자 모드가 제한을 푸는지, 다시 만들 때 정리한 대지 파일이 남아 있는지,
이전 결과만 지워지고 마지막 결과는 내려받을 수 있는지 확인한다.
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
KEY = "flow-test-key-456"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _call(base, path, cookie, data=None, headers=None, timeout=120):
    h = dict(headers or {}, Cookie=cookie)
    req = urllib.request.Request(base + path, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _wait_done(base, cookie, limit=600):
    t0 = time.time()
    while time.time() - t0 < limit:
        st, j = _call(base, "/status", cookie)
        assert st == 200, j
        if j.get("done"):
            return j
        time.sleep(1)
    raise AssertionError("모델 만들기가 끝나지 않음")


def test_사이트_흐름_대지파일_정리_후_두번_다시_만들기(tmp_path, fake_sheet, fake_site):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    data = tmp_path / "data"
    env = dict(os.environ, CONTOUR_SERVER="1", CONTOUR_DATA=str(data), PORT=str(port), MPLBACKEND="Agg",
               PYTHONUTF8="1", CONTOUR_ADMIN_TOKEN=KEY, CONTOUR_RUNS_PER_DAY="1")
    p = subprocess.Popen([sys.executable, "-m", "contour_ai", "serve"], cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(base + "/config", timeout=2)
                break
            except Exception:
                time.sleep(0.05)
        # 1) 개발자 열쇠 → 쿠키 두 개(방문자 칸 csid + 개발자 cdev)
        op = urllib.request.build_opener(_NoRedirect)
        try:
            op.open(base + "/dev?key=" + KEY, timeout=10)
            raise AssertionError("열쇠가 맞으면 /app 으로 넘겨야 함")
        except urllib.error.HTTPError as e:
            assert e.code == 302
            cookies = [v.split(";")[0] for k, v in e.headers.items() if k.lower() == "set-cookie"]
        cookie = "; ".join(cookies)
        assert "cdev=" in cookie
        st, cfg = _call(base, "/config", cookie)
        assert cfg["dev"] is True
        # 2) 수치지도 올리기
        st, src = _call(base, "/upload", cookie, fake_sheet.read_bytes(), {"X-Filename": quote(fake_sheet.name)})
        assert st == 200, src
        # 3) 대지 DXF 올리기 → 4) 레이어 정리(경계 레이어 고르기) → 표준 대지 파일
        st, site = _call(base, "/siteupload", cookie, fake_site.read_bytes(),
                         {"X-Filename": quote(fake_site.name), "X-Source": quote(src["id"])})
        path = site.get("path") or site.get("file") or str(next((data / "sessions").rglob(fake_site.name)))
        st, prep = _call(base, "/siteprep", cookie, json.dumps({"source": src["id"], "path": path}).encode())
        assert st == 200, prep
        roles = {L["name"]: ("경계" if L["name"] == "대지경계" else L["role"]) for L in prep["layers"]}
        st, saved = _call(base, "/sitesave", cookie, json.dumps({"source": src["id"], "path": path, "roles": roles}).encode())
        assert st == 200, saved
        std = saved["saved"]
        assert os.path.isfile(std)
        # 5) 만들기 ×3 (하루 1번 제한이지만 개발자 모드라 통과해야 함), 매번 같은 대지 파일
        body = dict(source=src["id"], w=300, h=300, green=False, detail=1, terrain_fit="exact", site_file=std,
                    sketchup=False, lights=False, road_detail=False, label="흐름시험")
        outs = []
        for i in range(3):
            st, j = _call(base, "/run", cookie, json.dumps(body).encode())
            assert st == 200, f"{i + 1}번째 만들기 거절: {j}"
            r = _wait_done(base, cookie)
            assert not r.get("error"), f"{i + 1}번째 만들기 실패: {r.get('error')}"
            assert os.path.isfile(std), f"{i + 1}번째 만들기 뒤 정리한 대지 파일이 사라짐"
            outs.append(r["out"])
            st, files = _call(base, "/files", cookie)
            assert any(f["name"] == "model.3dm" for f in files["files"]), files
            time.sleep(1.1)                       # 결과 폴더 이름(분 단위)이 겹치지 않아도 되게
        # 마지막 결과만 남고 그 전 결과는 지워짐 (같은 분이면 같은 폴더라 하나)
        home = Path(outs[-1]).parent
        results = [d for d in home.iterdir() if (d / "summary.json").exists()]
        assert len(results) == 1, [d.name for d in results]
        assert (home / "대지표준").is_dir() and (home / "_입력").is_dir()
        # 내려받기
        st, z = _call(base, "/download", cookie)
        assert st == 200 and len(z) > 1000
    finally:
        p.terminate(); p.wait(10)


def test_개발자_아니면_하루_횟수_제한(tmp_path, fake_sheet):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, CONTOUR_SERVER="1", CONTOUR_DATA=str(tmp_path), PORT=str(port), MPLBACKEND="Agg",
               PYTHONUTF8="1", CONTOUR_ADMIN_TOKEN=KEY, CONTOUR_RUNS_PER_DAY="0")
    p = subprocess.Popen([sys.executable, "-m", "contour_ai", "serve"], cwd=ROOT, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(base + "/config", timeout=2)
                break
            except Exception:
                time.sleep(0.05)
        req = urllib.request.Request(base + "/upload", data=fake_sheet.read_bytes(),
                                     headers={"X-Filename": quote(fake_sheet.name)}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            src = json.loads(r.read())
            cookie = r.headers.get("Set-Cookie").split(";")[0]
        st, j = _call(base, "/run", cookie, json.dumps(dict(source=src["id"], w=300, green=False)).encode())
        assert st != 200 and "하루에" in j.get("error", ""), j
    finally:
        p.terminate(); p.wait(10)

"""화면 서버의 HTTP 요청 처리(Handler): 화면 파일, 상태, 올리기, 만들기, 내려받기."""
import json
import mimetypes
import os
import re
import shutil
import time
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse


from .app_lazy import pipeline
from .app_util import _client_ip, _inside, open_folder, _zip_dir
from .app_mapview import overview_tile
from .app_siteprep import site_info, site_prep
from . import app_state as S
from .app_state import _home, _new_session, _sess, _slog, _sources, _state, _uploads
from .app_sessions import _free_mb, _make_room
from .app_inputs import inspect_source, site_save
from .app_results import _NO_RESULT_PAGE, recent_results, result_dir, _sample_zip
from .app_start import start_job
from .app_admin import _admin_feedback, _dev_login, _feedback, _is_dev


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    _cookie = None
    _feedback = _feedback          # 의견 보내기 (app_admin.py)

    _LIGHT = re.compile(r"^/(|about|legal|app|index\.html|config|status|site/.*|vendor/.*|[\w-]+\.(?:js|css)|feedback|admin/feedback|dev)$")

    def _begin(self):
        """서버 모드: 쿠키로 방문자를 알아보고 그 사람의 칸을 고른다."""
        if not S._READY.is_set() and not self._LIGHT.match(urlparse(self.path).path):
            S._READY.wait(120)
        self._cookie = None
        S._CTX.dev = S.SERVER and _is_dev(self)
        if not S.SERVER:
            S._CTX.sess = None
            return
        import secrets
        m = re.search(r"(?:^|;\s*)csid=([A-Za-z0-9_-]{16,64})", self.headers.get("Cookie") or "")
        sid = m.group(1) if m else None
        with S.LOCK:
            s = S.SESSIONS.get(sid) if sid else None
            if not s:
                sid = sid or secrets.token_urlsafe(18)
                s = S.SESSIONS[sid] = _new_session(sid, os.path.join(S.DATA, "sessions", sid))
                # 쿠키는 방문자를 알아보는 이름표일 뿐: 길게 둔다. 올린 파일·결과는 서버가 1시간 쉬면 따로 지운다.
                # (예전엔 처음 들어온 때부터 60분이라, 계속 쓰던 사람도 1시간 뒤 새 방문자가 되어 결과를 잃었다)
                self._cookie = f"csid={sid}; Path=/; Max-Age={30 * 24 * 3600}; HttpOnly; SameSite=Lax"
                if os.environ.get("CONTOUR_HTTPS") == "1":
                    self._cookie += "; Secure"
                if os.path.isdir(s["home"]):     # 서버를 다시 켠 뒤: 마지막으로 쓴 때를 폴더 시각에서 이어받음
                    s["at"] = max(os.path.getmtime(s["home"]), time.time() - 60)
            if time.time() - s["at"] > 60 and os.path.isdir(s["home"]):
                try:                                 # 쓰는 중이라는 표시를 디스크에도 (서버를 다시 켜도 바로 지우지 않게)
                    os.utime(s["home"])
                except OSError:
                    pass
            s["at"] = time.time()
        S._CTX.sess = s

    def end_headers(self):
        if self._cookie:
            self.send_header("Set-Cookie", self._cookie)
            self._cookie = None
        super().end_headers()

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        if not os.path.isfile(path):
            return self._send(404, {"error": "없음"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".js"):
            ctype = "text/javascript"
        elif path.endswith(".css"):            # 윈도우 레지스트리 값에 휘둘리지 않게 (틀리면 브라우저가 모양 파일을 버림)
            ctype = "text/css"
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        if path.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-cache")   # 새 판으로 바꾼 뒤 옛 화면이 남지 않게
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        self._begin()
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = unquote(u.path)
        if S.SERVER and path in ("/", "/about"):       # 공개 사이트 첫 화면: 소개 → '바로 써 보기'
            return self._file(os.path.join(S.WEB, "site", "index.html"))
        if S.SERVER and path == "/legal":
            return self._file(os.path.join(S.WEB, "site", "legal.html"))
        if S.SERVER and path.startswith("/site/"):
            p = os.path.normpath(os.path.join(S.WEB, path.lstrip("/")))
            return self._file(p) if p.startswith(os.path.join(S.WEB, "site")) else self._send(403, {})
        if path in ("/", "/index.html", "/app"):
            return self._file(os.path.join(S.WEB, "index.html"))
        if path == "/config":                        # 화면이 서버 모드인지 알고 맞는 칸만 보이게
            from . import __version__
            return self._send(200, dict(server=S.SERVER, version=__version__, dev=S.SERVER and S._CTX.dev,
                                        limits=S.LIMITS if S.SERVER else None,
                                        keys=dict(vworld=bool(S.SERVER_KEYS["vworld_key"]),
                                                  data=bool(S.SERVER_KEYS["data_key"])) if S.SERVER else None))
        if path == "/download":                      # 결과 파일 내려받기 (서버 모드에서 '폴더 열기' 대신)
            d = result_dir()
            if not d:
                if "text/html" in (self.headers.get("Accept") or ""):
                    return self._send(404, _NO_RESULT_PAGE, "text/html; charset=utf-8")
                return self._send(404, {"error": "결과가 없습니다"})
            name = os.path.basename(q.get("name") or "")
            stem = os.path.basename(d)
            if not name:                             # 전부 한 zip 으로
                return self._attach(_zip_dir(d), stem + ".zip")
            p = os.path.join(d, name)
            if os.path.isdir(p):
                return self._attach(_zip_dir(d, [name]), f"{stem}_{name}.zip")
            if not os.path.isfile(p):
                return self._send(404, {"error": "없음"})
            return self._attach(open(p, "rb").read(), name)
        if path.startswith("/live/"):
            job = _state()["job"]
            name = os.path.basename(path)
            return self._file(os.path.join(job.live, name)) if job else self._send(404, {})
        if re.fullmatch(r"/[\w-]+\.(?:js|css)", path):     # 화면 스크립트(ui_*.js 등)·모양(app.css)
            return self._file(os.path.join(S.WEB, path.lstrip("/")))
        if path.startswith("/vendor/"):
            p = os.path.normpath(os.path.join(S.WEB, path.lstrip("/")))
            return self._file(p) if p.startswith(os.path.join(S.WEB, "vendor")) else self._send(403, {})
        if path == "/sample.zip":                    # 예시 도엽(지어낸 지형): 수치지도가 없는 첫 방문자도 바로 해 볼 수 있게 (v2.2)
            return self._attach(open(_sample_zip(), "rb").read(), "예시도엽_지어낸지형.zip")
        if path == "/overview.png":
            src = _sources().get(q.get("id"))
            return self._file(os.path.join(src["work"], "overview.png")) if src else self._send(404, {})
        if path == "/status":
            job = _state()["job"]
            if not job:
                return self._send(200, {"running": False})
            since, ev = int(q.get("since") or 0), int(q.get("ev") or 0)
            events = job.progress.events
            return self._send(200, dict(running=not job.done, done=job.done, error=job.error,
                                        lines=job.lines[since:], n=len(job.lines), out=job.out,
                                        events=events[ev:], ne=len(events), cancelled=job.cancelled and not job.timed_out, refunded=getattr(job, "refunded", False),
                                        summary=job.summary, elapsed=round(time.time() - job.started),
                                        queue=job.queue_pos() if S.SERVER and not job.done else 0))
        if path.startswith("/result/"):
            d = result_dir()
            name = os.path.basename(path)
            if not d or not (name in ("model.glb", "preview_plan.png", "summary.json", "분석_경사.png", "분석_절성토.png",
                                      "site_diagram.svg") or re.fullmatch(r"section_\d+\.svg", name)):
                return self._send(404, {})
            return self._file(os.path.join(d, name))
        if path == "/files":                   # 완성 화면의 파일 목록 (폴더 안 laser 등 하위 폴더도 하나로)
            d = result_dir()
            if not d:
                return self._send(200, {"files": []})
            fs = []
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    fs.append(dict(name=n, size=os.path.getsize(p)))
                elif os.path.isdir(p):
                    sub = [os.path.join(p, x) for x in os.listdir(p)]
                    fs.append(dict(name=n, dir=True, count=len(sub),
                                   size=sum(os.path.getsize(x) for x in sub if os.path.isfile(x))))
            summ = {}
            try:
                summ = json.load(open(os.path.join(d, "summary.json"), encoding="utf-8"))
            except Exception:
                pass
            return self._send(200, {"files": fs, "dir": d, "name": os.path.basename(d), "summary": summ})
        if path == "/admin/feedback":
            return _admin_feedback(self, q)
        if S.SERVER and path == "/dev":
            return _dev_login(self, q)
        if path == "/recent":                  # 지난 결과 목록
            return self._send(200, {"items": recent_results(), "home": None if S.SERVER else _home()})
        if path == "/overview_tile.png":       # 지도 확대: 보이는 부분만 선명하게
            src = _sources().get(q.get("id"))
            if not src:
                return self._send(404, {})
            bb = [float(v) for v in (q.get("bbox") or "").split(",")]
            return self._send(200, overview_tile(src, bb, float(q.get("px") or 1000)), "image/png")
        self._send(404, {"error": "없음"})

    def _attach(self, data, name):
        from urllib.parse import quote
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "contour_model"
        if "." in name and "." not in ascii_name:
            ascii_name += os.path.splitext(name)[1]
        self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self._begin()
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if S.SERVER:
            if u.path in ("/inspect", "/open", "/testapi"):      # PC 경로·폴더 열기·키 시험은 내 PC 판에서만
                self.rfile.read(n)
                return self._send(403, {"error": "이 사이트에서는 쓸 수 없는 기능입니다."})
            if n > S.LIMITS["max_upload_mb"] * 1024 * 1024:
                self.close_connection = True
                return self._send(413, {"error": f"파일이 너무 큽니다. {S.LIMITS['max_upload_mb']}MB 까지 올릴 수 있습니다."})
            if u.path == "/feedback":
                return self._feedback(n)
            if u.path in ("/upload", "/siteupload"):
                _make_room(_sess()["sid"])
                if _free_mb() < n / 1e6 + 300:
                    self.rfile.read(n)
                    _slog("업로드 거절: 디스크 부족", f"{_free_mb():.0f}MB 남음")
                    return self._send(507, {"error": "서버 저장 공간이 잠시 모자라요. 몇 분 뒤 다시 올려 주세요."})
        try:
            if u.path == "/upload":
                name = unquote(self.headers.get("X-Filename") or "map.zip")
                name = re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(name)) or "map.zip"
                batch = re.sub(r"\W+", "", self.headers.get("X-Batch") or "")[:40]
                count = int(self.headers.get("X-Count") or 1)
                prev = _sources().get(unquote(self.headers.get("X-Add") or ""))   # 이미 넣은 도엽에 옆 도엽 더하기
                prev_paths = [q for q in (prev or {}).get("paths", []) if os.path.exists(q)]
                updir = os.path.join(_uploads(), "_batch_" + batch) if batch else _uploads()
                os.makedirs(updir, exist_ok=True)
                dest = os.path.join(updir, name)
                with open(dest, "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                if batch and count > 1:        # 여러 도엽: 다 올라오면 한꺼번에 잇는다
                    got = sorted(os.path.join(updir, f) for f in os.listdir(updir)
                                 if os.path.isfile(os.path.join(updir, f)))
                    if len(got) < count:
                        return self._send(200, {"pending": len(got), "count": count})
                    names = {os.path.basename(g) for g in got}
                    return self._send(200, inspect_source([q for q in prev_paths if os.path.basename(q) not in names] + got))
                if prev_paths:
                    keep = [q for q in prev_paths if os.path.basename(q) != os.path.basename(dest)]
                    return self._send(200, inspect_source(keep + [dest]))
                return self._send(200, inspect_source(dest))
            if u.path == "/siteupload":        # 대지 경계 파일(DXF·SHP zip·GeoJSON)
                src = _sources().get(unquote(self.headers.get("X-Source") or ""))
                if not src:
                    self.rfile.read(n)
                    raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
                name = unquote(self.headers.get("X-Filename") or "site.dxf")
                name = re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(name)) or "site.dxf"
                updir = os.path.join(_uploads(), "_site")
                os.makedirs(updir, exist_ok=True)
                dest = os.path.join(updir, name)
                with open(dest, "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                try:
                    return self._send(200, site_info(src, path=dest))
                except Exception as e:      # 경계를 못 찾아도 화면에서 레이어를 직접 골라 정리할 수 있게 경로를 함께 준다
                    from .site import SiteError
                    if isinstance(e, (SiteError, ValueError)) and str(e) and dest.lower().endswith(".dxf"):
                        return self._send(400, {"error": str(e), "path": dest, "prep": True})
                    raise
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/section":           # 지형 단면 (v2.2): 지도에 그은 선 → 결과 폴더에 SVG·DXF
                d = result_dir()
                if not d or not os.path.exists(os.path.join(d, "section_data.npz")):
                    raise ValueError("먼저 모델을 만들어 주세요. 단면은 만든 모델에서 잘라요.")
                from .section import make_section
                sv, dx, info = make_section(d, body["a"], body["b"], float(body.get("depth") or 200))
                return self._send(200, dict(svg=sv, dxf=dx, **info))
            if u.path == "/site":              # 경로로 준 경계 파일, 또는 화면에서 찍은 점
                src = _sources().get(body.get("source"))
                if not src:
                    raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
                path = (body.get("path") or "").strip().strip('"') or None
                if S.SERVER and path and not _inside(path, _home()):
                    raise ValueError("이 사이트에서는 직접 올린 파일만 쓸 수 있습니다.")
                return self._send(200, site_info(src, path=path, ring=body.get("ring")))
            if u.path in ("/siteprep", "/sitesave"):   # 대지 파일 정리 화면
                src = _sources().get(body.get("source"))
                if not src:
                    raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
                path = (body.get("path") or "").strip().strip('"')
                if not path or not os.path.isfile(path) or (S.SERVER and not _inside(path, _home())):
                    raise ValueError("정리할 대지 파일을 먼저 넣어 주세요.")
                return self._send(200, site_prep(src, path) if u.path == "/siteprep"
                                  else site_save(src, path, body.get("roles") or {}))
            if u.path == "/inspect":           # 이미 PC 에 있는 zip/폴더 경로
                ps = [x.strip().strip('"') for x in re.split(r"[\n;]", body.get("path", "")) if x.strip()]
                for p in ps:
                    if not os.path.exists(p):
                        raise ValueError(f"파일을 찾지 못했습니다: {p}")
                if not ps:
                    raise ValueError("파일 경로를 적어 주세요.")
                return self._send(200, inspect_source(ps if len(ps) > 1 else ps[0]))
            if u.path == "/run":
                if S.SERVER:
                    body["_ip"] = _client_ip(self)
                    body.pop("out", None)
                job = start_job(body)
                return self._send(200, {"out": job.out})
            if u.path == "/testapi":           # 화면의 '연결 시험'
                from .publicdata import selftest
                src = _sources().get(body.get("source"))
                if not src:
                    raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
                crs = pipeline.read_crs(src["folder"])
                sheet = pipeline.sheet_bounds(src["folder"])
                center = body.get("xy") or [(sheet[0] + sheet[2]) / 2, (sheet[1] + sheet[3]) / 2]
                bbox = pipeline.model_bbox(center, (float(body.get("w") or 500), float(body.get("h") or 500)), sheet)
                res = selftest(bbox, crs.to_wkt(), (body.get("vworld_key") or "").strip(),
                               (body.get("data_key") or "").strip(), (body.get("vworld_domain") or "").strip())
                if (body.get("seoul_key") or "").strip() or (body.get("safemap_key") or "").strip():
                    from .crosswalk_api import selftest as cw_test
                    res += [dict(name=a, ok=b, msg=c) for a, b, c in cw_test(
                        bbox, crs.to_wkt(), (body.get("seoul_key") or "").strip(), (body.get("safemap_key") or "").strip(),
                        (body.get("seoul_service") or "").strip() or None, (body.get("safemap_layer") or "").strip() or None,
                        safemap_light_layer=(body.get("safemap_light_layer") or "").strip() or None,
                        code=(body.get("seoul_code") or "").strip() or None)]
                return self._send(200, {"results": res})
            if u.path == "/landlaw":           # 화면의 '법규 검토'
                from pyproj import CRS
                src = _sources().get(body.get("source"))
                crs = pipeline.read_crs(src["folder"]) if src else CRS.from_epsg(5186)
                key = S.SERVER_KEYS["vworld_key"] if S.SERVER else (body.get("vworld_key") or "").strip()
                if S.SERVER:
                    body["vworld_domain"] = S.SERVER_KEYS["vworld_domain"]
                if not key:
                    raise ValueError("브이월드 인증키를 넣어 주세요(3번 가로수 칸 또는 4번 칸).")
                site = pipeline.site_review((body.get("address") or "").strip(), crs, key,
                                            (body.get("vworld_domain") or "").strip(), log=lambda *_: None,
                                            raise_errors=True)
                poly = site["poly"]
                g = max(getattr(poly, "geoms", [poly]), key=lambda q: q.area)
                c = poly.centroid
                in_sheet = None
                if src:
                    from shapely.geometry import box
                    in_sheet = bool(box(*pipeline.sheet_bounds(src["folder"])).contains(c))
                return self._send(200, dict(review=site["review"], xy=[c.x, c.y], in_sheet=in_sheet,
                                            ring=[list(p[:2]) for p in g.exterior.coords],
                                            rings=[[list(p[:2]) for p in q.exterior.coords]
                                                   for q in getattr(poly, "geoms", [poly])],
                                            area=round(poly.area, 1), bounds=list(poly.bounds)))
            if u.path == "/open":
                d = result_dir() or (_state()["job"].out if _state()["job"] else None)
                if d:
                    name = os.path.basename(str(body.get("name") or ""))
                    target = os.path.join(d, name) if name else d
                    open_folder(target if os.path.exists(target) else d)
                return self._send(200, {})
            if u.path == "/view":              # '최근 결과'에서 지난 결과 열기
                d = os.path.join(_home(), os.path.basename(str(body.get("dir") or "")) or "_")
                if not os.path.isfile(os.path.join(d, "summary.json")):
                    raise ValueError("그 결과 폴더를 찾지 못했습니다. 옮기거나 지웠을 수 있어요.")
                _state()["view"] = d
                return self._send(200, {"dir": d})
            if u.path == "/cancel":            # 화면의 '멈추기'
                job = _state()["job"]
                if job and not job.done:
                    job.cancelled = True
                return self._send(200, {})
        except SystemExit as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            from .site import SiteError
            if isinstance(e, (SiteError, ValueError)) and str(e):
                return self._send(400, {"error": str(e)})
            # 예상 못 한 오류: 검은 창에 자세한 기록을 남기고 화면에는 종류와 내용을 보인다
            traceback.print_exc()
            return self._send(500, {"error": f"{u.path} 처리 중 예상 못 한 오류 ({type(e).__name__}: {e}). "
                                             + ("오른쪽 아래 '의견 보내기'로 알려 주시면 고치겠습니다." if S.SERVER
                                             else "검은 창에 나온 내용을 알려 주시면 고치겠습니다.")})
        self._send(404, {"error": "없음"})

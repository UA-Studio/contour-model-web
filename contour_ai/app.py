"""사용 화면: 브라우저에서 zip을 넣고 범위를 고른 뒤 버튼 하나로 모델을 만든다.

python -m contour_ai          → 화면 열기 (http://127.0.0.1:8765)
외부 패키지 없이 파이썬 표준 http.server 로 돌아간다. 3D 미리보기용 three.js 는 web/vendor 에 들어 있다.
"""
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


# 기능별 파일에서 가져옴 (예전처럼 app.X 로 불러도 동작).
from .app_lazy import _LazyModule, pipeline  # noqa: F401
from .app_util import _client_ip, _inside, open_folder, _zip_dir  # noqa: F401
from .app_mapview import _named_places, _overview_png, overview_tile, _TILE  # noqa: F401
from .app_siteprep import _is_std, PREP, site_info, site_prep  # noqa: F401

_READY = threading.Event()          # 서버: 무거운 라이브러리를 다 불러왔는가 (내 PC 판은 처음부터 참)
_READY.set()


def _warm_up():
    """서버가 켜진 뒤 한 줄에서만 무거운 라이브러리를 불러온다. 여러 요청이 동시에 불러오면
    shapely 가 '반쯤 불러온 모듈' 오류를 낸다(2026-11-20 시험에서 발견) → 다른 요청은 _READY 를 기다린다."""
    try:
        from . import pipeline as _p, progress, site, sitefile  # noqa: F401
        pipeline._mod = _p
    except Exception:
        traceback.print_exc()
    finally:
        _READY.set()


def Progress(*a, **k):
    from .progress import Progress as _P
    return _P(*a, **k)

WEB = os.path.join(os.path.dirname(__file__), "web")
HOME = os.path.join(os.path.expanduser("~"), "콘타모델")
UPLOADS = os.path.join(HOME, "_입력")

LOCK = threading.Lock()

# ---------------------------------------------------------------- 공개 서버 모드
# CONTOUR_SERVER=1 이면 여러 사람이 함께 쓰는 웹 서비스로 돈다:
#   방문자마다 따로 쓰는 칸(쿠키), 작업은 서버 전체에서 한 번에 하나씩 순서대로,
#   PC 경로 입력·폴더 열기·정밀도로지도는 막고, 인증키는 서버 환경변수에서만 쓴다.
SERVER = os.environ.get("CONTOUR_SERVER") == "1"
DATA = os.environ.get("CONTOUR_DATA") or os.path.join(HOME, "_server")
LIMITS = dict(
    max_side=float(os.environ.get("CONTOUR_MAX_SIDE") or 1000),          # 모델 한 변 최대 (m)
    runs_per_day=int(os.environ.get("CONTOUR_RUNS_PER_DAY") or 5),       # 한 사람(IP)이 하루에 만들 수 있는 횟수
    max_upload_mb=int(os.environ.get("CONTOUR_MAX_UPLOAD_MB") or 150),   # 파일 하나 최대 크기
    job_minutes=int(os.environ.get("CONTOUR_JOB_MINUTES") or 12),        # 한 작업 최대 시간
    keep_minutes=int(os.environ.get("CONTOUR_KEEP_MINUTES") or 60),      # 아무것도 안 하면 올린 파일·결과를 지우는 시간 (분)
    min_free_mb=int(os.environ.get("CONTOUR_MIN_FREE_MB") or 1500),      # 디스크 남은 공간이 이보다 적으면 오래 쉰 칸부터 지움
    max_queue=int(os.environ.get("CONTOUR_MAX_QUEUE") or 20),            # 대기 줄 최대 길이
)
SERVER_KEYS = dict(vworld_key=os.environ.get("VWORLD_KEY") or None,
                   vworld_domain=os.environ.get("VWORLD_DOMAIN") or "",
                   data_key=os.environ.get("DATA_GO_KR_KEY") or None,
                   seoul_key=os.environ.get("SEOUL_KEY") or None,
                   safemap_key=os.environ.get("SAFEMAP_KEY") or None)
_CTX = threading.local()
SESSIONS = {}                  # 방문자 id → 그 사람만의 상태·폴더
RUNS = {}                      # (IP, 날짜) → 오늘 만든 횟수
QUEUE = []                     # 기다리는 작업 (서버 모드)
QCOND = threading.Condition()


class _Sources(dict):
    """넣은 수치지도 목록. 메모리에 없으면(서버가 새 판 배포·쉬었다 깨어남으로 다시 켜진 뒤) 디스크의 기록에서 되살린다."""

    def __init__(self, uploads):
        super().__init__()
        self.uploads = uploads

    def _load(self, sid):
        if not sid or not isinstance(sid, str) or "/" in sid or "\\" in sid or sid.startswith("."):
            return None
        fn = os.path.join(self.uploads, sid, "_source.json")
        try:
            with open(fn, encoding="utf-8") as f:
                src = json.load(f)
        except (OSError, ValueError):
            return None
        if not os.path.isdir(src.get("folder") or ""):
            return None
        dict.__setitem__(self, sid, src)
        return src

    def __setitem__(self, sid, src):
        super().__setitem__(sid, src)
        try:
            with open(os.path.join(src["work"], "_source.json"), "w", encoding="utf-8") as f:
                json.dump(src, f, ensure_ascii=False)
        except (OSError, KeyError, TypeError):
            pass

    def get(self, sid, default=None):
        v = super().get(sid) if isinstance(sid, str) else None
        return v if v is not None else (self._load(sid) or default)

    def __getitem__(self, sid):
        v = self.get(sid)
        if v is None:
            raise KeyError(sid)
        return v

    def __contains__(self, sid):
        return self.get(sid) is not None


def _new_session(sid, home):
    uploads = os.path.join(home, "_입력")
    return dict(sid=sid, home=home, uploads=uploads, state={"job": None}, sources=_Sources(uploads),
                at=time.time())


_LOCAL = _new_session("local", HOME)


def _sess():
    return getattr(_CTX, "sess", None) or _LOCAL


def _state():
    return _sess()["state"]


def _sources():
    return _sess()["sources"]


def _uploads():
    return _sess()["uploads"]


def _home():
    return _sess()["home"]


def _queue_worker():
    while True:
        with QCOND:
            while not QUEUE:
                QCOND.wait()
            job = QUEUE[0]
        if not job.cancelled:
            timer = threading.Timer(LIMITS["job_minutes"] * 60, job.timeout)
            timer.start()
            try:
                job.run()
            finally:
                timer.cancel()
        else:
            job.error, job.done = "멈췄습니다", True
        with QCOND:
            QUEUE.pop(0)


def _busy(s):
    job = s["state"].get("job")
    return bool(job and not job.done)


def _drop_session(sid):
    s = SESSIONS.pop(sid, None)
    if s:
        shutil.rmtree(s["home"], ignore_errors=True)


def _free_mb():
    try:
        return shutil.disk_usage(DATA).free / 1e6
    except OSError:
        return 1e9


def _make_room(keep_sid=None):
    """디스크가 모자라면 만드는 중이 아닌 칸을 오래 쉰 것부터 지운다 (지금 쓰는 사람 칸은 마지막까지 남김)."""
    if _free_mb() >= LIMITS["min_free_mb"]:
        return
    with LOCK:
        idle = sorted((s["at"], sid) for sid, s in SESSIONS.items() if sid != keep_sid and not _busy(s))
    root = os.path.join(DATA, "sessions")
    orphans = []
    if os.path.isdir(root):                       # 서버를 다시 켜서 목록에 없는 옛 폴더
        orphans = sorted((os.path.getmtime(os.path.join(root, d)), d) for d in os.listdir(root)
                         if d not in SESSIONS and d != keep_sid)
    for _, d in orphans:
        if _free_mb() >= LIMITS["min_free_mb"]:
            return
        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        _slog("공간 확보: 옛 폴더 지움", d[:6])
    for _, sid in idle:
        if _free_mb() >= LIMITS["min_free_mb"]:
            return
        _drop_session(sid)
        _slog("공간 확보: 쉬는 칸 지움", sid[:6])


def _drop_old_results(keep):
    """서버: 방문자마다 가장 최근 결과 하나만 남긴다 (새로 만들기 시작하면 이전 결과를 지움)."""
    home = _home()
    if not os.path.isdir(home):
        return
    for d in os.listdir(home):
        p = os.path.join(home, d)
        # 모델 결과 폴더(summary.json 또는 model.3dm 이 있는 것)만 지운다. 올린 지도(_입력)와 정리해 둔 대지 파일(대지표준)은
        # 다시 만들 때 쓰므로 남긴다 (2026-11-20: 대지표준까지 지워 '대지 경계 파일을 찾지 못했습니다' 오류).
        is_result = any(os.path.exists(os.path.join(p, n)) for n in ("summary.json", "model.3dm", "laser"))
        if (is_result and not d.startswith("_") and d != "대지표준" and os.path.isdir(p)
                and os.path.realpath(p) != os.path.realpath(keep)):
            shutil.rmtree(p, ignore_errors=True)
    _state()["view"] = None


def _cleanup_loop():
    """한동안(기본 60분) 아무것도 안 한 방문자 칸(올린 지도·결과)을 지우고, 디스크가 모자라면 더 지운다."""
    while True:
        time.sleep(120)
        try:
            limit = time.time() - LIMITS["keep_minutes"] * 60
            for sid, s in list(SESSIONS.items()):
                if s["at"] < limit and not _busy(s):
                    _drop_session(sid)
            root = os.path.join(DATA, "sessions")
            if os.path.isdir(root):                      # 서버를 다시 켜서 목록에 없는 옛 폴더도
                for d in os.listdir(root):
                    p = os.path.join(root, d)
                    if d not in SESSIONS and os.path.getmtime(p) < limit:
                        shutil.rmtree(p, ignore_errors=True)
            _make_room()
            today = time.strftime("%Y%m%d")
            for k in [k for k in RUNS if k[1] != today]:
                RUNS.pop(k, None)
        except Exception:                                # 정리 줄이 죽으면 디스크가 다시 찬다
            traceback.print_exc()


# ---------------------------------------------------------------- 입력 살펴보기
def inspect_source(path):
    """zip(하나 또는 여러 개)을 풀어 도엽 범위·이름 목록·개관도를 만든다. 여러 도엽은 한 장으로 잇는다."""
    paths = [path] if isinstance(path, str) else list(path)
    first = os.path.splitext(os.path.basename(paths[0].rstrip("/\\")))[0]
    sid = re.sub(r"[^A-Za-z0-9]+", "_", first).strip("_")[:40] + (f"_{len(paths)}" if len(paths) > 1 else "") + f"_{int(time.time())}"
    work = os.path.join(_uploads(), sid)
    os.makedirs(work, exist_ok=True)
    msgs = []
    folder = pipeline.prepare_source(paths if len(paths) > 1 else paths[0], work, log=msgs.append)
    path = paths[0] if len(paths) == 1 else f"{os.path.basename(paths[0])} 외 {len(paths) - 1}개"
    nsheet = 1
    sj = os.path.join(folder, "sheets.json")
    if os.path.exists(sj):
        nsheet = len(json.load(open(sj, encoding="utf-8"))["sheets"])
    crs = pipeline.read_crs(folder)
    sheet = pipeline.sheet_bounds(folder)
    names = _named_places(folder)
    _overview_png(folder, sheet, os.path.join(work, "overview.png"))
    lat, lon = pipeline.to_latlon(crs, (sheet[0] + sheet[2]) / 2, (sheet[1] + sheet[3]) / 2)
    _sources()[sid] = dict(zip=path, folder=folder, work=work, paths=[os.path.abspath(p) for p in paths], file=os.path.basename(path))
    files = []
    for p in paths:
        m = re.search(r"(?<!\d)(\d{8})(?!\d)", os.path.basename(p.rstrip("/\\")))
        files.append(dict(name=os.path.basename(p.rstrip("/\\")), sheet_no=m.group(1) if m else None))
    nos = [f["sheet_no"] for f in files if f["sheet_no"]]
    label = ("도엽" + "·".join(nos)) if nos else os.path.splitext(files[0]["name"])[0]
    return dict(id=sid, file=os.path.basename(path), files=files, label=label, crs=crs.name, sheet=list(sheet), sheets=nsheet,
                size=[round(sheet[2] - sheet[0]), round(sheet[3] - sheet[1])],
                center_latlon=[round(lat, 5), round(lon, 5)], names=names)


def site_save(src, path, roles):
    """고른 역할대로 표준 대지 파일을 저장하고, 그 파일을 대지 경계로 읽어 돌려준다"""
    from . import sitefile
    if (src["folder"], path) not in PREP:
        site_prep(src, path)
    an = PREP[(src["folder"], path)]
    msgs = []
    std = sitefile.build_standard(an, roles, log=msgs.append)
    outdir = os.path.join(_home(), "대지표준")
    os.makedirs(outdir, exist_ok=True)
    stem = re.sub(r'[\\/:*?"<>|]+', "_", os.path.splitext(os.path.basename(path))[0])
    out = os.path.join(outdir, (stem[:-3] if stem.endswith("_표준") else stem) + "_표준.dxf")
    info = sitefile.write_standard(std, out, an, roles, os.path.basename(path), log=msgs.append)
    v = site_info(src, path=out)
    return dict(v, saved=out, info=info, notes=[m.strip() for m in msgs])


# ---------------------------------------------------------------- 작업
def _slog(*a):
    """서버 기록(fly logs)에 한 줄. 화면에 안 보이는 멈춤·실패 이유를 나중에 찾을 수 있게."""
    if SERVER:
        print(time.strftime("[%H:%M:%S]"), *a, flush=True)


class Cancelled(BaseException):
    """화면의 '멈추기'. BaseException 이라 중간의 except Exception 에 삼켜지지 않는다."""


class Job:
    def __init__(self, params):
        self.params, self.lines, self.done, self.error, self.summary = params, [], False, None, None
        self.out = params["out"]
        self.started = time.time()
        self.live = os.path.join(_uploads(), "_live")      # 생성 과정 실황 그림·부품 (작업마다 비움)
        shutil.rmtree(self.live, ignore_errors=True)
        self.progress = Progress(self.live)
        self.cancelled = False
        self.timed_out = False
        self.src = _sources()[params["source"]]           # 작업은 따로 도는 줄에서 돌아서 미리 잡아 둔다
        emit = self.progress._emit

        def _emit(**ev):           # 진행 신호가 올 때마다 멈추기 확인
            if self.cancelled:
                raise Cancelled()
            emit(**ev)
        self.progress._emit = _emit

    def timeout(self):
        self.timed_out = self.cancelled = True

    def queue_pos(self):
        with QCOND:
            return QUEUE.index(self) if self in QUEUE else 0

    def log(self, *a):
        if self.cancelled:
            raise Cancelled()
        self.lines.append(" ".join(str(x) for x in a))

    def run(self):
        p = dict(self.params)
        _slog("작업 시작", f"범위 {p.get('size')}", f"지형 {p.get('terrain')}/{p.get('terrain_fit')}", f"디테일 {p.get('detail')}")
        try:
            p.pop("source")
            src = self.src
            self.started = time.time()
            if SERVER:                                 # 작업 중 임시 파일은 끝나면 바로 지운다
                import tempfile
                self.tmp = tempfile.tempdir = os.path.join(os.path.dirname(self.out), "_tmp")
                os.makedirs(self.tmp, exist_ok=True)
            if SERVER:
                self.summary = self._run_in_process(src["folder"], p.pop("out"), p)
            else:
                self.summary = pipeline.run(src["folder"], p.pop("out"), log=self.log, progress=self.progress, **p)
            self.summary["source"] = os.path.basename(src["zip"])
            # 화면 미리보기용: 결과 폴더 안에 이 화면 파일도 넣어 둔다(나중에 다시 열기)
        except Cancelled:
            if self.timed_out:
                self.error = f"{LIMITS['job_minutes']}분 안에 끝나지 않아 멈췄습니다. 범위를 줄이거나 디테일을 낮춰 다시 해 보세요."
                self.lines.append(self.error)
                return
            self.error = "멈췄습니다"
            self.lines.append("멈추기를 눌러 중간에 멈췄습니다.")
        except SystemExit as e:
            self.error = str(e)
        except BaseException as e:
            self.error = f"{type(e).__name__}: {e}"
            self.lines.append(traceback.format_exc())
        finally:
            if getattr(self, "tmp", None):
                shutil.rmtree(self.tmp, ignore_errors=True)
            self.done = True
            took = round(time.time() - self.started)
            if self.error:
                _slog("작업 실패", f"{took}초", self.error[:300])
            else:
                _slog("작업 완료", f"{took}초")


def _child_run(folder, out, params, live, tmp, q):
    """서버 모드: 모델 만들기를 따로 된 프로세스에서 돌린다.
    Rhino 저장(rhino3dm)이 파이썬 전체를 붙잡아도 웹 서버는 계속 대답하고, 멈추기·시간 제한 때 확실히 끝낼 수 있다."""
    import tempfile
    tempfile.tempdir = tmp
    from . import pipeline as pl
    from .progress import Progress
    pg = Progress(live)

    def emit(**ev):
        ev["at"] = round(time.time(), 2)
        q.put(("ev", ev))
    pg._emit = emit
    try:
        summ = pl.run(folder, out, log=lambda *a: q.put(("log", " ".join(str(x) for x in a))), progress=pg, **params)
        q.put(("done", json.loads(json.dumps(summ, default=str))))
    except SystemExit as e:
        q.put(("err", str(e), ""))
    except BaseException as e:
        q.put(("err", f"{type(e).__name__}: {e}", traceback.format_exc()))


def _job_process(self, folder, out, params):
    import multiprocessing as mp
    import queue as _q
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    params = dict(params)
    params.pop("progress", None)
    proc = ctx.Process(target=_child_run, args=(folder, out, params, self.live, self.tmp, q), daemon=True)
    proc.start()
    result, err = None, None
    try:
        while True:
            if self.cancelled:
                proc.terminate()
                proc.join(5)
                raise Cancelled()
            try:
                msg = q.get(timeout=0.5)
            except _q.Empty:
                if not proc.is_alive():
                    try:                      # 끝나는 순간 남은 신호
                        msg = q.get(timeout=1)
                    except _q.Empty:
                        code = proc.exitcode
                        raise RuntimeError("모델을 만드는 중 서버 메모리가 모자라 멈췄습니다. 범위를 줄이거나 디테일을 낮춰 다시 해 보세요."
                                           if code in (-9, 137) else f"모델 만들기가 중간에 끝났습니다 (코드 {code}).")
                else:
                    continue
            kind = msg[0]
            if kind == "log":
                self.lines.append(msg[1])
            elif kind == "ev":
                with self.progress._lock:
                    self.progress.events.append(msg[1])
            elif kind == "done":
                result = msg[1]
                break
            elif kind == "err":
                err = msg
                break
    finally:
        if proc.is_alive():
            proc.join(10)
            if proc.is_alive():
                proc.terminate()
    if err:
        if err[2]:
            self.lines.append(err[2])
        raise SystemExit(err[1])
    return result


Job._run_in_process = _job_process


def start_job(q):
    with LOCK:
        if _state()["job"] and not _state()["job"].done:
            raise ValueError("이미 만들고 있는 모델이 있습니다. 끝난 뒤 다시 눌러 주세요.")
        if q.get("source") not in _sources():
            raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
        size = [float(q.get("w") or 500), float(q.get("h") or q.get("w") or 500)]
        stem = re.sub(r"[^\w가-힣]+", "_", q.get("label") or "모델").strip("_") or "모델"
        out = q.get("out") or os.path.join(_home(), stem[:60] + time.strftime("_%m%d_%H%M"))
        _state()["view"] = None
        params = dict(source=q["source"], out=out, size=size[0] if size[0] == size[1] else tuple(size),
                      xy=tuple(q["xy"]) if q.get("xy") else None, green=bool(q.get("green", True)),
                      year=int(q["year"]) if q.get("year") else None,
                      scale=float(q.get("scale") or 1000), interval=float(q.get("interval") or 5),
                      terrain=q.get("terrain") or "combo", terrain_fit="exact" if q.get("terrain_fit") == "exact" else "smooth", floor_height=float(q.get("floor_height") or 3.3),
                      no_trees=not q.get("street_trees", True), tree_hole=float(q.get("tree_hole") or 1.0),
                      road_thickness=float(q.get("road_thickness") or 1.0), sketchup=bool(q.get("sketchup", True)),
                      vworld_key=(q.get("vworld_key") or "").strip() or None,
                      vworld_domain=(q.get("vworld_domain") or "").strip(),
                      data_key=(q.get("data_key") or "").strip() or None,
                      aerial_file=(q.get("aerial_file") or "").strip().strip('"') or None,
                      site_address=(q.get("site_address") or "").strip() or None,
                      site_file=(q.get("site_file") or "").strip().strip('"') or None,
                      pads=bool(q.get("pads", True)),
                      site_ring=q.get("site_ring") or None, site_clear=bool(q.get("site_clear")), site_keep_terrain=bool(q.get("site_keep_terrain")),
                      lights=bool(q.get("lights", True)), facade=bool(q.get("facade", True)),
                      detail=int(q.get("detail") or 3), structures=bool(q.get("structures", True)),
                      facade_radius=float(q["facade_radius"]) if q.get("facade_radius") else None,
                      light_files=tuple(x for x in [(q.get("light_file") or "").strip().strip('"')] if x),
                      road_detail=bool(q.get("road_detail", True)),
                      seoul_key=(q.get("seoul_key") or "").strip() or None,
                      safemap_key=(q.get("safemap_key") or "").strip() or None,
                      seoul_service=(q.get("seoul_service") or "").strip() or None,
                      seoul_code=(q.get("seoul_code") or "").strip() or None,
                      safemap_layer=(q.get("safemap_layer") or "").strip() or None,
                      safemap_light_layer=(q.get("safemap_light_layer") or "").strip() or None,
                      hd_dir=(q.get("hd_dir") or "").strip().strip('"') or None,
                      dem_files=[x.strip().strip('"') for x in str(q.get("dem_files") or "").replace(";", "\n").splitlines()
                                 if x.strip().strip('"')] or None,
                      road_files=tuple(x.strip().strip('"') for x in str(q.get("road_files") or "").replace(";", "\n").splitlines()
                                       if x.strip().strip('"')))
        if SERVER:
            _server_check(params, q)
        if params["hd_dir"] and not os.path.isdir(params["hd_dir"]):
            raise ValueError(f"정밀도로지도 폴더를 찾지 못했습니다: {params['hd_dir']}")
        for rf in params["road_files"]:
            if not os.path.isfile(rf):
                raise ValueError(f"도로시설 위치 파일을 찾지 못했습니다: {rf}")
        for lf in params["light_files"]:
            if not os.path.isfile(lf):
                raise ValueError(f"가로등 위치 파일을 찾지 못했습니다: {lf}")
        for df in params["dem_files"] or ():
            if not os.path.exists(df):
                raise ValueError(f"DEM 파일을 찾지 못했습니다: {df}")
        if params["site_file"] and not os.path.isfile(params["site_file"]):
            raise ValueError(f"대지 경계 파일을 찾지 못했습니다: {params['site_file']}")
        if params["aerial_file"] and not os.path.isfile(params["aerial_file"]):
            raise ValueError(f"항공사진 파일을 찾지 못했습니다: {params['aerial_file']}")
        if SERVER:
            _drop_old_results(params["out"])
            _make_room(_sess()["sid"])
        job = Job(params)
        _state()["job"] = job
        if SERVER:
            with QCOND:
                QUEUE.append(job)
                QCOND.notify()
        else:
            threading.Thread(target=job.run, daemon=True).start()
        return job


def _server_check(params, q):
    """공개 서버에서 받는 작업 확인: PC 경로·정밀도로지도 금지, 크기 제한, 인증키는 서버 것만."""
    root = _home()
    params["hd_dir"] = None                       # 정밀도로지도는 재배포 금지 자료라 서버에서 쓰지 않는다
    for k in ("aerial_file", "site_file"):
        if params[k] and not _inside(params[k], root):
            raise ValueError("이 사이트에서는 직접 올린 파일만 쓸 수 있습니다.")
    for k in ("road_files", "light_files"):
        params[k] = tuple(x for x in params[k] if _inside(x, root))
    params["dem_files"] = [x for x in (params["dem_files"] or []) if _inside(x, root)] or None
    params["out"] = os.path.join(root, os.path.basename(params["out"]))
    size = params["size"] if isinstance(params["size"], tuple) else (params["size"], params["size"])
    if max(size) > LIMITS["max_side"]:
        raise ValueError(f"이 사이트에서는 한 변 {LIMITS['max_side']:.0f}m 까지 만들 수 있습니다. 범위를 줄여 주세요.")
    params.update(SERVER_KEYS)
    with QCOND:
        if len(QUEUE) >= LIMITS["max_queue"]:
            raise ValueError("지금 기다리는 사람이 많습니다. 잠시 뒤 다시 눌러 주세요.")
    ip, today = q.get("_ip") or "?", time.strftime("%Y%m%d")
    if getattr(_CTX, "dev", False):              # 개발자(관리자 토큰 쿠키): 하루 횟수 제한 없음
        return
    used = RUNS.get((ip, today), 0)
    if used >= LIMITS["runs_per_day"]:
        raise ValueError(f"한 사람이 하루에 {LIMITS['runs_per_day']}번까지 만들 수 있습니다. 내일 다시 써 주세요. "
                         "더 필요하면 오른쪽 아래 '의견 보내기'로 알려 주세요.")
    RUNS[(ip, today)] = used + 1


def result_dir():
    """화면이 보여 줄 결과 폴더: '최근 결과'에서 고른 폴더, 아니면 방금 끝난 작업."""
    if _state().get("view") and os.path.isdir(_state()["view"]):
        return _state()["view"]
    job = _state()["job"]
    if job:
        return job.out if job.done and os.path.isdir(job.out) else None
    if SERVER:
        # 서버가 꺼졌다 켜지면(자동 꺼짐·배포) 메모리의 작업 기록이 사라진다. 디스크에 남은 그 사람의 가장 최근 결과를 쓴다.
        r = recent_results(1)
        return r[0]["path"] if r else None
    return None


_NO_RESULT_PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>내려받을 결과가 없어요</title>
<body style="font-family:Pretendard,system-ui,sans-serif;max-width:520px;margin:15vh auto;padding:0 24px;color:#111;line-height:1.6">
<h2 style="font-size:20px">내려받을 결과를 찾지 못했어요</h2>
<p style="color:#555">결과는 1시간 동안 쓰지 않으면 지워지고, 새로 만들면 이전 결과가 지워져요.
다른 브라우저나 시크릿 창에서 열었을 때도 이전 결과가 보이지 않아요.</p>
<p><a href="/app" style="display:inline-block;background:#111;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none">만들기 화면으로 돌아가기</a></p>
</body></html>"""


def recent_results(n=8):
    """사용자 폴더 › 콘타모델 안의 지난 결과(summary.json 이 있는 폴더), 새것부터."""
    out = []
    if not os.path.isdir(_home()):
        return out
    for d in os.listdir(_home()):
        p = os.path.join(_home(), d)
        sj = os.path.join(p, "summary.json")
        if d.startswith("_") or not os.path.isfile(sj):
            continue
        try:
            s = json.load(open(sj, encoding="utf-8"))
        except Exception:
            s = {}
        t = os.path.getmtime(sj)
        out.append(dict(dir=d, path=p, at=t, when=time.strftime("%m/%d %H:%M", time.localtime(t)),
                        sheets=s.get("sheets"), buildings=s.get("buildings"), seconds=s.get("seconds"),
                        size_mm=s.get("model_size_mm")))
    return sorted(out, key=lambda r: -r["at"])[:n]


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    _cookie = None

    _LIGHT = re.compile(r"^/(|about|legal|app|index\.html|config|status|site/.*|vendor/.*|[\w-]+\.(?:js|css)|feedback|admin/feedback|dev)$")

    def _begin(self):
        """서버 모드: 쿠키로 방문자를 알아보고 그 사람의 칸을 고른다."""
        if not _READY.is_set() and not self._LIGHT.match(urlparse(self.path).path):
            _READY.wait(120)
        self._cookie = None
        _CTX.dev = SERVER and _is_dev(self)
        if not SERVER:
            _CTX.sess = None
            return
        import secrets
        m = re.search(r"(?:^|;\s*)csid=([A-Za-z0-9_-]{16,64})", self.headers.get("Cookie") or "")
        sid = m.group(1) if m else None
        with LOCK:
            s = SESSIONS.get(sid) if sid else None
            if not s:
                sid = sid or secrets.token_urlsafe(18)
                s = SESSIONS[sid] = _new_session(sid, os.path.join(DATA, "sessions", sid))
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
        _CTX.sess = s

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
        if SERVER and path in ("/", "/about"):       # 공개 사이트 첫 화면: 소개 → '바로 써 보기'
            return self._file(os.path.join(WEB, "site", "index.html"))
        if SERVER and path == "/legal":
            return self._file(os.path.join(WEB, "site", "legal.html"))
        if SERVER and path.startswith("/site/"):
            p = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            return self._file(p) if p.startswith(os.path.join(WEB, "site")) else self._send(403, {})
        if path in ("/", "/index.html", "/app"):
            return self._file(os.path.join(WEB, "index.html"))
        if path == "/config":                        # 화면이 서버 모드인지 알고 맞는 칸만 보이게
            from . import __version__
            return self._send(200, dict(server=SERVER, version=__version__, dev=SERVER and _CTX.dev,
                                        limits=LIMITS if SERVER else None,
                                        keys=dict(vworld=bool(SERVER_KEYS["vworld_key"]),
                                                  data=bool(SERVER_KEYS["data_key"])) if SERVER else None))
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
            return self._file(os.path.join(WEB, path.lstrip("/")))
        if path.startswith("/vendor/"):
            p = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            return self._file(p) if p.startswith(os.path.join(WEB, "vendor")) else self._send(403, {})
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
                                        events=events[ev:], ne=len(events), cancelled=job.cancelled and not job.timed_out,
                                        summary=job.summary, elapsed=round(time.time() - job.started),
                                        queue=job.queue_pos() if SERVER and not job.done else 0))
        if path.startswith("/result/"):
            d = result_dir()
            name = os.path.basename(path)
            if not d or name not in ("model.glb", "preview_plan.png", "summary.json", "분석_경사.png", "분석_절성토.png"):
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
        if SERVER and path == "/dev":
            return _dev_login(self, q)
        if path == "/recent":                  # 지난 결과 목록
            return self._send(200, {"items": recent_results(), "home": None if SERVER else _home()})
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
        if SERVER:
            if u.path in ("/inspect", "/open", "/testapi"):      # PC 경로·폴더 열기·키 시험은 내 PC 판에서만
                self.rfile.read(n)
                return self._send(403, {"error": "이 사이트에서는 쓸 수 없는 기능입니다."})
            if n > LIMITS["max_upload_mb"] * 1024 * 1024:
                self.close_connection = True
                return self._send(413, {"error": f"파일이 너무 큽니다. {LIMITS['max_upload_mb']}MB 까지 올릴 수 있습니다."})
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
            if u.path == "/site":              # 경로로 준 경계 파일, 또는 화면에서 찍은 점
                src = _sources().get(body.get("source"))
                if not src:
                    raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
                path = (body.get("path") or "").strip().strip('"') or None
                if SERVER and path and not _inside(path, _home()):
                    raise ValueError("이 사이트에서는 직접 올린 파일만 쓸 수 있습니다.")
                return self._send(200, site_info(src, path=path, ring=body.get("ring")))
            if u.path in ("/siteprep", "/sitesave"):   # 대지 파일 정리 화면
                src = _sources().get(body.get("source"))
                if not src:
                    raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
                path = (body.get("path") or "").strip().strip('"')
                if not path or not os.path.isfile(path) or (SERVER and not _inside(path, _home())):
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
                if SERVER:
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
                key = SERVER_KEYS["vworld_key"] if SERVER else (body.get("vworld_key") or "").strip()
                if SERVER:
                    body["vworld_domain"] = SERVER_KEYS["vworld_domain"]
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
                                             + ("오른쪽 아래 '의견 보내기'로 알려 주시면 고치겠습니다." if SERVER
                                             else "검은 창에 나온 내용을 알려 주시면 고치겠습니다.")})
        self._send(404, {"error": "없음"})


def _feedback(self, n):
    """사이트 '의견 보내기': 서버 자료 폴더의 feedback.jsonl 에 한 줄씩 쌓는다."""
    try:
        body = json.loads(self.rfile.read(n) or b"{}")
    except Exception:
        return self._send(400, {"error": "잘못된 요청"})
    text = str(body.get("text") or "").strip()[:4000]
    if not text:
        return self._send(400, {"error": "내용을 적어 주세요."})
    job = _state().get("job")
    rec = dict(at=time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600)),   # 한국 시간 text=text, kind=str(body.get("kind") or "")[:20],
               rating=body.get("rating") if isinstance(body.get("rating"), int) else None,
               contact=str(body.get("contact") or "").strip()[:200], page=str(body.get("page") or "")[:40],
               sid=_sess()["sid"][:8],
               last_error=(job.error if job else None),
               ua=(self.headers.get("User-Agent") or "")[:200])
    os.makedirs(DATA, exist_ok=True)
    with LOCK, open(os.path.join(DATA, "feedback.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return self._send(200, {"ok": True})


Handler._feedback = _feedback


def _dev_mark():
    """개발자 쿠키 값: 관리자 토큰에서 만든 서명 (토큰 자체는 쿠키에 넣지 않음)."""
    import hashlib
    import hmac
    tok = os.environ.get("CONTOUR_ADMIN_TOKEN")
    return hmac.new(tok.encode(), b"contour-dev", hashlib.sha256).hexdigest() if tok else None


def _is_dev(handler):
    mark = _dev_mark()
    m = re.search(r"(?:^|;\s*)cdev=([0-9a-f]{64})", handler.headers.get("Cookie") or "")
    import hmac
    return bool(mark and m and hmac.compare_digest(m.group(1), mark))


def _dev_login(self, q):
    """/dev?key=관리자토큰 → 이 브라우저를 1년 동안 개발자로 기억하고 /app 으로. /dev?off=1 → 해제."""
    import hmac
    tok = os.environ.get("CONTOUR_ADMIN_TOKEN")
    if q.get("off"):
        cookie = "cdev=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    elif tok and hmac.compare_digest((q.get("key") or "").encode(), tok.encode()):
        cookie = f"cdev={_dev_mark()}; Path=/; Max-Age={365 * 86400}; HttpOnly; SameSite=Lax"
        if os.environ.get("CONTOUR_HTTPS") == "1":
            cookie += "; Secure"
        _slog("개발자 모드 켬")
    else:
        return self._send(403, {"error": "열쇠가 맞지 않습니다."})
    self.send_response(302)
    self.send_header("Set-Cookie", cookie)
    self.send_header("Location", "/app")
    self.send_header("Content-Length", "0")
    self.end_headers()


def _admin_feedback(self, q):
    tok = os.environ.get("CONTOUR_ADMIN_TOKEN")
    if not tok or q.get("token") != tok:
        return self._send(403, {"error": "없음"})
    p = os.path.join(DATA, "feedback.jsonl")
    rows = [json.loads(x) for x in open(p, encoding="utf-8")] if os.path.exists(p) else []
    stats = {"count": len(rows), "queue": len(QUEUE), "sessions": len(SESSIONS),
             "runs_today": sum(v for k, v in RUNS.items() if k[1] == time.strftime("%Y%m%d"))}
    if q.get("format") == "json":
        return self._send(200, dict(stats, items=rows[::-1]))
    from html import escape as h
    kinds = {"bug": "오류", "ux": "불편", "idea": "기능 제안", "etc": "기타"}
    trs = "".join(f"<tr><td>{h(r.get('at', ''))}</td><td>{h(kinds.get(r.get('kind'), r.get('kind') or ''))}</td>"
                  f"<td>{r.get('rating') or ''}</td><td class=t>{h(r.get('text', ''))}"
                  + (f"<small>마지막 오류: {h(r['last_error'])}</small>" if r.get("last_error") else "")
                  + f"</td><td>{h(r.get('contact') or '')}</td><td>{h(r.get('page') or '')}</td></tr>" for r in rows[::-1])
    page = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>받은 의견</title><style>
@font-face{{font-family:P;src:url(/vendor/fonts/PretendardVariable.woff2)}}
body{{font-family:P,system-ui;margin:32px;color:#111;background:#fff}}h1{{font-size:24px}}
.s{{color:#555;margin-bottom:20px}}.s b{{color:#2563eb}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border-bottom:1px solid #e6e6e6;padding:10px 8px;text-align:left;vertical-align:top}}th{{color:#555;font-weight:600}}
td.t{{white-space:pre-wrap;max-width:560px}}small{{display:block;color:#8a8a8a;margin-top:6px}}</style>
<h1>받은 의견 {stats['count']}개</h1>
<p class=s>오늘 만든 모델 <b>{stats['runs_today']}</b>번 · 지금 기다리는 작업 <b>{stats['queue']}</b>개 · 지금 남아 있는 방문 칸 <b>{stats['sessions']}</b>개</p>
<table><tr><th>시간(한국)</th><th>종류</th><th>점수</th><th>내용</th><th>연락처</th><th>위치</th></tr>{trs}</table>"""
    return self._send(200, page, "text/html; charset=utf-8")


def serve():
    """공개 서버 실행 (Docker 안에서): 0.0.0.0:$PORT, 브라우저 열지 않음."""
    os.makedirs(os.path.join(DATA, "sessions"), exist_ok=True)
    threading.Thread(target=_queue_worker, daemon=True).start()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    port = int(os.environ.get("PORT") or 8080)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    from . import __version__
    print(f"콘타 모델 공개 서버 v{__version__} :{port}  제한 {LIMITS}  디스크 남은 공간 {_free_mb():.0f}MB", flush=True)
    # 접속은 바로 받고, 무거운 라이브러리는 뒤에서 한 번만 불러 둔다. 화면·상태 확인은 바로 대답하고
    # 지도 올리기 같은 무거운 요청만 다 불러올 때까지(몇 초) 기다린다.
    _READY.clear()
    threading.Thread(target=_warm_up, daemon=True).start()
    srv.serve_forever()


def main(port=8765, open_browser=True):
    os.makedirs(_home(), exist_ok=True)
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    url = f"http://127.0.0.1:{srv.server_port}"
    from . import __version__, __date__
    print(f"콘타 모델 프로그램 v{__version__} ({__date__})")
    print(f"콘타 모델 화면: {url}  (이 창을 닫으면 프로그램이 꺼집니다)")
    print(f"결과는 {_home()} 아래에 저장됩니다.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

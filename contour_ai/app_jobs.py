"""모델 만들기 작업: 작업(Job) 실행·멈추기·시간 제한, 서버 모드의 따로 된 프로세스, 대기 줄, 하루 횟수 돌려주기."""
import json
import os
import shutil
import threading
import time
import traceback


from .app_lazy import pipeline
from . import app_state as S
from .app_state import _slog, _sources, _uploads


def Progress(*a, **k):
    from .progress import Progress as _P
    return _P(*a, **k)


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
        with S.QCOND:
            return S.QUEUE.index(self) if self in S.QUEUE else 0

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
            if S.SERVER:                                 # 작업 중 임시 파일은 끝나면 바로 지운다
                import tempfile
                self.tmp = tempfile.tempdir = os.path.join(os.path.dirname(self.out), "_tmp")
                os.makedirs(self.tmp, exist_ok=True)
            if S.SERVER:
                self.summary = self._run_in_process(src["folder"], p.pop("out"), p)
            else:
                self.summary = pipeline.run(src["folder"], p.pop("out"), log=self.log, progress=self.progress, **p)
            self.summary["source"] = os.path.basename(src["zip"])
            # 화면 미리보기용: 결과 폴더 안에 이 화면 파일도 넣어 둔다(나중에 다시 열기)
        except Cancelled:
            if self.timed_out:
                self.error = f"{S.LIMITS['job_minutes']}분 안에 끝나지 않아 멈췄습니다. 범위를 줄이거나 디테일을 낮춰 다시 해 보세요."
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
                _refund(self)
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


def _queue_worker():
    while True:
        with S.QCOND:
            while not S.QUEUE:
                S.QCOND.wait()
            job = S.QUEUE[0]
        if not job.cancelled:
            timer = threading.Timer(S.LIMITS["job_minutes"] * 60, job.timeout)
            timer.start()
            try:
                job.run()
            finally:
                timer.cancel()
        else:
            job.error, job.done = "멈췄습니다", True
        with S.QCOND:
            S.QUEUE.pop(0)


def _refund(job):
    """만들다 실패하면(오류·시간 초과) 하루 횟수를 돌려준다. 직접 '멈추기'를 누른 것은 돌려주지 않는다."""
    key = getattr(job, "runkey", None)
    if not key or not job.error or (job.cancelled and not job.timed_out):
        return False
    if S.RUNS.get(key, 0) > 0:
        S.RUNS[key] -= 1
    job.refunded = True
    job.lines.append("만들지 못해서 오늘 횟수는 깎지 않았습니다.")
    return True


Job._run_in_process = _job_process

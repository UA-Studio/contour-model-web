"""공개 서버: 방문자 칸 정리 — 오래 쉰 칸 지우기, 디스크가 모자라면 자리 만들기, 이전 결과 지우기."""
import os
import shutil
import time
import traceback
from . import app_state as S
from .app_state import _home, _slog, _state




def _busy(s):
    job = s["state"].get("job")
    return bool(job and not job.done)


def _drop_session(sid):
    s = S.SESSIONS.pop(sid, None)
    if s:
        shutil.rmtree(s["home"], ignore_errors=True)


def _free_mb():
    try:
        return shutil.disk_usage(S.DATA).free / 1e6
    except OSError:
        return 1e9


def _make_room(keep_sid=None):
    """디스크가 모자라면 만드는 중이 아닌 칸을 오래 쉰 것부터 지운다 (지금 쓰는 사람 칸은 마지막까지 남김)."""
    if _free_mb() >= S.LIMITS["min_free_mb"]:
        return
    with S.LOCK:
        idle = sorted((s["at"], sid) for sid, s in S.SESSIONS.items() if sid != keep_sid and not _busy(s))
    root = os.path.join(S.DATA, "sessions")
    orphans = []
    if os.path.isdir(root):                       # 서버를 다시 켜서 목록에 없는 옛 폴더
        orphans = sorted((os.path.getmtime(os.path.join(root, d)), d) for d in os.listdir(root)
                         if d not in S.SESSIONS and d != keep_sid)
    for _, d in orphans:
        if _free_mb() >= S.LIMITS["min_free_mb"]:
            return
        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        _slog("공간 확보: 옛 폴더 지움", d[:6])
    for _, sid in idle:
        if _free_mb() >= S.LIMITS["min_free_mb"]:
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
            limit = time.time() - S.LIMITS["keep_minutes"] * 60
            for sid, s in list(S.SESSIONS.items()):
                if s["at"] < limit and not _busy(s):
                    _drop_session(sid)
            root = os.path.join(S.DATA, "sessions")
            if os.path.isdir(root):                      # 서버를 다시 켜서 목록에 없는 옛 폴더도
                for d in os.listdir(root):
                    p = os.path.join(root, d)
                    if d not in S.SESSIONS and os.path.getmtime(p) < limit:
                        shutil.rmtree(p, ignore_errors=True)
            _make_room()
            today = time.strftime("%Y%m%d")
            for k in [k for k in S.RUNS if k[1] != today]:
                S.RUNS.pop(k, None)
        except Exception:                                # 정리 줄이 죽으면 디스크가 다시 찬다
            traceback.print_exc()

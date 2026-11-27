"""결과 폴더 찾기(지금 보여 줄 결과, 최근 결과 목록)와 예시 도엽 zip."""
import json
import os
import shutil
import threading
import time
from . import app_state as S
from .app_state import _home, _state




def result_dir():
    """화면이 보여 줄 결과 폴더: '최근 결과'에서 고른 폴더, 아니면 방금 끝난 작업."""
    if _state().get("view") and os.path.isdir(_state()["view"]):
        return _state()["view"]
    job = _state()["job"]
    if job:
        return job.out if job.done and os.path.isdir(job.out) else None
    if S.SERVER:
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


_SAMPLE_LOCK = threading.Lock()


def _sample_zip():
    """예시 도엽 zip 을 처음 한 번 만들어 두고 같은 것을 준다. 실제 수치지도가 아니라 지어낸 지형·건물·길."""
    d = os.path.join(S.DATA if S.SERVER else S.HOME, "_sample")
    zp = os.path.join(d, "예시도엽.zip")
    with _SAMPLE_LOCK:
        if not os.path.exists(zp):
            import subprocess
            import sys
            os.makedirs(d, exist_ok=True)
            tmp = zp + ".part"
            subprocess.run([sys.executable, "-m", "contour_ai.sample_sheet", os.path.join(d, "shp"), tmp],
                           check=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           stdout=subprocess.DEVNULL)
            os.replace(tmp, zp)
            shutil.rmtree(os.path.join(d, "shp"), ignore_errors=True)
    return zp

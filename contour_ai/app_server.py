"""화면 서버 켜기: 내 PC 판 main() (127.0.0.1, 브라우저 열기)과 공개 서버 serve()."""
import os
import threading
import traceback
import webbrowser
from http.server import ThreadingHTTPServer


from .app_lazy import pipeline
from . import app_state as S
from .app_state import _home
from .app_sessions import _cleanup_loop, _free_mb
from .app_jobs import _queue_worker
from .app_http import Handler


def _warm_up():
    """서버가 켜진 뒤 한 줄에서만 무거운 라이브러리를 불러온다. 여러 요청이 동시에 불러오면
    shapely 가 '반쯤 불러온 모듈' 오류를 낸다(2026-11-20 시험에서 발견) → 다른 요청은 _READY 를 기다린다."""
    try:
        from . import pipeline as _p, progress, site, sitefile  # noqa: F401
        pipeline._mod = _p
    except Exception:
        traceback.print_exc()
    finally:
        S._READY.set()


def serve():
    """공개 서버 실행 (Docker 안에서): 0.0.0.0:$PORT, 브라우저 열지 않음."""
    os.makedirs(os.path.join(S.DATA, "sessions"), exist_ok=True)
    threading.Thread(target=_queue_worker, daemon=True).start()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    port = int(os.environ.get("PORT") or 8080)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    from . import __version__
    print(f"콘타 모델 공개 서버 v{__version__} :{port}  제한 {S.LIMITS}  디스크 남은 공간 {_free_mb():.0f}MB", flush=True)
    # 접속은 바로 받고, 무거운 라이브러리는 뒤에서 한 번만 불러 둔다. 화면·상태 확인은 바로 대답하고
    # 지도 올리기 같은 무거운 요청만 다 불러올 때까지(몇 초) 기다린다.
    S._READY.clear()
    threading.Thread(target=_warm_up, daemon=True).start()
    srv.serve_forever()


def main(port=None, open_browser=None):
    """내 PC 판(윈도우 .bat 더블클릭): 127.0.0.1 에서 화면을 열고 브라우저를 띄운다.
    CONTOUR_PORT·CONTOUR_NO_BROWSER=1 은 자동 시험용."""
    port = port or int(os.environ.get("CONTOUR_PORT") or 8765)
    if open_browser is None:
        open_browser = os.environ.get("CONTOUR_NO_BROWSER") != "1"
    os.makedirs(_home(), exist_ok=True)
    # 무거운 라이브러리는 한 줄에서 먼저 불러 둔다 (여러 요청이 동시에 불러오면 shapely '반쯤 불러온 모듈' 오류)
    S._READY.clear()
    threading.Thread(target=_warm_up, daemon=True).start()
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
    print(f"결과는 {_home()} 아래에 저장됩니다.", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

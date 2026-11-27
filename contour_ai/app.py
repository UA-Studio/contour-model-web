"""사용 화면: 브라우저에서 zip을 넣고 범위를 고른 뒤 버튼 하나로 모델을 만든다.

python -m contour_ai          → 화면 열기 (http://127.0.0.1:8765)
외부 패키지 없이 파이썬 표준 http.server 로 돌아간다. 3D 미리보기용 three.js 는 web/vendor 에 들어 있다.
"""
# 입구: 코드는 기능별 파일(app_*.py)에 있다. 예전처럼 app.X 로 불러도 동작하도록 이름만 다시 내보낸다.
# 여기에 코드를 쓰지 말 것 (CLAUDE.md). 서버 상태(SESSIONS·LIMITS …)는 app_state.py 에만 있다.
from .app_lazy import _LazyModule, pipeline  # noqa: F401
from .app_util import _client_ip, _inside, open_folder, _zip_dir  # noqa: F401
from .app_mapview import _named_places, _overview_png, overview_tile, _TILE  # noqa: F401
from .app_siteprep import _is_std, PREP, site_info, site_prep  # noqa: F401
from .app_state import (  # noqa: F401
    _READY, WEB, HOME, UPLOADS, LOCK, SERVER, DATA, LIMITS, SERVER_KEYS, _CTX, SESSIONS, RUNS,
    QUEUE, QCOND, _Sources, _new_session, _LOCAL, _sess, _state, _sources, _uploads, _home, _slog)
from .app_sessions import (  # noqa: F401
    _busy, _drop_session, _free_mb, _make_room, _drop_old_results, _cleanup_loop)
from .app_inputs import inspect_source, site_save  # noqa: F401
from .app_jobs import (  # noqa: F401
    Progress, Cancelled, Job, _child_run, _job_process, _queue_worker, _refund)
from .app_start import OUTPUT_KINDS, _outputs, start_job, _server_check, _interval  # noqa: F401
from .app_results import (  # noqa: F401
    result_dir, _NO_RESULT_PAGE, recent_results, _SAMPLE_LOCK, _sample_zip)
from .app_admin import _feedback, _dev_mark, _is_dev, _dev_login, _admin_feedback  # noqa: F401
from .app_http import Handler  # noqa: F401
from .app_server import _warm_up, serve, main  # noqa: F401


if __name__ == "__main__":
    main()

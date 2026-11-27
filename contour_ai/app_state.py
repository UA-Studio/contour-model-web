"""화면 서버의 설정과 상태 (공용): 폴더 위치, 공개 서버 모드·제한, 방문자 칸, 작업 줄.

다른 app_*.py 는 여기 값을 `S.이름` 으로 쓴다 (시험이 이 파일을 다시 불러와 설정을 바꿔도 같은 값을 보게).
"""
import json
import os
import threading
import time




_READY = threading.Event()          # 서버: 무거운 라이브러리를 다 불러왔는가 (내 PC 판은 처음부터 참)
_READY.set()


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


# ---------------------------------------------------------------- 작업
def _slog(*a):
    """서버 기록(fly logs)에 한 줄. 화면에 안 보이는 멈춤·실패 이유를 나중에 찾을 수 있게."""
    if SERVER:
        print(time.strftime("[%H:%M:%S]"), *a, flush=True)

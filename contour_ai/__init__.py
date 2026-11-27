__version__ = "2.5"
__date__ = "2026-11-27"

if globals().get("__package__"):   # 묶기 스크립트가 이 파일만 따로 읽을 때(버전 확인)는 건너뜀
    # 윈도우판 묶음: 어떤 라이브러리보다 먼저 좌표계 자료 위치를 맞춘다 (contour_ai/bundle_env.py)
    from .bundle_env import setup as _bundle_env
    _bundle_env()

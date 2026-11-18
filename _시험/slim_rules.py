"""배포판 용량 줄이기 규칙 (v1.16): 실행에 쓰지 않는 파일만 뺀다. 기능은 그대로.
경로는 python 폴더 기준('Lib/site-packages/...'), 윈도우·리눅스 공통으로 쓰도록 '/' 로 비교한다.

빼는 것
- 개발용: pip·ensurepip(패키지 설치 도구), C/C++/Cython 소스와 헤더(.c .cpp .h .pyx .pxd .pxi), 타입 힌트(.pyi),
  lib2to3·pydoc_data(파이썬 2 변환·도움말 문서)
- scipy 안 쓰는 묶음: stats·signal·io·odr·cluster·integrate·datasets·misc·fftpack (프로그램과 다른 라이브러리가 부르지 않음)
- PIL _avif (AVIF 사진 코덱, 7.6MB) — 그림은 PNG 만 쓴다
(numpy.f2py 는 numpy 가 불러서, fontTools 는 ezdxf 가 불러서 남긴다 — 리눅스 시험에서 확인)
- matplotlib 견본 자료·PDF 글꼴 정보·안 쓰는 글꼴(DejaVu Serif/Mono, STIX, Computer Modern) — 그림 글꼴은 Pretendard,
  모자란 글자는 DejaVu Sans·LastResort 로 (그대로 둠)
"""
import re

SCIPY_DROP = ("stats", "signal", "io", "odr", "cluster", "integrate", "datasets", "misc", "fftpack")
_RULES = [
    r"^Lib/site-packages/pip(/|-[^/]*\.dist-info/)",
    r"^Lib/(ensurepip|lib2to3|pydoc_data|idlelib|turtledemo|tkinter)/",
    r"^Lib/site-packages/(?!contour_ai/).*\.(c|cc|cpp|cxx|h|hpp|pyx|pxd|pxi|pyi)$",
    r"^Lib/site-packages/scipy/(" + "|".join(SCIPY_DROP) + r")/",
    r"^Lib/site-packages/PIL/_avif\.",
    r"^Lib/site-packages/matplotlib/mpl-data/(sample_data|fonts/afm|fonts/pdfcorefonts)/",
    r"^Lib/site-packages/matplotlib/mpl-data/fonts/ttf/(DejaVuSerif|DejaVuSansMono|STIX|cm)[^/]*$",
    r"^Lib/site-packages/matplotlib/backends/web_backend/",
]
_RX = re.compile("|".join(f"(?:{r})" for r in _RULES))


def drop(rel):
    """rel: python 폴더 기준 경로 → 빼면 True"""
    return bool(_RX.search(rel.replace("\\", "/")))

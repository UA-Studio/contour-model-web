"""윈도우판 묶음에서 실행에 쓰지 않는 파일을 덜어 낸다 (build_windows.py 가 부른다).

덜어 내는 것은 모두 '프로그램이 돌 때 읽지 않는 것'뿐이다:
  1) 라이브러리를 만들 때 쓴 C·Cython 소스(.c .cpp .pyx .pxd .pxi)와 타입 설명(.pyi)
  2) 라이브러리 자체 시험 폴더(tests)
  3) scipy 에서 이 프로그램이 쓰지 않는 하위 묶음(통계·신호·파일 입출력·적분 등)
     — 2026-11-23 가짜 도엽 시험 전체를 돌리며 실제로 불러온 모듈을 기록해 정했다.
       contour_ai 가 쓰는 것: ndimage, interpolate, spatial, sparse(csgraph) → 이들이 부르는 linalg·optimize·special·fft 는 둔다.
  4) Pillow 의 AVIF 그림 모듈(7.7MB, 이 프로그램은 PNG·JPEG 만 씀. 없으면 Pillow 가 AVIF 만 끈다)
  5) matplotlib 예제 자료
  6) rasterio 안의 PROJ 좌표계 자료 사본 — pyproj 에 같은 판(레이아웃 1.6, EPSG v12.029)이 있어
     contour_ai/bundle_env.py 가 rasterio 를 그쪽으로 이어 준다.

새 라이브러리를 넣거나 contour_ai 가 scipy 의 다른 부분(예: scipy.stats)을 쓰기 시작하면 SCIPY_DROP 에서 빼야 한다.
windows.yml 의 시험(묶음을 새 폴더에 풀어 가짜 도엽 모델 만들기)이 빠진 모듈을 잡아낸다.
"""
import shutil
from pathlib import Path

SOURCE_EXT = {".c", ".cpp", ".cxx", ".pyx", ".pxd", ".pxi", ".pyi"}
SCIPY_DROP = ("stats", "signal", "io", "integrate", "odr", "fftpack", "datasets", "misc", "differentiate")
DROP_PATHS = (
    "matplotlib/mpl-data/sample_data",
    "rasterio/proj_data",
)
DROP_GLOBS = (
    "PIL/_avif.*",
)
KEEP_TOP = {"contour_ai"}   # 프로그램 본체는 손대지 않는다


def _size(p):
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _rm(p, log):
    n = _size(p)
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    log[0] += n
    log[1] += 1
    return n


def slim(site_packages, verbose=True):
    """site_packages 폴더를 제자리에서 줄이고 줄인 바이트 수를 돌려준다."""
    sp = Path(site_packages)
    log = [0, 0]
    by = {}

    def drop(p, why):
        if p.exists():
            by[why] = by.get(why, 0) + _rm(p, log)

    tops = [d for d in sp.iterdir() if d.name not in KEEP_TOP]
    # 2) 시험 폴더 (먼저 지워야 1)에서 그 안을 또 훑지 않는다)
    for top in tops:
        if top.is_dir():
            for d in sorted(top.rglob("tests"), key=lambda x: -len(x.parts)):
                if d.is_dir():
                    drop(d, "라이브러리 시험 폴더")
    # 3) scipy 하위 묶음
    for name in SCIPY_DROP:
        drop(sp / "scipy" / name, "scipy 안 쓰는 하위 묶음")
    # 4)~6)
    for rel in DROP_PATHS:
        drop(sp / rel, rel.split("/")[0] + " 자료")
    for g in DROP_GLOBS:
        for p in sp.glob(g):
            drop(p, "Pillow AVIF")
    # 1) 소스·타입 설명
    for top in tops:
        if top.exists():
            files = [top] if top.is_file() else top.rglob("*")
            for f in list(files):
                if f.is_file() and f.suffix.lower() in SOURCE_EXT:
                    drop(f, "C·Cython 소스, 타입 설명")
    if verbose:
        for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"  덜어냄 {v / 1e6:6.1f}MB  {k}")
        print(f"  덜어냄 합계 {log[0] / 1e6:.1f}MB ({log[1]}개)")
    return log[0]


if __name__ == "__main__":
    import sys
    slim(sys.argv[1])

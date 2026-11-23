"""윈도우 다운로드판(묶음)에서만 필요한 실행 환경 맞추기. 개발 PC·시험에서는 아무것도 바꾸지 않는다.

- 좌표계 자료(proj.db): 묶음은 용량을 줄이려고 rasterio 안의 사본을 빼고 pyproj 것 하나만 둔다
  (windows/slim.py). rasterio 를 불러오기 전에 PROJ_DATA 를 pyproj 자료로 가리켜 준다.
  rasterio 에 자기 사본이 있으면(일반 설치) 손대지 않는다.
"""
import importlib.util
import os


def _pkg_dir(name):
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        return None
    return list(spec.submodule_search_locations)[0]


def setup():
    if os.environ.get("PROJ_DATA") or os.environ.get("PROJ_LIB"):
        return
    rio = _pkg_dir("rasterio")
    if not rio or os.path.isfile(os.path.join(rio, "proj_data", "proj.db")):
        return
    pp = _pkg_dir("pyproj")
    if pp:
        d = os.path.join(pp, "proj_dir", "share", "proj")
        if os.path.isfile(os.path.join(d, "proj.db")):
            os.environ["PROJ_DATA"] = d

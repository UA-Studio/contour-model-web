"""윈도우판 용량 줄이기(windows/slim.py)와 묶음 환경 맞추기(contour_ai/bundle_env.py) 시험."""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "windows"))
from slim import slim  # noqa: E402


def _touch(p, n=10):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)


def test_실행에_쓰는_파일은_남기고_안_쓰는_것만_뺀다(tmp_path):
    sp = tmp_path / "site-packages"
    keep = ["scipy/ndimage/_filters.py", "scipy/spatial/_ckdtree.pyd", "scipy/linalg/x.py", "scipy/optimize/y.py",
            "numpy/_core/include/numpy/ndarraytypes.h", "matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf",
            "pyproj/proj_dir/share/proj/proj.db", "rasterio/gdal_data/gdalvrt.xsd", "PIL/_imaging.pyd",
            "numpy/testing/__init__.py", "contour_ai/tests/keep.py", "contour_ai/x.c"]
    drop = ["scipy/stats/_stats.py", "scipy/io/x.py", "scipy/signal/x.py", "rasterio/_io.c", "ezdxf/acc/vector.cpp",
            "shapely/lib.pyx", "numpy/__init__.pyi", "numpy/_core/tests/test_a.py", "matplotlib/tests/b.py",
            "matplotlib/mpl-data/sample_data/a.csv", "rasterio/proj_data/proj.db", "PIL/_avif.cp313-win_amd64.pyd"]
    for f in keep + drop:
        _touch(sp / f)
    n = slim(sp, verbose=False)
    assert n == 10 * len(drop)
    for f in keep:
        assert (sp / f).exists(), f
    for f in drop:
        assert not (sp / f).exists(), f


def test_rasterio_자료가_없을_때만_pyproj_자료로_잇는다(tmp_path, monkeypatch):
    import contour_ai.bundle_env as be
    rio, pp = tmp_path / "rasterio", tmp_path / "pyproj"
    _touch(pp / "proj_dir" / "share" / "proj" / "proj.db")
    rio.mkdir()

    class Spec:
        def __init__(self, d):
            self.submodule_search_locations = [str(d)]
    monkeypatch.setattr(importlib.util, "find_spec", lambda n: Spec(rio if n == "rasterio" else pp))
    monkeypatch.delenv("PROJ_DATA", raising=False)
    monkeypatch.delenv("PROJ_LIB", raising=False)
    be.setup()
    assert os.environ["PROJ_DATA"] == str(pp / "proj_dir" / "share" / "proj")
    # 일반 설치(rasterio 에 자기 자료가 있음)면 손대지 않는다
    monkeypatch.delenv("PROJ_DATA")
    _touch(rio / "proj_data" / "proj.db")
    be.setup()
    assert "PROJ_DATA" not in os.environ

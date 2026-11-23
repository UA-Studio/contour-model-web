"""기능별로 나눈 파일이 저마다 따로 불러와지는지 (빠진 이름·서로 물고 도는 불러오기가 없는지)."""
import importlib
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "contour_ai"
MODULES = sorted(p.stem for p in PKG.glob("*.py") if p.stem not in ("__main__",))


@pytest.mark.parametrize("name", MODULES)
def test_파일마다_따로_불러와짐(name):
    importlib.import_module(f"contour_ai.{name}")


def test_예전_이름으로_불러도_동작():
    """다른 곳에서 contour_model·road_detail 의 예전 이름으로 불러도 그대로 된다."""
    from contour_ai.contour_model import as_polys, build, heightfield_mesh, terrace_regions, write_3dm  # noqa: F401
    from contour_ai.road_detail import _zebra, marks_check, road_details  # noqa: F401


def test_화면_파일이_모두_있음():
    web = PKG / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "ui_main.js", "ui_presets.js", "ui_server.js"):
        assert name in html, f"index.html 이 {name} 을 부르지 않음"
    main = (web / "ui_main.js").read_text(encoding="utf-8")
    for p in web.glob("ui_*.js"):
        text = p.read_text(encoding="utf-8")
        for dep in __import__("re").findall(r"from '\./([\w-]+\.js)'", text):
            assert (web / dep).exists(), f"{p.name} 가 없는 {dep} 를 부름"
    assert "ui_files.js" in main

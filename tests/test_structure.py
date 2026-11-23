"""코드 구조 지키기: 기능별 파일이 서로 얽히지 않게 (CLAUDE.md 의 규칙)."""
import ast
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "contour_ai"
MAX_LINES = 700
# 아직 나누지 않은 큰 파일 (줄여 나갈 것. 여기에 새로 넣지 말 것)
BIG_OK = {"app.py", "site.py", "skp_export.py"}


def test_파일은_700줄_이하():
    big = [f"{p.relative_to(PKG)} ({n}줄)" for p in PKG.rglob("*.py")
           if p.name not in BIG_OK and (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_LINES]
    assert not big, "기능별로 나눠 주세요: " + ", ".join(big)


def _rel_imports(path):
    """맨 위(모듈 단계)에서 같은 폴더의 다른 파일을 불러오는 이름들."""
    out = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module:
            out.add(n.module.split(".")[0])
    return out


def test_기능_파일끼리_순환_import_없음():
    for sub in ("model", "roads"):
        files = {p.stem: p for p in (PKG / sub).glob("*.py") if p.stem != "__init__"}
        graph = {k: _rel_imports(p) & files.keys() for k, p in files.items()}
        state = {}

        def visit(u, path):
            if state.get(u) == 1:
                raise AssertionError(f"{sub}/ 순환 import: " + " → ".join(path + [u]))
            if state.get(u) == 2:
                return
            state[u] = 1
            for v in graph[u]:
                visit(v, path + [u])
            state[u] = 2

        for k in graph:
            visit(k, [])


def test_기능_파일은_하나씩_따로_불러와짐():
    """각 파일을 새 파이썬에서 혼자 불러와도 오류가 없어야 함 (다른 파일 순서에 기대지 않음)."""
    mods = [f"contour_ai.{sub}.{p.stem}" for sub in ("model", "roads")
            for p in sorted((PKG / sub).glob("*.py")) if p.stem != "__init__"]
    code = "import importlib, sys\nfor m in sys.argv[1:]:\n    importlib.import_module(m)\n"
    for m in mods:
        r = subprocess.run([sys.executable, "-c", code, m], capture_output=True, text=True, cwd=PKG.parent)
        assert r.returncode == 0, f"{m} 를 혼자 불러오지 못함:\n{r.stderr[-800:]}"


def test_예전_이름으로도_불러와짐():
    """contour_model.py·road_detail.py 는 입구: 예전 코드의 import 가 그대로 동작."""
    from contour_ai import contour_model as cm, road_detail as rd
    for name in ("build", "write_3dm", "write_stl", "write_laser", "terrace_regions", "as_polys",
                 "crisp_mesh", "_shift", "SOURCE_KO", "ROLE_KO", "USE_COLORS", "cut_regions", "main"):
        assert hasattr(cm, name), name
    for name in ("road_details", "marks_check", "_zebra", "_centerlines", "_nodes", "FIX_RGB", "PAINT",
                 "flat_mesh", "placed", "pole_parts", "signal_parts"):
        assert hasattr(rd, name), name

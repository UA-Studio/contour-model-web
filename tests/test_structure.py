"""코드 구조 지키기: 기능별 파일이 서로 얽히지 않게 (CLAUDE.md 의 규칙)."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "contour_ai"
MAX_LINES = 700
# 입구: 예전 이름으로 불러도 되게 이름만 다시 내보내는 파일. 코드를 쓰면 안 된다.
ENTRIES = ("contour_model.py", "site.py", "skp_export.py", "app.py")


def _lines(p):
    return len(p.read_text(encoding="utf-8").splitlines())


def test_파일은_700줄_이하():
    files = list(PKG.glob("*.py")) + [p for p in (PKG / "web").glob("*.*") if p.suffix in (".html", ".js", ".css")]
    big = [f"{p.relative_to(PKG)} ({n}줄)" for p in files if (n := _lines(p)) > MAX_LINES]
    assert not big, "기능별로 나눠 주세요: " + ", ".join(big)


def _top_imports(path):
    """맨 위(모듈 단계)에서 같은 폴더의 다른 파일을 불러오는 이름들."""
    out = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.ImportFrom) and n.level == 1:
            if n.module:
                out.add(n.module.split(".")[0])
            else:
                out |= {a.name for a in n.names}
    return out


def test_파일끼리_순환_import_없음():
    files = {p.stem: p for p in PKG.glob("*.py")}
    graph = {k: _top_imports(p) & files.keys() for k, p in files.items()}
    state = {}

    def visit(u, path):
        if state.get(u) == 1:
            raise AssertionError("순환 import: " + " → ".join(path[path.index(u):] + [u]))
        if state.get(u) == 2:
            return
        state[u] = 1
        for v in sorted(graph[u]):
            visit(v, path + [u])
        state[u] = 2

    for k in sorted(graph):
        visit(k, [])


def test_입구_파일에는_코드가_없음():
    for name in ENTRIES:
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for n in tree.body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                continue                                   # 설명 글
            if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test):
                continue                                   # if __name__ == "__main__": main()
            raise AssertionError(f"{name} 는 입구라 코드를 쓰지 않습니다 ({n.lineno}줄). 맞는 기능 파일에 써 주세요.")


def test_서버_상태는_app_state_에서_S_이름으로():
    """app_state 의 값(SESSIONS·LIMITS·SERVER …)을 다른 파일에 복사해 두면, 시험·설정이 바꾼 값을 못 본다.
    값은 `from . import app_state as S` 로 `S.이름`, 함수만 `from .app_state import 함수`."""
    st = ast.parse((PKG / "app_state.py").read_text(encoding="utf-8"))
    funcs = {n.name for n in st.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for p in PKG.glob("app_*.py"):
        if p.name == "app_state.py":
            continue
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module == "app_state":
                bad = [a.name for a in n.names if a.name not in funcs]
                assert not bad, f"{p.name}: {', '.join(bad)} 는 S.{bad[0]} 로 써 주세요"


def test_화면_서버는_무거운_라이브러리_없이_켜짐():
    """app_*.py 맨 위에서 numpy·shapely 같은 무거운 라이브러리를 불러오지 않는다 (켜질 때 접속을 바로 받게)."""
    heavy = {"numpy", "shapely", "trimesh", "rhino3dm", "rasterio", "scipy", "ezdxf", "matplotlib", "pyproj"}
    for p in PKG.glob("app*.py"):
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module or ""] if isinstance(n, ast.ImportFrom) and n.level == 0 else []
            for m in mods:
                assert m.split(".")[0] not in heavy, f"{p.name} 맨 위에서 {m} 을 불러옴 → 쓰는 함수 안에서 불러 주세요"


def test_예전_이름으로도_불러와짐():
    """입구 파일: 예전 코드의 import 가 그대로 동작."""
    from contour_ai import app, contour_model as cm, site, skp_export as skp
    for name in ("build", "main", "DEFAULTS", "OUTPUTS_DEFAULT", "OUTPUT_NAMES", "_outputs", "write_3dm", "write_stl",
                 "write_laser", "terrace_regions", "as_polys", "heightfield_mesh", "simplify_solid", "smooth_poly"):
        assert hasattr(cm, name), f"contour_model.{name}"
    for name in ("SiteError", "from_ring", "load_boundary", "load_site_file", "drawing_affine", "level_interpolator",
                 "blended", "outline_mesh", "split_masses", "_LAST_CRS", "_map_spots", "LEVEL_RE"):
        assert hasattr(site, name), f"site.{name}"
    for name in ("write_sketchup", "Dae", "SkpJson", "write_rbz", "find_sketchup", "make_skp_with_sketchup"):
        assert hasattr(skp, name), f"skp_export.{name}"
    for name in ("Handler", "main", "serve", "Job", "start_job", "SESSIONS", "LIMITS", "DATA", "SERVER", "_CTX",
                 "_make_room", "_drop_old_results", "_server_check", "_refund", "_interval", "_sample_zip",
                 "_outputs", "_new_session", "result_dir"):
        assert hasattr(app, name), f"app.{name}"

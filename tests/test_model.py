"""모델 만들기 시험: 결과 파일이 다 나오고, 각 파일을 실제 프로그램 라이브러리로 다시 열 수 있는지."""
import json

import pytest


def _files(out):
    return {p.relative_to(out).as_posix(): p for p in out.rglob("*") if p.is_file()}


def test_기본모델_파일이_모두_나옴(basic_model):
    out, s = basic_model
    f = _files(out)
    for name in ("model.3dm", "model.glb", "model.stl", "model_sketchup.dae", "preview_plan.png", "summary.json"):
        assert name in f, f"{name} 이 없음"
        assert f[name].stat().st_size > 200, f"{name} 이 비어 있음"
    assert any(n.startswith("laser/") and n.endswith(".dxf") for n in f), "레이저 판 DXF 가 없음"
    assert s["buildings"] > 50
    assert s["contours"] > 10
    json.loads(f["summary.json"].read_text(encoding="utf-8"))


def test_라이노_파일_열림(basic_model):
    import rhino3dm
    out, _ = basic_model
    m = rhino3dm.File3dm.Read(str(out / "model.3dm"))
    assert m is not None
    layers = [l.FullPath for l in m.Layers]
    assert len(m.Objects) > 100
    assert any("건물" in l for l in layers), layers[:20]


def test_레이저_판_열림(basic_model):
    import ezdxf
    out, _ = basic_model
    sheets = sorted((out / "laser").glob("*.dxf"))
    assert len(sheets) >= 3
    for p in sheets:
        doc = ezdxf.readfile(p)
        assert len(doc.modelspace()) > 0, p.name


def test_3D_프린트_STL_닫힌_솔리드(basic_model):
    import trimesh
    out, _ = basic_model
    m = trimesh.load(out / "model.stl")
    assert m.is_watertight, "STL 에 열린 모서리가 있음"
    size = m.bounds[1] - m.bounds[0]
    assert size[0] == pytest.approx(300, abs=1) and size[1] == pytest.approx(300, abs=1)   # 1:1000 → 300mm


def test_GLB_열림(basic_model):
    import trimesh
    out, _ = basic_model
    sc = trimesh.load(out / "model.glb")
    assert len(sc.geometry) > 0


def test_대지_경계와_레벨(site_model):
    import rhino3dm
    out, s = site_model
    assert s.get("site"), "대지 정보가 요약에 없음"
    m = rhino3dm.File3dm.Read(str(out / "model.3dm"))
    layers = [l.FullPath for l in m.Layers]
    assert any(l.startswith("대지") for l in layers), layers[:30]

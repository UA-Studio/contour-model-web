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


def test_계단식_대지(basic_model):
    """가짜 도엽의 건물 터·옹벽·논이 평평한 단으로 들어갔는지 (기본으로 켜짐)."""
    _, s = basic_model
    p = s.get("pads")
    assert p, "계단식 대지 요약이 없음"
    assert p["buildings"] > 50 and p["pads"] > 10
    assert p["paddies"] >= 1


def test_기본값은_스케치업만(skp_model):
    """v2.2 기본: 스케치업 파일(+결과 화면용 미리보기 GLB·그림)만. Rhino·STL·레이저 DXF 는 만들지 않는다."""
    out, s = skp_model
    f = _files(out)
    for name in ("model_sketchup.dae", "model_sketchup_build.json", "model.glb", "preview_plan.png", "summary.json"):
        assert name in f, f"{name} 이 없음"
    assert not {"model.3dm", "model.stl"} & set(f), sorted(f)
    assert not any(n.startswith("laser/") for n in f), "레이저 판이 만들어짐"
    assert s["outputs"] == ["skp"] and s["sheets"] is None


def test_스케치업에_대지_표시가_들어감(skp_model):
    """Rhino 에만 들어가던 대지 경계·레벨판 등이 스케치업 태그 '대지::…' 로 들어간다."""
    out, s = skp_model
    assert s.get("site"), "대지 정보가 요약에 없음"
    doc = json.loads((out / "model_sketchup_build.json").read_text(encoding="utf-8"))
    tags = {ln["tag"] for ln in doc["lines"]}
    assert {"대지::경계", "대지::경계(지형 위)"} <= tags, tags
    assert all(len(ln["p"]) % 3 == 0 and len(ln["p"]) >= 6 for ln in doc["lines"])


def test_rhino3dm_없이도_스케치업_만들기(tmp_path, fake_sheet, monkeypatch):
    """윈도우판(v2.2)에는 rhino3dm 이 없다: 불러오지 않고 끝나야 한다."""
    import sys
    from conftest import _run
    from contour_ai import contour_model
    monkeypatch.setitem(sys.modules, "rhino3dm", None)      # import 하면 ImportError
    monkeypatch.setattr(contour_model._Rhino, "_m", None)   # 앞 시험이 불러 둔 것도 잊게
    s = _run(fake_sheet, tmp_path / "out", size=150, detail=1, lights=False, road_detail=False)
    assert s["outputs"] == ["skp"]


def test_rhino3dm_없을_때_Rhino_를_고르면_한국어_안내(monkeypatch):
    import sys
    from contour_ai import contour_model
    monkeypatch.setitem(sys.modules, "rhino3dm", None)
    monkeypatch.setattr(contour_model._Rhino, "_m", None)
    with pytest.raises(SystemExit, match="스케치업 파일만"):
        contour_model.r3.File3dm()

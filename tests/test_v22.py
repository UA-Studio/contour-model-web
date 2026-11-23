"""v2.2 (STURA3D 참고 개선 1차) 시험: 등고 간격 0.1m 단위, 실패 시 하루 횟수 돌려주기, 건물 층별 레이어,
결과 사용 안내, 예시 도엽, 3D 보기 레이어 버튼."""
import importlib
import os
import numpy as np
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTOUR_SERVER", "1")
    monkeypatch.setenv("CONTOUR_DATA", str(tmp_path / "data"))
    import contour_ai.app as m
    m = importlib.reload(m)
    yield m
    monkeypatch.delenv("CONTOUR_SERVER")
    importlib.reload(m)


def test_등고_간격은_0_5에서_20m_0_1m_단위(app):
    assert app._interval("0.3") == 0.5
    assert app._interval("1.27") == 1.3
    assert app._interval("50") == 20.0
    assert app._interval(None) == 5.0
    assert app._interval("abc") == 5.0


def test_소수_간격도_판_높이가_정확함():
    """0.1m 단위 간격을 계속 더하면 52.300000001 같은 오차가 쌓였다 → k번째 판 = base + k·간격."""
    import numpy as np
    from shapely.geometry import LineString, box
    from contour_ai.terrain import terrace_regions
    bbox = (0, 0, 200, 200)
    contours = [(LineString([(0, y), (200, y)]), 30 + y * 0.05) for y in range(0, 201, 10)]
    zs = {c[1] for c in contours}

    def interp(P):
        return 30 + np.asarray(P)[:, 1] * 0.05
    regions, base = terrace_regions(contours, interp, bbox, 0.7, 4.0)
    lv = sorted(regions)
    assert len(lv) > 5
    for L in lv:
        assert abs(L / 0.7 - round(L / 0.7)) < 1e-6, L
        assert L == round(L, 6)
    assert zs


class _J:
    pass


def test_실패하면_하루_횟수를_돌려줌(app):
    key = ("1.2.3.4", "20261123")
    app.RUNS[key] = 3
    j = _J(); j.runkey = key; j.error = "ValueError: 무엇"; j.cancelled = j.timed_out = False; j.lines = []
    assert app._refund(j) and app.RUNS[key] == 2 and j.refunded
    j2 = _J(); j2.runkey = key; j2.error = "멈췄습니다"; j2.cancelled = True; j2.timed_out = False; j2.lines = []
    assert not app._refund(j2) and app.RUNS[key] == 2, "직접 멈추기는 돌려주지 않음"
    j3 = _J(); j3.runkey = key; j3.error = "12분 안에 끝나지 않아"; j3.cancelled = j3.timed_out = True; j3.lines = []
    assert app._refund(j3) and app.RUNS[key] == 1, "시간 초과는 돌려줌"
    j4 = _J(); j4.runkey = key; j4.error = None; j4.cancelled = j4.timed_out = False; j4.lines = []
    assert not app._refund(j4) and app.RUNS[key] == 1, "성공은 그대로"


def test_입력_확인만으로는_횟수를_세지_않음(app):
    s = app._new_session("q" * 20, os.path.join(app.DATA, "sessions", "q" * 20))
    app.SESSIONS[s["sid"]] = s
    app._CTX.sess = s; app._CTX.dev = False
    params = dict(hd_dir=None, aerial_file=None, site_file=None, road_files=(), light_files=(), dem_files=None,
                  out=os.path.join(s["home"], "모델"), size=500)
    key = app._server_check(dict(params), {"_ip": "9.9.9.9"})
    assert key and app.RUNS.get(key, 0) == 0


def test_건물은_층수별_레이어(basic_model):
    import rhino3dm
    out, _ = basic_model
    m = rhino3dm.File3dm.Read(str(out / "model.3dm"))
    paths = {l.FullPath for l in m.Layers}
    floors = sorted(p for p in paths if p.startswith("건물::") and p.count("::") == 1)
    assert floors and all(p.endswith("층") for p in floors), floors
    # 건물 개체는 모두 층 레이어 아래에, 개체 속성 층수와 레이어 층수가 같다
    idx = {i: m.Layers[i].FullPath for i in range(len(m.Layers))}
    n = 0
    for o in m.Objects:
        lp = idx[o.Attributes.LayerIndex]
        if lp.startswith("건물::") and o.Attributes.GetUserString("층수(모델)"):
            assert lp.split("::")[1] == f"{int(o.Attributes.GetUserString('층수(모델)')):02d}층", lp
            n += 1
    assert n > 50


def test_결과에_사용_안내(basic_model):
    out, _ = basic_model
    t = (out / "사용_안내.txt").read_text(encoding="utf-8-sig")
    for k in ("model.3dm", "단위: m", "laser", "단위: mm", "model.stl", "1:1000"):
        assert k in t, k


def test_예시_도엽과_화면_버튼(app, tmp_path):
    import zipfile
    zp = app._sample_zip()
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
    assert any(n.endswith("N3A_B0010000.shp") for n in names)
    assert app._sample_zip() == zp
    web = ROOT / "contour_ai" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "ui_files.js").read_text(encoding="utf-8")
    assert 'id="samplebtn"' in html and "/sample.zip" in js
    v3 = (web / "ui_view3d.js").read_text(encoding="utf-8")
    for k in ("지형", "건물", "길", "나무", "가로등", "대지 경계"):
        assert f"'{k}'" in v3
    assert 'id="ivquick"' in html and 'step="0.1"' in html


def test_2D_지형도_DXF(basic_model):
    import ezdxf
    out, s = basic_model
    doc = ezdxf.readfile(str(out / "plan_2d.dxf"))
    names = {l.dxf.name for l in doc.layers}
    for k in ("등고선_주곡선", "등고선_계곡선", "건물_층수", "도로_차도", "범위"):
        assert k in names, (k, sorted(names))
    assert any(n.startswith("건물_") and n.endswith("층") for n in names)
    msp = doc.modelspace()
    cs = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer.startswith("등고선_")]
    assert cs and all(e.dxf.elevation in s["levels"] for e in cs[:50])
    # 실제 좌표(TM): 가짜 도엽 근처
    x, y = cs[0].get_points("xy")[0]
    assert 201000 < x < 202200 and 554300 < y < 555500


def test_사이트_다이어그램_SVG(basic_model):
    import xml.etree.ElementTree as ET
    out, _ = basic_model
    root = ET.parse(out / "site_diagram.svg").getroot()
    labels = {g.get("{http://www.inkscape.org/namespaces/inkscape}label") for g in root.iter("{http://www.w3.org/2000/svg}g")}
    for k in ("등고선_계곡선", "건물", "도로_차도", "축척·방위"):
        assert k in labels, labels
    assert root.get("width").endswith("mm")


def test_영역별_지형은_꺼진_레이어로(basic_model):
    import rhino3dm
    out, _ = basic_model
    m = rhino3dm.File3dm.Read(str(out / "model.3dm"))
    zl = [l for l in m.Layers if l.FullPath.startswith("지형::영역별")]
    assert any(l.FullPath.endswith("::도로") for l in zl) and all(not l.Visible for l in zl)
    idx = {i: m.Layers[i].FullPath for i in range(len(m.Layers))}
    assert sum(1 for o in m.Objects if idx[o.Attributes.LayerIndex].startswith("지형::영역별::")) >= 3


def test_영역은_겹치지_않음():
    from shapely.geometry import box
    from contour_ai.terrain_zones import exclusive_zones
    clip = box(0, 0, 100, 100)
    z = exclusive_zones({"도로": box(0, 40, 100, 60), "보도": box(0, 30, 100, 70), "대지": box(40, 0, 60, 100)}, clip)
    names = [n for n, _ in z]
    assert names == ["대지", "도로", "보도", "그 밖"]
    assert abs(sum(g.area for _, g in z) - 10000) < 1e-6


def test_지형_단면_SVG_DXF(basic_model):
    """3차: 지도에 그은 선 → 단면 SVG(레이어) + DXF. 잘린 지형은 지형 높이와 맞아야 함."""
    import json
    import re
    import ezdxf
    from contour_ai.section import make_section, load_section_data
    out, _ = basic_model
    assert (Path(out) / "section_data.npz").exists()
    x0, y0, x1, y1 = json.loads((Path(out) / "summary.json").read_text(encoding="utf-8"))["bbox"]
    a, b = [x0 + 20, (y0 + y1) / 2], [x1 - 20, (y0 + y1) / 2 + 30]
    sv, dx, info = make_section(out, a, b, 150)
    assert re.fullmatch(r"section_\d\d\.svg", sv) and dx.endswith(".dxf")
    assert info["length"] > 200 and info["cut_buildings"] + info["back_buildings"] > 0
    svg = (Path(out) / sv).read_text(encoding="utf-8")
    for lay in ("잘린 지형", "뒤쪽 건물", "치수·글자"):
        assert lay in svg
    doc = ezdxf.readfile(str(Path(out) / dx))
    assert len(doc.modelspace().query("LWPOLYLINE POLYLINE LINE")) > 0
    D = load_section_data(out)
    import numpy as np
    assert np.isfinite(D["hz"]([(a[0] + b[0]) / 2], [(a[1] + b[1]) / 2])).all()
    sv2, _, _ = make_section(out, a, b, 0)          # 두 번째는 번호가 늘어남
    assert sv2 != sv


def test_해_그림자_화면():
    """3차: 해 그림자 패널 마크업과 해 위치 식(서울 하지 정오 무렵 고도 약 76°)."""
    html = (ROOT / "contour_ai/web/index.html").read_text(encoding="utf-8")
    for i in ("sunbtn", "sunpanel", "sundays", "sunhour", "sunpath", "sunsave", "secbar", "secbtn", "secgo"):
        assert f'id="{i}"' in html
    js = (ROOT / "contour_ai/web/ui_sun.js").read_text(encoding="utf-8")
    assert "export function sunPos" in js and "법정 일조" in js


def test_원형_범위(out_root, fake_sheet):
    """4차: 원형 범위 — 판이 원으로 잘리고, STL 도 원기둥 안, 2D 도면 범위선이 원."""
    import math
    import ezdxf
    import trimesh
    from conftest import _run
    out = out_root / "원형모델"
    s = _run(fake_sheet, out, shape="circle", outputs=["laser", "print", "plan"])
    assert s["sheets"] >= 2
    assert not (out / "model.3dm").exists()                       # 고르지 않은 파일은 안 만듦
    assert (out / "model.glb").exists()                            # 3D 보기는 늘
    doc = ezdxf.readfile(str(sorted((out / "laser").glob("*.dxf"))[0]))
    cut = [e for e in doc.modelspace().query("LWPOLYLINE") if e.dxf.layer.startswith("CUT") and e.closed]
    big = max(cut, key=lambda e: len(e))
    P = [(p[0], p[1]) for p in big.get_points()]
    xs, ys = [p[0] for p in P], [p[1] for p in P]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    area = abs(sum(P[i][0] * P[i - 1][1] - P[i - 1][0] * P[i][1] for i in range(len(P)))) / 2
    assert abs(w - 300) < 2 and abs(h - 300) < 2                   # 1:1000, 지름 300m = 300mm
    assert abs(area / (math.pi * 150 ** 2) - 1) < 0.02             # 원 넓이 (네모라면 1.27)
    m = trimesh.load(str(out / "model.stl"))
    assert m.is_watertight
    c = m.bounds.mean(axis=0)
    r = np.hypot(m.vertices[:, 0] - c[0], m.vertices[:, 1] - c[1]).max()
    assert r < 151
    assert len(ezdxf.readfile(str(out / "plan_2d.dxf")).modelspace().query('LWPOLYLINE[layer=="범위"]')) == 1


def test_받을_파일만_만듦(out_root, fake_sheet):
    from conftest import _run
    out = out_root / "레이저만"
    _run(fake_sheet, out, outputs=["laser"])
    names = {p.name for p in out.iterdir()}
    assert "laser" in names and "model.glb" in names and "summary.json" in names
    assert not names & {"model.3dm", "model.stl", "model_sketchup.dae", "plan_2d.dxf", "site_diagram.svg"}


def test_서버는_받을_파일_목록만_받음(app):
    assert app._outputs(None) is None
    assert app._outputs(["laser", "x", "rhino"]) == ["rhino", "laser"]
    assert app._outputs([]) == []


def test_항공사진_지형_OBJ_GLB(tmp_path):
    """4차: 항공사진 지형 — 꼭짓점 UV 가 그 자리 사진 색을 가리킨다."""
    import trimesh
    from shapely.geometry import Point
    from contour_ai.photo_terrain import photo_mesh, write_photo_terrain
    x0, y0, cell = 1000.0, 2000.0, 0.5
    rgb = np.zeros((400, 400, 3), np.uint8)
    rgb[:, :200] = (200, 0, 0)            # 서쪽 절반 빨강
    rgb[:, 200:] = (0, 0, 200)            # 동쪽 절반 파랑
    rgb[:200, :, 1] = 150                 # 북쪽 절반 초록 섞임 (위 행 = 북)
    aerial = (rgb, x0, y0, cell)
    clip = Point(x0 + 100, y0 + 100).buffer(90)
    hz = lambda x, y: 30 + 0.05 * (np.asarray(x) - x0)  # noqa: E731
    origin = (x0, y0)
    m, uv = photo_mesh(clip, hz, origin, aerial)
    X, Y = m.vertices[:, 0] + x0, m.vertices[:, 1] + y0
    col = rgb[(399 - np.floor(uv[:, 1] * 400).clip(0, 399)).astype(int), np.floor(uv[:, 0] * 400).clip(0, 399).astype(int)]
    inner = (np.abs(X - (x0 + 100)) > 2) & (np.abs(Y - (y0 + 100)) > 2)
    assert ((col[inner, 0] > 100) == (X[inner] < x0 + 100)).all()       # 서쪽 = 빨강
    assert ((col[inner, 1] > 100) == (Y[inner] > y0 + 100)).all()       # 북쪽 = 초록
    assert np.allclose(m.vertices[:, 2], hz(X, Y), atol=1e-6)
    names = write_photo_terrain(str(tmp_path), clip, hz, origin, aerial, log=lambda *a: None)
    assert set(names) == {"terrain_photo.obj", "terrain_photo.mtl", "terrain_photo.jpg", "terrain_photo.glb"}
    obj = (tmp_path / "terrain_photo.obj").read_text(encoding="utf-8")
    assert "mtllib terrain_photo.mtl" in obj and "\nvt " in obj and "/" in obj.split("\nf ")[1]
    g = trimesh.load(str(tmp_path / "terrain_photo.glb"))
    geom = list(g.geometry.values())[0]
    assert geom.visual.uv is not None and len(geom.visual.uv) == len(geom.vertices)
    assert abs(geom.bounds[1][1] - geom.bounds[0][1] - (hz(x0 + 190, 0) - hz(x0 + 10, 0))) < 1.0   # Y 위

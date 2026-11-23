"""시험 준비: 가짜 도엽을 만들고 모델을 한 번씩만 만들어 여러 시험이 나눠 씁니다.

실제 수치지도·개인 도면·정밀도로지도는 쓰지 않습니다 (저장소에 올리지 않는 자료).
결과는 '시험결과/' 폴더(환경변수 CONTOUR_TEST_OUT 로 바꿀 수 있음)에 남아
GitHub Actions 산출물로 올라갑니다.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

X0, Y0 = 201300.0, 554600.0      # make_fake_sheet.py 의 도엽 남서쪽 모서리


@pytest.fixture(scope="session")
def out_root():
    p = Path(os.environ.get("CONTOUR_TEST_OUT") or ROOT / "시험결과")
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture(scope="session")
def fake_sheet(out_root):
    """가짜 수치지도 2.0 도엽 zip (지형·건물·길을 지어낸 것)."""
    work = out_root / "_입력"
    zp = work / "가짜도엽.zip"
    if not zp.exists():
        work.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(ROOT / "tests" / "make_fake_sheet.py"), str(work / "shp"), str(zp)],
                       check=True)
    return zp


@pytest.fixture(scope="session")
def fake_site(out_root):
    """가짜 사업지 도면 DXF: 대지 경계 + 계획 레벨 글자 세 단."""
    import ezdxf
    p = out_root / "_입력" / "가짜사업지.dxf"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2010")
    m = doc.modelspace()
    bx0, by0, bx1, by1 = X0 + 300, Y0 + 320, X0 + 400, Y0 + 400
    m.add_lwpolyline([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)], close=True,
                     dxfattribs={"layer": "대지경계"})
    for i, z in enumerate((52.0, 55.0, 58.0)):
        x = bx0 + 15 + i * 33
        m.add_text(f"EL+{z:.2f}", dxfattribs={"layer": "TEXT", "height": 2}).set_placement((x, (by0 + by1) / 2))
    doc.saveas(p)
    return p


ALL_OUTPUTS = ("skp", "3dm", "stl", "laser")


def _run(source, out, size=300, **kw):
    from contour_ai import pipeline
    logs = []
    s = pipeline.run(str(source), str(out), size=size, green=False, log=lambda *a: logs.append(" ".join(map(str, a))), **kw)
    (Path(out) / "실행기록.txt").write_text("\n".join(logs), encoding="utf-8")
    return s


@pytest.fixture(scope="session")
def basic_model(out_root, fake_sheet):
    """기본 모델: 가짜 도엽, 300m, 위성영상 없이. 모든 출력 형식(Rhino·STL·레이저 코드도 시험하려고)."""
    out = out_root / "기본모델"
    return out, _run(fake_sheet, out, outputs=ALL_OUTPUTS)


@pytest.fixture(scope="session")
def site_model(out_root, fake_sheet, fake_site):
    """대지 경계 + 레벨 도면 + 대지 위 비우기."""
    out = out_root / "대지모델"
    return out, _run(fake_sheet, out, site_file=str(fake_site), site_clear=True, outputs=ALL_OUTPUTS)


@pytest.fixture(scope="session")
def skp_model(out_root, fake_sheet, fake_site):
    """출력 형식 기본값(v2.2: 스케치업만) + 대지 경계·레벨 도면."""
    out = out_root / "스케치업만"
    return out, _run(fake_sheet, out, site_file=str(fake_site))

"""시험용 가짜 수치지도 2.0 도엽(SHP zip) 만들기: 본체는 contour_ai/sample_sheet.py (사이트의 '예시 도엽'과 같은 것).

쓰는 법: python tests/make_fake_sheet.py <shp 폴더> <zip 경로> [quarry]
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
runpy.run_module("contour_ai.sample_sheet", run_name="__main__")

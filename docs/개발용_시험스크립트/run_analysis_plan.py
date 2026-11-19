"""대지 분석(경사·절토·성토)을 역북동 기숙사 배치 3차 레벨 판으로 시험한다.
원래 지형 = 수치지도 용인007·008 등고선, 계획 = 사용자가 그린 판(EL 165~190), 판 없는 남쪽은 원래 지형.
사용: python run_analysis_plan.py 결과폴더"""
import os, sys, pickle, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np, shapely
from contour_ai import pipeline, contour_model as cm
from contour_ai.analysis import site_analysis
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
rv = "/home/claude/rv"
folder = pipeline.prepare_source([f"{rv}/B010수치지도_37713007 (1).zip", f"{rv}/B010수치지도_37713008.zip"], tempfile.mkdtemp())
P = pickle.load(open("/home/claude/plan/pads.pkl", "rb")); D = pickle.load(open("/home/claude/plan/lv.pkl", "rb"))
site = D["site"]; regions = P["regions"]
b = site.buffer(80).bounds
data = cm.load_shp(folder, b)
orig = cm.build_interpolator(data["contours"], data["spots"])
items = sorted(regions.items())
for _, g in items: shapely.prepare(g)

def plan(xy):
    xy = np.atleast_2d(xy); z = orig(xy)
    for L, g in items:
        m = shapely.contains_xy(g, xy[:, 0], xy[:, 1]); z[m] = L
    return z
r = site_analysis(out, b, site, orig, plan)
import json; print(json.dumps(r, ensure_ascii=False)[:600])

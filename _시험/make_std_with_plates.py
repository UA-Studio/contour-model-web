"""시험용: 표준 대지 파일(plan2_std.dxf)에 배치 3차 레벨판·원지형 유지 구역(pads.pkl)을 더해 저장한다.
사용: python make_std_with_plates.py 표준.dxf pads.pkl 출력.dxf"""
import os, sys, pickle
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from contour_ai import sitefile
src, pk, out = sys.argv[1:4]
r = sitefile.read_standard(src)
d = pickle.load(open(pk, "rb"))
std = dict(site=r["poly"], plates=[(p, float(z)) for z, p in sorted(d["pads"].items())], keep=[d["keep"]],
           points=[], lines=[],
           buildings=r["buildings"], roads=r["roads"])
print(sitefile.write_standard(std, out, source="시험"))

"""대지 파일 정리(표준 대지 파일) 시험: 원본 DXF → 레이어 역할 추측 → 표준 DXF 저장 → 다시 읽기.
사용: python run_sitefile.py 수치지도SHP폴더 원본.dxf 출력.dxf [레이어=역할 ...]"""
import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from contour_ai import sitefile, pipeline
folder, src, out = sys.argv[1:4]
over = dict(a.split("=", 1) for a in sys.argv[4:])
crs = pipeline.read_crs(folder).to_wkt() if hasattr(pipeline.read_crs(folder), "to_wkt") else pipeline.read_crs(folder)
sheet = pipeline.sheet_bounds(folder)
t = time.time()
an = sitefile.analyze(src, crs, sheet, map_folder=folder)
print(f"분석 {time.time() - t:.1f}s  {an['how']}")
for L in an["layers"]:
    if L["role"] != "무시" or L["levels"] or L["area"] > 1000:
        print(f"  {L['name'][:30]:30s} {L['role']:6s} 닫힘{L['closed']}(z{L['closed_z']}) 열림{L['open']}(z{L['open_z']}) 레벨{L['levels']} 면적{L['area']:,.0f} {L['values'][:6]}")
roles = {L["name"]: L["role"] for L in an["layers"]}
roles.update(over)
std = sitefile.build_standard(an, roles)
info = sitefile.write_standard(std, out, an, roles, os.path.basename(src))
print(json.dumps(info["counts"], ensure_ascii=False), info["site_area_m2"], info["plate_levels"])
r = sitefile.read_standard(out)
print("다시 읽기", round(r["poly"].area), len(r["plates"]), len(r["keep"]), len(r["buildings"]), len(r["roads"]),
      r["levels"] and (len(r["levels"][0]), len(r["levels"][1])))

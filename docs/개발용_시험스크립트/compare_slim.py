"""용량 줄인 라이브러리로도 결과가 똑같은지: 같은 시험 4가지를 돌려 결과 파일 지문(sha1)을 남긴다.
사용: 파이썬 compare_slim.py 출력폴더      (원래 venv 와 줄인 venv 로 각각 돌린 뒤 지문 파일 두 개를 비교)
시험: ① 가짜 도엽 + 대지 + 비우기 + 스케치업 ② 가짜 도엽 + 가짜 5m DEM + 채석장 대지 ③ 가짜 도엽 + 가짜 항공사진(GeoTIFF)
      ④ 실제 역북동 도엽 + 표준 대지 파일(레벨판) + 대지 분석 그림"""
import hashlib, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from contour_ai import pipeline
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
T = "/home/claude/tests"
aerial = os.path.join(out, "fake_aerial.tif")
if not os.path.exists(aerial):
    import numpy as np, rasterio
    from rasterio.transform import from_origin
    rng = np.random.default_rng(7)
    img = (rng.random((3, 700, 700)) * 60 + 120).astype("uint8")
    img[1, 200:400, 200:420] = 150; img[0, 200:400, 200:420] = 70     # 초록 덩어리(나무처럼)
    with rasterio.open(aerial, "w", driver="GTiff", width=700, height=700, count=3, dtype="uint8", crs="EPSG:5186",
                       transform=from_origin(201250, 555250, 1.0, 1.0)) as ds:
        ds.write(img)
cases = {
    "fake_site_skp": dict(source=f"{T}/fake_sheet.zip", size=300, green=False, site_file=f"{T}/site.dxf", site_clear=True, sketchup=True),
    "fake_dem": dict(source=f"{T}/fake_sheet.zip", size=300, green=False, xy=(201300 + 380, 554600 + 400),
                     dem_files=f"{T}/fake_dem_5m.tif", sketchup=False, no_trees=True, site_file=f"{T}/quarry_site.dxf", site_clear=True),
    "fake_aerial": dict(source=f"{T}/fake_sheet.zip", size=300, green=True, aerial_file=aerial, sketchup=False),
    "real_plates": dict(source=["/home/claude/rv/B010수치지도_37713007 (1).zip", "/home/claude/rv/B010수치지도_37713008.zip"],
                        size=400, site_file="/tmp/claude-0/std/plan2_plates.dxf", site_clear=True, sketchup=False),
}
res = {}
for name, kw in cases.items():
    o = os.path.join(out, name)
    t = time.time()
    src = kw.pop("source")
    try:
        s = pipeline.run(src, o, log=lambda *a: None, **kw)
        ok = "ok"
    except Exception as e:
        ok = f"error {type(e).__name__}: {e}"
    h = {}
    for d, _, fs in os.walk(o):
        for f in sorted(fs):
            p = os.path.join(d, f)
            if f.endswith((".3dm", ".json", ".log")) or f == "fake_aerial.tif":
                continue      # 3dm·json 은 만든 시각이 들어가 매번 다르다
            h[os.path.relpath(p, o)] = hashlib.sha1(open(p, "rb").read()).hexdigest()[:12]
    res[name] = dict(status=ok, seconds=round(time.time() - t), files=h)
    print(name, ok, res[name]["seconds"], "s", len(h), "files", flush=True)
json.dump(res, open(os.path.join(out, "hashes.json"), "w"), ensure_ascii=False, indent=1)

"""표준 대지 파일로 모델 만들기 시험. 사용: python run_std_model.py 도엽zip,도엽zip 표준.dxf 결과폴더 [size]"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from contour_ai import pipeline
zips, std, out = sys.argv[1].split(","), sys.argv[2], sys.argv[3]
size = float(sys.argv[4]) if len(sys.argv) > 4 else 600
t0 = time.time()
s = pipeline.run(zips, out, size=size, site_file=std, site_clear=True, site_keep_terrain=os.environ.get("KEEP") == "1",
                 log=lambda m: print(f"{time.time() - t0:6.1f}s  {m}", flush=True))
print({k: s.get(k) for k in ("site", "analysis", "seconds")})

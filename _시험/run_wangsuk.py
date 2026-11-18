"""왕숙 배치계획(도면 좌표 mm) + 수치지도 37705027·028 → 표고점 맞추기로 대지 경계·계획 GL 읽어 모델까지.
사용: python run_wangsuk.py 결과폴더 [size]"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from contour_ai import pipeline
out = sys.argv[1]; size = float(sys.argv[2]) if len(sys.argv) > 2 else 800
w = "/home/claude/wangsuk"
t0 = time.time()
s = pipeline.run([f"{w}/B010수치지도_37705027.zip", f"{w}/B010수치지도_37705028.zip"], out, size=size,
                 site_file=f"{w}/왕숙 배치계획.dxf", site_clear=True,
                 log=lambda m: print(f"{time.time() - t0:6.1f}s  {m}", flush=True))
print({k: s.get(k) for k in ("site", "analysis", "seconds", "bbox")})

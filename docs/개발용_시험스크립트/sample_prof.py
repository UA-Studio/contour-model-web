"""표본 추출 프로파일: 0.25초마다 주 스레드가 어디(contour_ai 함수 → 가장 안쪽 함수)에 있는지 세어 단계별 시간을 낸다.
cProfile 과 달리 C 확장(rhino3dm Write 등) 안에서 보낸 시간도 잡힌다. 사용: python sample_prof.py 결과폴더 [size] key=value..."""
import sys, os, time, threading, collections, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
main_id = threading.get_ident()
hits = collections.Counter(); outer = collections.Counter(); stop = []
def sampler():
    while not stop:
        f = sys._current_frames().get(main_id)
        chain = []
        while f is not None:
            chain.append(f); f = f.f_back
        ours = [c for c in chain if "contour_ai" in c.f_code.co_filename]
        if ours:
            a = ours[0]; inner = chain[0]
            hits[f"{os.path.basename(a.f_code.co_filename)}:{a.f_code.co_name}:{a.f_lineno} → {inner.f_code.co_name}"] += 1
            top = [c for c in ours if os.path.basename(c.f_code.co_filename).startswith("build_")]  # build_flow → 단계(build_*.py)
            if top: outer[f"{top[-2].f_code.co_name if len(top) > 1 else top[-1].f_code.co_name}"] += 1
        time.sleep(0.25)
threading.Thread(target=sampler, daemon=True).start()
from contour_ai import pipeline
out = sys.argv[1]; size = float(sys.argv[2]) if len(sys.argv) > 2 else 500
kw = {}
for a in sys.argv[3:]:
    k, v = a.split("="); kw[k] = {"1": True, "0": False}.get(v, v)
rv = "/home/claude/rv"
t0 = time.time()
pipeline.run([f"{rv}/B010수치지도_37713007 (1).zip", f"{rv}/B010수치지도_37713008.zip"], out, size=size,
             site_file=f"{rv}/역북동 556-118번지 일원.dxf", log=lambda m: None, **kw)
stop.append(1)
print("총", round(time.time() - t0, 1), "초")
print("== contour_model 바깥 함수별 (초)")
for k, v in outer.most_common(15): print(f"{v / 4:7.1f}  {k}")
print("== 가장 많이 머문 곳 (초)")
for k, v in hits.most_common(25): print(f"{v / 4:7.1f}  {k}")

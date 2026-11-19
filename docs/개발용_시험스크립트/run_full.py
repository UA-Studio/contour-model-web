"""실제 자료로 처음부터 끝까지: 단계별 시간·메모리(RSS) 측정.
사용: python run_full.py 결과폴더 [size] [추가옵션 key=value ...]"""
import sys, os, time, json, threading, resource
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from contour_ai import pipeline
out = sys.argv[1]; size = float(sys.argv[2]) if len(sys.argv) > 2 else 1000
opts = dict(a.split("=", 1) for a in sys.argv[3:])
opts = {k: (v == "1") if v in ("0", "1") else v for k, v in opts.items()}
R = "/home/claude/rv/"
src = [R + "B010수치지도_37713007 (1).zip", R + "B010수치지도_37713008.zip"]
site = opts.pop("site", R + "역북동 556-118번지 일원.dxf")
def rss():
    with open("/proc/self/status") as f:
        for l in f:
            if l.startswith("VmRSS"): return int(l.split()[1]) / 1024
peak = [0]; stop = [False]
def watch():
    while not stop[0]:
        peak[0] = max(peak[0], rss()); time.sleep(0.2)
threading.Thread(target=watch, daemon=True).start()
t0 = time.time(); rows = []; last = [t0, 0]
def log(*a):
    msg = " ".join(map(str, a)); now = time.time()
    rows.append(dict(t=round(now - t0, 1), rss=round(rss()), peak=round(peak[0]), msg=msg))
    print(f"{now - t0:6.1f}s {rss():6.0f}MB  {msg}", flush=True)
err = None
try:
    s = pipeline.run(src, out, size=size, site_file=site or None, log=log, **opts)
except BaseException as e:
    import traceback; err = traceback.format_exc(); print(err)
stop[0] = True
json.dump(dict(rows=rows, peak=round(peak[0]), seconds=round(time.time() - t0, 1), error=err), open(out + "_timing.json", "w"), ensure_ascii=False, indent=1)
print("총", round(time.time() - t0, 1), "초, 최대 메모리", round(peak[0]), "MB", "오류" if err else "")

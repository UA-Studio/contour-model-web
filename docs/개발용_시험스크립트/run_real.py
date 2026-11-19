import sys, json, time
sys.path.insert(0, "/home/claude/work_v115")
from contour_ai import pipeline
out, fit, x, y = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
lines = []
s = pipeline.run("/home/claude/real/sheet_37713007.zip", out, size=500, xy=(x, y), green=False, sketchup=False,
                 terrain_fit=fit, breaklines=(len(sys.argv) < 6 or sys.argv[5] != "nob"), log=lambda *a: lines.append(" ".join(map(str, a))))
open(out + ".log", "w").write("\n".join(lines))
print(fit, s["levels"][0], s["levels"][-1], len(s["levels"]), s["buildings"], s["seconds"])

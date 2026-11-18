import sys, time, json
sys.path.insert(0, "/home/claude/work_v115")
from contour_ai import pipeline
out, clear = sys.argv[1], sys.argv[2] == "1"
t = time.time()
s = pipeline.run("/home/claude/tests/fake_sheet.zip", out, size=300, green=False, site_file="/home/claude/tests/site.dxf",
                 site_clear=clear, sketchup=True)
print(json.dumps(s.get("site"), ensure_ascii=False), s["buildings"], s["trees"], s["lights"], round(time.time() - t))

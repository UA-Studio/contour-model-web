import sys
sys.path.insert(0, "/home/claude/work_v115")
from contour_ai import pipeline
s = pipeline.run("/home/claude/tests/fake_sheet_q.zip", sys.argv[1], size=360, green=False, xy=(201300+380, 554600+400),
                 sketchup=False, no_trees=True, terrain_fit=sys.argv[2], log=lambda *a: None)
print(sys.argv[2], s["levels"], s["seconds"])

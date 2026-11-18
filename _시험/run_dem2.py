import sys
sys.path.insert(0, "/home/claude/work_v115")
from contour_ai import pipeline
s = pipeline.run("/home/claude/tests/fake_sheet.zip", sys.argv[1], size=300, green=False, xy=(201300+380, 554600+400),
                 dem_files=sys.argv[2] if sys.argv[2] != "-" else None, sketchup=False, no_trees=True,
                 site_file="/home/claude/tests/quarry_site.dxf", site_clear=True, log=lambda *a: None)
print(s["levels"], s["seconds"])

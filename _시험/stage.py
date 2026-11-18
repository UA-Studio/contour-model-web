import sys, time
sys.path.insert(0, "/home/claude/work_v115")
from contour_ai import pipeline, contour_model as cm
t0 = time.time(); last = [t0]
def log(*a):
    s = " ".join(map(str, a))
    if s[:1].isdigit() or s.startswith("끝"):
        print(f"{time.time()-t0:6.1f}s  {s}")
import functools
def timed(mod, name):
    f = getattr(mod, name)
    @functools.wraps(f)
    def w(*a, **k):
        t = time.time(); r = f(*a, **k); print(f"        {name}: {time.time()-t:.1f}s"); return r
    setattr(mod, name, w)
for n in ("write_3dm", "write_stl", "write_laser", "_preview_plan", "terrace_regions", "slope_heightfield", "load_shp", "building_masses", "_road_meshes", "_add_site"):
    timed(cm, n)
from contour_ai import skp_export; timed(skp_export, "write_sketchup")
pipeline.run("/home/claude/tests/fake_sheet.zip", sys.argv[1] if len(sys.argv) > 1 else "out_stage", size=500, green=False, sketchup=True, log=log)

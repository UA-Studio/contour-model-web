import json, os, sys, time
sys.path.insert(0, "/home/claude/repo")
os.environ["CONTOUR_OSM"] = "1"
from pyproj import Transformer, CRS
import contour_ai.osm_transit as ot
from contour_ai import pipeline
X0, Y0 = 201300.0, 554600.0
tf = Transformer.from_crs(CRS.from_epsg(5186), 4326, always_xy=True)
ll = lambda x, y: tf.transform(X0 + x, Y0 + y)
ents = [(329, 311.5, "1"), (322, 288.5, "2"), (288.5, 330, "3"), (311.5, 272, "4"), (336.4, 333.1, "5")]  # 5 = 건물 블록 안
el = []
for x, y, ref in ents:
    lo, la = ll(x, y); el.append({"type": "node", "lat": la, "lon": lo, "tags": {"railway": "subway_entrance", "ref": ref}})
lo, la = ll(300, 300); el.append({"type": "node", "lat": la, "lon": lo, "tags": {"railway": "station", "station": "subway", "name": "가짜역"}})
el.append({"type": "way", "tags": {"railway": "subway", "tunnel": "yes"}, "geometry": [{"lat": p[1], "lon": p[0]} for p in (ll(0, 300), ll(600, 300))]})
ot._post = lambda url, q, timeout=60: json.dumps({"elements": el}).encode()
out = sys.argv[1]; t = time.time()
logs = []
s = pipeline.run("/home/claude/tt/in/fake.zip", out, size=300, green=False, road_files=("/home/claude/tt/in/버스정류소_위치.csv",),
                 cache_dir="/home/claude/tt/cache", log=lambda *a: logs.append(" ".join(map(str, a))))
open(os.path.join(out, "log.txt"), "w").write("\n".join(logs))
print(time.time() - t); print("\n".join(l for l in logs if any(k in l for k in ("정류", "출입구", "OpenStreet", "지하", "무벽", "못"))))

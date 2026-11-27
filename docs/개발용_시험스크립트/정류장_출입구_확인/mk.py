"""가짜 도엽 + 정류장·지하철 출입구 무벽건물 + 정류장 자료 CSV."""
import os, sys, subprocess, zipfile, shutil
import shapefile
from shapely.geometry import box
from pyproj import Transformer, CRS
R = "/home/claude/repo"; W = "/home/claude/tt/in"
shutil.rmtree(W, ignore_errors=True)
subprocess.run([sys.executable, f"{R}/tests/make_fake_sheet.py", f"{W}/shp", f"{W}/base.zip"], check=True)
X0, Y0 = 201300.0, 554600.0
shp = f"{W}/shp/N3A_B0010000"
r = shapefile.Reader(shp, encoding="euc-kr")
fields = r.fields[1:]; recs = [list(s.record) for s in r.shapeRecords()]; shapes = [s.shape for s in r.shapeRecords()]
r.close()
add = [  # 20m 동서 도로(y 290~310), 북쪽 보도 y 310~313, 남쪽 보도 287~290
    (box(X0+240, Y0+310.8, X0+248, Y0+312.3), "버스정류장 쉘터(보도)"),
    (box(X0+320, Y0+310.1, X0+328, Y0+312.9), "지하철 출입구 캐노피(교차로 동쪽)"),
    (box(X0+272, Y0+287.2, X0+280, Y0+289.9), "지하철 출입구 캐노피(교차로 서남쪽)"),
    (box(X0+180, Y0+298.5, X0+210, Y0+301.5), "중앙버스정류장 지붕"),
    (box(X0+215, Y0+298.5, X0+235, Y0+301.5), "중앙버스정류장 지붕2"),
]
w = shapefile.Writer(shp, shapeType=shapefile.POLYGON, encoding="euc-kr")
for f in fields: w.field(*f)
for s, rc in zip(shapes, recs): w.shape(s); w.record(*rc)
for i, (b, nm) in enumerate(add):
    w.poly([list(b.exterior.coords)[::-1]]); w.record(f"T{i}", "무벽건물", 1, "", "")
w.close()
# 정류장 자료(위도·경도): 12m 남북 도로 x=450(차도 444~456), 동쪽 보도 456~459 / 20m 남북 도로 x=300 서쪽 보도
tf = Transformer.from_crs(CRS.from_epsg(5186), 4326, always_xy=True)
pts = [(X0+457.5, Y0+380, "가짜정류장A"), (X0+288.5, Y0+380, "가짜정류장B"), (X0+240+4, Y0+311.5, "쉘터와겹침")]
with open(f"{W}/버스정류소_위치.csv", "w", encoding="utf-8-sig") as f:
    f.write("정류소명,위도,경도\n")
    for x, y, n in pts:
        lo, la = tf.transform(x, y); f.write(f"{n},{la:.8f},{lo:.8f}\n")
z = zipfile.ZipFile(f"{W}/fake.zip", "w", zipfile.ZIP_DEFLATED)
for fn in sorted(os.listdir(f"{W}/shp")): z.write(f"{W}/shp/{fn}", "가짜도엽_376082055/" + fn)
z.close(); print("ok")

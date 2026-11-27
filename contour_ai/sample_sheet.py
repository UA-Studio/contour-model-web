"""예시·시험용 가짜 수치지도 2.0 도엽(SHP zip). 실제 자료가 아님: 삼선동 근처 좌표에 지어낸 지형·건물·길.

사이트의 "예시 도엽으로 해 보기"와 자동 시험(tests/make_fake_sheet.py)이 함께 씁니다.
쓰는 법: python -m contour_ai.sample_sheet <shp 폴더> <zip 경로> [quarry]
"""
import os, sys, zipfile, math
import numpy as np
import shapefile
from shapely.geometry import box, Polygon, LineString
from shapely.ops import unary_union
from pyproj import CRS


def main(argv):
    global out
    out = argv[1]; os.makedirs(out, exist_ok=True)
    X0, Y0, W = 201300.0, 554600.0, 600.0
    sheet = box(X0, Y0, X0 + W, Y0 + W)
    enc = "euc-kr"

    def writer(name, kind):
        w = shapefile.Writer(os.path.join(out, name), shapeType=kind, encoding=enc)
        return w
    def prj(name):
        open(os.path.join(out, name + ".prj"), "w").write(CRS.from_epsg(5186).to_wkt("WKT1_ESRI"))

    # 지형: 북동쪽으로 올라가는 비탈 + 둔덕
    QUARRY = len(argv) > 3
    def H0(x, y):
        u, v = (x - X0) / W, (y - Y0) / W
        return 30 + 28 * u * 0.6 + 22 * v + (40 if QUARRY else 9) * np.exp(-((u - .7) ** 2 + (v - .65) ** 2) / .03)
    def H(x, y):
        z = H0(x, y)
        if not QUARRY:
            return z
        # 채석장: 둔덕 남서쪽을 높이 8m·폭 14m 계단으로 깎음
        cx, cy = X0 + 380, Y0 + 400
        d = np.hypot(x - cx, (y - cy) * 1.3)
        bench = 40 + 8 * np.floor(np.clip(d, 0, 140) / 14)
        return np.where(d < 140, np.minimum(z, bench), z)
    import contourpy
    xs = np.linspace(X0 - 50, X0 + W + 50, 141); ys = np.linspace(Y0 - 50, Y0 + W + 50, 141)
    xs = np.linspace(X0 - 50, X0 + W + 50, 701); ys = np.linspace(Y0 - 50, Y0 + W + 50, 701)
    XX, YY = np.meshgrid(xs, ys); ZZ = H(XX, YY)
    cg = contourpy.contour_generator(XX, YY, ZZ)
    w = writer("N3L_F0010000", shapefile.POLYLINE); w.field("등고수치", "N", 10, 2)
    for lev in np.arange(20, 120, 5.0 if QUARRY else 1.0):
        for ln in cg.lines(lev):
            if len(ln) > 1:
                w.line([ln.tolist()]); w.record(float(lev))
    w.close(); prj("N3L_F0010000")
    w = writer("N3P_F0020000", shapefile.POINT); w.field("수치", "N", 10, 2)
    rng = np.random.default_rng(3)
    for _ in range(400 if QUARRY else 40):
        x, y = X0 + rng.uniform(0, W), Y0 + rng.uniform(0, W); w.point(x, y); w.record(float(H(x, y)))
    w.close(); prj("N3P_F0020000")

    # 길: 격자 가로(12m 차도 + 양쪽 3m 보도), 골목(6m)
    roads, walks, cls = [], [], []
    def street(a, b, wd, lanes, sw=3.0):
        ln = LineString([a, b]); roads.append(ln.buffer(wd / 2, cap_style="flat"))
        if sw: walks.append(ln.buffer(wd / 2 + sw, cap_style="flat"))
        cls.append((ln, lanes, wd))
    for k, (wd, ln_) in zip((150, 300, 450), ((12, 4), (20, 6), (12, 2))):
        street((X0, Y0 + k), (X0 + W, Y0 + k), wd, ln_)
        street((X0 + k, Y0), (X0 + k, Y0 + W), wd, ln_)
    for k in (75, 225, 375, 525):
        street((X0 + k, Y0), (X0 + k, Y0 + W), 6, 1, sw=0)
    road = unary_union(roads).intersection(sheet)
    walk = unary_union(walks).intersection(sheet).difference(road)
    def polyw(name, geoms, fields, recs):
        w = writer(name, shapefile.POLYGON)
        for f in fields: w.field(*f)
        for g, r in zip(geoms, recs):
            parts = [list(g.exterior.coords)[::-1] if g.exterior.is_ccw else list(g.exterior.coords)]
            for i in g.interiors:
                parts.append(list(i.coords) if i.is_ccw else list(i.coords)[::-1])
            w.poly(parts); w.record(*r)
        w.close(); prj(name)
    def polys(g): return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"]
    rp = polys(road); polyw("N3A_A0010000", rp, [("UFID", "C", 20)], [(f"R{i}",) for i in range(len(rp))])
    wp = polys(walk); polyw("N3A_A0033320", wp, [("UFID", "C", 20)], [(f"S{i}",) for i in range(len(wp))])
    w = writer("N3L_A0020000", shapefile.POLYLINE)
    w.field("차로수", "N", 4, 0); w.field("도로폭", "N", 8, 1); w.field("분리대유무", "C", 10); w.field("일방통행", "C", 10)
    for ln, n, wd in cls:
        w.line([list(ln.coords)]); w.record(n, wd, "무", "무")
    w.close(); prj("N3L_A0020000")

    # 논: 가운데 북쪽 한 블록은 건물 대신 논 (계단식 대지 시험: 한 필씩 평평하게, 비탈의 큰 논은 다랑이로)
    PADDY_ZONE = box(X0 + 229, Y0 + 314, X0 + 286, Y0 + 439)
    paddies = [box(X0 + 231, Y0 + 316 + k * 30.5, X0 + 284, Y0 + 316 + k * 30.5 + 29) for k in range(3)] + \
              [box(X0 + 231, Y0 + 407.5, X0 + 284, Y0 + 437)]
    polyw("N3A_D0010000", paddies, [("구분", "C", 10), ("UFID", "C", 20)], [("논", f"P{i}") for i in range(4)])

    # 건물: 블록 안에 줄지어
    blocked = unary_union([road, walk]).buffer(2.5)
    B, R = [], []
    kinds = ["일반주택", "연립주택", "아파트", "주택외건물"]; uses = ["주택", "근린생활시설", "교육연구시설", "기타시설"]
    i = 0
    for bx in np.arange(X0 + 6, X0 + W - 20, 19):
        for by in np.arange(Y0 + 6, Y0 + W - 20, 17):
            w_, d_ = rng.uniform(9, 15), rng.uniform(8, 13)
            b = box(bx, by, bx + w_, by + d_)
            if b.intersects(blocked) or rng.uniform() < 0.12: continue
            if QUARRY and np.hypot(bx - X0 - 380, (by - Y0 - 400) * 1.3) < 160: continue
            if b.intersects(PADDY_ZONE): continue
            fl = int(rng.choice([1, 2, 2, 3, 3, 4, 5, 7, 12]))
            B.append(b); R.append((f"B{i}", kinds[i % 4], fl, f"시험건물{i}" if i % 9 == 0 else "", uses[i % 4])); i += 1
    # 옹벽: 남서쪽 블록의 건물 앞(남쪽 3m)에 높이 2m 옹벽 (계단식 대지 시험: 옹벽이 터의 경계)
    w = writer("N3L_F0040000", shapefile.POLYLINE)
    for f in (("종류", "C", 10), ("상하구분", "C", 10), ("연장", "N", 10, 2), ("높이", "N", 8, 2), ("재질", "C", 10), ("UFID", "C", 20)):
        w.field(*f)
    nw = 0
    for b in B:
        x0_, y0_, x1_, y1_ = b.bounds
        if X0 + 155 < x0_ < X0 + 295 and Y0 + 155 < y0_ < Y0 + 295:
            ln = [(x0_ - 2, y0_ - 3), (x1_ + 2, y0_ - 3)]
            w.line([ln]); w.record("옹벽", "상단", x1_ - x0_ + 4, 2.0, "콘크리트", f"W{nw}"); nw += 1
    w.close(); prj("N3L_F0040000")
    polyw("N3A_B0010000", B, [("UFID", "C", 20), ("종류", "C", 20), ("층수", "N", 4, 0), ("명칭", "C", 40), ("용도", "C", 30)], R)
    print("건물", len(B), "차도면", len(rp), "보도면", len(wp), "옹벽", nw, "논", len(paddies))
    z = zipfile.ZipFile(argv[2], "w", zipfile.ZIP_DEFLATED)
    for f in sorted(os.listdir(out)): z.write(os.path.join(out, f), "가짜도엽_376082055/" + f)
    z.close()


if __name__ == "__main__":
    main(sys.argv)

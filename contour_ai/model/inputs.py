"""수치지도 입력 읽기: DXF·SHP 에서 등고선·표고점·건물·도로 등 꺼내기."""
import os
from collections import defaultdict
import ezdxf
from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon, box
from .layers import BUILDING_PREFIX, CONTOUR_LAYERS, ROAD_PREFIXES, SPOT_LAYERS, WALL_PREFIX, WATER_PREFIX



# ---------------------------------------------------------------- 입력
def load_dxf(path, bbox):
    doc = ezdxf.readfile(path, encoding="cp949")
    msp = doc.modelspace()
    clip = box(*bbox)
    big = clip.buffer(200)  # 보간용으로 주변 등고선까지 읽음
    data = defaultdict(list)
    for e in msp.query("LWPOLYLINE"):
        layer = e.dxf.layer
        pts = [(x, y) for x, y, *_ in e.get_points()]
        if len(pts) < 2:
            continue
        if layer in CONTOUR_LAYERS:
            ls = LineString(pts)
            if ls.intersects(big):
                data["contours"].append((ls, float(e.dxf.elevation)))
        elif layer.startswith(BUILDING_PREFIX) and e.closed and len(pts) >= 3:
            pg = make_valid(Polygon(pts))
            if pg.intersects(clip):
                data["buildings"].append((pg, layer, 0, ""))
        else:
            kind = ("roads" if layer.startswith(ROAD_PREFIXES) else
                    "walls" if layer.startswith(WALL_PREFIX) else
                    "water" if layer.startswith(WATER_PREFIX) else None)
            if kind:
                if e.closed:
                    pts.append(pts[0])
                ls = LineString(pts)
                if ls.intersects(clip):
                    data[kind].append(ls.intersection(clip))
    for e in msp.query("INSERT"):
        if e.dxf.layer in SPOT_LAYERS:
            x, y, z = e.dxf.insert
            if big.contains(Point(x, y)):
                data["spots"].append((x, y, z))
    return data


def load_shp(folder, bbox):
    """수치지도 2.0 SHP 폴더. 건물 층수(층수 필드)와 종류가 들어 있다."""
    import shapefile
    from shapely.geometry import shape

    def read(name):
        fn = os.path.join(folder, name)
        if not os.path.exists(fn + ".shp"):
            return []
        r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
        names = [f[0] for f in r.fields[1:]]
        return [(shape(sr.shape.__geo_interface__), dict(zip(names, sr.record)))
                for sr in r.iterShapeRecords()]
    clip = box(*bbox)
    big = clip.buffer(200)
    data = defaultdict(list)
    for g, rec in read("N3L_F0010000"):
        if g.intersects(big):
            for ls in getattr(g, "geoms", [g]):
                data["contours"].append((LineString([c[:2] for c in ls.coords]), float(rec["등고수치"])))
    for g, rec in read("N3P_F0020000"):
        if big.contains(g):
            data["spots"].append((g.x, g.y, float(rec["수치"])))
    for g, rec in read("N3A_B0010000"):
        g = make_valid(g)
        if g.intersects(clip):
            data["buildings"].append((g, rec.get("종류", ""), int(rec.get("층수") or 0), rec.get("명칭", ""), rec))
    # 절토·성토면의 위 끝·아래 끝 선, 옹벽(높이)을 지형 꺾임선으로 (1:5000 이상 수치지도에 있음)
    for g, rec in read("N3L_F0030000"):
        if g.intersects(big):
            for ls in getattr(g, "geoms", [g]):
                data["breaks"].append((LineString([c[:2] for c in ls.coords]), str(rec.get("구분") or ""),
                                       str(rec.get("상하구분") or ""), 0.0))
    for g, rec in read("N3L_F0040000"):
        if g.intersects(big):
            try:
                h = float(rec.get("높이") or 0)
            except (TypeError, ValueError):
                h = 0.0
            for ls in getattr(g, "geoms", [g]):
                data["breaks"].append((LineString([c[:2] for c in ls.coords]), str(rec.get("종류") or "옹벽"),
                                       str(rec.get("상하구분") or ""), h))
    # 논: 계단식 대지에서 한 필씩 평평하게 (비탈의 큰 논은 다랑이로)
    for g, rec in read("N3A_D0010000"):
        if str(rec.get("구분") or "").strip() == "논":
            g = make_valid(g)
            if g.intersects(clip.buffer(20)):
                data["paddies"].append(g)
    for name, kind in [("N3A_A0010000", "roads"), ("N3A_A0033320", "roads"),
                       ("N3L_B0020000", "walls"), ("N3A_E0010001", "water")]:
        for g, rec in read(name):
            if g.intersects(clip):
                if kind == "roads" and g.geom_type.endswith("Polygon"):
                    data["road_areas"].append(make_valid(g))     # 계단식 대지: 길 면은 터가 넘지 않는다
                line = g.boundary if g.geom_type.endswith("Polygon") else g
                data[kind].append(line.intersection(clip))
    return data

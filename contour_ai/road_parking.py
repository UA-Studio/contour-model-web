"""주차장: 수치지도 주차장 면 안에 주차 구획 줄 깔기.
"""
import os

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box


# ---------------------------------------------------------------- 주차 구획
def parking_lots(folder, bbox, buildings=None):
    import shapefile
    from shapely.geometry import shape
    fn = os.path.join(folder, "N3A_C0430000")
    if not os.path.exists(fn + ".shp"):
        return []
    clip = box(*bbox)
    r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
    out = []
    for sr in r.iterShapeRecords():
        try:
            g = shape(sr.shape.__geo_interface__).buffer(0).intersection(clip)
        except Exception:
            continue
        if buildings is not None and not buildings.is_empty:
            g = g.difference(buildings)      # 주차 건물(입체 주차장)은 건물이 대신
        for p in getattr(g, "geoms", [g]):
            if p.geom_type == "Polygon" and p.area > 30:
                out.append(p)
    r.close()
    return out


def parking_marks(lot, stall=(2.5, 5.0), aisle=6.0, line_w=0.12):
    """긴 축 방향으로 [구획 줄][통로][구획 줄] 반복. 반환 [(Polygon, "주차구획")]"""
    rr = lot.minimum_rotated_rectangle
    c = np.asarray(rr.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
        c0 = c[1]
    else:
        c0 = c[0]
    L1, L2 = np.linalg.norm(e1), np.linalg.norm(e2)
    u, v = e1 / L1, e2 / L2
    # 기준 모서리를 c0 로 잡았을 때 v 가 사각형 안쪽을 향하도록
    if not rr.buffer(0.01).contains(Point(*(c0 + u * L1 / 2 + v * 0.5))):
        v = -v
    inner = lot.buffer(-0.4)
    sw, sl = stall
    out = []
    t = 0.5                          # 가장자리 여유(담·연석)
    rows = []
    while t + sl <= L2 + 1e-6:      # 구획 줄 위치 (v 방향 시작점, 차가 들어오는 쪽 부호)
        rows.append((t, +1))
        if t + sl + aisle + sl <= L2 + 1e-6:
            rows.append((t + sl + aisle, -1))
        t += 2 * sl + aisle
    for t0, _ in rows:
        n = int((L1 - 1.0) // sw)
        off = (L1 - n * sw) / 2
        for k in range(n):
            a = off + k * sw
            q = [c0 + u * a + v * t0, c0 + u * (a + sw) + v * t0,
                 c0 + u * (a + sw) + v * (t0 + sl), c0 + u * a + v * (t0 + sl)]
            cell_ = Polygon(q)
            if not inner.contains(cell_):
                continue
            for p_a, p_b in ((q[0], q[3]), (q[1], q[2])):   # 구획 옆선 두 개
                out.append((LineString([p_a, p_b]).buffer(line_w / 2, cap_style=2), "주차구획"))
    return out

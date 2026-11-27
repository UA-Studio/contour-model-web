"""대지 위 비우기: 건물·면·선·도로 정보를 대지 경계로 자르고, 대지 윤곽 메시를 만든다."""


from shapely.geometry import MultiPolygon
from .site_read import _polys


# ---------------------------------------------------------------- 대지 위 비우기
def inside_ratio(poly, site):
    """poly 넓이 가운데 대지 안에 든 비율"""
    a = poly.area
    return poly.intersection(site).area / a if a > 0 else 0.0


def split_masses(masses, site, ratio=0.5):
    """건물 매스를 (남길 것, 대지 위라 뺄 것)으로. 발자국의 절반 넘게 대지 안이면 뺀다.

    옆 대지 건물이 경계선을 조금 넘어온 것은 남긴다.
    """
    from shapely import prepare
    prepare(site)
    keep, gone = [], []
    for m in masses:
        p = m["poly"]
        (gone if p.intersects(site) and inside_ratio(p, site) > ratio else keep).append(m)
    return keep, gone


def cut_areas(areas, site, min_area=0.5):
    """{종류: 면} 에서 대지 부분을 뺀다. 빈 것은 지운다."""
    out = {}
    for k, g in areas.items():
        if g is None or not hasattr(g, "difference"):
            out[k] = g
            continue
        d = g.difference(site)
        ps = [p for p in _polys(d) if p.area >= min_area]
        if ps:
            out[k] = MultiPolygon(ps) if len(ps) > 1 else ps[0]
    return out


def cut_lines(lines, site):
    out = []
    for ls in lines:
        d = ls.difference(site)
        for g in getattr(d, "geoms", [d]):
            if not g.is_empty and g.geom_type == "LineString" and g.length > 0.2:
                out.append(g)
    return out


def outside(points, site, xy=lambda t: (t[0], t[1])):
    """점 목록에서 대지 밖 것만"""
    import numpy as np
    from shapely import contains_xy
    pts = list(points)
    if not pts:
        return pts
    P = np.array([xy(t) for t in pts], float)
    inn = contains_xy(site, P[:, 0], P[:, 1])
    return [t for t, k in zip(pts, inn) if not k]


def cut_road_info(info, site):
    """도로 디테일(노면표시·신호등·전봇대·정류장 등)에서 대지 안 것을 뺀다. 뺀 개수를 돌려준다."""
    n = 0
    if not info:
        return 0
    if info.get("marks"):
        keep = [t for t in info["marks"] if not (t[0].intersects(site) and inside_ratio(t[0], site) > 0.5)]
        n += len(info["marks"]) - len(keep)
        info["marks"] = keep
    for key in ("signals", "poles"):
        if info.get(key):
            keep = outside(info[key], site)
            n += len(info[key]) - len(keep)
            info[key] = keep
    if info.get("transit"):
        keep = [d for d in info["transit"] if not ("poly" in d and inside_ratio(d["poly"], site) > 0.5)]
        n += len(info["transit"]) - len(keep)
        info["transit"] = keep
    isl = info.get("islands")
    if isl is not None and not isl.is_empty:
        d = isl.difference(site)
        info["islands"] = d if not d.is_empty else None
    return n


def outline_mesh(site, zf, origin, width=0.7, lift=0.25, step=1.0):
    """대지 경계를 지형 위에 얹은 얇은 띠 메시(미리보기용). zf(x, y) → 지표 높이."""
    import numpy as np
    import trimesh
    ms = []
    for p in _polys(site):
        for ring in [p.exterior, *p.interiors]:
            n = max(4, int(ring.length / step))
            pts = np.array([ring.interpolate(i / n, normalized=True).coords[0][:2] for i in range(n)])
            d = np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)
            L = np.hypot(d[:, 0], d[:, 1]); L[L == 0] = 1
            nrm = np.column_stack([-d[:, 1], d[:, 0]]) / L[:, None] * width / 2
            z = np.array([float(zf(x, y)) for x, y in pts]) + lift
            a = np.column_stack([pts + nrm, z]); b = np.column_stack([pts - nrm, z])
            v = np.vstack([a, b, a + [0, 0, 0.15], b + [0, 0, 0.15]])
            f = []
            for i in range(n):
                j = (i + 1) % n
                for o1, o2 in ((2 * n, 3 * n), (0, n)):            # 윗면, 밑면
                    f += [[o1 + i, o2 + i, o1 + j], [o1 + j, o2 + i, o2 + j]]
                for o1, o2 in ((0, 2 * n), (n, 3 * n)):            # 옆면
                    f += [[o1 + i, o1 + j, o2 + i], [o1 + j, o2 + j, o2 + i]]
            v[:, 0] -= origin[0]; v[:, 1] -= origin[1]
            f = np.array(f)
            # 얇은 띠라 어느 쪽에서 봐도 보이게 앞뒤 두 면으로
            ms.append(trimesh.Trimesh(v, np.vstack([f, f[:, ::-1]]), process=False))
    return trimesh.util.concatenate(ms) if ms else None

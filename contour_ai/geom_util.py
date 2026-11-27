"""여러 기능이 함께 쓰는 작은 도형 도구: 면 펼치기, 모서리 다듬기, 좌표 옮기기, manifold 변환.
"""
from collections import defaultdict

import numpy as np
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union


def as_polys(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def chaikin(coords, n=2, bbox=None, tol=1e-6):
    """닫힌 링을 Chaikin 방식으로 n번 깎아 부드러운 곡선으로.

    bbox 를 주면 모델 테두리 위의 점은 움직이지 않는다. 테두리를 따라가는 긴 변의 끝(모델 모서리)을
    깎으면 변 길이의 1/4 씩 큰 삼각형이 잘려 나가 계단판 모서리가 비었다(2026-11-19 사용자 신고)."""
    pts = np.asarray(coords, float)[:-1, :2]
    for _ in range(n):
        if len(pts) < 3:
            break
        nxt = np.roll(pts, -1, axis=0)
        q, r = 0.75 * pts + 0.25 * nxt, 0.25 * pts + 0.75 * nxt
        if bbox is not None:
            eps = tol
            pin = ((np.abs(pts[:, 0] - bbox[0]) < eps) | (np.abs(pts[:, 0] - bbox[2]) < eps) |
                   (np.abs(pts[:, 1] - bbox[1]) < eps) | (np.abs(pts[:, 1] - bbox[3]) < eps))
            q[pin] = pts[pin]
            pin_n = np.roll(pin, -1)
            r[pin_n] = nxt[pin_n]
        pts = np.column_stack([q, r]).reshape(-1, 2)
        keep = np.r_[True, np.any(np.abs(np.diff(pts, axis=0)) > 1e-9, axis=1)]
        pts = pts[keep]
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
    return np.vstack([pts, pts[:1]])


def smooth_poly(g, n=2, bbox=None, tol=1e-6):
    out = []
    for p in as_polys(g):
        if len(p.exterior.coords) < 4:
            continue
        holes = [chaikin(h.coords, n, bbox, tol) for h in p.interiors if len(h.coords) >= 4]
        out.append(make_valid(Polygon(chaikin(p.exterior.coords, n, bbox, tol), holes)))
    return unary_union(out) if out else g


def cut_regions(regions, inlay):
    """판마다 그 판 높이에 드러난 도로 자리를 파낸 판 모양. 반환: {L: geometry}"""
    by = defaultdict(list)
    for p, L, _ in inlay:
        by[L].append(p)
    return {L: (g.difference(unary_union(by[L])) if by[L] else g) for L, g in regions.items()}


def _shifted(mesh, origin):
    m = mesh.copy()
    m.apply_translation([-origin[0], -origin[1], 0])
    return m


# ---------------------------------------------------------------- 출력: 3D 프린트용 STL
def _mf_from_trimesh(m):
    import manifold3d as mf
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, dtype=np.float32),
                               tri_verts=np.asarray(m.faces, dtype=np.uint32)))


def _mf_extrude(poly, z0, z1, origin):
    """shapely 면 → manifold 솔리드 (z0 ~ z1). 구멍 있는 면도 그대로."""
    import manifold3d as mf
    rings = [np.asarray(poly.exterior.coords)[:-1, :2] - origin[:2]]
    rings += [np.asarray(r.coords)[:-1, :2] - origin[:2] for r in poly.interiors]
    cs = mf.CrossSection([r.astype(np.float64) for r in rings], mf.FillRule.EvenOdd)
    if cs.area() <= 1e-6 or z1 - z0 <= 1e-6:
        return None
    return cs.extrude(float(z1 - z0)).translate((0.0, 0.0, float(z0)))


# ---------------------------------------------------------------- 출력: STL
def _shift(p, origin):
    return Polygon([(x - origin[0], y - origin[1]) for x, y in p.exterior.coords],
                   [[(x - origin[0], y - origin[1]) for x, y in h.coords] for h in p.interiors])

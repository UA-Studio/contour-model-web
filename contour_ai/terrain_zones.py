"""영역별 지형 (v2.2): 곡면 지형을 대지 · 물 · 도로 · 보도 · 녹지 · 그 밖 조각으로 나눈다.

지형 메시의 삼각형을 가운데 점으로 나누면 길쭉한 삼각형 때문에 경계가 톱니처럼 된다.
그래서 영역마다 격자 칸으로 잘라 칸 조각을 삼각형으로 나누고, 높이는 지형 표면(hz)에서 다시 읽는다.
경계선이 영역 모양 그대로 깔끔하고, 높이는 전체 지형과 같다.
"""
import numpy as np
import shapely
import trimesh
from shapely.geometry import box
from shapely.ops import unary_union

ZONE_ORDER = ["대지", "물", "도로", "보도", "녹지"]       # 겹치면 앞쪽이 이김
ZONE_RGB = {"대지": (37, 99, 235), "물": (150, 170, 190), "도로": (118, 118, 115), "보도": (228, 226, 220),
            "녹지": (170, 185, 160), "그 밖": (214, 212, 205)}


def exclusive_zones(zones, clip):
    """겹치지 않게: 앞 영역이 차지한 곳은 뒤 영역에서 뺀다. 남는 곳은 '그 밖'. [(이름, 면)]"""
    taken, out = None, []
    for name in ZONE_ORDER:
        g = zones.get(name)
        if g is None or g.is_empty:
            continue
        g = g.intersection(clip)
        if taken is not None:
            g = g.difference(taken)
        if g.area > 1.0:
            out.append((name, g))
            taken = g if taken is None else unary_union([taken, g])
    rest = clip if taken is None else clip.difference(taken)
    if rest.area > 1.0:
        out.append(("그 밖", rest))
    return out


def _tri_piece(p):
    """칸 조각(면) → (꼭짓점 xy, 삼각형). 구멍이 있어도 된다."""
    from .geom_util import as_polys
    vs, fs, n = [], [], 0
    for q in as_polys(p):
        if q.area < 1e-4:
            continue
        v, f = trimesh.creation.triangulate_polygon(q, engine="earcut")
        vs.append(v); fs.append(f + n); n += len(v)
    if not vs:
        return None, None
    return np.vstack(vs), np.vstack(fs)


def _leaves(g, i0, i1, j0, j1, xs0, ys0, cell, max_pts=300, min_n=8):
    """격자 칸 범위를 4등분해 가며 영역 면도 그 칸 범위로 잘라 둔다 → (칸 범위, 그 안의 영역 조각).

    큰 영역(도로망 전체 등)은 꼭짓점이 수만 개라, 경계 칸마다 통째로 자르면 1500m에서 2분이 넘게 걸렸다.
    잘게 나눈 조각에 대고 자르면 같은 결과가 훨씬 빨리 나온다.
    """
    if g.is_empty:
        return
    if shapely.get_num_coordinates(g) <= max_pts or (i1 - i0 <= min_n and j1 - j0 <= min_n):
        yield (i0, i1, j0, j1), g
        return
    im, jm = (i0 + i1) // 2, (j0 + j1) // 2
    for a0, a1 in ((i0, im), (im, i1)):
        for b0, b1 in ((j0, jm), (jm, j1)):
            if a1 <= a0 or b1 <= b0:
                continue
            q = box(xs0 + b0 * cell, ys0 + a0 * cell, xs0 + b1 * cell, ys0 + a1 * cell)
            yield from _leaves(g.intersection(q), a0, a1, b0, b1, xs0, ys0, cell, max_pts, min_n)


def zone_mesh(g, hz, origin, cell):
    """영역 면 하나를 격자 칸으로 잘라 지형 높이에 얹은 메시."""
    x0, y0, x1, y1 = g.bounds
    xs = np.arange(np.floor(x0 / cell) * cell, x1, cell)
    ys = np.arange(np.floor(y0 / cell) * cell, y1, cell)
    if not len(xs) or not len(ys):
        return None
    V, F, n = [], [], 0
    for (i0, i1, j0, j1), piece in _leaves(g, 0, len(ys), 0, len(xs), xs[0], ys[0], cell):
        X, Y = np.meshgrid(xs[j0:j1], ys[i0:i1])
        X, Y = X.ravel(), Y.ravel()
        cells = shapely.box(X, Y, X + cell, Y + cell)
        shapely.prepare(piece)
        inside = shapely.contains(piece, cells)
        touch = shapely.intersects(piece, cells) & ~inside
        # 영역 안에 통째로 든 칸: 네모 두 삼각형
        full = np.nonzero(inside)[0]
        if len(full):
            cx, cy = X[full], Y[full]
            quad = np.stack([np.column_stack([cx, cy]), np.column_stack([cx + cell, cy]),
                             np.column_stack([cx + cell, cy + cell]), np.column_stack([cx, cy + cell])], axis=1)
            V.append(quad.reshape(-1, 2))
            base = n + np.arange(len(full))[:, None] * 4
            F.append(np.vstack([base + [0, 1, 2], base + [0, 2, 3]]))
            n += len(full) * 4
        # 경계에 걸친 칸: 잘라서 삼각형으로
        if touch.any():
            for p in shapely.intersection(cells[touch], piece):
                v, f = _tri_piece(p)
                if v is None:
                    continue
                V.append(v); F.append(f + n); n += len(v)
    if not V:
        return None
    V = np.vstack(V); F = np.vstack(F)
    z = hz(V[:, 0], V[:, 1])
    m = trimesh.Trimesh(np.column_stack([V[:, 0] - origin[0], V[:, 1] - origin[1], z]), F, process=True)
    # 위를 보게 (earcut 은 반시계, 네모는 반시계로 넣었으므로 그대로). 혹시 뒤집힌 면은 바로잡는다
    bad = m.face_normals[:, 2] < 0
    if bad.any():
        f2 = m.faces.copy(); f2[bad] = f2[bad][:, ::-1]; m = trimesh.Trimesh(m.vertices, f2, process=False)
    return m


def zone_meshes(zones, clip, hz, origin):
    """[(이름, 메시)] — 넓을수록 칸을 키워 가볍게."""
    side = max(clip.bounds[2] - clip.bounds[0], clip.bounds[3] - clip.bounds[1])
    cell = 2.0 if side <= 400 else 3.0 if side <= 700 else 4.0
    out = []
    for name, g in exclusive_zones(zones, clip):
        m = zone_mesh(g, hz, origin, cell)
        if m is not None and len(m.faces):
            out.append((name, m))
    return out

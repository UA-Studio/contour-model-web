"""지형 메시: 높이장 메시, 길·면 얹기(드레이프), 메시 간소화."""
import math
from collections import defaultdict
import numpy as np
import trimesh
from shapely.geometry import LineString, box
from shapely.ops import unary_union
from .layers import INLAY_KINDS
from .mesh_tools import _mf_from_trimesh, _with_ground_normals
from .terrain import as_polys



def surface_sampler(X, Y, H):
    from scipy.interpolate import RegularGridInterpolator
    f = RegularGridInterpolator((Y[:, 0], X[0]), H, bounds_error=False, fill_value=None)
    return lambda xs, ys: f(np.column_stack([ys, xs]))


def _grid_triangulate(p, cell):
    """면을 cell(m) 격자 칸으로 잘라 칸마다 삼각분할한다. 반환: (꼭짓점 (n,2), 삼각형 (m,3)).

    곡면 위에 얹을 때 칸 크기마다 높이를 다시 읽게 하려는 것. 예전에는 테두리를 잘게 나눈 뒤 긴 삼각형을
    max_edge 까지 쪼갰는데, 가는 삼각형이 끝없이 쪼개져 같은 넓이에 면이 수십 배 생겼다(Rhino 저장이 느려짐).
    칸 경계에 생긴 점은 이웃 칸과 똑같아서 합치면 빈틈 없는 한 장이 된다.
    """
    import shapely
    from mapbox_earcut import triangulate_float64
    x0, y0, x1, y1 = p.bounds
    xs = np.arange(math.floor(x0 / cell) * cell, x1, cell)
    ys = np.arange(math.floor(y0 / cell) * cell, y1, cell)
    X, Y = np.meshgrid(xs, ys)
    cells = shapely.box(X.ravel(), Y.ravel(), X.ravel() + cell, Y.ravel() + cell)
    shapely.prepare(p)
    hit = shapely.intersects(p, cells)
    cells = cells[hit]
    inside = shapely.contains_properly(p, cells)
    pieces = list(cells[inside])
    if (~inside).any():
        for g in shapely.intersection(cells[~inside], p):
            pieces += [q for q in as_polys(g) if q.area > 1e-6]
    V, F, n = [], [], 0
    for q in pieces:
        rings = [np.asarray(q.exterior.coords)[:-1, :2]] + [np.asarray(r.coords)[:-1, :2] for r in q.interiors]
        pts = np.vstack(rings)
        if len(pts) < 3:
            continue
        ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
        t = np.asarray(triangulate_float64(pts, ends), dtype=np.int64).reshape(-1, 3)
        if len(t):
            V.append(pts); F.append(t + n); n += len(pts)
    if not F:
        return np.zeros((0, 2)), np.zeros((0, 3), int)
    return np.vstack(V), np.vstack(F)


def drape_area_meshes(areas, hz, origin, max_edge=3.0):
    """길 면을 경사면 위에 얹은 얇은 메시로. 반환: [(Trimesh, kind)]"""
    from ..paths_trees import PATH_STYLE
    out = []
    for p, L, kind in areas:
        p = p.simplify(0.05)
        if p.is_empty or p.area < 0.5:
            continue
        try:
            v2, f = _grid_triangulate(p, max_edge)
        except Exception:
            continue
        if len(f) == 0 or len(v2) < 3:
            continue
        v = np.column_stack([v2, np.zeros(len(v2))])
        v[:, 2] = hz(v[:, 0], v[:, 1]) + PATH_STYLE[kind][2]
        v[:, 0] -= origin[0]; v[:, 1] -= origin[1]
        out.append((trimesh.Trimesh(v, f, process=False), kind))
    return out


def drape_area_solids(areas, hz, origin, thickness, max_edge=3.0):
    """길 면을 곡면 대지 위에 얹은 두께 있는 닫힌 솔리드로. 반환: [(Trimesh, kind)]

    바닥은 곡면보다 5cm 아래(틈 없이 묻힘), 윗면은 곡면 + thickness (보도·교통섬은 + 연석 높이).
    """
    from ..paths_trees import PATH_STYLE
    out = []
    for p, L, kind in areas:
        p = p.simplify(0.05)
        if p.is_empty or p.area < 0.5:
            continue
        try:
            v2, f = _grid_triangulate(p, max_edge)
        except Exception:
            continue
        if len(f) == 0 or len(v2) < 3:
            continue
        v = np.column_stack([v2, np.zeros(len(v2))])
        # 칸끼리 맞닿은 점을 하나로 합쳐야 옆벽이 정확히 테두리에만 생긴다
        v, inv = np.unique(np.round(v, 6), axis=0, return_inverse=True)
        f = inv.reshape(-1)[f]
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        f = f[np.unique(np.sort(f, axis=1), axis=0, return_index=True)[1]]
        # 윗면이 위를 보게: 삼각분할 전체 방향(넓이 합의 부호)으로 한꺼번에 뒤집는다
        # (한 장씩 판정하면 넓이 0인 가는 삼각형 때문에 방향이 섞인다)
        d = (v[f[:, 1], 0] - v[f[:, 0], 0]) * (v[f[:, 2], 1] - v[f[:, 0], 1]) - \
            (v[f[:, 2], 0] - v[f[:, 0], 0]) * (v[f[:, 1], 1] - v[f[:, 0], 1])
        if d.sum() < 0:
            f = f[:, ::-1]
        g = hz(v[:, 0], v[:, 1])
        curb = PATH_STYLE[kind][2] if kind not in INLAY_KINDS else 0.0
        top, bot = v.copy(), v.copy()
        top[:, 2] = g + thickness + curb
        bot[:, 2] = g - 0.05
        n = len(v)
        # 한 번만 쓰인 모서리 = 테두리 → 옆벽
        e = np.sort(np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
        e_dir = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        border = e_dir[trimesh.grouping.group_rows(e, require_count=1)]
        a, b = border[:, 0], border[:, 1]
        walls = np.vstack([np.column_stack([a, a + n, b]), np.column_stack([b, a + n, b + n])])
        faces = np.vstack([f, f[:, ::-1] + n, walls])
        verts = np.vstack([top, bot])
        verts[:, 0] -= origin[0]; verts[:, 1] -= origin[1]
        m = trimesh.Trimesh(verts, faces, process=False)
        if not (m.is_watertight and m.is_winding_consistent and m.volume > 0):
            m = _drape_solid_tiles(p, hz, origin, g_off=-0.05, t_off=thickness + curb, max_edge=max_edge)
        if m is not None:
            out.append((_with_ground_normals(m, hz, origin), kind))
    return out


def _drape_solid_tiles(p, hz, origin, g_off, t_off, max_edge=3.0, tile=6.0):
    """복잡한 면(겹친 점·가는 틈)은 작은 칸으로 나눠 칸마다 닫힌 솔리드로 만든다."""
    x0, y0, x1, y1 = p.bounds
    parts = []
    for x in np.arange(x0, x1, tile):
        for y in np.arange(y0, y1, tile):
            for q in as_polys(p.intersection(box(x, y, x + tile, y + tile))):
                if q.area < 0.05:
                    continue
                try:
                    m = trimesh.creation.extrude_polygon(q.segmentize(max_edge), 1.0)
                except Exception:
                    continue
                v = m.vertices.copy()
                g = hz(v[:, 0], v[:, 1])
                v[:, 2] = np.where(v[:, 2] > 0.5, g + t_off, g + g_off)
                v[:, 0] -= origin[0]; v[:, 1] -= origin[1]
                parts.append(trimesh.Trimesh(v, m.faces, process=False))
    if not parts:
        return None
    m = trimesh.util.concatenate(parts)
    # 칸끼리 맞닿은 안쪽 벽(같은 자리에 마주 보는 두 면)은 지워 하나의 판으로
    m.merge_vertices()
    key = np.sort(m.faces, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    keep = cnt[inv.reshape(-1)] == 1
    m = trimesh.Trimesh(m.vertices, m.faces[keep], process=False)
    m.remove_unreferenced_vertices()
    return m


def split_inlays(areas):
    """계단판에 끼워 넣을 도로(차도·자전거도로)와 나머지 길로 나눈다."""
    inlay = [(p, L, k) for p, L, k in areas if k in INLAY_KINDS]
    rest = [(p, L, k) for p, L, k in areas if k not in INLAY_KINDS]
    return inlay, rest


def cut_regions(regions, inlay):
    """판마다 그 판 높이에 드러난 도로 자리를 파낸 판 모양. 반환: {L: geometry}"""
    by = defaultdict(list)
    for p, L, _ in inlay:
        by[L].append(p)
    return {L: (g.difference(unary_union(by[L])) if by[L] else g) for L, g in regions.items()}


def drape_line_3d(ls, hz, step=2.0):
    ls = ls.segmentize(step)
    xy = np.asarray(ls.coords)[:, :2]
    return LineString(np.column_stack([xy, hz(xy[:, 0], xy[:, 1]) + 0.03]))


def heightfield_mesh(X, Y, H, bottom, origin):
    """높이 격자를 바닥이 닫힌 솔리드 메시로."""
    ny, nx = H.shape
    idx = np.arange(nx * ny).reshape(ny, nx)
    top = np.column_stack([X.ravel() - origin[0], Y.ravel() - origin[1], H.ravel()])
    bot = top.copy(); bot[:, 2] = np.ravel(bottom) if np.ndim(bottom) else bottom
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = [np.column_stack([a, b, c]), np.column_stack([a, c, d])]
    n = nx * ny
    faces += [np.column_stack([a + n, c + n, b + n]), np.column_stack([a + n, d + n, c + n])]
    ring = np.concatenate([idx[0, :], idx[:, -1][1:], idx[-1, ::-1][1:], idx[::-1, 0][1:]])
    p, q = ring[:-1], ring[1:]
    faces += [np.column_stack([p, p + n, q]), np.column_stack([q, p + n, q + n])]
    m = trimesh.Trimesh(np.vstack([top, bot]), np.vstack(faces), process=True)
    trimesh.repair.fix_normals(m)
    return m


def simplify_solid(m, tol=0.01):
    """닫힌 지형 메시에서 평평하거나 고르게 기운 곳의 잔 삼각형을 합친다 (모양 차이 tol m 이하).

    1m 격자 그대로면 1000m 모델 지형 하나가 꼭짓점 200만 개라 Rhino 저장만 70초, GLB 85MB 가 된다.
    계단판·터처럼 평평한 곳이 많아 꼭짓점이 1/6~1/10 로 준다. 실패하면 원래 메시를 그대로 쓴다.
    """
    if len(m.vertices) < 20000:
        return m
    try:
        import manifold3d as mf
        M = _mf_from_trimesh(m)
        if M.status() != mf.Error.NoError:
            return m
        out = M.simplify(tol).to_mesh()
        v = np.asarray(out.vert_properties, dtype=float)[:, :3]
        s = trimesh.Trimesh(v, np.asarray(out.tri_verts, dtype=np.int64), process=False)
    except Exception:
        return m
    if len(s.faces) == 0 or not s.is_watertight:
        return m
    return s


# ---------------------------------------------------------------- 도로 드레이프
def drape_lines(lines, regions):
    """선형 요소를 각 계단 판 위로 나눠 올린다. 반환: [(LineString, level)]"""
    lv = list(regions)
    merged = unary_union([l for l in lines if not l.is_empty])
    out = []
    for i, L in enumerate(lv):
        band = regions[L]
        if i + 1 < len(lv):
            band = band.difference(regions[lv[i + 1]])
        part = merged.intersection(band)
        for g in getattr(part, "geoms", [part]):
            if isinstance(g, LineString) and g.length > 0.5:
                out.append((g, L))
    return out

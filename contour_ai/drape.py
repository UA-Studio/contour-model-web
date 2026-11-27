"""길·면을 지형 위에 얹기: 길 면 메시·두께 있는 길 솔리드, 계단판에 끼우는 길, 도로 디테일 3D 메시.
"""
import math
from collections import defaultdict

import numpy as np
import trimesh
from shapely.geometry import Point, box

from .geom_util import as_polys
from .mesh_shading import _with_ground_normals
from .terrain import level_at


INLAY_KINDS = ("road", "bike")   # 계단판에 판 두께로 끼워 넣는 길 (차도·자전거도로)
ROAD_THICKNESS = 1.0             # 곡면 대지 위 길 한 층 두께(m). 1:1000에서 1mm 판


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
    from .paths_trees import PATH_STYLE
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
    from .paths_trees import PATH_STYLE
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


def _road_meshes(info, hz, regions, road_thickness):
    """도로 디테일 → [(월드 좌표 메시, 레이어, 색, 이름)]. hz 가 있으면 곡면, 없으면 계단판 높이."""
    from .road_fixtures import FIX_RGB, flat_mesh, placed, pole_parts, signal_parts
    from .road_marks import PAINT
    from .paths_trees import PATH_STYLE
    walk = PATH_STYLE["sidewalk"][2]
    if hz is not None:
        def zr(v):                      # 차도 윗면
            return np.asarray(hz(v[:, 0], v[:, 1]), float) + road_thickness

        def zg(v):                      # 땅(주차장) 윗면: 곡면 덮개가 5cm 떠 있음
            return np.asarray(hz(v[:, 0], v[:, 1]), float) + 0.05

        def zp(x, y):                   # 보도 위
            return float(hz([x], [y])[0]) + road_thickness + walk
    else:
        def zr(v):
            return np.full(len(v), level_at(regions, Point(*v.mean(axis=0))) + 0.03)
        zg = zr

        def zp(x, y):
            return level_at(regions, Point(x, y)) + walk
    out = []
    if info.get("transit"):
        from .transit import meshes as transit_meshes
        out += transit_meshes(info["transit"], zp, lambda x, y: float(zr(np.array([[x, y]]))[0]) - 0.03 * (hz is None))
    if info.get("underground"):
        from .underground import meshes as ug_meshes
        ug = info["underground"]
        out += ug_meshes(ug, lambda x, y: float(zg(np.array([[x, y]], float))[0]), ug.get("road"), ug.get("clip"))
    if info.get("islands") is not None:
        from .transit import island_beds
        out += island_beds(info["islands"], zp)
    groups = defaultdict(list)
    for g, kind, src in info["marks"]:
        groups[(kind, src)].append(g)
    for (kind, src), polys in groups.items():
        m = flat_mesh(polys, zg if kind == "주차구획" else zr, lift=0.03)
        if m is not None:
            out.append((m, f"도로시설::노면표시::{kind}::{src}", PAINT.get(kind, (240, 240, 234)), f"{kind}({src})"))
    sig = defaultdict(list)
    for x, y, ang, kind, src, arm in info["signals"]:
        sig[(kind, src)].append(placed(signal_parts(kind, arm), x, y, zp(x, y), ang))
    for (kind, src), ms in sig.items():
        out.append((trimesh.util.concatenate(ms), f"도로시설::신호등::{kind}::{src}", FIX_RGB["신호등"],
                    f"{kind} 신호등 {len(ms)}개({src})"))
    pol = defaultdict(list)
    base = pole_parts()
    for x, y, src in info["poles"]:
        pol[src].append(placed(base, x, y, zp(x, y), math.atan2(y, x) % math.pi))
    for src, ms in pol.items():
        out.append((trimesh.util.concatenate(ms), f"도로시설::전봇대::{src}", FIX_RGB["전봇대"], f"전봇대 {len(ms)}개({src})"))
    return out

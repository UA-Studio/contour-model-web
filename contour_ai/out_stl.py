"""3D 프린트 STL 저장: 닫힌 솔리드 한 덩어리로 합치기, 대지 경계 홈, 열린 모서리 검사.

이 파일만 고치면 model.stl 만 바뀝니다.
"""
import os
import time
from collections import defaultdict

import numpy as np
import trimesh
from shapely.geometry import LineString
from shapely.ops import unary_union

from .buildings import FLOOR_HEIGHT
from .geom_util import as_polys, cut_regions, _mf_extrude, _mf_from_trimesh, _shift, _shifted
from .mesh_shading import crisp_mesh
from .terrain import heightfield_mesh


STL_SEC_PER_FACE = 0.25e-6
STL_SEC_PER_FACE_JOIN = 1.4e-6


SITE_GROOVE_W = 1.0     # 대지 경계 홈 폭 (모형 mm)
SITE_GROOVE_D = 0.6     # 지형 면에서 파 들어가는 깊이 (모형 mm)


def print_solid(regions, masses, origin, scale=1000.0, field=None, combo=False, tick=None, site=None):
    """3D 프린트용 한 덩어리 솔리드(물샐틈없는 메시, 단위 mm, 모형 크기 그대로, 왼쪽 아래 바닥 = 0,0,0).

    지형(경사면은 높이 격자, 계단판은 판 돌출)과 건물 매스를 불리언 합집합으로 합친다. 나무·가로등·노면표시·
    길 층은 얇거나 떠 있어 프린트에 맞지 않아 넣지 않는다(Rhino·GLB 에는 그대로 있다).
    field: (X, Y, H, B) 경사면 높이 격자(있으면), combo 면 판 높이 B 와 겹친 윗면 = max(H, B).
    """
    import manifold3d as mf
    T = tick or (lambda *a, **k: None)
    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    mm = 1000.0 / float(scale or 1000.0)                 # 실제 m → 모형 mm
    base_z = lv[0] - interval * 2 if lv else 0.0
    parts = []
    if field is not None:
        X, Y, H = field[:3]
        top = np.maximum(H, field[3]) if combo and len(field) > 3 and field[3] is not None else H
        # 프린트 해상도: 격자 한 칸이 모형에서 0.5mm 이상이 되게 솎는다(노즐보다 잘게 해도 안 보인다)
        cell = float(abs(X[0, 1] - X[0, 0])) if X.shape[1] > 1 else 1.0
        k = max(1, int(np.ceil(0.5 / max(cell * mm, 1e-9))))
        Xs, Ys, Hs = X[::k, ::k], Y[::k, ::k], top[::k, ::k]
        base_z = min(base_z, float(np.nanmin(Hs)) - interval)
        T("STL 지형")
        parts.append(_mf_from_trimesh(heightfield_mesh(Xs, Ys, Hs, base_z, origin)))
    else:
        # 계단판도 높이 격자로(격자 한 칸 = 모형 0.4mm): 판 돌출을 합치면 판끼리 점·모서리로 닿는 곳에서 꼬인 모서리가
        # 생기는데, 높이 격자 솔리드는 언제나 물샐틈없다. 0.4mm 계단 윤곽은 노즐 굵기라 출력에서 거의 안 보인다.
        T("STL 계단판")
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        env = regions[lv[0]].envelope.bounds if lv else (0, 0, 1, 1)
        cell = 0.4 / mm
        nx = max(2, int(np.ceil((env[2] - env[0]) / cell)) + 1)
        ny = max(2, int(np.ceil((env[3] - env[1]) / cell)) + 1)
        tf = from_origin(env[0] - cell / 2, env[3] + cell / 2, cell, cell)
        Hs = np.full((ny, nx), lv[0] if lv else 0.0)
        for L in lv[1:]:
            if not regions[L].is_empty:
                msk = rasterize([(regions[L], 1)], out_shape=(ny, nx), transform=tf, all_touched=False, dtype="uint8")
                Hs[msk == 1] = L
        xs = env[0] + np.arange(nx) * cell
        ys = env[3] - np.arange(ny) * cell
        Xs, Ys = np.meshgrid(xs, ys)
        Xs, Ys, Hs = Xs[::-1], Ys[::-1], Hs[::-1]          # 아래(남)부터
        parts.append(_mf_from_trimesh(heightfield_mesh(Xs, Ys, Hs, base_z, origin)))
    model_box = regions[lv[0]].envelope if lv else None
    if site is not None and not site.is_empty:
        # 대지 경계: 경계선을 폭 1mm 띠로 만들어 지형 면을 따라 0.6mm 깊이 홈으로 판다(건물은 뒤에 합치므로 그대로).
        T("STL 대지 경계선")
        try:
            w, d = SITE_GROOVE_W / mm, SITE_GROOVE_D / mm
            rings = [r for g in as_polys(site) for r in [g.exterior, *g.interiors]]
            band = unary_union([LineString(r.coords).buffer(w / 2, join_style=2) for r in rings])
            if model_box is not None:
                band = band.intersection(model_box)
            top_z = float(np.nanmax(Hs)) + interval
            low = _mf_from_trimesh(heightfield_mesh(Xs, Ys, np.maximum(Hs - d, base_z + d), base_z, origin))
            cut = [pr for p in as_polys(band) if (pr := _mf_extrude(p, base_z + d * 0.5, top_z, origin)) is not None]
            if cut:
                cutter = (mf.Manifold.batch_boolean(cut, mf.OpType.Add) if len(cut) > 1 else cut[0]) - low
                parts[0] = parts[0] - cutter
        except Exception as e:      # 홈 실패해도 모델은 그대로
            print("STL 대지 경계선 건너뜀:", e)
    T("STL 건물", 0, len(masses))
    for b in masses:
        for p in as_polys(b["poly"].buffer(0.05, join_style=2).intersection(model_box)):
            m = _mf_extrude(p, min(b["base"], b["top"]) - interval, b["top"], origin)
            if m is not None:
                parts.append(m)
    T("STL 합치기 (구멍·겹침 없는 한 덩어리로)")
    solid = mf.Manifold.batch_boolean(parts, mf.OpType.Add) if len(parts) > 1 else parts[0]
    try:      # 평평한 판 윗면의 잔 삼각형 줄이기 (모양 차이 0.02mm 이내, 물샐틈없는 상태는 그대로)
        solid = solid.simplify(0.02 / mm)
    except Exception:
        pass
    out = solid.to_mesh()
    v = np.asarray(out.vert_properties)[:, :3].astype(float)
    v[:, 2] -= base_z
    v *= mm
    vmin = v[:, :2].min(axis=0)
    v[:, :2] -= vmin
    f = np.asarray(out.tri_verts).astype(np.int64)
    if os.environ.get("CONTOUR_TIMING"):
        print("STL 검사(떼기 전):", stl_check(v, f))
    v = _unpinch(v, f)
    return trimesh.Trimesh(v, f, process=False)


def stl_check(v, f):
    """슬라이서처럼 STL(float32) 좌표가 같은 점을 한 점으로 보고 모서리를 센다 → (열린, 꼬인, 납작한 삼각형)."""
    _, inv = np.unique(np.asarray(v, dtype=np.float32), axis=0, return_inverse=True)
    F = inv.reshape(-1)[np.asarray(f)]
    flat = int(((F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])).sum())
    E = np.sort(np.r_[F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]], axis=1)
    _, c = np.unique(E, axis=0, return_counts=True)
    return int((c == 1).sum()), int((c > 2).sum()), flat


def _unpinch(v, f, eps=0.01):
    """불리언 결과에서 두 덩어리가 모서리·점 하나로만 닿은 곳(예: 꼭짓점끼리 맞닿은 두 건물의 세로 모서리)은 manifold 안에서는
    서로 다른 점이지만 STL 로 쓰면 같은 좌표가 되어 슬라이서가 '꼬인 모서리(non-manifold edge)'로 본다.
    같은 좌표에 겹친 점들을 각자 붙은 면 쪽으로 eps mm 만큼 떼어 놓는다(모양 차이 0.01mm)."""
    v = np.array(v, dtype=float)
    for _ in range(3):
        key = np.asarray(v, dtype=np.float32)
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        inv = inv.reshape(-1)
        dup = np.where(cnt[inv] > 1)[0]
        if not len(dup):
            break
        cen = v[f].mean(axis=1)
        acc = np.zeros_like(v); num = np.zeros(len(v))
        np.add.at(acc, f.ravel(), np.repeat(cen, 3, axis=0)); np.add.at(num, f.ravel(), 1)
        d = acc[dup] / np.maximum(num[dup], 1)[:, None] - v[dup]
        d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
        v[dup] += d * eps
    return v


def write_stl(path, regions, masses, origin, areas=(), trees=(), terrain_mesh=None, area_meshes=(),
              steps_too=False, floor_height=FLOOR_HEIGHT, inlays=(), lights=(), structs=(), tree_style="species",
              roadbits=(), site_mesh=None, tick=None, scale=1000.0, field=None, site_poly=None):
    from .building_detail import building_parts
    from .paths_trees import PATH_STYLE, TREE
    T = tick or (lambda *a, **k: None)
    T("STL 부품 모으기")
    meshes = []
    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    tr = trimesh.transformations.translation_matrix
    with_steps = terrain_mesh is None or steps_too
    cut = cut_regions(regions, inlays)
    for i, L in enumerate(lv if with_steps else []):
        bottom = L - interval if i else L - interval * 2
        for p in as_polys(cut[L]):
            try:
                m = trimesh.creation.extrude_polygon(_shift(p, origin), L - bottom)
            except Exception:
                continue
            m.apply_transform(tr([0, 0, bottom])); meshes.append(m)
    n_step = len(meshes)
    inlay_meshes = defaultdict(list)
    for p, L, kind in (inlays if with_steps else []):
        try:
            m = trimesh.creation.extrude_polygon(_shift(p, origin), interval)
        except Exception:
            continue
        m.apply_transform(tr([0, 0, L - interval])); inlay_meshes[kind].append(m)
    if terrain_mesh is not None:
        meshes.append(terrain_mesh)
    n_terr = len(meshes)
    for b in masses:
        for part in building_parts(b, floor_height):
            try:
                if part[1] == "ext":
                    m = trimesh.creation.extrude_polygon(_shift(part[2], origin), part[4] - part[3])
                    m.apply_transform(tr([0, 0, part[3]]))
                elif part[1] == "mesh":
                    m = part[2].copy(); m.apply_translation([-origin[0], -origin[1], 0])
                else:
                    continue
            except Exception:
                continue
            meshes.append(m)
    meshes += [_shifted(mm, origin) for mm, k, n in structs]
    n_bld = len(meshes)
    path_meshes = defaultdict(list)
    for p, L, kind in areas:
        try:
            m = trimesh.creation.extrude_polygon(_shift(p, origin), PATH_STYLE[kind][2])
        except Exception:
            continue
        m.apply_transform(tr([0, 0, L])); path_meshes[kind].append(m)
    for m, kind in area_meshes:
        path_meshes[kind].append(m)
    tree_meshes = []
    if trees and tree_style != "species":      # 프린트 STL 에는 나무를 넣지 않는다. 단순 나무 미리보기(GLB)용만
        from .paths_trees import tree_dims
        crown0 = trimesh.creation.icosphere(subdivisions=2, radius=1.0)   # 아래에서 (r, r, rz)로 늘여 타원구
        trunk0 = trimesh.creation.cylinder(radius=1.0, height=1.0, sections=8)
        trunk0.apply_translation([0, 0, 0.5])
        for ti, (x, y, z, r, kind) in enumerate(trees):
            if ti % max(1, len(trees) // 20) == 0:
                T("STL 나무", ti, len(trees))
            cz, tr_, rz = tree_dims(r)
            c = crown0.copy(); c.apply_scale([r, r, rz]); c.apply_translation([x - origin[0], y - origin[1], z + cz])
            t = trunk0.copy(); t.apply_scale([tr_, tr_, cz]); t.apply_translation([x - origin[0], y - origin[1], z])
            tree_meshes.append(trimesh.util.concatenate([c, t]))
    # 3D 프린트용 STL: 지형 + 건물을 한 덩어리 솔리드로, 단위 mm·모형 크기 그대로 (v2.0)
    t0 = time.time()
    scene = print_solid(regions, masses, origin, scale, field, steps_too, tick=T, site=site_poly)
    T("STL 저장")
    scene.export(path)
    if os.environ.get("CONTOUR_TIMING"):
        print(f"STL print solid {time.time() - t0:.1f}s faces {len(scene.faces)} watertight {scene.is_watertight}")
    op, nm, fl = stl_check(scene.vertices, scene.faces)
    if op or nm:
        print(f"STL 검사: 열린 모서리 {op}, 꼬인 모서리 {nm}, 납작한 삼각형 {fl}")
    T("미리보기 GLB")
    # 미리보기용 GLB (지형 회색, 건물 흰색)
    terr = trimesh.util.concatenate(meshes[:n_step] if steps_too else meshes[:n_terr])
    terr.visual.face_colors = [178, 178, 172, 255]
    blds = meshes[n_terr:n_bld]
    sc = trimesh.Scene()
    sc.add_geometry(terr, geom_name="terrain_step" if steps_too else "terrain")
    if steps_too and terrain_mesh is not None:
        fill = terrain_mesh.copy(); fill.visual.face_colors = [196, 194, 186, 255]
        sc.add_geometry(fill, geom_name="terrain_slope")
    if blds:
        bm = crisp_mesh(trimesh.util.concatenate(blds)); bm.visual.face_colors = [246, 246, 242, 255]
        sc.add_geometry(bm, geom_name="buildings")
    for kind, ms in inlay_meshes.items():
        pm = trimesh.util.concatenate(ms); pm.visual.face_colors = [*PATH_STYLE[kind][1], 255]
        sc.add_geometry(pm, geom_name=f"inlay_{kind}")
    for kind, ms in path_meshes.items():
        pm = trimesh.util.concatenate(ms)
        if "vertex_normals" not in pm._cache.cache:
            pm = crisp_mesh(pm)
        pm.visual.face_colors = [*PATH_STYLE[kind][1], 255]
        sc.add_geometry(pm, geom_name=f"path_{kind}")
    if trees and tree_style == "species":
        # 미리보기 GLB: 수종 형태별 메시 하나를 여러 자리에 놓는다(인스턴스) — 수만 그루도 가볍다
        from .tree_models import FORMS, WOOD, pick
        made = set()
        for i, (x, y, z, r, kind) in enumerate(trees):
            key, (w, lf), _ = pick(x, y, r, kind)
            gname = "tree_{}_{:g}_{}".format(*key)
            if gname not in made:
                w2, l2 = w.copy(), lf.copy()
                w2.visual.face_colors = [*WOOD, 255]; l2.visual.face_colors = [*FORMS[key[0]][2], 255]
                sc.add_geometry(trimesh.util.concatenate([w2, l2]), geom_name=gname, node_name=f"t{i}",
                                transform=tr([x - origin[0], y - origin[1], z]))
                made.add(gname)
            else:
                sc.graph.update(frame_to=f"t{i}", frame_from=sc.graph.base_frame,
                                matrix=tr([x - origin[0], y - origin[1], z]), geometry=gname)
    elif tree_meshes:
        tm = trimesh.util.concatenate(tree_meshes); tm.visual.face_colors = [222, 214, 190, 255]
        sc.add_geometry(tm, geom_name="trees")
    if lights:   # 가로등은 3D 프린트(STL)에는 너무 가늘어 미리보기(GLB)에만
        from .streetlights import placed_mesh
        lm = trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin) for x, y, z, h, ang, kind, _ in lights])
        lm.visual.face_colors = [96, 96, 100, 255]
        sc.add_geometry(lm, geom_name="streetlights")
    by_rgb = defaultdict(list)       # 도로 디테일(노면표시·신호등·전봇대): 미리보기에만, 색별로 묶어
    for mesh, lay, rgb, name in roadbits:
        by_rgb[tuple(rgb)].append(_shifted(mesh, origin))
    for i, (rgb, ms) in enumerate(by_rgb.items()):
        rm = trimesh.util.concatenate(ms); rm.visual.face_colors = [*rgb, 255]
        sc.add_geometry(rm, geom_name=f"road_detail_{i}")
    if site_mesh is not None:   # 대지 경계 띠: 미리보기(GLB)에만
        sm = site_mesh.copy(); sm.visual.face_colors = [37, 99, 235, 255]
        sc.add_geometry(sm, geom_name="site_boundary")
    sc.export(os.path.splitext(path)[0] + ".glb")
    return scene

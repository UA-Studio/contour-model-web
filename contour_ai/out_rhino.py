"""Rhino 3dm 저장: 레이어 나누기, 지형·건물·길·나무·대지 경계를 Rhino 물체로.

이 파일만 고치면 model.3dm 만 바뀝니다.
"""
import math
import os
import time

import numpy as np
import rhino3dm as r3
from shapely.geometry import LineString, Point, Polygon

from .buildings import FLOOR_HEIGHT, ROLE_KO, SOURCE_KO, USE_COLORS
from .geom_util import as_polys, cut_regions, _shifted
from .mesh_shading import crisp_mesh
from .terrain import drape_lines


# ---------------------------------------------------------------- 출력: Rhino
def _rh_curve(ring, z, origin):
    if ring.has_z:
        pl = r3.Polyline([r3.Point3d(x - origin[0], y - origin[1], zz) for x, y, zz in ring.coords])
        return r3.PolylineCurve(pl)
    pl = r3.Polyline([r3.Point3d(x - origin[0], y - origin[1], z) for x, y in ring.coords])
    return r3.PolylineCurve(pl)


def _rh_extrusion(poly, z0, z1, origin):
    """평면 다각형(구멍 포함) → 수직 Extrusion.
    Extrusion 은 안쪽 구멍 윤곽을 프로필 평면 좌표로 읽는데, 그냥 Create 하면 평면이 바깥 윤곽의 첫 점·첫 변
    방향으로 잡혀 구멍이 엉뚱한 곳에 가거나 빠진다. 그래서 모든 윤곽을 원점 근처 z=0 에 그리고
    프로필 평면을 월드 XY 로 못박은 뒤(CreateWithPlane) 마지막에 제자리로 옮긴다."""
    poly = poly.simplify(0.01)
    outer = Polygon(poly.exterior.coords)
    if not outer.exterior.is_ccw:
        outer = Polygon(list(outer.exterior.coords)[::-1])
    px, py = outer.exterior.coords[0][:2]
    ext = r3.Extrusion.CreateWithPlane(_rh_poly_xy([(x - px, y - py) for x, y, *_ in outer.exterior.coords[:-1]]),
                                       r3.Plane.WorldXY(), z1 - z0, True)
    if ext is None:
        return None
    for hole in poly.interiors:
        ext.AddInnerProfile(_rh_poly_xy([(x - px, y - py) for x, y, *_ in hole.coords[:-1]]))
    ext.Translate(r3.Vector3d(float(px - origin[0]), float(py - origin[1]), float(z0)))
    return ext


def _rh_poly_xy(pts):
    pl = r3.Polyline(len(pts) + 1)
    for x, y in list(pts) + [pts[0]]:
        pl.Add(float(x), float(y), 0.0)
    return r3.PolylineCurve(pl)


def _rh_wall(A, t, nrm, L, ops, z0, z1, depth, origin):
    """벽 한 면 = 창 구멍이 뚫린 한 장의 판(Extrusion). 바깥 면이 벽 선 위, 두께는 안쪽으로 depth.
    프로필은 평면 (s, z) 에 그리고 (s→벽 방향, z→위, 두께→바깥 법선) 으로 옮긴다."""
    # 프로필은 벽 아래 끝(z0)을 0 으로, 평면은 월드 XY 로 못박아 그린다: Extrusion 은 창 구멍을 프로필 평면
    # 좌표로 읽기 때문에 절대 높이로 넣으면 창이 땅 높이(z0)만큼 위로 떠 버린다 (v2.3.1 버그).
    ext = r3.Extrusion.CreateWithPlane(_rh_poly_xy([(0, 0), (L, 0), (L, z1 - z0), (0, z1 - z0)]),
                                       r3.Plane.WorldXY(), depth, True)
    if ext is None:
        return None
    for o in ops:
        s0, s1, a, b = o[:4]
        ext.AddInnerProfile(_rh_poly_xy([(s0, a - z0), (s0, b - z0), (s1, b - z0), (s1, a - z0)]))
    T = r3.Transform(1.0)
    T.M00, T.M01, T.M02 = float(t[0]), 0.0, float(nrm[0])
    T.M10, T.M11, T.M12 = float(t[1]), 0.0, float(nrm[1])
    T.M20, T.M21, T.M22 = 0.0, 1.0, 0.0
    T.M03 = float(A[0] - origin[0] - nrm[0] * depth)
    T.M13 = float(A[1] - origin[1] - nrm[1] * depth)
    T.M23 = float(z0)
    ext.Transform(T)
    return ext


def _rh_quads(quads, origin):
    """사각형들(각 4점) → 사각 면 메시(삼각형으로 나누지 않음)."""
    from .facade_windows import quad_arrays
    rm = r3.Mesh()
    add, addf = rm.Vertices.Add, rm.Faces.AddFace
    V, F = quad_arrays(quads)          # 같은 평면에서 맞닿는 꼭짓점은 하나로 (창틀 테두리 등)
    V = V - np.array([float(origin[0]), float(origin[1]), 0.0])
    for x, y, z in V.tolist():
        add(x, y, z)
    for a, b, c, d in F.tolist():
        addf(a, b, c, d)
    rm.Normals.ComputeNormals()
    rm.Compact()
    return rm


_RH_COUNT = [0]      # 이번 3dm 에 넣은 메시 꼭짓점 수(저장 시간 어림용)


def _rh_mesh(m, crisp=True):
    _RH_COUNT[0] += len(m.vertices)
    fixed = m.metadata.get("fixed_normals") and "vertex_normals" in m._cache.cache
    if crisp and not fixed:
        m = crisp_mesh(m)
    rm = r3.Mesh()
    # numpy 한 줄씩 꺼내 float()/int() 로 바꾸면 느리다(꼭짓점 100만 개에 10초 넘게) → 파이썬 목록으로 한 번에 바꿔 넣는다
    add = rm.Vertices.Add
    for x, y, z in np.asarray(m.vertices, dtype=float).tolist():
        add(x, y, z)
    addf = rm.Faces.AddFace
    for a, b, c in np.asarray(m.faces, dtype=np.int64).tolist():
        addf(a, b, c)
    if fixed:
        addn = rm.Normals.Add
        for x, y, z in np.asarray(m.vertex_normals, dtype=float).tolist():
            addn(x, y, z)
    else:
        rm.Normals.ComputeNormals()
    rm.Compact()
    return rm


def _add_site(f, site, interp, origin, log, step=None):
    """model.3dm 에 검토 대지 경계와 정북일조 사선(참고)을 더한다. site = {poly(모델 좌표), review}"""
    from .landlaw import envelope_mesh
    poly, rv = site["poly"], site.get("review") or {}
    xs = np.linspace(poly.bounds[0], poly.bounds[2], 12)
    ys = np.linspace(poly.bounds[1], poly.bounds[3], 12)
    pts = [(x, y) for x in xs for y in ys if poly.contains(Point(x, y))] or [tuple(poly.representative_point().coords[0])]
    ground = float(np.nanmean(interp(np.array(pts))))

    def layer(name, rgb, parent=None):
        L = r3.Layer(); L.Name = name; L.Color = (*rgb, 255)
        if parent is not None:
            L.ParentLayerId = f.Layers[parent].Id
        return f.Layers.Add(L)
    top = layer("대지", (220, 60, 40))
    lb = layer("경계", (220, 60, 40), top)
    for g in as_polys(poly):
        a = r3.ObjectAttributes(); a.LayerIndex = lb; a.Name = f"대지 경계 {rv.get('address', '')}".strip()
        a.SetUserString("면적(㎡)", f"{poly.area:.1f}")
        a.SetUserString("PNU", str(rv.get("pnu") or ""))
        for k in ("zone", "bcr", "far", "area"):
            if rv.get(k) is not None:
                a.SetUserString({"zone": "용도지역", "bcr": "건폐율(%)", "far": "용적률(%)", "area": "대지면적(㎡)"}[k],
                                str(round(rv[k], 1) if isinstance(rv[k], float) else rv[k]))
        f.Objects.AddCurve(_rh_curve(g.exterior, ground + 0.05, origin), a)
    # 경계선을 지형에 눌러 붙인 선: 계단판마다 그 판 윗면 높이에(레이저 SITE 각인과 같은 선), 그리고 매끈한 지형 위에
    regions = site.get("regions")
    n_dr = 0
    if regions:
        ld = layer("경계(계단판 위)", (220, 60, 40), top)
        rings = [r for g in as_polys(poly) for r in [g.exterior, *g.interiors]]
        for ls, L in drape_lines([LineString(r.coords) for r in rings], regions):
            a = r3.ObjectAttributes(); a.LayerIndex = ld; a.Name = f"대지 경계 · 판 EL {L:g}"
            f.Objects.AddCurve(_rh_curve(ls, L + 0.02, origin), a); n_dr += 1
    lt = layer("경계(지형 위)", (220, 60, 40), top)
    for g in as_polys(poly):
        for r in [g.exterior, *g.interiors]:
            seg = LineString(r.coords).segmentize(1.0)
            zz = interp(np.array(seg.coords)[:, :2])
            a = r3.ObjectAttributes(); a.LayerIndex = lt; a.Name = "대지 경계 · 지형 위"
            f.Objects.AddCurve(_rh_curve(LineString([(x, y, z + 0.02) for (x, y, *_), z in zip(seg.coords, zz)]), 0, origin), a)
    log(f"   대지 경계선을 지형에 눌러 붙였습니다 (Rhino 대지::경계(계단판 위) {n_dr}조각 · 대지::경계(지형 위))")
    if step:
        # 대지 안을 사업지 레벨로 다시 그린 등고선 (step m 간격, 각 선은 제 높이에)
        import contourpy
        b = poly.bounds
        xs = np.arange(b[0] - 1, b[2] + 1.5, 0.5); ys = np.arange(b[1] - 1, b[3] + 1.5, 0.5)
        GX, GY = np.meshgrid(xs, ys)
        GZ = interp(np.column_stack([GX.ravel(), GY.ravel()])).reshape(GX.shape)
        gen = contourpy.contour_generator(GX, GY, GZ)
        lc = layer("레벨 등고선", (40, 90, 220), top)
        n = 0
        for z in np.arange(np.ceil(np.nanmin(GZ) / step) * step, np.nanmax(GZ) + 1e-6, step):
            for c in gen.lines(z - 0.01):     # 평평한 단(레벨 = z)에서 선이 부서지지 않게 살짝 아래를 딴다
                if len(c) < 2:
                    continue
                g2 = LineString(c).intersection(poly)
                for part in getattr(g2, "geoms", [g2]):
                    if isinstance(part, LineString) and part.length > 0.5:
                        a = r3.ObjectAttributes(); a.LayerIndex = lc; a.Name = f"EL {z:g}"
                        f.Objects.AddCurve(_rh_curve(part, z, origin), a); n += 1
        log(f"   대지 안 레벨 등고선 {n}줄을 넣었습니다 ({step:g}m 간격, Rhino 대지::레벨 등고선)")
    # 표준 대지 파일의 레벨판·원지형 유지·계획 건물·도로 (선으로, 그 자리 계획 높이에)
    plates, keep, blds, roads = (site.get(k) or [] for k in ("plates", "keep", "buildings", "roads"))
    if site.get("keep_terrain"):
        plates, keep = [], []      # 원래 지형 유지: 계획 레벨판은 넣지 않는다(건물·도로 선은 원래 지형 위에)
    if plates:
        lp = layer("레벨판", (40, 90, 220), top)
        for p, z in plates:
            for g in as_polys(p):
                a = r3.ObjectAttributes(); a.LayerIndex = lp; a.Name = f"레벨판 EL {z:g}"
                a.SetUserString("계획 레벨", f"{z:g}"); a.SetUserString("면적(㎡)", f"{g.area:.1f}")
                f.Objects.AddCurve(_rh_curve(g.exterior, z + 0.05, origin), a)
    if keep:
        lk = layer("원지형 유지", (90, 90, 90), top)
        for p in keep:
            for g in as_polys(p):
                zz = interp(np.array(g.exterior.coords)[:, :2])
                a = r3.ObjectAttributes(); a.LayerIndex = lk; a.Name = "원지형 유지"
                f.Objects.AddCurve(_rh_curve(LineString([(x, y, z + 0.05) for (x, y), z in zip(g.exterior.coords, zz)]), 0, origin), a)
    if blds:
        lbd = layer("계획 건물", (30, 30, 30), top)
        for i, p in enumerate(blds):
            for g in as_polys(p):
                c = g.representative_point()
                z = float(interp(np.array([[c.x, c.y]]))[0])
                a = r3.ObjectAttributes(); a.LayerIndex = lbd; a.Name = f"계획 건물 {i + 1} (EL {z:.1f})"
                f.Objects.AddCurve(_rh_curve(g.exterior, z + 0.1, origin), a)
    if roads:
        lr = layer("계획 도로", (120, 120, 120), top)
        for ls in roads:
            seg = ls.segmentize(2.0)
            zz = interp(np.array(seg.coords)[:, :2])
            a = r3.ObjectAttributes(); a.LayerIndex = lr; a.Name = "계획 도로·램프"
            f.Objects.AddCurve(_rh_curve(LineString([(x, y, z + 0.1) for (x, y, *_), z in zip(seg.coords, zz)]), 0, origin), a)
    if plates or keep or blds or roads:
        log(f"   표준 대지 파일 도형을 넣었습니다 (Rhino 대지:: 레벨판 {len(plates)} · 원지형 유지 {len(keep)} · 계획 건물 {len(blds)} · 계획 도로 {len(roads)})")
    env = None
    if rv.get("sunlight"):
        env = envelope_mesh(poly, ground)
        if env is not None:
            env.apply_translation([-origin[0], -origin[1], 0])
            le = layer("정북일조 사선(참고)", (240, 150, 60), top)
            a = r3.ObjectAttributes(); a.LayerIndex = le; a.Name = "정북일조 사선: 높이 10m 이하 1.5m, 초과 부분 높이의 1/2"
            f.Objects.AddMesh(_rh_mesh(env), a)
    log(f"   대지 경계" + (" + 정북일조 사선을" if env is not None else "를") + f" 넣었습니다 (기준 지표면 EL {ground:.1f})")
    return dict(address=rv.get("address"), pnu=rv.get("pnu"), ground=round(ground, 2), envelope=env is not None)


def _write_estimate(verts, objs):
    """Rhino 저장(f.Write) 어림 초. 실측(2026-11-17, 리눅스): 꼭짓점·개체 수에 거의 비례."""
    return round(verts * WRITE_SEC_PER_VERT + objs * WRITE_SEC_PER_OBJ, 1)


WRITE_SEC_PER_VERT = 30e-6
WRITE_SEC_PER_OBJ = 2e-4


def write_3dm(path, regions, masses, draped, origin, areas=(), trees=(), edges=(), terrain_mesh=None,
              area_meshes=(), steps_too=False, floor_height=FLOOR_HEIGHT, inlays=(), lights=(), structs=(),
              tree_style="species", roadbits=(), before_write=None, tick=None, zones=None):
    """Rhino 모델. 레이어는 지형 / 길 / 건물(용도별 › 부위별) / 나무 로 나뉜다.

    inlays: 계단판에 판 두께로 끼워 넣는 도로 [(Polygon, level, kind)] — 계단판은 그 자리를 파낸다.
    area_meshes: 곡면 대지 위 길 솔리드 [(Trimesh, kind)].
    """
    from .building_detail import building_parts
    from .paths_trees import PATH_STYLE
    f = r3.File3dm()
    f.Settings.ModelUnitSystem = r3.UnitSystem.Meters
    layers = {}

    def layer(path, rgb=(200, 200, 200)):
        """'건물::주택::지붕' 같은 경로로 중첩 레이어를 만들고 번호를 돌려준다."""
        if path in layers:
            return layers[path]
        parent, _, name = path.rpartition("::")
        L = r3.Layer(); L.Name = name; L.Color = (*rgb, 255)
        if parent:
            L.ParentLayerId = f.Layers.FindIndex(layer(parent, rgb)).Id
        layers[path] = f.Layers.Add(L)
        return layers[path]

    def attrs(li, name=""):
        a = r3.ObjectAttributes(); a.LayerIndex = li; a.Name = name
        return a

    groups = {}

    def group(name):
        if name not in groups:
            g = r3.Group(); g.Name = name; f.Groups.Add(g)
            groups[name] = len(groups)
        return groups[name]

    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    T = tick or (lambda *a, **k: None)     # 화면 진행 표시: 저장 중에도 어디까지 썼는지 알린다
    _RH_COUNT[0] = 0
    layer("지형", (205, 205, 200)); layer("길", (130, 130, 126)); layer("건물", (245, 245, 245))
    if terrain_mesh is not None:
        T("지형 메시")
        li = layer("지형::경사매스" if steps_too else "지형::곡면대지", (214, 212, 205))
        f.Objects.AddMesh(_rh_mesh(terrain_mesh, crisp=False), attrs(li, "경사 매스(계단판 위)" if steps_too else "곡면 대지"))
        if zones:
            # 영역별 지형 (v2.2): 같은 지형을 도로·보도·녹지·물·대지·그 밖 조각으로 나눠 재료를 따로 입히거나 골라 쓰게.
            # 전체 지형과 겹치므로 처음엔 꺼 둔다 (레이어 '지형::영역별' 을 켜고 전체 지형을 끄면 됨)
            from .terrain_zones import ZONE_RGB
            T("영역별 지형")
            parent = layer("지형::영역별", (200, 200, 196))
            f.Layers[parent].Visible = False
            for name, part in zones:
                lz = layer(f"지형::영역별::{name}", ZONE_RGB.get(name, (200, 200, 196)))
                f.Layers[lz].Visible = False
                f.Objects.AddMesh(_rh_mesh(part, crisp=False), attrs(lz, f"지형 · {name}"))
        li_ct = layer("지형::등고선", (140, 140, 136))
        for L in lv[1:]:
            for p in as_polys(regions[L]):
                for ring in [p.exterior, *p.interiors]:
                    f.Objects.AddCurve(_rh_curve(ring, L, origin), attrs(li_ct, f"EL {L:g}"))
    cut = cut_regions(regions, inlays)
    li_terr = layer("지형::계단판", (205, 205, 200))
    for i, L in enumerate(lv if (terrain_mesh is None or steps_too) else []):
        T("계단판", i, len(lv))
        bottom = L - interval if i else L - interval * 2
        for p in as_polys(cut[L]):
            ext = _rh_extrusion(p, bottom, L, origin)
            if ext:
                f.Objects.AddExtrusion(ext, attrs(li_terr, f"계단판 EL {L:g}"))
    T("길")
    for p, L, kind in (inlays if (terrain_mesh is None or steps_too) else []):
        name, rgb = PATH_STYLE[kind][:2]
        ext = _rh_extrusion(p, L - interval, L, origin)
        if ext:
            f.Objects.AddExtrusion(ext, attrs(layer(f"길::계단판::{name}", rgb), f"{name} EL {L:g}"))
    for p, L, kind in areas:
        name, rgb, lift, _ = PATH_STYLE[kind]
        ext = _rh_extrusion(p, L, L + lift, origin)
        if ext:
            f.Objects.AddExtrusion(ext, attrs(layer(f"길::계단판::{name}", rgb), name))
    for m, kind in area_meshes:
        name, rgb = PATH_STYLE[kind][:2]
        f.Objects.AddMesh(_rh_mesh(m), attrs(layer(f"길::곡면대지::{name}", rgb), name))
    for ls, L in draped:
        f.Objects.AddCurve(_rh_curve(ls, L + 0.02, origin), attrs(layer("길::도로선", (90, 140, 200))))
    for ls, L in edges:
        f.Objects.AddCurve(_rh_curve(ls, L + 0.02, origin), attrs(layer("길::도로경계선", (120, 120, 116))))

    # 건물: 층수별 레이어(건물::05층::외벽·유리·지붕…, v2.2). 재료(붉은 벽돌·유리 …)는 개체마다 렌더 재료로 붙이고,
    # 수치지도·대장 정보는 개체 속성(UserText)으로, 같은 건물 부위·용도·종류·층수대는 그룹(SelGroup)으로 묶는다.
    # (예전엔 재료별 레이어였는데, 높이별로 골라 숨기기 쉽게 층수별 레이어로 바꿈. 재료로 고르려면 SelMaterialName·그룹 '용도:')
    from .facade import MATERIALS, facade_meshes, material_of
    mats = {}

    def mat_index(name):
        if name not in mats:
            rgb, tr_ = MATERIALS.get(name, ((230, 230, 228), 0.0))
            mt = r3.Material(); mt.Name = name; mt.DiffuseColor = (*rgb, 255); mt.Transparency = tr_
            mats[name] = (f.Materials.Add(mt), rgb)
        return mats[name]

    def floor_layer(n, part):
        g = max(150, 235 - 4 * n)          # 높을수록 조금 진한 회색: 레이어 목록에서도 높이가 보이게
        layer(f"건물::{n:02d}층", (g, g, g - 2))
        return layer(f"건물::{n:02d}층::{part}", (g, g, g - 2))

    def with_mat(a, name):
        idx, rgb = mat_index(name)
        a.MaterialSource = r3.ObjectMaterialSource.MaterialFromObject; a.MaterialIndex = idx
        a.ColorSource = r3.ObjectColorSource.ColorFromObject; a.ObjectColor = (*rgb, 255)
        return a
    for bi, m in enumerate(masses):
        if bi % max(1, len(masses) // 25) == 0:
            T("건물·창문", bi, len(masses))
        at = m.get("attrs", {})
        use = m.get("use", "용도미상")
        fc = m.get("facade") or {}
        title = at.get("명칭") or at.get("주기") or m["layer"] or "건물"
        label = f"{title} {m['floors']}F"
        height = round(m["top"] - m["base"], 2)
        band = ("1층" if m["floors"] <= 1 else "2-4층" if m["floors"] <= 4 else
                "5-9층" if m["floors"] <= 9 else "10-19층" if m["floors"] <= 19 else "20층 이상")
        gids = [group(f"건물 {bi + 1:04d} {label}"), group(f"용도: {use}"), group(f"종류: {m['layer'] or '미상'}"),
                group(f"층수: {band}")] + ([group(f"준공: {m['year'] // 10 * 10}년대")] if m.get("year") else [])

        def a_for(li, part_name):
            a = attrs(li, f"{label} {part_name}")
            for k, v in at.items():
                a.SetUserString(k, str(v))
            a.SetUserString("용도", use)
            a.SetUserString("층수(모델)", str(m["floors"]))
            a.SetUserString("높이(m)", str(height))
            a.SetUserString("층수출처", SOURCE_KO.get(m["source"], "추정"))
            a.SetUserString("층고(m)", f"{m['floor_h']:.2f} (대장 높이÷층수)" if m.get("floor_h") else f"{floor_height:.2f} (기본값)")
            a.SetUserString("부위", part_name)
            if fc:
                a.SetUserString("외벽재료", fc["wall"]); a.SetUserString("지붕재료", fc["roof"])
                a.SetUserString("창형식", fc["window"]); a.SetUserString("재료근거", fc["basis"])
                a.SetUserString("지붕근거", fc["roof_basis"])
            for g in gids:
                a.AddToGroup(g)
            return a
        fm = facade_meshes(m, floor_height, origin) if fc.get("windows") else None
        nf = int(m["floors"])
        walls_done = False
        for part in building_parts(m, floor_height):
            role, typ = part[0], part[1]
            if role == "body" and fm and walls_done:
                continue   # 셋백 건물은 본체가 단마다 있지만 layout 이 모든 단의 벽을 한 번에 만든다 (겹쳐 두 벌 생기지 않게)
            if role == "body" and fm:
                walls_done = True
                # 벽은 면마다 창 구멍 뚫린 한 장의 판, 유리는 사각 면, 지붕은 판 (삼각형으로 쪼개지 않음, v2.3)
                from .facade import DEPTH, layout
                from .facade_windows import part_of, trim
                for lay in layout(m, floor_height):
                    aw = with_mat(a_for(floor_layer(nf, "외벽"), "외벽"), lay["wall"])
                    for A, t_, nrm, L, ops in lay["edges"]:
                        ext = _rh_wall(A, t_, nrm, L, ops, lay["zwall"], lay["top"], DEPTH, origin)
                        if ext is not None:
                            f.Objects.AddExtrusion(ext, aw)
                    # 유리(틴트·불투명)·창틀·창턱·셔터·간판: 재료마다 사각 면 메시 하나 (v2.4)
                    for mat_, quads in trim(lay, lay.get("trim"), DEPTH, fine=bool(fc.get("fine", True))).items():
                        pn_ = part_of(mat_)
                        f.Objects.AddMesh(_rh_quads(quads, origin), with_mat(a_for(floor_layer(nf, pn_), pn_), mat_))
                    ext = _rh_extrusion(lay["poly"], lay["top"] - 0.02, lay["top"], origin)
                    if ext is not None:
                        f.Objects.AddExtrusion(ext, with_mat(a_for(floor_layer(nf, "지붕면"), "지붕면"), lay["roof"]))
                    ext = _rh_extrusion(lay["poly"], lay["zwall"], lay["zwall"] + 0.02, origin)
                    if ext is not None:
                        f.Objects.AddExtrusion(ext, aw)
                    for q in lay["cols"]:
                        from shapely.geometry import box as _box
                        ext = _rh_extrusion(_box(q[0] - 0.225, q[1] - 0.225, q[0] + 0.225, q[1] + 0.225),
                                            lay["base"], lay["zwall"], origin)
                        if ext is not None:
                            f.Objects.AddExtrusion(ext, aw)
                continue
            if role == "floorline":
                if fm:
                    continue   # 창이 층을 보여 주므로 층선은 생략
                a = a_for(floor_layer(nf, "층선"), "층선")
            else:
                pn = ROLE_KO.get(role, role)
                mat = material_of(m, role) if fc else None
                a = a_for(floor_layer(nf, pn), pn)
                if mat:
                    with_mat(a, mat)
                else:             # 재료 정보가 없으면 용도 색을 개체 색으로
                    a.ColorSource = r3.ObjectColorSource.ColorFromObject
                    a.ObjectColor = (*USE_COLORS.get(use, (232, 232, 230)), 255)
            if typ == "ext":
                ext = _rh_extrusion(part[2], part[3], part[4], origin)
                if ext:
                    f.Objects.AddExtrusion(ext, a)
            elif typ == "mesh":
                mm = part[2].copy(); mm.apply_translation([-origin[0], -origin[1], 0])
                f.Objects.AddMesh(_rh_mesh(mm), a)
            else:
                f.Objects.AddCurve(_rh_curve(part[2], 0, origin), a)
    if structs:
        from .structures import STRUCT_RGB
        for mesh, kind, name in structs:
            f.Objects.AddMesh(_rh_mesh(_shifted(mesh, origin)), attrs(layer(f"구조물::{kind}", STRUCT_RGB.get(kind, (176, 174, 168))), name))
    T("도로 디테일")
    for mesh, lay, rgb, name in roadbits:
        f.Objects.AddMesh(_rh_mesh(_shifted(mesh, origin), crisp=False), attrs(layer(lay, rgb), name))
    if trees:
        # 나무는 블록(수관 반지름 0.1m 단위)으로: 숲이 넓어도 파일이 가볍고, 블록 하나를 고치면 같은 크기 나무가 다 바뀐다
        from .paths_trees import TREE_KIND, tree_dims
        li_trunk = layer("나무::줄기", (150, 130, 110))
        li_crown = layer("나무::수관", (214, 205, 180))
        defs = {}

        def tree_def(r):
            if r not in defs:
                cz, tr_, rz = tree_dims(r)
                crown = r3.Sphere(r3.Point3d(0, 0, 0), 1.0).ToNurbsSurface()
                t = r3.Transform(1.0)
                t.M00, t.M11, t.M22, t.M23 = r, r, rz, cz
                crown.Transform(t)
                trunk = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), tr_), cz).ToBrep(True, True)
                idx = f.InstanceDefinitions.Add(f"나무 r{r:.1f}m", f"수관 반지름 {r:.1f}m", "", "", r3.Point3d(0, 0, 0),
                                                (crown, trunk), (attrs(li_crown, "수관"), attrs(li_trunk, "줄기")))
                defs[r] = f.InstanceDefinitions[idx].Id
            return defs[r]
        from .tree_models import FORMS, WOOD, pick
        sdefs = {}

        def species_def(key, parts):
            if key not in sdefs:
                form, rq, var = key
                fname, _, rgb = FORMS[form]
                w, lf = parts
                idx = f.InstanceDefinitions.Add(f"나무 {fname} r{rq:g}m #{var + 1}", f"{fname}, 수관 반지름 {rq:g}m",
                                                "", "", r3.Point3d(0, 0, 0), (_rh_mesh(w), _rh_mesh(lf)),
                                                (attrs(layer("나무::줄기", WOOD), "줄기"),
                                                 attrs(layer(f"나무::수관::{fname}", rgb), "수관")))
                sdefs[key] = f.InstanceDefinitions[idx].Id
            return sdefs[key]
        for ti, (x, y, z, r, kind) in enumerate(trees):
            if ti % max(1, len(trees) // 25) == 0:
                T("나무", ti, len(trees))
            xf = r3.Transform.Translation(x - origin[0], y - origin[1], z)
            if tree_style == "species":
                key, parts, sp = pick(x, y, r, kind)
                a_ = attrs(layer(f"나무::{TREE_KIND[kind]}", (214, 205, 180)), sp or TREE_KIND[kind])
                if sp:
                    a_.SetUserString("수종", sp)
                a_.SetUserString("형태", FORMS[key[0]][0])
                f.Objects.AddInstanceObject(r3.InstanceReference(species_def(key, parts), xf), a_)
                continue
            rq = round(max(r, 0.5) * 10) / 10
            f.Objects.AddInstanceObject(r3.InstanceReference(tree_def(rq), xf),
                                        attrs(layer(f"나무::{TREE_KIND[kind]}", (214, 205, 180)), TREE_KIND[kind]))
    if lights:
        T("가로등")
        from .streetlights import light_parts
        li_pole = layer("가로등::기둥", (90, 90, 92))
        li_lamp = layer("가로등::등기구", (250, 230, 160))
        ldefs = {}
        for x, y, z, h, ang, kind, src in lights:
            key = (kind, round(h, 1))
            if key not in ldefs:
                geo, at = [], []
                for m, part in light_parts(h, kind):
                    geo.append(_rh_mesh(m)); at.append(attrs(li_lamp if part == "등기구" else li_pole, part))
                idx = f.InstanceDefinitions.Add(f"{kind} {h:g}m", f"{kind} 높이 {h:g}m", "", "",
                                                r3.Point3d(0, 0, 0), tuple(geo), tuple(at))
                ldefs[key] = f.InstanceDefinitions[idx].Id
            t = r3.Transform(1.0)
            c, sn = math.cos(ang), math.sin(ang)
            t.M00, t.M01, t.M10, t.M11 = c, -sn, sn, c
            t.M03, t.M13, t.M23 = x - origin[0], y - origin[1], z
            grp = "추정" if src == "추정" else "자료"
            a_ = attrs(layer(f"가로등::{grp}", (240, 200, 90)), f"{kind} ({src})")
            a_.SetUserString("출처", src); a_.SetUserString("높이(m)", f"{h:g}")
            f.Objects.AddInstanceObject(r3.InstanceReference(ldefs[key], t), a_)
    if before_write is not None:
        T("대지 경계")
        before_write(f)      # 대지 경계 등: 다 쓴 파일을 다시 읽고 쓰지 않도록 저장 전에 더한다
    nobj = len(f.Objects)
    T("디스크에 저장", est=_write_estimate(_RH_COUNT[0], nobj))
    if tick:
        # rhino3dm 저장은 끝날 때까지 파이썬 전체를 붙잡아 화면 물음에도 답하지 못한다. 화면이 '저장 중 · 약 N초'를
        # 먼저 받아 가서 스스로 세도록 잠깐 기다린다.
        time.sleep(1.5)
    t0 = time.time()
    f.Write(path, 7)
    if os.environ.get("CONTOUR_TIMING"):
        print(f"3dm Write {time.time() - t0:.1f}s verts {_RH_COUNT[0]} objs {nobj}")

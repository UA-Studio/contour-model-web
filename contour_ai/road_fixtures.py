"""길가 시설: 신호등·전봇대 위치 추정과 3D 부품(기둥·팔·등), 평면 칠 메시.

이 파일만 고치면 신호등·전봇대만 바뀝니다.
"""
import math

import numpy as np

from .road_sources import _dedupe


FIX_RGB = {"신호등": (70, 72, 76), "전봇대": (172, 170, 162)}


# ---------------------------------------------------------------- 신호등·전봇대
def estimate_signals(crossings, approaches, curb, buildings=None, road=None):
    """반환 [(x, y, 팔 방향, 종류, "추정", 팔 길이)]"""
    from shapely import contains_xy, prepare
    road_in = None
    if road is not None and not road.is_empty:
        road_in = road.buffer(-0.1)
        prepare(road_in)

    def ok(px, py):                          # 차도 안이나 건물 안에는 세우지 않는다
        return not ((road_in is not None and contains_xy(road_in, px, py))
                    or (blk is not None and contains_xy(blk, px, py)))
    blk = None
    if buildings is not None and not buildings.is_empty:
        blk = buildings.buffer(0.3)
        prepare(blk)
    out = []
    for cr in crossings:                   # 횡단보도 양 끝 보도·교통섬 위
        x, y, nrm, span = cr[:4]
        for sd in (1, -1):
            if len(cr) > 4:                 # 끝점을 알면 그 바로 바깥
                e = cr[4] if sd == -1 else cr[5]
                px, py = e[0] + nrm[0] * sd * 0.6, e[1] + nrm[1] * sd * 0.6
            else:
                d = curb.hit((x, y), (nrm[0] * sd, nrm[1] * sd), span / 2 + 6) or span / 2
                px, py = x + nrm[0] * sd * (d + 0.6), y + nrm[1] * sd * (d + 0.6)
            if ok(px, py):
                out.append((px, py, math.atan2(-nrm[1] * sd, -nrm[0] * sd), "보행자", "추정", 0.0))
    for p0, u, w, R in approaches:          # 차량 신호: 진입로 건너편 오른쪽 모서리, 팔은 차로 위로
        t = -u                               # 진입하는 차의 진행 방향
        right = np.array([t[1], -t[0]])
        c = p0 + t * (R + 1.0)
        d = curb.hit(tuple(c), tuple(right), w / 2 + 8) or w / 2
        px, py = c + right * (d + 0.8)
        if not ok(px, py):
            continue
        out.append((float(px), float(py), math.atan2(-right[1], -right[0]), "차량", "추정", min(max(w / 2 - 1, 2.5), 8.0)))
    return _dedupe(out, 2.5)


def estimate_poles(lines, curb, bbox, buildings=None, lights=(), spacing=30.0, road=None):
    """폭 12m 미만 생활도로 한쪽에 30m 간격. 반환 [(x, y)]"""
    from shapely import contains_xy, prepare
    road_in = None
    if road is not None and not road.is_empty:
        road_in = road.buffer(-0.1)
        prepare(road_in)
    from scipy.spatial import cKDTree
    blk = None
    if buildings is not None and not buildings.is_empty:
        blk = buildings.buffer(0.25)
        prepare(blk)
    lt = cKDTree([(l[0], l[1]) for l in lights]) if lights else None
    out = []
    for d in lines:
        if d["w"] >= 12 or d["n"] >= 3:
            continue
        ln = d["line"]
        n = int(ln.length // spacing)
        if n < 1:
            continue
        start = (ln.length - (n - 1) * spacing) / 2
        for j in range(n):
            s = start + j * spacing
            a, b = ln.interpolate(max(s - 1, 0)), ln.interpolate(min(s + 1, ln.length))
            tx, ty = b.x - a.x, b.y - a.y
            L = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / L, tx / L
            c = ln.interpolate(s)
            for side in (1, -1):
                dd = curb.hit((c.x, c.y), (nx * side, ny * side), d["w"] / 2 + 5)
                if dd is None:
                    continue
                x, y = c.x + nx * side * (dd + 0.35), c.y + ny * side * (dd + 0.35)
                if road_in is not None and contains_xy(road_in, x, y):
                    continue
                if not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                    break
                if blk is not None and contains_xy(blk, x, y):
                    continue
                if lt is not None and lt.query_ball_point((x, y), 5.0):
                    break            # 골목 보안등은 보통 전봇대에 달려 있다 → 그 자리를 전봇대로 본다
                out.append((x, y))
                break
    return _dedupe(out, 8.0)


# ---------------------------------------------------------------- 모양
def signal_parts(kind, arm=0.0):
    """원점 바닥, 팔은 +x. 반환 trimesh."""
    import trimesh
    parts = []
    if kind == "보행자":
        h = 3.2
        p = trimesh.creation.cylinder(radius=0.07, height=h, sections=8); p.apply_translation([0, 0, h / 2])
        hd = trimesh.creation.box([0.32, 0.3, 0.75]); hd.apply_translation([0.18, 0, h - 0.45])
        parts += [p, hd]
    else:
        h = 6.2
        p = trimesh.creation.cylinder(radius=0.14, height=h, sections=8); p.apply_translation([0, 0, h / 2])
        parts.append(p)
        if arm > 0:
            a = trimesh.creation.box([arm, 0.14, 0.16]); a.apply_translation([arm / 2, 0, h - 0.4])
            parts.append(a)
            for xx in (arm * 0.55, arm - 0.6):          # 가로형 3·4색 등
                hd = trimesh.creation.box([0.35, 1.25, 0.4])
                hd.apply_translation([xx, 0, h - 0.75])
                parts.append(hd)
        hd = trimesh.creation.box([0.3, 0.3, 0.9]); hd.apply_translation([0.2, 0, 3.2])  # 기둥에 붙은 보조등
        parts.append(hd)
    return trimesh.util.concatenate(parts)


def pole_parts(h=12.0):
    import trimesh
    from .tree_models import _frustum
    p = _frustum((0, 0, 0), (0, 0, h), 0.2, 0.14, sections=8)
    arm = trimesh.creation.box([0.12, 1.8, 0.12]); arm.apply_translation([0, 0, h - 1.0])
    arm2 = trimesh.creation.box([0.12, 1.4, 0.12]); arm2.apply_translation([0, 0, h - 2.4])
    return trimesh.util.concatenate([p, arm, arm2])


def placed(mesh, x, y, z, ang=0.0):
    import trimesh
    m = mesh.copy()
    m.apply_transform(trimesh.transformations.rotation_matrix(ang, [0, 0, 1]))
    m.apply_translation([x, y, z])
    return m


def flat_mesh(polys, zfun, lift=0.03):
    """2D 면들 → 높이를 따라 얹힌 얇은 판 (윗면만). zfun(xy 배열) → 높이."""
    import trimesh
    Vs, Fs, n = [], [], 0
    for g in polys:
        for p in getattr(g, "geoms", [g]):
            if p.geom_type != "Polygon" or p.area < 0.02:
                continue
            try:
                v2, f = trimesh.creation.triangulate_polygon(p, engine="earcut")
            except Exception:
                continue
            if len(f) == 0:
                continue
            z = np.asarray(zfun(v2), float) + lift
            Vs.append(np.column_stack([v2, z])); Fs.append(f + n); n += len(v2)
    if not Vs:
        return None
    return trimesh.Trimesh(np.vstack(Vs), np.vstack(Fs), process=False)

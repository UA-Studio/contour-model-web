"""신호등·전봇대 3D 모양과 바닥에 붙이는 얇은 면."""
import numpy as np



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
    from ..tree_models import _frustum
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

"""원형 범위 (v2.2): 네모 범위로 모델을 만들고, 원 밖을 잘라 둥근 모형으로.

판(계단 면)은 원으로 자르면 그 위의 길 면·녹지·선은 저절로 원 안만 남는다(판 위로 나누기 때문).
건물은 원 경계에서 잘리고(발자국을 원으로 자름), 나무·가로등은 원 안 것만, 도로 디테일·구조물 메시는 원 밖 면을 뺀다.
경사면 지형과 3D 프린트 솔리드는 원기둥과 교집합으로 자른다.
"""
import numpy as np
import shapely
import trimesh
from shapely.geometry import Point, box


def make_circle(bbox):
    """네모 범위 안에 꼭 맞는 원 (가운데, 짧은 변 = 지름)."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    r = min(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2
    return Point(cx, cy).buffer(r, quad_segs=64)


def outside_of(circle, bbox, pad=50.0):
    return box(*bbox).buffer(pad, join_style=2).difference(circle)


def cut_regions(regions, circle, min_area=0.0):
    """{레벨: 면} → 원 안만. 맨 아래 판은 비어도 원판 그대로 남긴다."""
    out = {}
    lv = list(regions)
    for i, L in enumerate(lv):
        g = regions[L].intersection(circle)
        if i == 0 or (not g.is_empty and g.area > min_area):
            out[L] = g
    return out


def keep_points(items, circle, xy=lambda t: (t[0], t[1])):
    items = list(items)
    if not items:
        return items
    P = np.array([xy(t) for t in items], float)
    inn = shapely.contains_xy(circle, P[:, 0], P[:, 1])
    return [t for t, k in zip(items, inn) if k]


def clip_mesh_faces(m, circle, origin=(0.0, 0.0)):
    """메시에서 가운데가 원 밖인 면을 뺀다 (얇은 노면표시·다리 등 렌더용). 남는 면이 없으면 None."""
    if m is None or not len(m.faces):
        return m
    c = m.triangles_center
    inn = shapely.contains_xy(circle, c[:, 0] + origin[0], c[:, 1] + origin[1])
    if inn.all():
        return m
    if not inn.any():
        return None
    sub = m.submesh([np.nonzero(inn)[0]], append=True)
    if hasattr(m, "visual") and getattr(m.visual, "kind", None) == "face":
        try:
            sub.visual.face_colors = m.visual.face_colors[inn]
        except Exception:
            pass
    return sub


def cylinder_cut(m, circle, origin=(0.0, 0.0)):
    """닫힌 솔리드 메시 ∩ 원기둥 (manifold). 실패하면 면 빼기로."""
    if m is None or not len(m.faces):
        return m
    try:
        from .geom_util import _mf_extrude, _mf_from_trimesh
        M = _mf_from_trimesh(m)
        z0, z1 = float(m.bounds[0][2]) - 10.0, float(m.bounds[1][2]) + 10.0
        cyl = _mf_extrude(circle, z0, z1, origin)
        out = (M ^ cyl).to_mesh()
        v = np.asarray(out.vert_properties)[:, :3].astype(float)
        f = np.asarray(out.tri_verts).astype(np.int64)
        if not len(f):
            return clip_mesh_faces(m, circle, origin)
        return trimesh.Trimesh(v, f, process=True)
    except Exception:
        return clip_mesh_faces(m, circle, origin)

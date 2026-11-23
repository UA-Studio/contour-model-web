"""여러 저장 형식이 같이 쓰는 메시 도구 (모서리 음영 분리, 원점 옮기기, manifold 변환)."""
import numpy as np
import trimesh
from shapely.geometry import Polygon



def crisp_mesh(m, deg=30.0):
    """렌더링용: 꺾인 모서리(윗면↔옆벽 등)에서 꼭짓점을 나눠, 매끈한 음영이 모서리 너머로 번지지 않게 한다.

    Rhino·three.js 는 꼭짓점 법선을 그 꼭짓점을 쓰는 면들의 평균으로 잡는다. 길 솔리드처럼 얇은 판의
    윗면과 옆벽이 꼭짓점을 같이 쓰면 윗면 가장자리 법선이 옆으로 기울어 '베개' 같은 얼룩이 생긴다.
    겹친 점을 합친 뒤 deg 보다 크게 꺾인 모서리에서만 끊는다(모양은 그대로).
    """
    mm = trimesh.Trimesh(m.vertices, m.faces, process=False)
    mm.merge_vertices()
    if len(mm.faces) == 0:
        return mm
    try:
        return _split_sharp(mm, np.radians(deg))
    except Exception:
        try:
            return trimesh.graph.smooth_shade(mm, angle=np.radians(deg), facet_minarea=None)
        except Exception:
            return mm


def _split_sharp(mm, angle):
    """trimesh.graph.smooth_shade(facet_minarea=None) 와 같은 결과를 한 번의 배열 연산으로.

    완만하게 이어진 면끼리 한 묶음으로 보고, 꼭짓점을 (묶음, 꼭짓점) 짝마다 하나씩 둔다.
    smooth_shade 는 묶음마다 부분 메시를 따로 떠내 붙여서, 묶음이 많은 메시(건물·길)에서 느리다.
    """
    adj = mm.face_adjacency
    if len(adj) == 0:
        return mm.copy()
    ok = adj[mm.face_adjacency_angles < angle]
    labels = trimesh.graph.connected_component_labels(ok, node_count=len(mm.faces))
    nv = len(mm.vertices)
    key = labels.astype(np.int64)[:, None] * nv + mm.faces
    uniq, inv = np.unique(key.reshape(-1), return_inverse=True)
    return trimesh.Trimesh(mm.vertices[uniq % nv], inv.reshape(-1, 3), process=False)


def _with_ground_normals(m, hz, origin, eps=1.5):
    """길 솔리드: 꺾인 모서리에서 나눈 뒤, 윗면·밑면 꼭짓점 법선을 대지 곡면의 기울기에서 바로 잡는다.

    칸으로 나눠 만든 면도 칸 경계에서 법선이 똑같아 음영 이음매가 생기지 않는다. 옆벽은 면 법선 그대로.
    """
    m = crisp_mesh(m)
    vn = np.array(m.vertex_normals, dtype=float)
    x, y = m.vertices[:, 0] + origin[0], m.vertices[:, 1] + origin[1]
    gx = (hz(x + eps, y) - hz(x - eps, y)) / (2 * eps)
    gy = (hz(x, y + eps) - hz(x, y - eps)) / (2 * eps)
    gn = np.column_stack([-gx, -gy, np.ones(len(x))])
    gn /= np.linalg.norm(gn, axis=1)[:, None]
    up, down = vn[:, 2] > 0.5, vn[:, 2] < -0.5
    vn[up] = gn[up]
    vn[down] = -gn[down]
    m.vertex_normals = vn
    m.metadata["fixed_normals"] = True
    return m


def _shifted(mesh, origin):
    m = mesh.copy()
    m.apply_translation([-origin[0], -origin[1], 0])
    return m


# ---------------------------------------------------------------- 출력: 3D 프린트용 STL
def _mf_from_trimesh(m):
    import manifold3d as mf
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, dtype=np.float32),
                               tri_verts=np.asarray(m.faces, dtype=np.uint32)))


# ---------------------------------------------------------------- 출력: STL
def _shift(p, origin):
    return Polygon([(x - origin[0], y - origin[1]) for x, y in p.exterior.coords],
                   [[(x - origin[0], y - origin[1]) for x, y in h.coords] for h in p.interiors])

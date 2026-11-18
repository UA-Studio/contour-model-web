"""생성 과정 실황: 모델을 만드는 동안 단계 신호, 단계별 2D 평면 그림, 3D 부품(가벼운 GLB)을 내보낸다.

화면(app.py)은 events 를 매초 받아 '2D 작업 → 3D 부품' 대응표와 실시간 3D 보기를 채운다.
live_dir 이 없으면(명령줄 실행) 그림과 부품은 만들지 않고 단계 신호만 쌓는다.
"""
import os
import threading
import time

import numpy as np
import trimesh

# 부품 색 (화면에서 그대로 쓴다)
COLOR = dict(base=(96, 96, 92), c3d=(54, 54, 50), tin=(150, 170, 196), sheet=(200, 199, 192),
             mass=(246, 246, 242), detail=(246, 246, 242), tree=(150, 172, 120), slope=(196, 194, 186), light=(90, 90, 96), roadx=(240, 240, 234))


class Progress:
    def __init__(self, live_dir=None):
        self.live = live_dir
        self.events = []
        self._lock = threading.Lock()
        self._seq = 0
        if live_dir:
            os.makedirs(live_dir, exist_ok=True)

    def _emit(self, **ev):
        ev["at"] = round(time.time(), 2)
        with self._lock:
            self.events.append(ev)

    def _name(self, key, ext):
        self._seq += 1
        return f"{self._seq:03d}_{key}.{ext}"

    # ---------------------------------------------------------------- 신호
    def step(self, sid, r=None, n=None, x=""):
        """2D 작업 한 칸이 시작됨. sid 는 화면 표의 번호(예 '2.5'), r/n 은 반복(판 r+1 / n)."""
        self._emit(t="step", id=sid, r=r, n=n, x=x)

    def steps(self, *sids):
        for s in sids:
            self.step(s)

    def note(self, t, **kw):
        self._emit(t=t, **kw)

    # ---------------------------------------------------------------- 3D 부품
    def part(self, key, meshes, color=None, replaces=(), info=""):
        """3D 부품 하나를 GLB 로 쓰고 알린다. meshes: Trimesh 또는 (Trimesh, (r,g,b)) 목록."""
        if not self.live:
            return
        try:
            ms = []
            for m in meshes if isinstance(meshes, (list, tuple)) else [meshes]:
                c = color or COLOR.get(key.split("_")[0], (200, 200, 200))
                if isinstance(m, tuple):
                    m, c = m
                if m is None or len(m.faces) == 0:
                    continue
                m = m.copy()
                m.visual.face_colors = [*c, 255]
                ms.append(m)
            if not ms:
                return
            name = self._name(key, "glb")
            trimesh.util.concatenate(ms).export(os.path.join(self.live, name))
            self._emit(t="part", key=key, url="/live/" + name, replaces=list(replaces), info=info)
        except Exception as e:  # 실황 그림이 실패해도 모델은 계속 만든다
            self._emit(t="warn", msg=f"3D 부품 {key}: {e}")

    # ---------------------------------------------------------------- 2D 평면
    def plan(self, key, bbox, draw, label=""):
        """draw(ax) 로 평면 그림 하나를 그려 알린다."""
        if not self.live:
            return
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            fig = plt.figure(figsize=(4, 4 * h / w), dpi=100)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_facecolor("#f4f3ef")
            draw(ax)
            ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
            ax.set_aspect("equal"); ax.set_axis_off()
            name = self._name(key, "png")
            fig.savefig(os.path.join(self.live, name), facecolor="#f4f3ef")
            plt.close(fig)
            self._emit(t="plan", key=key, url="/live/" + name, label=label)
        except Exception as e:
            self._emit(t="warn", msg=f"평면 {key}: {e}")


# ---------------------------------------------------------------- 그리기 도우미
def polys_xy(g):
    """shapely 면 → matplotlib 다각형 좌표 목록(구멍은 무시하지 않고 따로 그림)."""
    out = []
    for p in getattr(g, "geoms", [g]):
        if p.is_empty or p.geom_type != "Polygon":
            continue
        out.append((np.asarray(p.exterior.coords)[:, :2], [np.asarray(r.coords)[:, :2] for r in p.interiors]))
    return out


def fill(ax, geoms, fc, ec="none", lw=0.3, z=1):
    """여러 면을 구멍까지 채워 그린다."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    for g in geoms:
        for ext, holes in polys_xy(g):
            verts = [ext] + holes
            codes = []
            for v in verts:
                c = np.full(len(v), Path.LINETO); c[0] = Path.MOVETO; codes.append(c)
            ax.add_patch(PathPatch(Path(np.vstack(verts), np.concatenate(codes)), facecolor=fc,
                                   edgecolor=ec, linewidth=lw, zorder=z))


def ribbon(lines, origin, width=0.8, height=0.5):
    """등고선을 제 높이에 띄운 얇은 띠 솔리드로. lines: [(LineString, z)]"""
    out = []
    for ls, z in lines:
        g = ls.buffer(width / 2, cap_style=2, join_style=2).simplify(0.2)
        for p in getattr(g, "geoms", [g]):
            if p.is_empty or p.area < 0.05:
                continue
            try:
                m = trimesh.creation.extrude_polygon(_shift(p, origin), height)
            except Exception:
                continue
            m.apply_translation([0, 0, z - height / 2])
            out.append(m)
    return out


def extrude(poly, z0, z1, origin):
    try:
        m = trimesh.creation.extrude_polygon(_shift(poly, origin), max(z1 - z0, 0.01))
    except Exception:
        return None
    m.apply_translation([0, 0, z0])
    return m


def low_trees(trees, origin):
    """나무를 가벼운 메시로(수관 20면 타원구 + 다섯 모 줄기)."""
    from .paths_trees import tree_dims
    if not trees:
        return None
    crown = trimesh.creation.icosphere(subdivisions=0, radius=1.0)
    trunk = trimesh.creation.cylinder(radius=1.0, height=1.0, sections=5)
    trunk.apply_translation([0, 0, 0.5])
    V, F, n = [], [], 0
    for x, y, z, r, kind in trees:
        cz, tr_, rz = tree_dims(r)
        for base, s, dz in ((crown, (r, r, rz), cz), (trunk, (tr_, tr_, cz), 0.0)):
            v = base.vertices * s + [x - origin[0], y - origin[1], z + dz]
            V.append(v); F.append(base.faces + n); n += len(v)
    return trimesh.Trimesh(np.vstack(V), np.vstack(F), process=False)


def _shift(p, origin):
    from shapely.affinity import translate
    return translate(p, -origin[0], -origin[1])

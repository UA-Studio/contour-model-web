"""만드는 중 화면(진행 화면)에 보내는 미리보기 도형.
"""
import math

import numpy as np
import trimesh
from shapely.geometry import LineString, box

from .progress import extrude, fill, ribbon
from .geom_util import as_polys
from .preview import _plan_sheets


# ---------------------------------------------------------------- 생성 과정 실황 (화면용)
def _live_input(pg, data, bbox, origin, interval):
    """받침판 + 제 높이에 띄운 등고선."""
    clip = box(*bbox)
    cs = [(ls.intersection(clip), z) for ls, z in data["contours"]]
    cs = [(g, z) for g, z in cs if not g.is_empty]
    lines = [(l, z) for g, z in cs for l in getattr(g, "geoms", [g]) if isinstance(l, LineString)]
    zs = [z for _, z in lines] or [0.0]
    z0 = math.floor(min(zs) / interval) * interval - 2 * interval
    pg.part("base", extrude(clip, z0 - 1.0, z0, origin), info=f"{bbox[2] - bbox[0]:.0f} × {bbox[3] - bbox[1]:.0f} m")
    pg.part("c3d", ribbon(lines, origin), info=f"등고선 {len(lines)}개")
    pg.plan("c", bbox, lambda ax: [ax.plot(*np.asarray(l.coords).T, color="#555", lw=0.4) for l, _ in lines])


def _mass_meshes(masses, origin):
    out = []
    for m in masses:
        out.append(extrude(m["poly"], m["base"], m["top"], origin))
    return out


def _live_tin(pg, interp, bbox, origin):
    """보간한 지형면을 4m 격자 곡면으로."""
    xs = np.arange(bbox[0], bbox[2] + 2, 4.0)
    ys = np.arange(bbox[1], bbox[3] + 2, 4.0)
    X, Y = np.meshgrid(xs, ys)
    H = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
    ny, nx = H.shape
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    v = np.column_stack([X.ravel() - origin[0], Y.ravel() - origin[1], H.ravel()])
    f = np.vstack([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    pg.part("tin", trimesh.Trimesh(v, f, process=False))
    pg.plan("tin", bbox, lambda ax: ax.imshow(H, origin="lower", cmap="terrain",
                                              extent=(bbox[0], bbox[2], bbox[1], bbox[3])))


def _live_sheet(pg, regions, i, bbox, origin, j=None, key=None):
    """판 i..j-1 (한 묶음)을 화면 실황에 3D 부품 하나·평면 그림 하나로."""
    lv = list(regions)
    j = j or i + 1
    key = i if key is None else key
    iv = lv[1] - lv[0] if len(lv) > 1 else 1.0
    meshes = []
    for k in range(i, j):
        L = lv[k]
        bottom = L - iv if k else L - iv * 2
        meshes += [extrude(p, bottom, L, origin) for p in as_polys(regions[L])]
    g = 0.86 - 0.3 * i / max(1, len(lv) - 1)
    c = tuple(int(255 * v) for v in (g, g, g * 0.97))
    pg.part(f"sheet_{key:02d}", meshes, color=c, replaces=["tin", "c3d"] if i == 0 else [],
            info=f"EL {lv[i]:g}" if j == i + 1 else f"EL {lv[i]:g}~{lv[j - 1]:g} ({j - i}장)")

    def draw(ax):
        _plan_sheets(ax, {k: regions[k] for k in lv[:i]}, n=len(lv))
        fill(ax, [regions[lv[k]] for k in range(i, j)], "#f08a3c", "#9a3f06", 0.4, 2)
    pg.plan(f"sheet_{key:02d}", bbox, draw,
            label=f"판 {i + 1} 영역" if j == i + 1 else f"판 {i + 1}~{j} 영역 (EL {lv[i]:g}~{lv[j - 1]:g})")


def _live_details(pg, masses, floor_height, origin):
    from .building_detail import building_parts
    pg.step("7.1")
    out = []
    for b in masses:
        for part in building_parts(b, floor_height):
            if part[1] == "ext":
                out.append(extrude(part[2], part[3], part[4], origin))
            elif part[1] == "mesh":
                m = part[2].copy(); m.apply_translation([-origin[0], -origin[1], 0]); out.append(m)
    pg.steps("7.2", "7.3", "7.4", "7.5", "7.6")
    pg.part("detail", out, replaces=["mass"], info="지붕·옥탑·파라펫")

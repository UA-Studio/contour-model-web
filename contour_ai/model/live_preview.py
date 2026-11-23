"""만드는 중 진행 화면에 보내는 그림과 평면 미리보기 PNG."""
import math
import numpy as np
import trimesh
from shapely.geometry import LineString, box
from ..progress import extrude, fill, ribbon
from .terrain import as_polys



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
    from ..building_detail import building_parts
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


def _plan_sheets(ax, regions, n=None):
    lv = list(regions)
    n = n or len(lv)
    step = max(1, math.ceil(len(lv) / 40))   # 판이 많으면 그림에는 40장 정도만(겹쳐 보여 차이 없음)
    for i, L in enumerate(lv):
        if i % step and i != len(lv) - 1:
            continue
        g = 0.93 - 0.35 * i / max(1, n - 1)
        fill(ax, [regions[L]], (g, g, g * 0.97), "#aaa", 0.2, 1)


def _plan_paths(ax, areas):
    from ..paths_trees import PATH_STYLE
    for p, L, kind in areas:
        fill(ax, [p], np.array(PATH_STYLE[kind][1]) / 255, z=2)


def _plan_greens(ax, greens):
    for p, L, kind in greens:
        fill(ax, [p], {"forest": "#b9d7a8", "grass": "#eef3cf"}.get(kind, "#dcebc8"), z=2)


def _plan_trees(ax, trees):
    t = np.array([(x, y, r) for x, y, z, r, k in trees])
    ax.scatter(t[:, 0], t[:, 1], s=(t[:, 2] * 0.5) ** 2, c="#5b7f4a", alpha=0.8, linewidths=0, zorder=4)


def _preview_plan(path, bbox, regions, masses, areas, trees, greens, lights=(), site=None):
    """평면 미리보기 PNG: 계단 높이(회색 단계), 길, 녹지, 건물, 나무."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.plotting import plot_polygon
    from ..paths_trees import PATH_STYLE
    fig, ax = plt.subplots(figsize=(10, 10 * (bbox[3] - bbox[1]) / (bbox[2] - bbox[0])), dpi=120)
    lv = list(regions)
    for i, L in enumerate(lv):
        g = 0.93 - 0.35 * i / max(1, len(lv) - 1)
        for p in as_polys(regions[L]):
            plot_polygon(p, ax=ax, add_points=False, facecolor=(g, g, g * 0.97), edgecolor="#999", linewidth=0.3)
    for p, L, kind in greens:
        plot_polygon(p, ax=ax, add_points=False,
                     facecolor={"forest": "#b9d7a8", "grass": "#eef3cf"}.get(kind, "#dcebc8"), edgecolor="none")
    for p, L, kind in areas:
        c = np.array(PATH_STYLE[kind][1]) / 255
        plot_polygon(p, ax=ax, add_points=False, facecolor=c, edgecolor="none")
    for m in masses:
        plot_polygon(m["poly"], ax=ax, add_points=False, facecolor="white", edgecolor="#444", linewidth=0.3)
    if trees:
        t = np.array([(x, y, r) for x, y, z, r, k in trees])
        ax.scatter(t[:, 0], t[:, 1], s=(t[:, 2] * 1.2) ** 2, c="#5b7f4a", alpha=0.75, linewidths=0)
    if lights:
        q = np.array([(x, y) for x, y, *_ in lights])
        ax.scatter(q[:, 0], q[:, 1], s=4, c="#e0a020", marker="o", linewidths=0, zorder=5)
    if site is not None:
        for p in as_polys(site):
            plot_polygon(p, ax=ax, add_points=False, facecolor=(0.145, 0.388, 0.922, 0.06), edgecolor="#2563eb",
                         linewidth=1.6, zorder=8)
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3]); ax.set_aspect("equal"); ax.set_axis_off()
    fig.savefig(path, bbox_inches="tight"); plt.close(fig)

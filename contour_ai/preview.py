"""평면 확인 그림(preview_plan.png) 그리기.
"""
import math

import numpy as np

from .progress import fill
from .geom_util import as_polys


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
    from .paths_trees import PATH_STYLE
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
    from .paths_trees import PATH_STYLE
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

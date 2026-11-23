"""노면표시 확인 그림(road_marks_check.png)."""
from shapely.geometry import box



# ---------------------------------------------------------------- 확인 그림
SRC_COLOR = {"정밀도로지도": "#0a8f5a", "서울도보네트워크": "#1f6fd1", "생활안전지도": "#1f6fd1", "자료": "#1f6fd1", "항공사진": "#d1501f",
             "추정": "#222222"}


def marks_check(path, road, walk, marks, islands=None, inferred=None, bbox=None, title="", core=None):
    """노면표시 확인 그림: 출처별 색(파랑 = 공공 API·자료, 주황 = 항공사진, 검정 = 추정),
    중앙선은 노랑, 수치지도 교통섬은 회색, 추정 교통섬은 빨간 테두리."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.plotting import plot_polygon

    def polys(g):
        if g is None or g.is_empty:
            return []
        return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"]
    fig, ax = plt.subplots(figsize=(12, 12), dpi=130)
    clip = box(*bbox) if bbox else None
    for g, fc in ((road, "#8a8a8a"), (walk, "#e4e2dc"), (islands, "#b8b4a8")):
        if g is not None and clip is not None:
            g = g.intersection(clip)
        for p in polys(g):
            plot_polygon(p, ax=ax, add_points=False, facecolor=fc, edgecolor="none")
    for p in polys(core):
        plot_polygon(p, ax=ax, add_points=False, facecolor="none", edgecolor="#2a9d5c", linewidth=1.0, linestyle="--")
    for p in polys(inferred):
        plot_polygon(p, ax=ax, add_points=False, facecolor="#c9c3b3", edgecolor="#e01010", linewidth=1.2)
    for g, k, s in marks:
        fc = "#f2c230" if k in ("중앙선", "주정차금지선") else ("#2860c4" if k == "버스전용차선" else SRC_COLOR.get(s, "#222222"))
        if k in ("유도선", "안전지대"):
            fc = "#ffffff"
        for p in polys(g if clip is None else g.intersection(clip)):
            plot_polygon(p, ax=ax, add_points=False, facecolor=fc, edgecolor="none")
    if bbox:
        ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color="#0a8f5a", label="HD map"), Patch(color="#1f6fd1", label="API / data file"), Patch(color="#d1501f", label="aerial photo"),
                       Patch(color="#222222", label="estimated"), Patch(color="#f2c230", label="center line"),
                       Patch(facecolor="#c9c3b3", edgecolor="#e01010", label="inferred island"),
                       Patch(facecolor="none", edgecolor="#2a9d5c", linestyle="--", label="junction core")],
              loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)

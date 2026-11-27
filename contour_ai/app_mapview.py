"""지도 화면 그림: 도엽 전체 그림(overview.png), 확대했을 때 보이는 칸 그림, 이름 있는 곳 목록.
"""
import json
import os
import re


def _named_places(folder):
    """이름 → 위치. 같은 이름(단지의 여러 동)은 합쳐서 가운데를 잡는다(명령줄 --name 과 같은 방식)."""
    import shapefile
    from shapely.geometry import shape
    from shapely.ops import unary_union
    found = {}
    for layer in ("N3A_B0010000", "N3P_H0040000", "N3A_C0430000"):
        fn = os.path.join(folder, layer)
        if not os.path.exists(fn + ".shp"):
            continue
        r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
        idx = [i for i, f in enumerate(r.fields[1:]) if f[0] == "명칭"]
        for sr in r.iterShapeRecords():
            for i in idx:
                v = str(sr.record[i] or "").strip()
                if len(v) >= 2 and not re.fullmatch(r"[\d\-]+동?", v):
                    found.setdefault(v, []).append(shape(sr.shape.__geo_interface__))
    out = []
    for k in sorted(found):
        c = unary_union(found[k]).centroid
        out.append({"name": k, "xy": [round(c.x, 1), round(c.y, 1)]})
    return out[:1500]


def _overview_png(folder, sheet, path):
    """도엽 전체 개관도(건물·도로). 화면에서 클릭해 범위를 고르는 데 쓴다."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import shapefile
    from matplotlib.collections import PolyCollection
    w, h = sheet[2] - sheet[0], sheet[3] - sheet[1]
    fig = plt.figure(figsize=(8, 8 * h / w), dpi=125)
    ax = fig.add_axes([0, 0, 1, 1])
    for layer, fc in (("N3A_A0010000", "#8d8d89"), ("N3A_B0010000", "#ffffff")):
        fn = os.path.join(folder, layer)
        if not os.path.exists(fn + ".shp"):
            continue
        polys = []
        for s in shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace").iterShapes():
            parts = list(s.parts) + [len(s.points)]
            polys += [s.points[a:b] for a, b in zip(parts[:-1], parts[1:]) if b - a > 2]
        ax.add_collection(PolyCollection(polys, facecolors=fc, edgecolors="#55554f", linewidths=0.15))
    fn = os.path.join(folder, "N3L_F0010000")
    if os.path.exists(fn + ".shp"):
        for s in shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace").iterShapes():
            xs, ys = zip(*s.points)
            ax.plot(xs, ys, color="#b9b2a2", lw=0.3, zorder=0)
    sj = os.path.join(folder, "sheets.json")
    if os.path.exists(sj):        # 이어 붙인 도엽 경계
        for sh in json.load(open(sj, encoding="utf-8"))["sheets"]:
            x0, y0, x1, y1 = sh["bbox"]
            ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], color="#c0562f", lw=0.8, ls=(0, (6, 4)), zorder=5)
    ax.set_xlim(sheet[0], sheet[2])
    ax.set_ylim(sheet[1], sheet[3])
    ax.set_facecolor("#e4e2dc")
    ax.axis("off")
    fig.savefig(path, facecolor="#e4e2dc")
    plt.close(fig)


_TILE = {}


def overview_tile(src, bbox, px):
    """지도 확대용: 보이는 부분만 화면 해상도로 다시 그린 그림(PNG 바이트). 도형은 도엽마다 한 번만 읽어 둔다."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import shapefile
    from matplotlib.collections import LineCollection, PolyCollection
    folder = src["folder"]
    if folder not in _TILE:
        g = {}
        for layer in ("N3A_A0010000", "N3A_B0010000"):
            fn = os.path.join(folder, layer)
            polys = []
            if os.path.exists(fn + ".shp"):
                for s in shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace").iterShapes():
                    parts = list(s.parts) + [len(s.points)]
                    polys += [s.points[a:b] for a, b in zip(parts[:-1], parts[1:]) if b - a > 2]
            g[layer] = polys
        lines = []
        fn = os.path.join(folder, "N3L_F0010000")
        if os.path.exists(fn + ".shp"):
            for s in shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace").iterShapes():
                parts = list(s.parts) + [len(s.points)]
                lines += [s.points[a:b] for a, b in zip(parts[:-1], parts[1:]) if b - a > 1]
        g["contour"] = lines
        _TILE.clear()
        _TILE[folder] = g
    g = _TILE[folder]
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    pw = max(64, min(int(px), 2400))
    ph = max(64, int(pw * h / max(w, 1e-6)))
    k = pw / max(w, 1e-6)                   # 화면 픽셀 / m: 확대할수록 선을 조금 굵게
    fig = plt.figure(figsize=(pw / 100, ph / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    lw = min(0.9, 0.15 * max(1.0, k / 0.45) ** 0.5)
    ax.add_collection(LineCollection(g["contour"], colors="#b9b2a2", linewidths=min(1.0, 0.3 * max(1.0, k / 0.45) ** 0.5), zorder=0))
    for layer, fc in (("N3A_A0010000", "#8d8d89"), ("N3A_B0010000", "#ffffff")):
        ax.add_collection(PolyCollection(g[layer], facecolors=fc, edgecolors="#55554f", linewidths=lw))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_facecolor("#e4e2dc")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, facecolor="#e4e2dc", format="png")
    plt.close(fig)
    return buf.getvalue()

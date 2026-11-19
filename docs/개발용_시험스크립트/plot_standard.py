"""표준 대지 파일 확인 그림: 수치지도(건물·등고선 회색) 위에 표준 파일 도형(경계 파랑, 레벨판 파랑 면, 건물 검정, 도로 회색, 레벨점 숫자).
사용: python plot_standard.py 수치지도SHP폴더 표준.dxf 제목 출력.png"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import shapefile
from contour_ai import sitefile
folder, std, title, out = sys.argv[1:5]
from contour_ai.analysis import _font
_font()
r = sitefile.read_standard(std)
site = r["poly"]; x0, y0, x1, y1 = site.bounds; m = max(x1 - x0, y1 - y0) * 0.2
fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
for lay, col, lw in (("N3L_F0010000", "#cfcfcf", .5), ("N3A_B0010000", "#9a9a9a", .6)):
    fn = os.path.join(folder, lay)
    if not os.path.exists(fn + ".shp"): continue
    for sh in shapefile.Reader(fn).shapes():
        bx = sh.bbox
        if bx[2] < x0 - m or bx[0] > x1 + m or bx[3] < y0 - m or bx[1] > y1 + m: continue
        pts = sh.points; parts = list(sh.parts) + [len(pts)]
        for a, b in zip(parts[:-1], parts[1:]):
            xs, ys = zip(*pts[a:b]); ax.plot(xs, ys, color=col, lw=lw)
def fill(p, **k):
    for g in getattr(p, "geoms", [p]):
        xs, ys = g.exterior.xy; ax.fill(xs, ys, **k)
fill(site, facecolor=(0.11, 0.31, 0.85, 0.05), edgecolor="#1d4ed8", lw=2.2)
for p, z in r["plates"]:
    fill(p, facecolor=(0.11, 0.31, 0.85, 0.18), edgecolor="#1d4ed8", lw=.8)
    c = p.representative_point(); ax.text(c.x, c.y, f"EL {z:g}", color="#1d4ed8", fontsize=8, ha="center")
for p in r["keep"]:
    fill(p, facecolor="none", edgecolor="#555", hatch="///", lw=.8)
for p in r["buildings"]:
    fill(p, facecolor="#222", edgecolor="#222", lw=.4)
for ls in r["roads"]:
    xs, ys = ls.xy; ax.plot(xs, ys, color="#7a7a7a", lw=.6)
if r["levels"]:
    for x, y, z in r["levels"][1]:
        ax.plot(x, y, "o", color="#1d4ed8", ms=3); ax.text(x + 3, y + 3, f"{z:g}", color="#1d4ed8", fontsize=7)
ax.set_xlim(x0 - m, x1 + m); ax.set_ylim(y0 - m, y1 + m); ax.set_aspect("equal"); ax.axis("off")
n = dict(레벨판=len(r["plates"]), 레벨점=len(r["levels"][1]) if r["levels"] else 0, 건물=len(r["buildings"]), 도로=len(r["roads"]), 원지형유지=len(r["keep"]))
ax.set_title(f"{title}  ·  대지 {site.area:,.0f}㎡  ·  " + " · ".join(f"{k} {v}" for k, v in n.items()), fontsize=11, loc="left")
fig.savefig(out, bbox_inches="tight", facecolor="white"); print(out)

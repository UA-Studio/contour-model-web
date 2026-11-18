"""'대지 안 원래 지형 유지' 확인 그림: 레이저 판(계단판) 외곽을 높이대로 쌓고, 판 위에 눌러 붙인 대지 경계(SITE)를 파랑으로.
사용: python plot_site_drape.py 결과폴더/laser 출력.png [제목]"""
import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ezdxf
from contour_ai.analysis import _font
_font()
d, out = sys.argv[1], sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else "대지 경계선을 원래 등고(계단판)에 눌러 붙인 모습"
fig = plt.figure(figsize=(12, 9), dpi=150)
ax = fig.add_subplot(111, projection="3d")
site_xy = []
sheets = sorted(f for f in os.listdir(d) if f.endswith(".dxf"))
for f in sheets:
    z = float(re.search(r"EL(-?[\d.]+)", f).group(1))
    for e in ezdxf.readfile(os.path.join(d, f)).modelspace():
        if e.dxftype() != "LWPOLYLINE" or e.dxf.layer not in ("CUT", "SITE"):
            continue
        P = [(p[0], p[1]) for p in e.get_points("xy")]
        if e.closed:
            P.append(P[0])
        xs, ys = zip(*P)
        if e.dxf.layer == "SITE":
            ax.plot(xs, ys, [z + 0.3] * len(xs), color="#1d4ed8", lw=2.2, zorder=10)
            site_xy += P
        else:
            ax.plot(xs, ys, [z] * len(xs), color="#8a8a8a", lw=0.5)
if site_xy:
    xs, ys = zip(*site_xy); m = (max(xs) - min(xs)) * 0.12
    win = (min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m)
    ax.set_xlim(win[0], win[1]); ax.set_ylim(win[2], win[3])
    import numpy as np
    for ln in ax.lines:      # 창 밖은 지운다(3D 축은 잘라 주지 않는다)
        X, Y, Z = [np.array(a, float) for a in ln.get_data_3d()]
        far = (X < win[0]) | (X > win[1]) | (Y < win[2]) | (Y > win[3])
        X[far] = np.nan
        ln.set_data_3d(X, Y, Z)
ax.view_init(elev=32, azim=-60)
ax.set_box_aspect((1, 1, 0.45), zoom=1.35); ax.set_axis_off()
ax.set_title(title + "\n회색 = 계단판 외곽(원래 지형, 판마다 높이), 파랑 = 대지 경계(그 판 윗면에 드러나는 부분)", fontsize=11, loc="left")
fig.savefig(out, bbox_inches="tight", facecolor="white"); print(out)

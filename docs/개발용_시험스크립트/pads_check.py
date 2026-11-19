"""계단식 대지 확인 그림: 같은 자리의 지형을 '지금(등고선 보간)'과 '계단식 대지'로 나란히.

사용: python docs/개발용_시험스크립트/pads_check.py <SHP 폴더> <중심 X> <중심 Y> <반경 m> <그림.png>
(실제 수치지도로 만든 그림은 저장소에 올리지 않습니다.)
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from scipy.ndimage import gaussian_filter
from shapely.ops import unary_union

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from contour_ai import contour_model as cm   # noqa: E402
from contour_ai.pads import build_pads       # noqa: E402

FONT = os.path.join(ROOT, "contour_ai", "fonts", "Pretendard-Regular.ttf")
font_manager.fontManager.addfont(FONT)
plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()


def shaded(Z, cell, zlo, zspan):
    gy, gx = np.gradient(Z, cell)
    n = np.dstack([-gx * 3, -gy * 3, np.ones_like(Z)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    L = np.array([-0.5, 0.5, 0.7]); L /= np.linalg.norm(L)
    sh = np.clip(n @ L, 0, 1)
    col = plt.cm.YlOrBr_r(0.15 + 0.7 * (Z - zlo) / (zspan + 1e-9))[..., :3] * (0.35 + 0.75 * sh[..., None])
    return np.clip(col, 0, 1)


def main(src, cx, cy, R, out):
    bb = (cx - R, cy - R, cx + R, cy + R)
    d = cm.load_shp(src, bb)
    f = cm.build_interpolator(d["contours"], d["spots"])
    P = build_pads([b[0] for b in d["buildings"]], f, bb,
                   barriers=[b[0] for b in d["breaks"]] + list(d["walls"]),
                   roads=unary_union(d["road_areas"]) if d["road_areas"] else None, paddies=d["paddies"])
    g = P.wrap(f) if P is not None else f
    c = 0.5
    X, Y = np.meshgrid(np.arange(bb[0], bb[2], c), np.arange(bb[1], bb[3], c))
    xy = np.column_stack([X.ravel(), Y.ravel()])
    # 프로그램과 같이: 원래 지형은 고르게 펴고, 터 둘레는 펴지 않는다
    Z0 = gaussian_filter(f(xy).reshape(X.shape), 2 / c)
    Z1 = g(xy).reshape(X.shape)
    if P is not None:
        w = P.keep_weight(X, Y)
        Z1 = w * Z1 + (1 - w) * gaussian_filter(Z1, 2 / c)
    W = bb[2] - bb[0]
    fig, axs = plt.subplots(1, 2, figsize=(20, 10.6))
    for ax, Z, t in zip(axs, (Z0, Z1), ("지금 (등고선 보간)", "계단식 대지 (건물 터 · 옹벽 · 다랑이 논)")):
        ax.imshow(shaded(Z, c, Z0.min(), np.ptp(Z0)), origin="lower", extent=(0, W, 0, W))
        ax.contour(X - bb[0], Y - bb[1], Z, levels=np.arange(np.floor(Z.min()), Z.max(), 1.0),
                   colors="#3070d0", linewidths=0.4)
        for b in d["buildings"]:
            for p in getattr(b[0], "geoms", [b[0]]):
                if p.geom_type == "Polygon":
                    x, y = p.exterior.xy
                    ax.plot(np.array(x) - bb[0], np.array(y) - bb[1], color="#111", lw=1.1)
        for b in d["breaks"]:
            if b[3] > 0 or b[1] in ("옹벽", "석축"):
                for ls in getattr(b[0], "geoms", [b[0]]):
                    x, y = ls.xy
                    ax.plot(np.array(x) - bb[0], np.array(y) - bb[1], color="red", lw=0.9)
        for p in d["paddies"]:
            for q in getattr(p, "geoms", [p]):
                x, y = q.exterior.xy
                ax.plot(np.array(x) - bb[0], np.array(y) - bb[1], color="#1a8a4a", lw=0.8, ls="--")
        ax.set_xlim(0, W); ax.set_ylim(0, W); ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(t, fontsize=17)
    s = P.stats if P is not None else {}
    fig.text(0.5, 0.015, "파란 선: 1m 등고선 · 빨간 선: 수치지도 옹벽·석축 · 초록 점선: 논 · 검정: 건물"
             + (f"   |   건물 터 {s['buildings']}동 → 평평한 단 {s['pads']}개, 논 {s['paddies']}필(다랑이 {s['paddy_terraced']}필)" if s else ""),
             ha="center", fontsize=13)
    plt.tight_layout(rect=(0, 0.035, 1, 1))
    plt.savefig(out, dpi=80)


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5])

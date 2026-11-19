"""계단식 대지 확인 그림: 같은 자리의 지형을 '지금(등고선 보간)'과 '계단식 대지'로 나란히.

사용: python docs/개발용_시험스크립트/pads_check.py <SHP 폴더[,폴더…]> <중심 X> <중심 Y> <반경 m> <그림.png> [연속지적도 SHP] [대지 경계 WKT 파일]
연속지적도를 주면 '지금 / 건물로 추정 / 필지 기준' 세 장을 나란히.
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


def main(src, cx, cy, R, out, parcel_file=None, site_wkt=None):
    from collections import defaultdict
    from pyproj import CRS
    from contour_ai.pads import load_parcels
    bb = (cx - R, cy - R, cx + R, cy + R)
    d = defaultdict(list)
    for one in src.split(","):
        for k, v in cm.load_shp(one, bb).items():
            d[k] += v
    f = cm.build_interpolator(d["contours"], d["spots"])
    common = dict(barriers=[b[0] for b in d["breaks"]] + list(d["walls"]),
                  roads=unary_union(d["road_areas"]) if d["road_areas"] else None, paddies=d["paddies"])
    bl = [b[0] for b in d["buildings"]]
    runs = [("지금 (등고선 보간)", None)]
    P = build_pads(bl, f, bb, **common)
    runs.append(("계단식 대지 · 건물로 추정", P))
    pcl = []
    if parcel_file:
        prj = os.path.join(src.split(",")[0], "N3A_B0010000.prj")
        crs = CRS(open(prj, encoding="utf-8", errors="replace").read()).to_wkt()
        pcl = load_parcels(parcel_file, bb, crs)
        runs.append(("계단식 대지 · 필지(지적도) 기준", build_pads(bl, f, bb, parcels=pcl, **common)))
    site = None
    if site_wkt:
        from shapely import wkt
        site = wkt.loads(open(site_wkt).read())
    c = 0.5
    X, Y = np.meshgrid(np.arange(bb[0], bb[2], c), np.arange(bb[1], bb[3], c))
    xy = np.column_stack([X.ravel(), Y.ravel()])
    Z0 = gaussian_filter(f(xy).reshape(X.shape), 2 / c)
    W = bb[2] - bb[0]
    n = len(runs)
    fig, axs = plt.subplots(1, n, figsize=(10 * n, 10.6))
    for ax, (t, PP) in zip(axs, runs):
        if PP is None:
            Z = Z0
        else:
            # 프로그램과 같이: 원래 지형은 고르게 펴고, 터 둘레는 펴지 않는다
            Z = PP.wrap(f)(xy).reshape(X.shape)
            w = PP.keep_weight(X, Y)
            Z = w * Z + (1 - w) * gaussian_filter(Z, 2 / c)
        ax.imshow(shaded(Z, c, Z0.min(), np.ptp(Z0)), origin="lower", extent=(0, W, 0, W))
        ax.contour(X - bb[0], Y - bb[1], Z, levels=np.arange(np.floor(Z.min()), Z.max(), 2.0),
                   colors="#3070d0", linewidths=0.4)

        def line(g, **k):
            for q in getattr(g, "geoms", [g]):
                if q.is_empty:
                    continue
                for r in ([q.exterior] if q.geom_type == "Polygon" else [q]):
                    x, y = r.xy
                    ax.plot(np.array(x) - bb[0], np.array(y) - bb[1], **k)
        if PP is not None and "필지" in t:
            for g, j in pcl:
                line(g, color="#7a3fb0", lw=0.6)
        for b in d["buildings"]:
            line(b[0], color="#111", lw=1.0)
        for b in d["breaks"]:
            if b[3] > 0 or b[1] in ("옹벽", "석축"):
                line(b[0], color="red", lw=0.9)
        for p in d["paddies"]:
            line(p, color="#1a8a4a", lw=0.8, ls="--")
        if site is not None:
            line(site, color="#0050ff", lw=2.2)
        ax.set_xlim(0, W); ax.set_ylim(0, W); ax.set_xticks([]); ax.set_yticks([])
        st = PP.stats if PP is not None else None
        ax.set_title(t + (f"\n평평한 단 {st['pads']}개 · {st['area'] / 1e4:.1f}ha" if st else ""), fontsize=16)
    fig.text(0.5, 0.015, "파란 선: 2m 등고선 · 빨간 선: 수치지도 옹벽·석축 · 초록 점선: 수치지도 논 · 보라: 지적선 · 굵은 파랑: 대지 경계 · 검정: 건물",
             ha="center", fontsize=13)
    plt.tight_layout(rect=(0, 0.035, 1, 1))
    plt.savefig(out, dpi=80)


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5],
         *(sys.argv[6:8] if len(sys.argv) > 6 else []))

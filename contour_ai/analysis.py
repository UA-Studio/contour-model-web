"""대지 분석: 경사 분석도, 절토·성토(원래 지형 ↔ 사업지 레벨 계획), 숫자 요약표.

결과(결과 폴더):
  분석_경사.png       원래 지형 경사 구간도 (기준 경사 이상은 파랑)
  분석_절성토.png     레벨 계획이 있을 때만: 깎는 곳(파랑)·쌓는 곳(회색) 깊이 지도
  분석_요약.csv       위 숫자 표 (엑셀에서 바로 열림, UTF-8 BOM)

수치는 1m 격자(대지가 아주 크면 2m)에서 계산한 개략값이다. 인허가 도서용 정밀 토량 계산이 아니다.
"""
import csv
import os

import numpy as np

BLUE = "#2563eb"
# 경사 구간(도). 17.5° 는 경기도 여러 시·군 개발행위허가 기준으로 쓰이는 값이지만 시·군 조례마다 다르다 → 설정값
SLOPE_BINS = (0.0, 5.0, 10.0, 15.0, 17.5, 25.0, 90.0)
SLOPE_GRAYS = ("#f4f4f4", "#dcdcdc", "#bdbdbd", "#9a9a9a", None, None)   # None = 기준 이상(파랑 계열)
FLAT = 0.3   # 계획 높이와 원래 높이 차이가 이보다 작으면 '그대로'로 본다(m)


def _font():
    """화면과 같은 Pretendard(프로그램에 들어 있는 글꼴)로 그림 글자를 쓴다."""
    from matplotlib import font_manager, rcParams
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    names = []
    for fn in ("Pretendard-Regular.ttf", "Pretendard-SemiBold.ttf"):
        p = os.path.join(here, fn)
        if os.path.exists(p):
            font_manager.fontManager.addfont(p)
            names.append(font_manager.FontProperties(fname=p).get_name())
    rcParams["font.family"] = names[:1] or ["Malgun Gothic", "AppleGothic", "NanumGothic", "sans-serif"]
    rcParams["axes.unicode_minus"] = False


def _grid(bbox, site, cell):
    b = site.buffer(30).bounds
    x0, y0 = max(bbox[0], b[0]), max(bbox[1], b[1])
    x1, y1 = min(bbox[2], b[2]), min(bbox[3], b[3])
    if (x1 - x0) * (y1 - y0) / cell ** 2 > 4e6:     # 400만 칸이 넘으면 격자를 키운다
        cell = float(np.ceil(np.sqrt((x1 - x0) * (y1 - y0) / 4e6)))
    xs = np.arange(x0 + cell / 2, x1, cell)
    ys = np.arange(y0 + cell / 2, y1, cell)
    X, Y = np.meshgrid(xs, ys)
    return X, Y, cell, (x0, y0, x1, y1)


def _eval(f, X, Y):
    out = np.empty(X.size)
    pts = np.column_stack([X.ravel(), Y.ravel()])
    for i in range(0, len(pts), 200000):
        out[i:i + 200000] = f(pts[i:i + 200000])
    return out.reshape(X.shape)


def _slope_deg(Z, cell):
    gy, gx = np.gradient(Z, cell)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def _bins(slope, inside, cell, limit):
    edges = sorted(set(SLOPE_BINS[:-1]) | {limit}) + [90.0]
    rows = []
    tot = inside.sum() * cell ** 2
    for a, b in zip(edges[:-1], edges[1:]):
        m = inside & (slope >= a) & (slope < b)
        area = m.sum() * cell ** 2
        rows.append(dict(a=a, b=b, area=area, pct=100 * area / max(tot, 1e-9), over=a >= limit))
    return rows


def _bin_color(row, k_over, n_over):
    if row["over"]:
        # 기준 이상: 연한 파랑 → 진한 파랑
        t = (k_over + 1) / max(n_over, 1)
        c0, c1 = np.array([0.75, 0.82, 0.98]), np.array([0.145, 0.388, 0.922])
        return tuple(c0 + (c1 - c0) * t)
    g = 0.96 - 0.11 * row["i"]
    return (g, g, g)


def _scalebar(ax, ext):
    x0, y0, x1, y1 = ext
    span = x1 - x0
    L = next(v for v in (500, 200, 100, 50, 20, 10) if v <= span / 4)
    px, py = x0 + span * 0.04, y0 + (y1 - y0) * 0.04
    ax.plot([px, px + L], [py, py], color="#111", lw=2.2, solid_capstyle="butt", zorder=20)
    ax.text(px + L / 2, py + (y1 - y0) * 0.012, f"{L} m", ha="center", va="bottom", fontsize=8, color="#111", zorder=20)
    nx, ny = x1 - span * 0.05, y1 - (y1 - y0) * 0.09
    ax.annotate("", xy=(nx, ny + (y1 - y0) * 0.05), xytext=(nx, ny),
                arrowprops=dict(arrowstyle="-|>", color="#111", lw=1.2), zorder=20)
    ax.text(nx, ny - (y1 - y0) * 0.01, "N", ha="center", va="top", fontsize=9, color="#111", zorder=20)


def _site_line(ax, site):
    from .contour_model import as_polys
    for p in as_polys(site):
        xs, ys = p.exterior.xy
        ax.plot(xs, ys, color=BLUE, lw=1.8, zorder=15)


def _outside_veil(ax, X, Y, inside, ext):
    veil = np.where(inside, np.nan, 1.0)
    ax.imshow(veil, extent=(ext[0], ext[2], ext[1], ext[3]), origin="lower", cmap="gray", vmin=0, vmax=1,
              alpha=0.62, interpolation="nearest", zorder=5)


def _contours(ax, X, Y, Z, step=5.0):
    # 거의 평평한 곳에서 등고선이 자잘한 고리로 깨지지 않게 그림용으로만 살짝 고른다
    from scipy.ndimage import gaussian_filter
    nan = np.isnan(Z)
    Z = gaussian_filter(np.where(nan, np.nanmean(Z), Z), 1.5)
    Z[nan] = np.nan
    lo, hi = np.floor(np.nanmin(Z) / step) * step, np.ceil(np.nanmax(Z) / step) * step
    lv = np.arange(lo, hi + step, step)
    if len(lv) > 1:
        ax.contour(X, Y, Z, levels=lv, colors="#6b6b6b", linewidths=0.35, zorder=6)


def _panel(fig, title, sub, rows, notes):
    """오른쪽 숫자 칸: 제목, 범례+면적, 메모"""
    ax = fig.add_axes([0.66, 0.06, 0.31, 0.88]); ax.set_axis_off()
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0, 0.985, title, fontsize=15, fontweight="semibold", va="top", color="#111")
    ax.text(0, 0.935, sub, fontsize=8.5, va="top", color="#666")
    y = 0.86
    for color, label, val, pct in rows:
        ax.add_patch(__import__("matplotlib").patches.Rectangle((0, y - 0.022), 0.06, 0.03, color=color, ec="#bbb", lw=0.4))
        ax.text(0.09, y - 0.007, label, fontsize=9.5, va="center", color="#111")
        ax.text(0.80, y - 0.007, val, fontsize=9.5, va="center", ha="right", color="#111")
        if pct is not None:
            ax.text(1.0, y - 0.007, pct, fontsize=9, va="center", ha="right", color="#666")
        y -= 0.052
    y -= 0.02
    ax.plot([0, 1], [y, y], color="#ddd", lw=0.8)
    y -= 0.035
    for k, v in notes:
        if k is None:
            ax.text(0, y, v, fontsize=7.5, va="top", color="#888", wrap=True); y -= 0.06; continue
        ax.text(0, y, k, fontsize=9.5, va="top", color="#555")
        ax.text(1.0, y, v, fontsize=10.5, va="top", ha="right", color="#111", fontweight="semibold")
        y -= 0.046


def site_analysis(out_dir, bbox, site, orig_f, plan_f=None, slope_limit=17.5, cell=1.0, log=print):
    """대지 경사·절토·성토 분석. orig_f: 원래 지형 높이 함수, plan_f: 레벨 계획을 넣은 높이 함수(없으면 None).
    반환: summary.json 에 넣을 dict"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import shapely
    _font()
    X, Y, cell, ext = _grid(bbox, site, cell)
    inside = shapely.contains_xy(site, X, Y)
    if not inside.any():
        log("   (대지 분석: 대지가 모델 범위 밖이라 건너뜁니다)")
        return None
    A = cell ** 2
    Zo = _eval(orig_f, X, Y)
    So = _slope_deg(Zo, cell)
    rows = _bins(So, inside, cell, slope_limit)
    for i, r in enumerate(rows):
        r["i"] = i
    over = [r for r in rows if r["over"]]
    colors = [_bin_color(r, over.index(r) if r["over"] else 0, len(over)) for r in rows]
    site_area = inside.sum() * A
    over_area = sum(r["area"] for r in over)
    zin = Zo[inside]
    res = dict(cell_m=cell, site_area_m2=int(round(float(site_area))), slope_limit_deg=slope_limit,
               slope_mean_deg=round(float(So[inside].mean()), 1), slope_max_deg=round(float(So[inside].max()), 1),
               over_limit_m2=int(round(float(over_area))), over_limit_pct=round(float(100 * over_area / site_area), 1),
               ground_min=round(float(zin.min()), 2), ground_max=round(float(zin.max()), 2),
               ground_mean=round(float(zin.mean()), 2),
               slope_bins=[dict(range=f"{r['a']:g}~{r['b']:g}°" if r["b"] < 90 else f"{r['a']:g}° 이상",
                                m2=int(round(float(r["area"]))), pct=round(float(r["pct"]), 1)) for r in rows])

    # --- 경사 분석도
    from matplotlib.colors import ListedColormap, BoundaryNorm
    edges = [r["a"] for r in rows] + [90.0]
    cmap, norm = ListedColormap(colors), BoundaryNorm(edges, len(colors))
    fig = plt.figure(figsize=(15, 15 * 0.62), dpi=150, facecolor="white")
    ax = fig.add_axes([0.02, 0.04, 0.62, 0.92]); ax.set_axis_off()
    ax.imshow(So, extent=(ext[0], ext[2], ext[1], ext[3]), origin="lower", cmap=cmap, norm=norm,
              interpolation="nearest", zorder=1)
    _contours(ax, X, Y, Zo)
    _outside_veil(ax, X, Y, inside, ext)
    _site_line(ax, site)
    _scalebar(ax, ext)
    ax.set_xlim(ext[0], ext[2]); ax.set_ylim(ext[1], ext[3]); ax.set_aspect("equal")
    legend = [(c, (f"{r['a']:g} ~ {r['b']:g}°" if r["b"] < 90 else f"{r['a']:g}° 이상"),
               f"{r['area']:,.0f} ㎡", f"{r['pct']:.1f}%") for c, r in zip(colors, rows)]
    _panel(fig, "경사 분석", f"원래 지형 · 대지 {site_area:,.0f} ㎡ · {cell:g} m 격자 · 등고선 5 m",
           legend, [("평균 경사", f"{res['slope_mean_deg']:.1f}°"),
                    (f"{slope_limit:g}° 이상", f"{over_area:,.0f} ㎡ ({res['over_limit_pct']:.1f}%)"),
                    ("지반 높이", f"EL {res['ground_min']:.1f} ~ {res['ground_max']:.1f}"),
                    (None, f"기준 경사 {slope_limit:g}°는 참고값입니다. 개발행위허가 경사 기준은 시·군 조례마다 다릅니다.")])
    p1 = os.path.join(out_dir, "분석_경사.png")
    fig.savefig(p1, facecolor="white"); plt.close(fig)
    files = [p1]

    # --- 절토·성토
    if plan_f is not None:
        Zp = _eval(plan_f, X, Y)
        d = np.where(inside, Zp - Zo, 0.0)
        cut = float(-d[d < -FLAT].sum() * A)
        fill = float(d[d > FLAT].sum() * A)
        cut_a = float((d < -FLAT).sum() * A)
        fill_a = float((d > FLAT).sum() * A)
        Sp = _slope_deg(Zp, cell)
        res.update(cut_m3=round(cut), fill_m3=round(fill), net_m3=round(fill - cut),
                   cut_area_m2=round(cut_a), fill_area_m2=round(fill_a),
                   keep_area_m2=int(round(float(site_area) - cut_a - fill_a)),
                   cut_max_m=round(float(-d.min()), 1) if d.min() < 0 else 0.0,
                   fill_max_m=round(float(d.max()), 1) if d.max() > 0 else 0.0,
                   plan_over_limit_m2=round(float((inside & (Sp >= slope_limit)).sum() * A)))
        # 깊이 구간: 깎기 파랑, 쌓기 회색
        dep = (-20, -10, -5, -2, -FLAT, FLAT, 2, 5, 10, 20)
        lo, hi = min(-20.0, float(d.min()) - 1), max(20.0, float(d.max()) + 1)
        e = [lo] + list(dep) + [hi]
        blues = ["#1e3a8a", "#1d4ed8", BLUE, "#60a5fa", "#bfdbfe"]
        grays = ["#e5e5e5", "#c4c4c4", "#9e9e9e", "#737373", "#4a4a4a"]
        cols = blues + ["#ffffff"] + grays
        cm2, nm2 = ListedColormap(cols), BoundaryNorm(e, len(cols))
        fig = plt.figure(figsize=(15, 15 * 0.62), dpi=150, facecolor="white")
        ax = fig.add_axes([0.02, 0.04, 0.62, 0.92]); ax.set_axis_off()
        ax.imshow(np.where(inside, d, np.nan), extent=(ext[0], ext[2], ext[1], ext[3]), origin="lower",
                  cmap=cm2, norm=nm2, interpolation="nearest", zorder=1)
        ax.imshow(np.where(inside, np.nan, 1.0), extent=(ext[0], ext[2], ext[1], ext[3]), origin="lower",
                  cmap=ListedColormap(["#f2f2f2"]), interpolation="nearest", zorder=0)
        _contours(ax, X, Y, np.where(inside, Zp, np.nan))
        _site_line(ax, site)
        _scalebar(ax, ext)
        ax.set_xlim(ext[0], ext[2]); ax.set_ylim(ext[1], ext[3]); ax.set_aspect("equal")

        def ar(m):
            return f"{m.sum() * A:,.0f} ㎡"
        labels = []
        names = ["깎기 20 m 이상", "깎기 10~20 m", "깎기 5~10 m", "깎기 2~5 m", f"깎기 {FLAT:g}~2 m", "그대로",
                 f"쌓기 {FLAT:g}~2 m", "쌓기 2~5 m", "쌓기 5~10 m", "쌓기 10~20 m", "쌓기 20 m 이상"]
        for k, (c, n) in enumerate(zip(cols, names)):
            m = inside & (d >= e[k]) & (d < e[k + 1])
            if m.sum() or n == "그대로":
                labels.append((c, n, ar(m), f"{100 * m.sum() * A / site_area:.1f}%"))
        _panel(fig, "절토 · 성토", f"레벨 계획 − 원래 지형 · 대지 {site_area:,.0f} ㎡ · 등고선 5 m(계획)",
               labels, [("깎는 흙", f"{cut:,.0f} ㎥"), ("쌓는 흙", f"{fill:,.0f} ㎥"),
                        ("남는 흙" if cut >= fill else "모자란 흙", f"{abs(fill - cut):,.0f} ㎥"),
                        ("가장 깊게 깎는 곳", f"{res['cut_max_m']:.1f} m"),
                        ("가장 높게 쌓는 곳", f"{res['fill_max_m']:.1f} m"),
                        (None, f"{cell:g} m 격자 개략값입니다. 흙의 부풀음·다짐 비율은 넣지 않았습니다.")])
        p2 = os.path.join(out_dir, "분석_절성토.png")
        fig.savefig(p2, facecolor="white"); plt.close(fig)
        files.append(p2)

    # --- 숫자 표
    p3 = os.path.join(out_dir, "분석_요약.csv")
    with open(p3, "w", encoding="utf-8-sig", newline="") as fh:
        w_ = csv.writer(fh)
        w_.writerow(["구분", "항목", "값", "단위"])
        w_.writerow(["대지", "면적", res["site_area_m2"], "㎡"])
        w_.writerow(["대지", "지반 높이 최저", res["ground_min"], "m"])
        w_.writerow(["대지", "지반 높이 최고", res["ground_max"], "m"])
        w_.writerow(["대지", "지반 높이 평균", res["ground_mean"], "m"])
        w_.writerow(["경사", "평균 경사", res["slope_mean_deg"], "°"])
        w_.writerow(["경사", f"{slope_limit:g}° 이상 면적", res["over_limit_m2"], "㎡"])
        w_.writerow(["경사", f"{slope_limit:g}° 이상 비율", res["over_limit_pct"], "%"])
        for b in res["slope_bins"]:
            w_.writerow(["경사 구간", b["range"], b["m2"], "㎡"])
        if "cut_m3" in res:
            for k, n, u in (("cut_m3", "깎는 흙", "㎥"), ("fill_m3", "쌓는 흙", "㎥"), ("net_m3", "쌓기−깎기 (−면 흙이 남음)", "㎥"),
                            ("cut_area_m2", "깎는 면적", "㎡"), ("fill_area_m2", "쌓는 면적", "㎡"),
                            ("keep_area_m2", "그대로 두는 면적", "㎡"), ("cut_max_m", "가장 깊게 깎는 깊이", "m"),
                            ("fill_max_m", "가장 높게 쌓는 높이", "m"),
                            ("plan_over_limit_m2", f"계획 후 {slope_limit:g}° 이상 면적", "㎡")):
                w_.writerow(["절토·성토", n, res[k], u])
        w_.writerow(["참고", "격자", cell, "m"])
    files.append(p3)
    res["files"] = [os.path.basename(f) for f in files]
    msg = f"   대지 분석: 평균 경사 {res['slope_mean_deg']:.1f}°, {slope_limit:g}° 이상 {res['over_limit_pct']:.1f}%"
    if "cut_m3" in res:
        msg += f" · 깎기 {res['cut_m3']:,}㎥ · 쌓기 {res['fill_m3']:,}㎥"
    log(msg + " (분석_경사.png" + (" · 분석_절성토.png" if plan_f is not None else "") + " · 분석_요약.csv)")
    return res

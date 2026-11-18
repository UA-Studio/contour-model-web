"""위성영상(Sentinel-2)으로 녹지를 찾아 나무로 채운다.

1) fetch: AWS 공개 버킷의 Sentinel-2 L2A 영상 중 잎이 무성한 계절(5~9월)의
   맑은 장면을 골라 NDVI(식생지수) 최댓값 합성을 만든다. 10m 해상도.
   python vegetation.py fetch --bbox xmin ymin xmax ymax --tile 52SCG --year 2025 --out ndvi.npz
2) contour_model.py --ndvi ndvi.npz 로 넘기면 녹지를 나무로 채운다.

녹지 판정: NDVI ≥ 0.5 이고 건물·길이 아닌 곳.
  NDVI ≥ 0.65 이고 넓은 곳 → 숲(촘촘, 큰 나무), 그 밖 → 정원·단지 조경(드문드문)
"""
import argparse
import math
import json
import os
import urllib.request

import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

S3 = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/"
PREFIX = "sentinel-s2-l2a-cogs"

FOREST = dict(ndvi=0.65, min_area=600, spacing=4.5, r=(2.2, 3.4))
GARDEN = dict(ndvi=0.50, min_area=40, spacing=7.0, r=(1.6, 2.6))


# ---------------------------------------------------------------- 영상 받기
def _list(prefix):
    import re
    url = f"{S3}?list-type=2&prefix={prefix}&delimiter=/"
    xml = urllib.request.urlopen(url, timeout=60).read().decode()
    return re.findall(r"<Prefix>([^<]+)</Prefix>", xml)[1:]


def fetch_ndvi(bbox, tile, year, months=(5, 6, 7, 8, 9), cell=2.0, max_cloud=30, out=None,
               crs="EPSG:5186", log=print):
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    # 윈도우용 GDAL 은 인증서 묶음을 못 찾아 https 영상을 못 여는 경우가 있다 → certifi 묶음을 알려 준다
    try:
        import certifi
        for k in ("CURL_CA_BUNDLE", "GDAL_CURL_CA_BUNDLE"):
            if not os.environ.get(k):
                os.environ[k] = certifi.where()
    except ImportError:
        pass
    zone, band, sq = str(int(tile[:2])), tile[2], tile[3:]   # S3 경로는 앞 0 없이 (04 → 4)
    pad = 100
    x0, y0, x1, y1 = bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad
    W, H = int((x1 - x0) / cell), int((y1 - y0) / cell)
    T = from_origin(x0, y1, cell, cell)

    def get(path, rs=Resampling.bilinear, bands=(1,)):
        with rasterio.open("/vsicurl/" + path) as ds:
            out_ = np.zeros((len(bands), H, W), np.float32)
            for i, b in enumerate(bands):
                reproject(rasterio.band(ds, b), out_[i], src_transform=ds.transform, src_crs=ds.crs,
                          dst_transform=T, dst_crs=crs, resampling=rs)
        return out_ if len(bands) > 1 else out_[0]
    stack, used, tci = [], [], None
    for m in months:
        log(f"   {year}년 {m}월 장면 찾는 중")
        for sc in _list(f"{PREFIX}/{zone}/{band}/{sq}/{year}/{m}/"):
            name = sc.rstrip("/").split("/")[-1]
            try:
                cc = json.load(urllib.request.urlopen(f"{S3}{sc}{name}.json", timeout=60))["properties"]["eo:cloud_cover"]
            except Exception:
                continue
            if cc > max_cloud:
                continue
            scl = get(S3 + sc + "SCL.tif", Resampling.nearest)
            ok = np.isin(scl, [4, 5, 6, 7])
            if ok.mean() < 0.8:
                continue
            r, n = get(S3 + sc + "B04.tif"), get(S3 + sc + "B08.tif")
            nd = (n - r) / (n + r + 1e-6)
            nd[~ok] = np.nan
            stack.append(nd); used.append(name)
            log(f"   위성영상 {name} 사용 (구름 {cc:.0f}%)")
            if tci is None:
                tci = get(S3 + sc + "TCI.tif", bands=(1, 2, 3)).astype(np.uint8)
    if not stack:
        raise SystemExit("맑은 장면을 찾지 못했습니다")
    ndvi = np.nanmax(np.stack(stack), axis=0)
    if out:
        np.savez_compressed(out, ndvi=ndvi, tci=tci, x0=x0, y1=y1, cell=cell, scenes=np.array(used))
    return ndvi


def load_ndvi(path):
    d = np.load(path)
    ndvi, x0, y1, cell = d["ndvi"], float(d["x0"]), float(d["y1"]), float(d["cell"])
    ys = y1 - (np.arange(ndvi.shape[0]) + 0.5) * cell
    xs = x0 + (np.arange(ndvi.shape[1]) + 0.5) * cell
    return xs, ys, ndvi


# ---------------------------------------------------------------- 녹지 영역
def _filled(xs, ys, Z, level):
    import contourpy
    from shapely import make_valid
    X, Y = np.meshgrid(xs, ys)
    gen = contourpy.contour_generator(X, Y, np.nan_to_num(Z, nan=0), fill_type="OuterOffset")
    pts, offs = gen.filled(level, np.inf)
    polys = []
    for P, O in zip(pts, offs):
        rings = [P[O[i]:O[i + 1]] for i in range(len(O) - 1)]
        if len(rings[0]) >= 4:
            polys.append(make_valid(Polygon(rings[0], [r for r in rings[1:] if len(r) >= 4])))
    return unary_union(polys) if polys else Polygon()


def green_areas(ndvi_path, bbox, blocked):
    """반환: {"forest": geom, "garden": geom} — 건물·길(blocked)을 뺀 녹지."""
    from scipy.ndimage import gaussian_filter
    xs, ys, nd = load_ndvi(ndvi_path)
    nd = gaussian_filter(np.nan_to_num(nd, nan=0), 0.8)
    clip = box(*bbox)
    out = {}
    taken = blocked
    for key, cfg in (("forest", FOREST), ("garden", GARDEN)):
        g = _filled(xs, ys, nd, cfg["ndvi"]).intersection(clip).difference(taken)
        g = g.buffer(-0.5).buffer(0.5)
        g = unary_union([p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon) and p.area >= cfg["min_area"]])
        out[key] = g
        taken = unary_union([taken, g])
    return out


def scatter_trees(area, spacing, rrange, seed=0, avoid=()):
    """영역 안에 지터 격자로 나무를 흩뿌린다. 반환: [(x, y, 크기배율)]"""
    from shapely import contains_xy, prepare
    if area.is_empty:
        return []
    rng = np.random.default_rng(seed)
    x0, y0, x1, y1 = area.bounds
    gx = np.arange(x0, x1, spacing)
    gy = np.arange(y0, y1, spacing * 0.866)
    pts = []
    for j, y in enumerate(gy):
        off = spacing / 2 if j % 2 else 0
        for x in gx:
            pts.append((x + off + rng.uniform(-0.35, 0.35) * spacing, y + rng.uniform(-0.35, 0.35) * spacing))
    pts = np.array(pts)
    inner = area.buffer(-0.8)
    prepare(inner)
    keep = contains_xy(inner, pts[:, 0], pts[:, 1])
    pts = pts[keep]
    if len(avoid):
        av = np.asarray([(a[0], a[1]) for a in avoid])
        from scipy.spatial import cKDTree
        d, _ = cKDTree(av).query(pts)
        pts = pts[d > spacing * 0.7]
    r = rng.uniform(*rrange, len(pts))
    return [(x, y, s) for (x, y), s in zip(pts, r)]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--bbox", nargs=4, type=float, required=True)
    f.add_argument("--tile", default="52SCG", help="Sentinel-2 MGRS 타일 (서울 52SCG)")
    f.add_argument("--year", type=int, default=2025)
    f.add_argument("--out", default="ndvi.npz")
    a = ap.parse_args()
    fetch_ndvi(tuple(a.bbox), a.tile, a.year, out=a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- 녹지 출처 더하기
# 위성(Sentinel-2, 10m)만으로는 받지 못하거나 거칠 때가 있어 출처를 여럿 겹친다.
#   1) 수치지도 식생 면(경지계·지류계 등)의 구분이 수림지·조림지·임야 …  → 숲, 과수원·묘포지 → 조경
#   2) 고화질 항공사진(0.3m)에서 질감 있는 초록(수관) 덩어리 → 넓으면 숲, 좁으면 조경
#   3) 위성 NDVI (위 green_areas)
MAP_FOREST = ("수림", "조림", "임야", "산림", "삼림", "숲", "죽림", "대나무")
MAP_GARDEN = ("과수", "묘포", "수목원", "조경")
MAP_BARE = ("논", "밭", "초지", "목장", "잔디", "골프")   # 항공사진 초록이어도 나무가 아님


def map_green_areas(folder, bbox):
    """수치지도 면 레이어(식생 D 계열 우선) 중 구분·종류·명칭이 숲/과수원인 면. 반환 {"forest","garden","bare"}"""
    import glob
    import shapefile
    from shapely.geometry import shape
    clip = box(*bbox)
    got = {"forest": [], "garden": [], "bare": []}
    for f in sorted(glob.glob(os.path.join(folder, "N3A_*.shp"))):
        base = os.path.basename(f)
        if not base.startswith("N3A_D") and not base.startswith("N3A_C"):
            continue
        try:
            r = shapefile.Reader(f[:-4], encoding="euc-kr", encodingErrors="replace")
            names = [x[0] for x in r.fields[1:]]
            keys = [k for k in ("구분", "종류", "용도", "명칭", "지류") if k in names]
            if not keys:
                r.close(); continue
            for sr in r.iterShapeRecords():
                rec = sr.record.as_dict()
                txt = " ".join(str(rec.get(k) or "") for k in keys)
                kind = ("forest" if any(w in txt for w in MAP_FOREST) else
                        "garden" if any(w in txt for w in MAP_GARDEN) else
                        "bare" if base.startswith("N3A_D") and any(w in txt for w in MAP_BARE) else None)
                if kind is None:
                    continue
                try:
                    g = shape(sr.shape.__geo_interface__).buffer(0)
                except Exception:
                    continue
                if g.intersects(clip):
                    got[kind].append(g.intersection(clip))
            r.close()
        except Exception:
            continue
    return {k: unary_union(v) if v else Polygon() for k, v in got.items()}


def canopy_mask(rgb, cell, exg_min=None, bright_min=12, tex_min=6.5, cv_s=0.05, cv_l=0.09):
    """항공사진 → 나무 수관 화소(bool). v1.8 강화판.

    1. 초록 판정을 셋으로 겹쳐 본다: 초록 색도(ExG) + 색상(초록~청록 55~170°)·채도 + 동네별 문턱(Otsu).
    2. 그늘진 수관: 어둡지만 초록이 빨강보다 크고, 밝은 잎 2m 안에 붙은 화소는 넣는다.
    3. 잔디·논밭·인조잔디: 매끈하다(초록 채널 국소 표준편차가 작다) → 뺀다.
    4. 칠한 초록(우레탄 코트·초록 지붕·방수층): 매끈하고 채도가 높은 덩어리 → 통째로 뺀다.
    5. 잡티 정리: 열고 닫기, 4㎡ 미만 조각 버림.
    """
    from scipy import ndimage
    f = rgb.astype(float)
    R, G, B = f[..., 0], f[..., 1], f[..., 2]
    ssum = f.sum(axis=2) + 1e-6
    exg = (2 * G - R - B) / ssum
    bright = ssum / 3
    mx, mn = f.max(axis=2), f.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)
    d = np.maximum(mx - mn, 1e-6)
    hue = np.where(mx == G, 60 * ((B - R) / d) + 120, np.where(mx == R, (60 * ((G - B) / d)) % 360, 60 * ((R - G) / d) + 240))
    valid = rgb.max(axis=2) > 0
    if exg_min is None:                       # 동네·계절마다 다른 문턱: 초록 색도의 Otsu
        v = exg[valid & (bright > bright_min)]
        exg_min = 0.04
        if v.size > 1000:
            hist, edges = np.histogram(np.clip(v, -0.2, 0.5), 128)
            c = (edges[:-1] + edges[1:]) / 2
            w0 = np.cumsum(hist); w1 = w0[-1] - w0
            m0 = np.cumsum(hist * c) / np.maximum(w0, 1); m1 = (np.sum(hist * c) - np.cumsum(hist * c)) / np.maximum(w1, 1)
            k = int(np.argmax(w0 * w1 * (m0 - m1) ** 2))
            exg_min = float(np.clip(c[k], 0.025, 0.09))
    greenish = (hue > 55) & (hue < 170) & (sat > 0.08)
    leaf = valid & (exg > exg_min) & greenish & (bright > bright_min)
    # 그늘진 잎
    near = ndimage.binary_dilation(leaf, iterations=max(1, int(round(2.0 / cell))))
    shade = valid & (bright <= np.percentile(bright[valid], 30) if valid.any() else False) & (G > R * 1.02) & (G >= B * 0.92) & near
    cand = leaf | shade
    # ---- 숲(나무) / 풀(잔디·초지) 가르기: 4m 칸마다 다섯 가지 단서로 투표
    #  나무 수관은 ① 잎 덩어리마다 밝고 어두운 얼룩(작은 질감)이 크고 ② 수관 사이·옆에 그늘이 있고
    #  ③ 더 짙은 초록(색상 각이 큼)이며 ④ 같은 사진의 초록 중 어둡고 ⑤ 칸 안 밝기 차이(큰 질감)가 크다.
    #  잔디·초지는 매끈하고 밝은 연두, 그늘이 없다.
    win = max(3, int(round(1.2 / cell)) | 1)
    mean = ndimage.uniform_filter(G, win)
    tex = np.sqrt(np.maximum(ndimage.uniform_filter(G * G, win) - mean * mean, 0))
    gb_med = float(np.median(bright[leaf])) if leaf.any() else 0.0
    k = max(2, int(round(4.0 / cell)))
    H_, W_ = (cand.shape[0] // k) * k, (cand.shape[1] // k) * k

    def blocks(a):
        return a[:H_, :W_].reshape(H_ // k, k, W_ // k, k)
    cg = blocks(cand).astype(float)
    n_g = cg.sum(axis=(1, 3))
    gfrac = n_g / (k * k)
    with np.errstate(invalid="ignore", divide="ignore"):
        texS = (blocks(tex) * cg).sum(axis=(1, 3)) / n_g
        mG = (blocks(G) * cg).sum(axis=(1, 3)) / n_g
        texL = np.sqrt(np.maximum((blocks(G * G) * cg).sum(axis=(1, 3)) / n_g - mG ** 2, 0))
        hueM = (blocks(np.where(leaf, hue, 0)) * blocks(leaf)).sum(axis=(1, 3)) / np.maximum(blocks(leaf).sum(axis=(1, 3)), 1)
        brM = (blocks(bright) * cg).sum(axis=(1, 3)) / n_g
    # 그늘: 잎 밝기 중앙값의 60% 아래로 떨어지는 화소(수관 그늘·수관 사이 틈). 잔디밭에는 거의 없다
    shadow = valid & (bright < 0.6 * gb_med) & ndimage.binary_dilation(cand, iterations=max(1, int(round(1.5 / cell))))
    darkF = blocks(shadow).mean(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        cvS, cvL = texS / np.maximum(mG, 1), texL / np.maximum(mG, 1)   # 밝기로 나눈 질감: 사진 밝기·계절에 덜 흔들림
    votes = ((cvS > cv_s).astype(int) + (darkF > 0.08) + (hueM > 95) + (brM < 0.9 * gb_med) + (cvL > cv_l))
    votes = np.where(gfrac > 0.2, votes, 0).astype(float)
    sm = ndimage.uniform_filter(votes, 3) * 9 / np.maximum(ndimage.uniform_filter((gfrac > 0.2).astype(float), 3) * 9, 1)
    tree_cell = (gfrac > 0.2) & ((votes >= 3) | ((votes == 2) & (sm >= 2.6)))
    grass_cell = (gfrac > 0.2) & ~tree_cell
    up = lambda c: np.pad(np.repeat(np.repeat(c, k, 0), k, 1), ((0, cand.shape[0] - H_), (0, cand.shape[1] - W_)))
    tree = cand & up(tree_cell)
    grass = leaf & up(grass_cell)
    info_cells = dict(tree_cells=int(tree_cell.sum()), grass_cells=int(grass_cell.sum()),
                      _dbg=dict(cvS=cvS, cvL=cvL, texS=texS, darkF=darkF, hueM=hueM, brM=brM, texL=texL, gb_med=gb_med, votes=votes, k=k, gfrac=gfrac))
    # 칠한 초록 덩어리 제거: 매끈(질감 낮음) + 채도 높음 + 넓음
    smooth = leaf & (tex <= tex_min * 0.6)
    lab, n = ndimage.label(smooth)
    if n:
        idx = np.arange(1, n + 1)
        area = ndimage.sum(np.ones_like(lab), lab, idx) * cell * cell
        msat = ndimage.mean(sat, lab, idx)
        mhue = ndimage.mean(hue, lab, idx)
        mtex = ndimage.mean(tex, lab, idx)
        # 우레탄·페인트 초록은 청록 쪽(색상 120° 넘음)이고 아주 매끈하다. 잔디는 연두(70~110°)라 남긴다
        bad = idx[(area > 30) & (msat > 0.30) & (mhue > 120) & (mtex < 2.5)]
        if len(bad):
            painted = ndimage.binary_dilation(np.isin(lab, bad), iterations=2)
            tree &= ~painted
            grass &= ~painted
    tree = ndimage.binary_opening(tree, iterations=1)
    tree = ndimage.binary_closing(tree, iterations=2)
    lab, n = ndimage.label(tree)
    if n:
        sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1)) * cell * cell
        tree &= np.isin(lab, np.arange(1, n + 1)[sizes >= 4.0])
    grass = ndimage.binary_opening(grass, iterations=2) & ~tree
    return tree, dict(exg_min=exg_min, grass=grass, **info_cells)


def detect_crowns(mask, cell, x0, y1, min_dist=3.2, rrange=(1.0, 6.0), rgb=None):
    """수관 마스크 → 나무 한 그루씩 [(x, y, 수관 반지름)].

    사진(rgb)이 있으면 숲처럼 수관이 붙은 덩어리 안에서도 한 그루씩 가른다: 나무 꼭대기는 해를 받아
    둘레보다 밝으므로, 1m 정도 흐린 밝기의 국소 최댓값(3m 안 가장 밝은 점)을 나무 꼭대기로 본다.
    반지름은 이웃 나무까지 거리의 반과 가장자리까지 거리 중 작은 쪽.
    사진이 없거나 봉우리가 없는 작은 덩어리는 가장자리에서 가장 먼 곳(거리 변환의 봉우리)에 한 그루."""
    from scipy import ndimage
    dist = ndimage.distance_transform_edt(mask) * cell
    size = max(3, int(round(min_dist / cell)) | 1)
    if rgb is not None:
        br = ndimage.gaussian_filter(rgb.astype(float).mean(axis=2), 0.8 / cell)
        br = np.where(mask, br, -1.0)
        peak = (br == ndimage.maximum_filter(br, size=size)) & mask & (dist >= 0.6)
    else:
        sm = ndimage.gaussian_filter(dist, 0.6 / cell)
        peak = (sm == ndimage.maximum_filter(sm, size=size)) & (dist >= rrange[0] * 0.6) & mask
    rr, cc = np.nonzero(peak)
    out = []
    seen = np.zeros(mask.shape, bool)
    nn = None
    if rgb is not None and len(rr) > 1:
        from scipy.spatial import cKDTree
        Q = np.c_[rr, cc] * cell
        nn = cKDTree(Q).query(Q, k=2)[0][:, 1]
    for i, (r, c) in enumerate(zip(rr, cc)):
        rad = dist[r, c] * 1.1 if nn is None else min(nn[i] * 0.55, max(dist[r, c], nn[i] * 0.5))
        rad = float(np.clip(rad, *rrange))
        out.append((x0 + (c + 0.5) * cell, y1 - (r + 0.5) * cell, rad))
        seen[r, c] = True
    lab, n = ndimage.label(mask)
    if n:
        has = ndimage.maximum(seen, lab, np.arange(1, n + 1))
        for k in np.nonzero(~has.astype(bool))[0]:
            ys, xs = np.nonzero(lab == k + 1)
            if len(xs) * cell * cell < 3:
                continue
            rad = float(np.clip(math.sqrt(len(xs) * cell * cell / math.pi), *rrange))
            out.append((x0 + (xs.mean() + 0.5) * cell, y1 - (ys.mean() + 0.5) * cell, rad))
    # 가까이 붙은 봉우리 정리
    if out:
        from scipy.spatial import cKDTree
        P = np.array([(a, b) for a, b, _ in out])
        order = np.argsort([-o[2] for o in out])
        alive = np.ones(len(out), bool)
        tr = cKDTree(P)
        for i in order:
            if not alive[i]:
                continue
            for j in tr.query_ball_point(P[i], min_dist * 0.8):
                if j != i and out[j][2] <= out[i][2]:
                    alive[j] = False
        out = [o for o, k in zip(out, alive) if k]
    return out


def aerial_green_areas(aerial_path, bbox, step=1.0, preview_path=None):
    """항공사진에서 나무 수관이 덮은 곳과 나무 한 그루씩의 위치.

    반환 {"forest": 넓은 수관 덩어리(≥600㎡), "garden": 작은 덩어리(≥40㎡), "canopy": 수관 면 전체,
          "crowns": [(x, y, r)]}
    """
    from scipy import ndimage
    from .aerial import load
    rgb, x0, y1, cell, _ = load(aerial_path)
    tree, info = canopy_mask(rgb, cell)
    crowns = [c for c in detect_crowns(tree, cell, x0, y1, rgb=rgb)
              if bbox[0] <= c[0] <= bbox[2] and bbox[1] <= c[1] <= bbox[3]]
    k = max(1, int(round(step / cell)))
    h, w = (tree.shape[0] // k) * k, (tree.shape[1] // k) * k
    frac = tree[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))
    frac = ndimage.gaussian_filter(frac, 1.0 / step)
    cs = cell * k
    ys = y1 - (np.arange(frac.shape[0]) + 0.5) * cs
    xs = x0 + (np.arange(frac.shape[1]) + 0.5) * cs
    g = _filled(xs, ys, frac, 0.5).intersection(box(*bbox))
    polys = [p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon)]
    gr = info["grass"][:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))
    gg = _filled(xs, ys, ndimage.gaussian_filter(gr, 1.0 / step), 0.5).intersection(box(*bbox))
    grass = unary_union([p for p in getattr(gg, "geoms", [gg]) if isinstance(p, Polygon) and p.area >= 20] or [Polygon()])
    if preview_path:
        try:
            _green_preview(preview_path, rgb, tree, crowns, x0, y1, cell, info["grass"])
        except Exception:
            pass
    return {"forest": unary_union([p for p in polys if p.area >= 600]) if polys else Polygon(),
            "garden": unary_union([p for p in polys if 40 <= p.area < 600]) if polys else Polygon(),
            "canopy": unary_union(polys) if polys else Polygon(), "crowns": crowns, "exg_min": info["exg_min"],
            "grass": grass}


def _green_preview(path, rgb, mask, crowns, x0, y1, cell, grass=None, max_px=1600):
    """확인 그림: 사진 위에 숲 수관(진초록), 풀(노랑), 나무 자리(빨간 원)."""
    from PIL import Image, ImageDraw
    im = rgb.astype(float) * 0.75
    im[mask] = im[mask] * 0.4 + np.array([20, 150, 40]) * 0.6
    if grass is not None:
        im[grass] = im[grass] * 0.4 + np.array([235, 215, 60]) * 0.6
    img = Image.fromarray(np.clip(im, 0, 255).astype(np.uint8))
    sc = min(1.0, max_px / max(img.size))
    if sc < 1:
        img = img.resize((int(img.size[0] * sc), int(img.size[1] * sc)))
    d = ImageDraw.Draw(img)
    for x, y, r in crowns:
        c, rr = (x - x0) / cell * sc, (y1 - y) / cell * sc
        rp = max(2, r / cell * sc)
        d.ellipse([c - rp, rr - rp, c + rp, rr + rp], outline=(230, 40, 40))
    img.save(path)


def combine_greens(sources, blocked, bare=None):
    """출처별 {"forest","garden"} 를 합친다. 숲이 조경보다 먼저, 건물·길(blocked)과 논밭(bare)은 뺀다."""
    forest = unary_union([s["forest"] for s in sources if not s["forest"].is_empty] or [Polygon()])
    garden = unary_union([s["garden"] for s in sources if not s["garden"].is_empty] or [Polygon()])
    out = {}
    taken = blocked if bare is None or bare.is_empty else unary_union([blocked, bare])
    for key, g, cfg in (("forest", forest, FOREST), ("garden", garden, GARDEN)):
        g = g.difference(taken).buffer(-0.5).buffer(0.5)
        g = unary_union([p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon) and p.area >= cfg["min_area"]]
                        or [Polygon()])
        out[key] = g
        taken = unary_union([taken, g])
    return out


def spacing_for(area, spacing, max_trees):
    """나무가 max_trees 를 넘지 않게 간격을 넓힌다 (육각 격자 한 그루 넓이 = 0.866·s²)."""
    if area <= 0:
        return spacing
    return max(spacing, math.sqrt(area / (0.866 * max_trees)))

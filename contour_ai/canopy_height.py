"""Meta·WRI 1m 나무 높이 지도로 숲 나무를 한 그루씩 세운다.

자료: Meta "High Resolution Canopy Height Maps" v2 (DINOv3, 2025), CC BY 4.0.
      AWS 공개 버킷 dataforgood-fb-data, 확대 10단계 쿼드키 GeoTIFF(EPSG:3857, 약 1.2m, 값 = 나무 높이 m).
      인증키 없이 받는다. 출처 표시: "Meta and World Resources Institute (WRI), High Resolution Canopy Height Maps".

주의: 영상이 몇 년 전 것이라 그 뒤 개발된 곳(새 도로·단지)도 숲으로 남아 있다.
      그래서 이 지도만으로 숲을 정하지 않고, 지금 위성·항공사진이 녹지라고 한 곳 안에서만
      나무 자리·높이를 고르는 데 쓴다.
"""
import math
import os

import numpy as np

BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/"
ZOOM = 10
CELL = 1.0
SOURCE = "Meta and World Resources Institute (WRI), High Resolution Canopy Height Maps v2 (CC BY 4.0)"


def _tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _quadkey(x, y, z):
    s = ""
    for i in range(z, 0, -1):
        m = 1 << (i - 1)
        s += str((1 if x & m else 0) + (2 if y & m else 0))
    return s


def fetch_chm(bbox, crs_wkt, out, log=print):
    """모델 범위의 나무 높이(m)를 1m 격자로 받아 npz 로 저장. 반환 out. 받지 못하면 예외."""
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    try:
        import certifi
        for k in ("CURL_CA_BUNDLE", "GDAL_CURL_CA_BUNDLE"):
            if not os.environ.get(k):
                os.environ[k] = certifi.where()
    except ImportError:
        pass
    to_ll = Transformer.from_crs(crs_wkt, 4326, always_xy=True)
    lon, lat = to_ll.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    x0, y0 = _tile(min(lon), max(lat), ZOOM)
    x1, y1 = _tile(max(lon), min(lat), ZOOM)
    W, H = int(round((bbox[2] - bbox[0]) / CELL)), int(round((bbox[3] - bbox[1]) / CELL))
    T = from_origin(bbox[0], bbox[3], CELL, CELL)
    chm = np.zeros((H, W), np.float32)
    got = 0
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            url = "/vsicurl/" + BASE + _quadkey(tx, ty, ZOOM) + ".tif"
            try:
                with rasterio.open(url) as ds:
                    part = np.zeros((H, W), np.float32)
                    reproject(rasterio.band(ds, 1), part, src_transform=ds.transform, src_crs=ds.crs,
                              dst_transform=T, dst_crs=crs_wkt, resampling=Resampling.bilinear)
            except Exception:
                continue          # 바다 등 자료가 없는 칸
            chm = np.maximum(chm, part)
            got += 1
    if not got:
        raise RuntimeError("나무 높이 지도를 받지 못했습니다")
    np.savez_compressed(out, chm=chm, x0=bbox[0], y1=bbox[3], cell=CELL, source=SOURCE)
    log(f"   나무 높이 지도(Meta 1m) {got}칸 받음")
    return out


def load_chm(path):
    d = np.load(path)
    return d["chm"], float(d["x0"]), float(d["y1"]), float(d["cell"])


def crown_r_for_height(h):
    """나무 높이(m) → 수관 가로 반지름(m). paths_trees.tree_dims 의 꼭대기 높이 = 1.8 + 3.5·r 를 거꾸로 푼 것."""
    return (h - 1.8) / 3.5


def trees_from_chm(path, zone, min_h=4.0, step=2.5, gap=0.75, max_trees=30000, seed=3):
    """나무 높이 지도에서 zone(지금 녹지인 곳) 안에 나무를 세운다.

    이 지도는 1m지만 값이 정수(m)이고 수관끼리 붙어 있어 한 그루씩 꼭대기를 가르기 어렵다.
    그래서 꼭대기 찾기 대신, 높이에 맞는 간격으로 나무를 둔다:
    1. step 간격 지터 격자의 후보 중 높이 min_h 이상인 곳만, 큰 나무부터.
    2. 그 자리 높이 → 수관 반지름. 두 나무 사이가 gap·(r1 + r2)보다 가까우면 뒤 나무는 버린다.
       → 큰 나무는 듬성듬성, 낮은 숲은 촘촘, 높이 0인 빈터는 비게 된다.
    반환 (trees [(x, y, 수관 반지름, 높이)], covered 덩어리 — 높이 지도에 나무가 있는 곳)
    """
    from collections import defaultdict
    from scipy import ndimage
    from shapely import contains_xy, prepare
    from .vegetation import _filled
    chm, x0, y1, cell = load_chm(path)
    rng = np.random.default_rng(seed)
    H, W = chm.shape
    gx = np.arange(x0 + step / 2, x0 + W * cell, step)
    gy = np.arange(y1 - step / 2, y1 - H * cell, -step)
    X, Y = np.meshgrid(gx, gy)
    X = X + rng.uniform(-0.4, 0.4, X.shape) * step
    Y = Y + rng.uniform(-0.4, 0.4, Y.shape) * step
    X, Y = X.ravel(), Y.ravel()
    c = np.clip(((X - x0) / cell).astype(int), 0, W - 1)
    r_ = np.clip(((y1 - Y) / cell).astype(int), 0, H - 1)
    h = ndimage.uniform_filter(chm, 3)[r_, c].astype(float)
    prepare(zone)
    ok = (h >= min_h) & contains_xy(zone, X, Y)
    X, Y, h = X[ok], Y[ok], h[ok]
    order = np.argsort(-h + rng.uniform(0, 0.5, len(h)))
    R = np.clip(crown_r_for_height(h), 1.2, 6.0)
    cs = 2 * 6.0 * gap
    grid, out = defaultdict(list), []
    for i in order:
        if len(out) >= max_trees:
            break
        x, y, r = X[i], Y[i], R[i]
        k = (int(x // cs), int(y // cs))
        if any((x - u[0]) ** 2 + (y - u[1]) ** 2 < (gap * (r + u[2])) ** 2
               for a in (-1, 0, 1) for b in (-1, 0, 1) for u in grid[(k[0] + a, k[1] + b)]):
            continue
        t = (float(x), float(y), float(r), float(h[i]))
        grid[k].append(t)
        out.append(t)
    gxs = x0 + (np.arange(W) + 0.5) * cell
    gys = y1 - (np.arange(H) + 0.5) * cell
    cover = _filled(gxs, gys, ndimage.gaussian_filter((ndimage.uniform_filter(chm, 3) >= min_h).astype(float), 2.0 / cell), 0.5)
    return out, cover

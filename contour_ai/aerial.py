"""고화질 항공사진에서 실제 가로수를 찾는다.

가로수를 길마다 일정 간격으로 까는 대신, 항공사진에서 보도 위(와 그 위로 뻗은) 초록 수관을 찾아
나무 하나하나의 위치와 수관 크기를 얻는다. 수치지도에 가로수 레이어가 있으면 그것을 먼저 쓴다.

항공사진은 둘 중 하나:
  - 브이월드(국토교통부 공간정보 오픈플랫폼) 영상지도 타일. 무료 인증키 필요. 확대 19단계 ≈ 0.24m/화소.
  - 사용자가 받은 정사영상 파일(GeoTIFF 등 좌표가 들어 있는 영상, 예: 국토정보플랫폼 정사영상).
둘 다 모델 좌표계의 일정 격자(기본 0.3m)로 바꿔 npz 로 저장해 두고 다시 쓴다.
"""
import math
import os
import re
import urllib.request

import numpy as np

VWORLD_URL = "https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg"
CELL = 0.3
TREE_WORDS = ("가로수", "독립수", "수목", "나무")


# ---------------------------------------------------------------- 영상 준비
def _grid(bbox, cell):
    xs = np.arange(bbox[0] + cell / 2, bbox[2], cell)
    ys = np.arange(bbox[3] - cell / 2, bbox[1], -cell)   # 위(북)에서 아래로
    return xs, ys


def fetch_vworld(bbox, crs_wkt, key, out, zoom=19, domain="", log=print, cache_dir=None):
    """브이월드 영상 타일을 받아 모델 좌표 격자 RGB 로 저장한다. 반환: out"""
    from pyproj import Transformer
    from PIL import Image
    import io
    to_m = Transformer.from_crs(crs_wkt, 3857, always_xy=True)
    pad = 10.0
    cx = [bbox[0] - pad, bbox[2] + pad, bbox[0] - pad, bbox[2] + pad]
    cy = [bbox[1] - pad, bbox[1] - pad, bbox[3] + pad, bbox[3] + pad]
    mx, my = to_m.transform(cx, cy)
    R = 20037508.342789244
    n = 2 ** zoom
    size = 2 * R / n
    tx0, tx1 = int((min(mx) + R) // size), int((max(mx) + R) // size)
    ty0, ty1 = int((R - max(my)) // size), int((R - min(my)) // size)
    tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    if tiles > 900:
        raise SystemExit(f"범위가 너무 넓어 항공사진 타일이 {tiles}장입니다 (최대 900).")
    log(f"   항공사진(브이월드 영상, {zoom}단계) 타일 {tiles}장 받는 중")
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256, 3), np.uint8)
    tile_dir = os.path.join(cache_dir, "vworld_tiles") if cache_dir else None
    if tile_dir:
        os.makedirs(tile_dir, exist_ok=True)
    headers = {"User-Agent": "contour-ai/1.0"}
    if domain:
        headers["Referer"] = domain
    got = 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            fn = os.path.join(tile_dir, f"{zoom}_{tx}_{ty}.jpeg") if tile_dir else None
            data = None
            if fn and os.path.exists(fn):
                data = open(fn, "rb").read()
            else:
                url = VWORLD_URL.format(key=key, z=zoom, y=ty, x=tx)
                if domain:
                    url += "?domain=" + urllib.request.quote(domain, safe="")
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                        data = r.read()
                except Exception as e:
                    if got == 0 and tx == tx0 and ty == ty0:
                        raise SystemExit(f"브이월드 항공사진을 받지 못했습니다: {e}. 인증키와 인터넷 연결을 확인해 주세요.")
                    continue
                if data[:3] != b"\xff\xd8\xff" and data[:4] != b"\x89PNG":
                    msg = data[:300].decode("utf-8", "replace")
                    raise SystemExit(f"브이월드가 영상 대신 다른 응답을 보냈습니다(인증키·등록 주소 확인): {msg}")
                if fn:
                    open(fn, "wb").write(data)
            img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
            i, j = (ty - ty0) * 256, (tx - tx0) * 256
            mosaic[i:i + 256, j:j + 256] = img[:256, :256]
            got += 1
    # 모델 좌표 격자 → 메르카토르 → 모자이크 화소 (쌍선형)
    xs, ys = _grid(bbox, CELL)
    X, Y = np.meshgrid(xs, ys)
    MX, MY = to_m.transform(X.ravel(), Y.ravel())
    px = (np.asarray(MX) + R) / size * 256 - tx0 * 256 - 0.5
    py = (R - np.asarray(MY)) / size * 256 - ty0 * 256 - 0.5
    rgb = _bilinear(mosaic, px, py).reshape(*X.shape, 3)
    np.savez_compressed(out, rgb=rgb, x0=bbox[0], y1=bbox[3], cell=CELL, source=f"브이월드 영상 {zoom}단계")
    return out


def _bilinear(img, px, py):
    h, w = img.shape[:2]
    px = np.clip(px, 0, w - 1.001); py = np.clip(py, 0, h - 1.001)
    x0, y0 = px.astype(int), py.astype(int)
    fx, fy = (px - x0)[:, None], (py - y0)[:, None]
    a, b = img[y0, x0].astype(float), img[y0, x0 + 1].astype(float)
    c, d = img[y0 + 1, x0].astype(float), img[y0 + 1, x0 + 1].astype(float)
    return ((a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy).round().astype(np.uint8)


def load_image(path, bbox, crs_wkt, out):
    """좌표가 들어 있는 항공사진 파일(GeoTIFF, 월드파일 동반 JPG 등)을 모델 격자로 바꿔 저장."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    xs, ys = _grid(bbox, CELL)
    dst = np.zeros((3, len(ys), len(xs)), np.uint8)
    with rasterio.open(path) as ds:
        if ds.crs is None:
            raise SystemExit("항공사진 파일에 좌표 정보가 없습니다. GeoTIFF나 월드파일(.jgw/.tfw)이 있는 영상을 넣어 주세요.")
        for i in range(3):
            reproject(rasterio.band(ds, min(i + 1, ds.count)), dst[i], src_transform=ds.transform, src_crs=ds.crs,
                      dst_transform=from_origin(bbox[0], bbox[3], CELL, CELL), dst_crs=crs_wkt,
                      resampling=Resampling.bilinear)
    if dst.max() == 0:
        raise SystemExit("항공사진 파일이 모델 범위를 덮지 않습니다.")
    np.savez_compressed(out, rgb=np.moveaxis(dst, 0, -1), x0=bbox[0], y1=bbox[3], cell=CELL,
                        source=os.path.basename(path))
    return out


def load(path):
    z = np.load(path)
    return z["rgb"], float(z["x0"]), float(z["y1"]), float(z["cell"]), str(z["source"])


# ---------------------------------------------------------------- 수치지도에 있는 가로수
def mapped_trees(folder, bbox):
    """수치지도 점 레이어 중 가로수·독립수로 표시된 것. 반환: [(x, y)]"""
    import shapefile
    out = []
    if not os.path.isdir(folder):
        return out
    for fn in sorted(os.listdir(folder)):
        if not re.match(r"N3P_.*\.shp$", fn, re.I):
            continue
        r = shapefile.Reader(os.path.join(folder, fn[:-4]), encoding="euc-kr", encodingErrors="replace")
        names = [f[0] for f in r.fields[1:]]
        for sr in r.iterShapeRecords():
            rec = dict(zip(names, sr.record))
            words = " ".join(str(rec.get(k) or "") for k in ("명칭", "구분", "종류", "용도"))
            if any(w in words for w in TREE_WORDS) and sr.shape.points:
                x, y = sr.shape.points[0][:2]
                if bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
                    out.append((x, y))
    return out


# ---------------------------------------------------------------- 수관 찾기
def vegetation(rgb):
    """RGB 항공사진의 초록 정도(ExG, 정규화 색도 2g-r-b)와 밝기."""
    f = rgb.astype(float)
    s = f.sum(axis=2) + 1e-6
    r, g, b = f[..., 0] / s, f[..., 1] / s, f[..., 2] / s
    return 2 * g - r - b, s / 3


def detect_street_trees(aerial_path, walk, road, buildings, exg_min=0.05, bright_min=25, min_dist=3.5,
                        rrange=(1.2, 5.0), reach=3.0):
    """보도(walk) 위와 그 둘레 reach(m) 안의 초록 수관을 찾아 가로수로. 반환 ([(x, y, r)], 확인용 dict)

    1. 초록 정도(ExG)가 높고 너무 어둡지 않은 화소 = 나뭇잎.
    2. 보도 둘레 reach 안(가지가 차도 위로 뻗음)만, 건물 지붕은 뺀다.
    3. 잎 비율을 1m로 고른 뒤 min_dist(m) 안에서 가장 높은 곳 = 수관 가운데.
    4. 수관 크기는 그 덩어리 넓이를 나무 수로 나눠서. 줄기는 보도 안 가장 가까운 점.
    """
    import shapely
    from scipy import ndimage
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    rgb, x0, y1, cell, source = load(aerial_path)
    h, w = rgb.shape[:2]
    exg, bright = vegetation(rgb)
    leaf = (exg > exg_min) & (bright > bright_min)
    leaf = ndimage.binary_opening(leaf, iterations=1)
    zone_geom = walk.buffer(reach)
    if buildings is not None and not buildings.is_empty:
        zone_geom = zone_geom.difference(buildings)
    xs = x0 + (np.arange(w) + 0.5) * cell
    ys = y1 - (np.arange(h) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    shapely.prepare(zone_geom)
    zone = shapely.intersects_xy(zone_geom, X, Y)
    leaf &= zone
    lab, n = ndimage.label(leaf)
    if n == 0:
        return [], dict(rgb=rgb, leaf=leaf, extent=(x0, x0 + w * cell, y1 - h * cell, y1), source=source)
    areas = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1)) * cell * cell
    keep = np.zeros(n + 1, bool); keep[1:] = areas >= math.pi * rrange[0] ** 2 * 0.6
    leaf = keep[lab]
    lab = np.where(leaf, lab, 0)
    frac = ndimage.gaussian_filter(leaf.astype(float), 1.0 / cell)
    k = max(3, int(round(min_dist / cell)) | 1)
    peak = (frac == ndimage.maximum_filter(frac, size=k)) & (frac > 0.45) & leaf
    py, px = np.nonzero(peak)
    # 같은 덩어리 안 나무 수로 수관 넓이 나누기
    per = np.bincount(lab[py, px], minlength=lab.max() + 1)
    comp_area = ndimage.sum(np.ones_like(lab), lab, index=np.arange(lab.max() + 1)) * cell * cell
    walk_in = walk.buffer(-0.4)
    if walk_in.is_empty:
        walk_in = walk
    trees = []
    for yy, xx in zip(py, px):
        c = lab[yy, xx]
        r = math.sqrt(comp_area[c] / max(per[c], 1) / math.pi)
        r = float(np.clip(r, *rrange))
        p = Point(X[yy, xx], Y[yy, xx])
        if not walk.contains(p):
            q = nearest_points(walk_in, p)[0]
            if q.distance(p) > reach:
                continue
            p = q
        if buildings is not None and buildings.contains(p):
            continue
        trees.append((p.x, p.y, r))
    # 너무 붙은 줄기 합치기
    trees.sort(key=lambda t: -t[2])
    out = []
    for t in trees:
        if all((t[0] - u[0]) ** 2 + (t[1] - u[1]) ** 2 > (min_dist * 0.7) ** 2 for u in out):
            out.append(t)
    return out, dict(rgb=rgb, leaf=leaf, extent=(x0, x0 + w * cell, y1 - h * cell, y1), source=source)


def preview(path, info, trees, walk, mapped=()):
    """확인용 그림: 항공사진 + 찾은 나무(초록 원) + 보도 윤곽 + 수치지도 가로수(파랑)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    e = info["extent"]
    fig = plt.figure(figsize=(10, 10 * (e[3] - e[2]) / (e[1] - e[0])), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(info["rgb"], extent=e)
    for p in getattr(walk, "geoms", [walk]):
        if hasattr(p, "exterior"):
            ax.plot(*np.asarray(p.exterior.coords).T, color="#ffffff", lw=0.3, alpha=0.7)
    for x, y, r in trees:
        ax.add_patch(Circle((x, y), r, fill=False, ec="#39ff6a", lw=0.8))
        ax.plot(x, y, ".", color="#39ff6a", ms=2)
    for x, y in mapped:
        ax.plot(x, y, "o", mfc="none", mec="#3aa0ff", ms=5, mew=0.8)
    ax.set_xlim(e[0], e[1]); ax.set_ylim(e[2], e[3]); ax.set_axis_off()
    fig.savefig(path); plt.close(fig)

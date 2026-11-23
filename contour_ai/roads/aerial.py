"""항공사진에서 노면표시·횡단보도 찾기."""
import numpy as np
from .centerlines import _crossing



# ---------------------------------------------------------------- 항공사진 노면표시
def aerial_marks(aerial_path, road, min_area=0.25, log=print):
    """차도 안 밝고 무채색(흰) 또는 노란 화소 → 면. 반환 [(Polygon, 종류)]"""
    from shapely import contains_xy
    from ..aerial import load
    rgb, x0, y1, cell, _ = load(aerial_path)
    img = rgb.astype(float)
    R, G, B = img[..., 0], img[..., 1], img[..., 2]
    V = img.max(axis=2)
    S = (V - img.min(axis=2)) / np.maximum(V, 1)
    h, w = V.shape
    xs = x0 + (np.arange(w) + 0.5) * cell
    ys = y1 - (np.arange(h) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    inside = contains_xy(road.buffer(-0.3), X, Y)
    if inside.sum() < 100:
        return []
    # 노면 밝기는 동네마다 달라서 차도 화소의 분포로 문턱을 정한다
    base = np.percentile(V[inside], 60)
    white = inside & (V > max(base + 55, 170)) & (S < 0.18)
    yellow = inside & (R > base + 25) & (G > base + 5) & (B < G - 35) & (S > 0.3)
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape
    tr = from_origin(x0, y1, cell, cell)
    out = []
    for mask, kind in ((white, "노면표시"), (yellow, "노면표시(노랑)")):
        if mask.sum() < 4:
            continue
        for geom, val in shapes(mask.astype(np.uint8), mask=mask, connectivity=8, transform=tr):
            pg = shape(geom).buffer(0).simplify(cell * 0.5)
            if pg.is_empty or pg.area < min_area:
                continue
            # 페인트는 가늘다(차선 0.15, 횡단보도 줄 0.45~0.5, 정지선 0.45m). 흰 차 지붕(폭 1.8m) 같은 덩어리는 버림
            rr = np.asarray(pg.minimum_rotated_rectangle.exterior.coords)
            short = min(np.linalg.norm(rr[1] - rr[0]), np.linalg.norm(rr[2] - rr[1]))
            if short <= 1.0 or pg.area / max(pg.minimum_rotated_rectangle.area, 1e-6) < 0.45:
                out.append((pg, kind))
    log(f"   항공사진에서 노면표시 {len(out)}조각 (흰색 {sum(k == '노면표시' for _, k in out)} · 노랑 {sum(k != '노면표시' for _, k in out)})")
    return out


# ---------------------------------------------------------------- 묶음

def aerial_zebras(aerial_path, road, walk=None, log=print):
    """항공사진 줄무늬를 직접 읽은 횡단보도 → [(띠, u, nrm, e0, e1)].

    방향은 도로 중심선이 아니라 줄무늬 자체에서 잰다(오거리의 섬과 섬을 비스듬히 잇는 횡단보도도 그 각도 그대로).
    차·그림자로 일부만 보이면 그 방향 그대로 양 끝 보도·교통섬까지 늘인다. 못 늘이면 보인 만큼만."""
    from shapely import contains_xy
    from scipy.ndimage import median_filter
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape
    from ..aerial import load
    from ..marking_assets import find_zebras
    rgb, x0, y1, cell, _ = load(aerial_path)
    img = rgb.astype(float)
    V = img.max(axis=2)
    S = (V - img.min(axis=2)) / np.maximum(V, 1)
    h, w = V.shape
    X, Y = np.meshgrid(x0 + (np.arange(w) + 0.5) * cell, y1 - (np.arange(h) + 0.5) * cell)
    near = contains_xy(road.buffer(1.0), X, Y)
    if near.sum() < 100:
        return []
    # 주변(3m)보다 뚜렷이 밝은 무채색·옅은 노랑 = 페인트 (그늘 속 줄무늬도 잡히게 주변 밝기 기준)
    loc = median_filter(V, size=int(round(3.0 / cell)) | 1)
    paint = near & (V - loc > np.maximum(16, 0.18 * loc)) & (S < 0.45)
    tr = from_origin(x0, y1, cell, cell)
    blobs = []
    for con in (4, 8):                       # 비스듬한 줄은 4-연결에서 끊기고, 붙은 줄은 8-연결에서 붙는다 → 둘 다
        for geom, _ in shapes(paint.astype(np.uint8), mask=paint, connectivity=con, transform=tr):
            pg = shape(geom).buffer(0)
            if pg.area >= 0.15:
                blobs.append((pg, "w"))
    out = []
    for z in find_zebras(blobs, log=log):
        u, c = z["u"], np.asarray(z["c"])
        hit = _crossing(c, u, road, walk, min(max(z["width"], 3.0), 6.0), slide=(0.0, 0.5, -0.5)) \
            if walk is not None else None
        if hit is not None:
            band, (e0, e1), nrm = hit
            if np.linalg.norm(e1 - e0) <= z["length"] + 30:      # 보인 줄무늬보다 터무니없이 길면 믿지 않음
                out.append((band, u, nrm, e0, e1))
                continue
        band = z["band"].intersection(road.buffer(0.3))
        if band.is_empty or band.geom_type != "Polygon":
            continue
        nrm = z["nrm"]
        t = [float((np.asarray(q) - c) @ nrm) for q in band.exterior.coords]
        out.append((band, u, nrm, c + nrm * min(t), c + nrm * max(t)))
    return out

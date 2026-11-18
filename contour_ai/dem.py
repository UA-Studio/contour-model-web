"""수치표고모델(DEM) 파일로 지형 높이를 읽는다.

수치지도 등고선(대개 5m 간격)을 이어 만든 지형은 등고선 사이에서 꺾이는 모양(절토면 계단, 옹벽, 둑)을
뭉갠다. 국토지리정보원 수치표고모델처럼 격자로 잰 높이가 있으면 그 격자를 직접 쓴다.

읽는 형식
- 격자 영상: GeoTIFF(.tif), ERDAS(.img), ASCII 격자(.asc) 등 GDAL(rasterio)이 여는 것. 좌표계가 들어 있으면 그것을 쓴다.
- 점 목록 텍스트(.txt .xyz .csv): 한 줄에 X Y Z (쉼표·공백 구분). 국토지리정보원 DEM 텍스트판처럼 격자 간격으로 놓인 점.
- 위 파일들을 묶은 zip, 또는 그 파일들이 든 폴더. 여러 도엽이면 모두 이어 붙인다.

좌표계 정보가 없으면 수치지도와 같은 좌표로 보고, 범위가 맞지 않으면 국내 좌표계를 차례로 넣어 본다.
"""
import glob
import os
import tempfile
import zipfile

import numpy as np

RASTER_EXT = (".tif", ".tiff", ".img", ".asc", ".bil", ".dem", ".flt", ".hgt", ".vrt")
POINT_EXT = (".txt", ".xyz", ".csv")
CANDIDATE_EPSG = (5186, 5185, 5187, 5188, 5179, 5174, 5173, 5176, 4326)


class DemError(ValueError):
    pass


def _expand(paths, work):
    """zip·폴더를 풀어 DEM 파일 목록으로"""
    out = []
    for p in paths:
        p = str(p).strip().strip('"')
        if not p:
            continue
        if os.path.isdir(p):
            for d, _, fs in os.walk(p):
                out += _expand([os.path.join(d, f) for f in sorted(fs)
                                if f.lower().endswith(RASTER_EXT + POINT_EXT + (".zip",))], work)
        elif p.lower().endswith(".zip"):
            dest = tempfile.mkdtemp(prefix="dem_", dir=work)
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    try:
                        name = name.encode("cp437").decode("cp949")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    tgt = os.path.join(dest, *[s for s in name.replace("\\", "/").split("/") if s not in ("", "..")])
                    os.makedirs(os.path.dirname(tgt), exist_ok=True)
                    with z.open(info) as s, open(tgt, "wb") as d:
                        d.write(s.read())
            out += _expand([dest], work)
        elif os.path.isfile(p):
            if p.lower().endswith(RASTER_EXT + POINT_EXT):
                out.append(p)
        else:
            raise DemError(f"DEM 파일을 찾지 못했습니다: {p}")
    # ESRI 격자 부속 파일(.aux.xml 등)·같은 영상의 .ovr 은 빼고, .asc 옆 .prj 는 rasterio 가 알아서 읽는다
    return [f for f in dict.fromkeys(out) if not f.lower().endswith((".aux.xml", ".ovr"))]


def _read_points(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.replace(",", " ").replace("\t", " ").split()
            if len(parts) < 3:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue          # 머리줄
    if len(rows) < 4:
        raise DemError(f"{os.path.basename(path)}에서 X Y Z 점을 읽지 못했습니다.")
    return np.asarray(rows)


def _guess_crs(bounds_list, map_crs, region):
    """좌표계 정보가 없는 자료: 수치지도 범위와 겹치는 좌표계를 고른다"""
    from pyproj import CRS, Transformer
    from shapely.geometry import box
    best = None
    for c in [map_crs] + [CRS.from_epsg(e) for e in CANDIDATE_EPSG]:
        try:
            t = Transformer.from_crs(c, map_crs, always_xy=True)
            g = [box(*t.transform_bounds(*b)) for b in bounds_list]
        except Exception:
            continue
        s = sum(x.intersection(region).area for x in g)
        if best is None or s > best[0]:
            best = (s, c)
    return best[1] if best and best[0] > 0 else None


class Dem:
    """모델 범위 둘레를 덮는 높이 격자(수치지도 좌표). 비어 있는 칸은 NaN."""

    def __init__(self, x0, y0, cell, Z, sources):
        self.x0, self.y0, self.cell, self.Z, self.sources = x0, y0, cell, Z, sources

    @property
    def coverage(self):
        return float(np.isfinite(self.Z).mean())

    def sample(self, xy):
        """점들의 높이(겹선형). 자료가 없는 곳은 NaN."""
        from scipy.ndimage import map_coordinates
        xy = np.atleast_2d(np.asarray(xy, float))
        c = (xy[:, 0] - self.x0) / self.cell
        r = (xy[:, 1] - self.y0) / self.cell
        Zf = np.where(np.isfinite(self.Z), self.Z, 0.0)
        W = np.isfinite(self.Z).astype(float)
        v = map_coordinates(Zf, [r, c], order=1, mode="nearest")
        w = map_coordinates(W, [r, c], order=1, mode="nearest")
        out = np.where(w > 0.999, v / np.maximum(w, 1e-9), np.nan)
        out[(r < -0.5) | (c < -0.5) | (r > self.Z.shape[0] - 0.5) | (c > self.Z.shape[1] - 0.5)] = np.nan
        return out


def load_dem(paths, bbox, crs_wkt, margin=60.0, log=print, work=None):
    """DEM 파일(들) → 모델 범위(+margin)를 덮는 Dem. 격자 간격은 자료의 가장 고운 간격(1m보다 곱게는 안 함)."""
    import rasterio
    from pyproj import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    from shapely.geometry import box
    if isinstance(paths, (str, os.PathLike)):
        paths = [p for p in str(paths).replace(";", "\n").splitlines() if p.strip()]
    work = work or tempfile.mkdtemp(prefix="contour_dem_")
    files = _expand(paths, work)
    if not files:
        raise DemError("DEM 파일이 없습니다 (GeoTIFF·IMG·ASC 격자, 또는 X Y Z 텍스트).")
    map_crs = CRS.from_wkt(crs_wkt)
    region = box(bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin)

    rasters, pts = [], []
    for f in files:
        if f.lower().endswith(POINT_EXT):
            pts.append((f, _read_points(f)))
            continue
        try:
            ds = rasterio.open(f)
        except Exception as e:
            log(f"   (DEM 파일을 열지 못함: {os.path.basename(f)}: {e})")
            continue
        rasters.append(ds)
    if not rasters and not pts:
        raise DemError("DEM 파일을 하나도 열지 못했습니다.")

    # 격자 간격: 자료 중 가장 고운 것 (최소 1m)
    res = [abs(ds.res[0]) for ds in rasters if ds.crs is None or not ds.crs.is_geographic]
    for _, P in pts:
        u = np.unique(np.round(P[:, 0], 3))
        if len(u) > 1:
            res.append(float(np.median(np.diff(u))))
    cell = max(1.0, min(res)) if res else 5.0
    x0, y0, x1, y1 = region.bounds
    nx, ny = int(np.ceil((x1 - x0) / cell)) + 1, int(np.ceil((y1 - y0) / cell)) + 1
    # 행 0 = 아래쪽(y0) 으로 쓰기 위해 rasterio 에는 위쪽 기준으로 만들고 나중에 뒤집는다
    dst_tf = from_origin(x0 - cell / 2, y0 + (ny - 0.5) * cell, cell, cell)
    acc = np.full((ny, nx), np.nan)
    used = []
    no_crs = [ds for ds in rasters if ds.crs is None]
    guessed = _guess_crs([tuple(ds.bounds) for ds in no_crs], map_crs, region) if no_crs else None
    for ds in rasters:
        src_crs = ds.crs or guessed or map_crs
        dst = np.full((ny, nx), np.nan, dtype="float64")
        try:
            reproject(source=rasterio.band(ds, 1), destination=dst, dst_transform=dst_tf, dst_crs=map_crs,
                      src_crs=src_crs, src_nodata=ds.nodata, dst_nodata=np.nan, resampling=Resampling.bilinear)
        except Exception as e:
            log(f"   (DEM {os.path.basename(ds.name)}을 수치지도 좌표로 옮기지 못함: {e})")
            continue
        dst[(dst < -500) | (dst > 9000)] = np.nan       # 빈 칸 표시값(-9999 등)
        if np.isfinite(dst).any():
            acc = np.where(np.isfinite(acc), acc, dst)
            used.append(os.path.basename(ds.name))
        ds.close()
    if pts:
        from pyproj import Transformer
        from scipy.interpolate import griddata
        allp = np.vstack([P for _, P in pts])
        src = _guess_crs([(allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())], map_crs, region)
        if src is None:
            log("   (DEM 점 파일의 좌표가 모델 범위와 맞지 않아 건너뜁니다)")
        else:
            if src != map_crs:
                tx, ty = Transformer.from_crs(src, map_crs, always_xy=True).transform(allp[:, 0], allp[:, 1])
                allp = np.column_stack([tx, ty, allp[:, 2]])
            keep = ((allp[:, 0] > x0 - 3 * cell) & (allp[:, 0] < x1 + 3 * cell) &
                    (allp[:, 1] > y0 - 3 * cell) & (allp[:, 1] < y1 + 3 * cell))
            allp = allp[keep]
            if len(allp) >= 4:
                # acc 는 행 0 이 위쪽(북쪽): 행 i 의 y = y0 + (ny - 1 - i) * cell
                GX, GY = np.meshgrid(x0 + np.arange(nx) * cell, y0 + (ny - 1 - np.arange(ny)) * cell)
                Zp = griddata(allp[:, :2], allp[:, 2], (GX, GY), method="linear")
                acc = np.where(np.isfinite(acc), acc, Zp)
                used += [os.path.basename(f) for f, _ in pts]
    Z = acc[::-1].copy()       # 행 0 = y0
    dem = Dem(x0, y0, cell, Z, used)
    cov = float(np.isfinite(dem.sample(np.column_stack([
        np.random.default_rng(0).uniform(bbox[0], bbox[2], 4000),
        np.random.default_rng(1).uniform(bbox[1], bbox[3], 4000)]))).mean())
    if not used or cov == 0:
        raise DemError("DEM 자료가 모델 범위를 덮지 않습니다. 범위에 맞는 도엽의 DEM인지, 좌표가 맞는지 확인해 주세요.")
    dem.cover = cov
    log(f"   수치표고모델(DEM): 파일 {len(used)}개 · 격자 {cell:g}m · 모델 범위의 {cov:.0%}를 덮음"
        + ("" if cov > 0.995 else " (나머지는 등고선으로)"))
    return dem


def merged_interpolator(dem, fallback):
    """DEM 이 있는 곳은 DEM, 없는 곳은 등고선 보간(fallback)"""
    def f(xy):
        xy = np.atleast_2d(np.asarray(xy, float))
        v = dem.sample(xy)
        bad = ~np.isfinite(v)
        if bad.any():
            v[bad] = fallback(xy[bad])
        return v
    return f

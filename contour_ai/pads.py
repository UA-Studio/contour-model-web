"""개발지 계단 대지: 산지·비탈에 들어선 건물 터와 논을 평평한 단으로 만든다.

수치지도 등고선은 건물·도로가 있는 곳에서 끊겨 있어, 그냥 보간하면 마을·단지가 자연 비탈처럼
매끈하게 이어진다. 실제(브이월드 3D 지형처럼)는 집마다·동마다 땅을 깎고 메워 평평한 터를 만들고,
터 사이는 옹벽·석축으로 끊긴다. 이것을 쓸 수 있는 자료로 대략 되살린다:

- 건물 발자국: 건물마다 발자국 안 원래 지형의 중앙값을 그 터의 높이로 한다.
- 터 넓히기: 건물에서 reach(m)까지 마당을 넓히되, 길(차도·보도 면)·옹벽·석축·절토·성토 선·담장은
  넘지 않는다. 그래서 옹벽이 그려진 곳은 옹벽이 터의 경계가 된다(필지 경계를 따라가는 효과).
  옹벽·담·길로 둘러싸인 작은 마당(lot_max ㎡ 이하)은 reach 보다 멀어도 끝까지 채운다.
- 이웃 터의 높이 차가 merge_tol(m) 안이면 한 단으로 합친다(아파트 단지처럼 넓은 평지).
- 논: 논 한 필은 평평하게. 비탈에 걸친 큰 논은 paddy_step(m)마다 계단식 다랑이로 나눈다.
- 터 밖: 옹벽·석축 선 가까이는 거의 수직(0.5m 폭)으로, 그 밖은 1:1 비탈(최대 6m 폭)로 원래 지형과 잇는다.
  길 면은 고치지 않는다(길은 원래 높이 그대로).

결과는 격자(cell m)로 된 높이 규칙이고, 보간 함수(interp)를 감싸 계단판·경사면·건물 터가 모두 같은 단을 쓴다.
"""
import math

import numpy as np

# 연속지적도 지목 중 깎고 메워 평평하게 쓰는 땅 (대지·공장·학교·주차장·주유소·창고·종교·체육·잡종지·묘지·유원지)
DEVELOPED = set("대장학차주창종체잡묘원")


def jimok(jibun):
    """연속지적도 지번 글자('556-118대', '산12임')에서 지목 한 글자."""
    j = str(jibun or "").strip()
    return j[-1] if j and not j[-1].isdigit() and j[-1] != "-" else ""


class PadField:
    """평평한 터 높이 격자. apply(xy, z) 로 원래 높이 z 를 터 높이로 바꾼다."""

    def __init__(self, x0, y1, cell, level, dist, near, wall, road, max_ramp, slope, stats):
        self.x0, self.y1, self.cell = x0, y1, cell
        self.level, self.dist, self.near, self.wall, self.road = level, dist, near, wall, road
        self.max_ramp, self.slope, self.stats = max_ramp, slope, stats

    def _idx(self, x, y):
        ny, nx = self.level.shape
        c = np.floor((np.asarray(x) - self.x0) / self.cell).astype(np.int64)
        r = np.floor((self.y1 - np.asarray(y)) / self.cell).astype(np.int64)
        ok = (r >= 0) & (r < ny) & (c >= 0) & (c < nx)
        return np.clip(r, 0, ny - 1), np.clip(c, 0, nx - 1), ok

    def apply(self, xy, z):
        xy = np.atleast_2d(np.asarray(xy, float))
        z = np.array(z, float, copy=True).reshape(-1)
        r, c, ok = self._idx(xy[:, 0], xy[:, 1])
        lv = self.level[r, c]
        inpad = ok & np.isfinite(lv)
        z[inpad] = lv[inpad]
        d = self.dist[r, c]
        rz = ok & ~inpad & (d < self.max_ramp) & ~self.road[r, c] & np.isfinite(z)
        if rz.any():
            nl = self.near[r, c][rz].astype(float)
            dz = z[rz] - nl
            w = np.where(self.wall[r, c][rz], 0.5, np.clip(np.abs(dz) * self.slope, 1.0, self.max_ramp))
            t = np.clip(d[rz] / w, 0.0, 1.0)
            z[rz] = nl + dz * t
        return z

    def wrap(self, interp):
        """보간 함수를 감싼다: 터 안은 터 높이, 둘레는 옹벽·비탈로 원래 지형과 잇는다."""
        def f(xy):
            xy = np.atleast_2d(np.asarray(xy, float))
            return self.apply(xy, interp(xy))
        f.pads = self
        return f

    def keep_weight(self, X, Y, reach=6.0):
        """계단판을 만들 때 지형을 고르게 펴지 않을 정도(1 = 그대로). 터 안과 둘레 reach m."""
        r, c, ok = self._idx(np.asarray(X).ravel(), np.asarray(Y).ravel())
        d = np.where(ok, self.dist[r, c], np.inf)
        return np.clip(1.0 - d / reach, 0.0, 1.0).reshape(np.shape(X))

    def polygons(self, min_area=4.0):
        """터 윤곽(확인 그림·Rhino 레이어용): [(면, 높이)]"""
        from rasterio import features, transform
        from shapely.geometry import shape
        T = transform.from_origin(self.x0, self.y1, self.cell, self.cell)
        q = np.where(np.isfinite(self.level), np.round(self.level * 10).astype(np.int32), -999999)
        out = []
        for geom, v in features.shapes(q, mask=q != -999999, transform=T, connectivity=4):
            g = shape(geom)
            if g.area >= min_area:
                out.append((g.simplify(self.cell * 0.7), v / 10.0))
        return out


def _union_find_merge(lab, levels, counts, tol):
    """옆에 붙은 터끼리 높이 차가 tol 안이면 한 단으로. 합친 무리의 높이 폭도 tol 을 넘지 않게."""
    pairs = []
    for a, b in ((lab[:, :-1], lab[:, 1:]), (lab[:-1, :], lab[1:, :])):
        m = (a > 0) & (b > 0) & (a != b)
        if m.any():
            p = np.stack([a[m], b[m]], 1)
            p.sort(1)
            pairs.append(p)
    n = len(levels)
    parent = np.arange(n)
    lo, hi = levels.copy(), levels.copy()
    wsum, wz = counts.astype(float).copy(), counts * levels

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    if pairs:
        P = np.unique(np.concatenate(pairs), axis=0)
        dz = np.abs(levels[P[:, 0]] - levels[P[:, 1]])
        for (a, b), d in zip(P[np.argsort(dz)], np.sort(dz)):
            if d > tol:
                break
            ra, rb = find(a), find(b)
            if ra == rb:
                continue
            nlo, nhi = min(lo[ra], lo[rb]), max(hi[ra], hi[rb])
            if nhi - nlo > tol:
                continue
            parent[rb] = ra
            lo[ra], hi[ra] = nlo, nhi
            wsum[ra] += wsum[rb]; wz[ra] += wz[rb]
    roots = np.array([find(i) for i in range(n)])
    glevel = np.where(wsum[roots] > 0, wz[roots] / np.maximum(wsum[roots], 1e-9), levels)
    return glevel, roots


def build_pads(buildings, interp, bbox, barriers=(), roads=None, paddies=(), exclude=None, parcels=(),
               dev_step=3.0, dev_max=60000.0,
               cell=0.5, reach=10.0, merge_tol=1.0, max_cut=6.0, max_span=8.0, paddy_step=1.5,
               max_ramp=6.0, slope=1.0, margin=12.0, lot_reach=30.0, lot_max=1500.0, lot_gap=3.0, log=print):
    """건물 발자국·막는 선·길 면·논으로 평평한 터 격자를 만든다. 반환: PadField 또는 None.

    buildings: 건물 면 목록, barriers: 옹벽·석축·절토·성토·담장 선, roads: 길 면(합친 것),
    paddies: 논 면, exclude: 터를 만들지 않을 곳(사업지 레벨을 쓰는 대지 등).
    parcels: 연속지적도 필지 [(면, 지목 한 글자)]. 주면 필지 경계가 터의 경계(옹벽)가 되고,
      대지·학교·공장 같은 개발 필지는 통째로 한 단(높이 폭이 dev_step 넘으면 dev_step 마다 단), 답(논)은 논처럼.
    """
    from rasterio import features, transform
    from scipy import ndimage
    from shapely.geometry import box

    x0, y0, x1, y1 = bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin
    nx, ny = int(math.ceil((x1 - x0) / cell)), int(math.ceil((y1 - y0) / cell))
    y1 = y0 + ny * cell
    T = transform.from_origin(x0, y1, cell, cell)
    shp = (ny, nx)
    area = box(x0, y0, x1, y1)

    def raster(items, all_touched=False, dtype="uint8"):
        items = [(g, v) for g, v in items if g is not None and not g.is_empty and g.intersects(area)]
        if not items:
            return np.zeros(shp, dtype)
        return features.rasterize(items, out_shape=shp, transform=T, fill=0, dtype=dtype, all_touched=all_touched)

    blds = [g for g in buildings if g is not None and not g.is_empty and g.area >= 4.0 and g.intersects(area)]
    if exclude is not None and not exclude.is_empty:
        blds = [g for g in blds if g.intersection(exclude).area < 0.5 * g.area]
    pcl = [(g, j) for g, j in parcels if g is not None and not g.is_empty and g.intersects(area)]
    pdy = [g for g in list(paddies) + [g for g, j in pcl if j == "답"]
           if g is not None and not g.is_empty and g.area >= 20.0 and g.intersects(area)]
    dev = [g for g, j in pcl if j in DEVELOPED and 20.0 <= g.area <= dev_max]
    if exclude is not None and not exclude.is_empty:
        dev = [g for g in dev if g.intersection(exclude).area < 0.5 * g.area]
    if pcl:
        barriers = list(barriers) + [g.boundary for g, j in pcl]
    if not blds and not pdy and not dev:
        return None
    # 큰 건물을 먼저 그려 작은 부속 건물이 제 칸을 지키게
    blds.sort(key=lambda g: -g.area)
    blab = raster(((g, i + 1) for i, g in enumerate(blds)), dtype="int32")
    road = raster([(roads, 1)]).astype(bool) if roads is not None else np.zeros(shp, bool)
    wall = raster(((g, 1) for g in barriers), all_touched=True).astype(bool)
    excl = raster([(exclude, 1)]).astype(bool) if exclude is not None else np.zeros(shp, bool)
    plab = raster(((g, i + 1) for i, g in enumerate(pdy)), dtype="int32")
    dlab = raster(((g, i + 1) for i, g in enumerate(dev)), dtype="int32")

    bmask = blab > 0
    nb = len(blds)
    # 원래 높이는 건물 둘레·논에서만 잰다(격자 전체를 재면 느림)
    need = (ndimage.distance_transform_edt(~bmask) * cell <= reach + 1.0) if bmask.any() else np.zeros(shp, bool)
    need |= (plab > 0) | (dlab > 0)
    need &= ~excl
    rr, cc = np.nonzero(need)
    Zn = np.full(shp, np.nan, np.float32)
    if len(rr):
        Zn[rr, cc] = interp(np.column_stack([x0 + (cc + 0.5) * cell, y1 - (rr + 0.5) * cell]))

    # 1) 건물 터 높이 = 발자국 안 원래 지형 중앙값. 발자국 안 높이 폭이 너무 큰(큰 비탈에 걸친) 건물은 터를 만들지 않는다
    idx = np.arange(1, nb + 1)
    levels = np.full(nb + 1, np.nan)
    skipped = 0
    if nb:
        bm = bmask & ~excl
        med = np.asarray(ndimage.median(Zn, labels=np.where(bm, blab, 0), index=idx), float)
        zmin = np.asarray(ndimage.minimum(Zn, labels=np.where(bm, blab, 0), index=idx), float)
        zmax = np.asarray(ndimage.maximum(Zn, labels=np.where(bm, blab, 0), index=idx), float)
        span = zmax - zmin
        bad = ~np.isfinite(med) | (span > max_span)
        skipped = int((np.isfinite(med) & (span > max_span)).sum())
        med[bad] = np.nan
        levels[1:] = med
    lab = np.where(bmask & ~excl & np.isfinite(levels[blab]), blab, 0).astype(np.int32)
    blocked = (bmask & (lab == 0)) | excl        # 터를 못 만든 건물 자리·제외 구역에는 넓히지 않는다

    # 2) 마당 넓히기: 길·옹벽·담 선을 넘지 않고 건물에서 reach m 까지, 깎거나 메우는 높이 max_cut 이하
    #    작은 건물(창고·부속)은 터도 작게: 건물 크기의 0.8배(최소 3m)까지만
    cross = ndimage.generate_binary_structure(2, 1)
    square = ndimage.generate_binary_structure(2, 2)
    # 대각선으로 새지 않도록 막는 선을 한 칸 두껍게, 넓히기는 + 와 □ 를 번갈아(마름모 대신 팔각형으로 퍼짐)
    wall_b = ndimage.binary_dilation(wall, structure=cross) if wall.any() else wall
    free = ~wall_b & ~road & ~blocked & need
    lv_of = np.nan_to_num(levels, nan=-1e9)
    reach_of = np.zeros(nb + 1)
    if nb:
        reach_of[1:] = np.clip(0.8 * np.sqrt([g.area for g in blds]), 3.0, reach)
    for it in range(int(round(reach / cell))):
        g = ndimage.grey_dilation(lab, footprint=cross if it % 2 == 0 else square)
        new = (lab == 0) & (g > 0) & free
        new &= reach_of[g] >= (it + 1) * cell
        if not new.any():
            break
        new &= np.abs(Zn - lv_of[g]) <= max_cut
        if not new.any():
            break
        lab[new] = g[new]

    # 2b) 옹벽·담·길로 둘러싸인 작은 마당(필지)은 reach 보다 멀어도 끝까지 채운다.
    #     둘러싸였는지는: 건물 lot_reach m 안에서 막는 선·길·건물로 나뉜 조각 중, 바깥 경계에 닿지 않고 lot_max ㎡ 이하인 것.
    if bmask.any() and lot_reach > reach:
        dz = ndimage.distance_transform_edt(~bmask) * cell
        zone = dz <= lot_reach
        # 옹벽 끝과 건물 사이처럼 lot_gap m 이하로 벌어진 틈은 막힌 것으로 본다(선을 그만큼 두껍게 해서 조각을 나눔)
        k = max(1, int(round(lot_gap / 2 / cell)))
        thick = ndimage.binary_dilation(wall | road | bmask | blocked, structure=cross, iterations=k)
        cand = zone & ~thick
        comp, n = ndimage.label(cand, structure=cross)
        if n:
            size = np.bincount(comp.ravel(), minlength=n + 1) * cell * cell
            edge = np.zeros(n + 1, bool)
            edge[np.unique(comp[(dz > lot_reach - 2 * cell) & (comp > 0)])] = True
            edge[np.unique(np.concatenate([comp[0], comp[-1], comp[:, 0], comp[:, -1]]))] = True
            okc = (size <= lot_max) & ~edge
            okc[0] = False
            encl = ndimage.binary_dilation(okc[comp], structure=cross, iterations=k + 1)
            encl &= zone & ~wall_b & ~road & ~blocked & (lab == 0)
            if encl.any():
                rr2, cc2 = np.nonzero(encl & np.isnan(Zn))
                if len(rr2):
                    Zn[rr2, cc2] = interp(np.column_stack([x0 + (cc2 + 0.5) * cell, y1 - (rr2 + 0.5) * cell]))
                for _ in range(int(round(lot_reach / cell))):
                    g = ndimage.grey_dilation(lab, footprint=cross)
                    new = encl & (lab == 0) & (g > 0)
                    if not new.any():
                        break
                    new &= np.abs(Zn - lv_of[g]) <= max_cut
                    if not new.any():
                        break
                    lab[new] = g[new]

    extra = []
    nid = [nb + 1]

    def bands(sl, m, z, step, base_level=None):
        """sl 조각 안 m 칸을 높이 폭에 따라 한 단 또는 step 마다 여러 단으로. 반환: 다랑이로 나눴는지"""
        ok = np.isfinite(z)
        if ok.sum() < 8:
            return False
        zlo, zhi = np.nanmin(z), np.nanmax(z)
        split = zhi - zlo > step
        band = np.floor((z - zlo) / step).astype(int) if split else np.zeros(z.shape, int)
        sub = lab[sl]
        mi = np.argwhere(m)
        for b in np.unique(band[ok]):
            sel = ok & (band == b)
            if sel.sum() < 4:
                continue
            extra.append(float(base_level) if (base_level is not None and not split) else float(np.median(z[sel])))
            sub[mi[sel, 0], mi[sel, 1]] = nid[0]
            nid[0] += 1
        return split

    # 2c) 개발 필지(대지·학교·공장 …): 필지 하나를 통째로 한 단. 건물이 있으면 건물 터 높이를 따른다.
    #     높이 폭이 dev_step 을 넘는 큰 필지(아파트 단지·학교)는 dev_step 마다 단으로 나누고, 건물 터는 제 높이를 지킨다.
    n_dev = n_dev_split = 0
    for k, sl in enumerate(ndimage.find_objects(dlab), start=1):
        if sl is None:
            continue
        m = (dlab[sl] == k) & ~road[sl] & ~excl[sl] & ~blocked[sl]
        if m.sum() < 8:
            continue
        z = Zn[sl][m]
        if not np.isfinite(z).any():
            continue
        span = np.nanmax(z) - np.nanmin(z)
        bl = blab[sl][m]
        bl = bl[(bl > 0) & np.isfinite(levels[np.minimum(bl, nb)])] if nb else bl[:0]
        n_dev += 1
        if span <= dev_step:
            base = float(np.median(levels[bl])) if len(bl) else None
            bands(sl, m, z, dev_step, base_level=base)
        else:
            mm = m & (lab[sl] == 0)
            if bands(sl, mm, Zn[sl][mm], dev_step):
                n_dev_split += 1

    # 3) 논: 한 필은 평평하게, 비탈에 걸친 큰 논은 paddy_step 마다 다랑이로
    n_terr = 0
    if pdy:
        for k, sl in enumerate(ndimage.find_objects(plab), start=1):
            if sl is None:
                continue
            m = (plab[sl] == k) & (lab[sl] == 0) & ~road[sl] & ~excl[sl]
            z = Zn[sl][m]
            if bands(sl, m, z, paddy_step if np.isfinite(z).any() and np.nanmax(z) - np.nanmin(z) > 2 * paddy_step
                     else 1e9):
                n_terr += 1
    levels = np.concatenate([levels, np.array(extra, float)])
    levels[0] = 0.0

    # 4) 높이 차가 작은 이웃 터는 한 단으로
    counts = np.bincount(lab.ravel(), minlength=len(levels)).astype(float)
    counts[0] = 0
    lv0 = np.nan_to_num(levels, nan=0.0)
    glevel, roots = _union_find_merge(lab, lv0, counts, merge_tol)
    L = np.where(lab > 0, glevel[lab], np.nan).astype(np.float32)

    padm = lab > 0
    if not padm.any():
        return None
    d, (ir, ic) = ndimage.distance_transform_edt(~padm, return_indices=True)
    dist = (d * cell).astype(np.float32)
    near = L[ir, ic]
    del ir, ic
    wall_near = ndimage.distance_transform_edt(~wall) * cell <= 1.5 if wall.any() else np.zeros(shp, bool)
    groups = len(np.unique(roots[np.unique(lab[padm])]))
    stats = dict(buildings=int(np.isfinite(levels[1:nb + 1]).sum()), skipped=skipped, paddies=len(pdy),
                 paddy_terraced=n_terr, pads=groups, area=float(padm.sum() * cell * cell),
                 parcels=len(pcl), dev_parcels=n_dev, dev_split=n_dev_split)
    log(f"   계단식 대지: 건물 터 {stats['buildings']}동 → 평평한 단 {groups}개 ({stats['area']:,.0f}㎡)"
        + (f" · 연속지적도 필지 {len(pcl):,}개(개발 필지 {n_dev}개, 그중 여러 단 {n_dev_split}개)" if pcl else "")
        + (f" · 논 {len(pdy)}필(다랑이 {n_terr}필)" if pdy else "")
        + (f" · 큰 비탈에 걸친 건물 {skipped}동은 원래 지형" if skipped else ""))
    return PadField(x0, y1, cell, L, dist, near, wall_near, road | excl, max_ramp, slope, stats)


def load_parcels(path, bbox, crs_wkt, margin=50.0):
    """연속지적도 SHP(폴더·zip·.shp)를 읽어 모델 좌표로. 반환: [(면, 지목 한 글자)]"""
    import glob
    import os
    import tempfile
    import zipfile

    import shapefile
    from pyproj import CRS, Transformer
    from shapely.geometry import box, shape
    from shapely.ops import transform
    from shapely.validation import make_valid
    if str(path).lower().endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="parcels_")
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        path = tmp
    shps = [path] if str(path).lower().endswith(".shp") else glob.glob(os.path.join(path, "**", "*.shp"), recursive=True)
    clip = box(*bbox).buffer(margin)
    out = []
    for fn in shps:
        prj = fn[:-4] + ".prj"
        src = CRS(open(prj, encoding="utf-8", errors="replace").read()) if os.path.exists(prj) else CRS.from_epsg(5186)
        tr = Transformer.from_crs(src, CRS(crs_wkt), always_xy=True)
        inv = Transformer.from_crs(CRS(crs_wkt), src, always_xy=True)
        xs, ys = inv.transform([clip.bounds[0], clip.bounds[2]] * 2, [clip.bounds[1]] * 2 + [clip.bounds[3]] * 2)
        q = (min(xs), min(ys), max(xs), max(ys))
        for enc in ("cp949", "utf-8"):
            got = []
            try:
                r = shapefile.Reader(fn, encoding=enc)
                names = [f[0].upper() for f in r.fields[1:]]
                ji = next((i for i, n in enumerate(names) if n in ("JIBUN", "지번", "A5")), None)
                for sr in r.iterShapeRecords(bbox=q):
                    g = make_valid(transform(lambda x, y, z=None: tr.transform(x, y), shape(sr.shape.__geo_interface__)))
                    if g.intersects(clip):
                        got.append((g, jimok(sr.record[ji]) if ji is not None else ""))
            except UnicodeDecodeError:
                continue
            out += got
            break
    return out

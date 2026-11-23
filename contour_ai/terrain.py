"""지형: 등고선 → 높이 보간, 계단 판(층) 면, 경사 높이면, 건물 터, 지형 메시, 선을 지형 위에 얹기.

이 파일만 고치면 '땅 모양'만 바뀝니다.
"""
import math

import numpy as np
import trimesh
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from shapely import make_valid
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from .geom_util import as_polys, _mf_from_trimesh, smooth_poly


# ---------------------------------------------------------------- 지형
def breakline_points(breaks, interp, step=2.0, reach=(1.5, 3.0, 5.0, 7.0)):
    """절토·성토·옹벽 선에 높이를 붙인 점. 반환: (점 (n,2), 높이 (n,))

    수치지도의 이 선들에는 높이가 없다. 등고선 보간면을 선 양쪽(reach m)에서 재어
    위 끝(상단) 선은 둘레의 높은 값, 아래 끝(하단) 선은 낮은 값을 준다. 그러면 5m 등고선 사이에 있는
    절토면의 꺾임(평평한 단 → 급한 비탈)이 보간면에 생긴다. 높이가 적힌 옹벽은 선 양쪽에 0.3m 띄운 두 줄로
    위쪽·아래쪽 높이를 따로 준다(아래쪽 = 위쪽 - 옹벽 높이).
    """
    P, Z = [], []
    for ls, kind, side, h in breaks:
        if ls.length < 1.0:
            continue
        n = max(2, int(ls.length / step) + 1)
        d = np.linspace(0, ls.length, n)
        xy = np.array([ls.interpolate(t).coords[0][:2] for t in d])
        tan = np.gradient(xy, axis=0)
        tan /= np.maximum(np.linalg.norm(tan, axis=1), 1e-9)[:, None]
        nrm = np.column_stack([-tan[:, 1], tan[:, 0]])
        samp = np.stack([interp(xy + s_ * r * nrm) for r in reach for s_ in (1, -1)] + [interp(xy)], axis=1)
        top, bot = np.nanmax(samp, axis=1), np.nanmin(samp, axis=1)
        if h > 0.3:
            # 옹벽: 어느 쪽이 높은지는 양쪽 평균으로
            hi = np.nanmean(samp[:, 0::2][:, :len(reach)], axis=1) >= np.nanmean(samp[:, 1::2][:, :len(reach)], axis=1)
            sgn = np.where(hi, 1.0, -1.0)[:, None]
            P += [xy + 0.3 * sgn * nrm, xy - 0.3 * sgn * nrm]
            Z += [top, np.maximum(top - h, bot - 1.0)]
            continue
        if side == "상단":
            P.append(xy); Z.append(top)
        elif side == "하단":
            P.append(xy); Z.append(bot)
    if not P:
        return np.zeros((0, 2)), np.zeros(0)
    return np.vstack(P), np.concatenate(Z)


def build_interpolator(contours, spots, extra=None):
    pts, zs = [], []
    for ls, z in contours:
        for x, y in ls.segmentize(5).coords:
            pts.append((x, y)); zs.append(z)
    for x, y, z in spots:
        pts.append((x, y)); zs.append(z)
    pts, zs = np.array(pts), np.array(zs)
    if extra is not None and len(extra[0]):
        ok = np.isfinite(extra[1])
        pts, zs = np.vstack([pts, extra[0][ok]]), np.concatenate([zs, extra[1][ok]])
    lin = LinearNDInterpolator(pts, zs)
    near = NearestNDInterpolator(pts, zs)

    def f(xy):
        xy = np.atleast_2d(xy)
        v = lin(xy)
        bad = np.isnan(v)
        if bad.any():
            v[bad] = near(xy[bad])
        return v
    return f


def terrace_regions(contours, interp, bbox, interval, min_area, cell=0.5, smooth=1.5, sigma=4.0, chaikin_n=3, keep=None,
                    keep_w=None):
    """보간한 지형면을 레벨마다 잘라 계단 영역을 만든다.

    수치지도 등고선은 건물·도로에서 끊겨 있어 그대로 폐합할 수 없으므로,
    등고선 정점으로 TIN을 만든 뒤 격자(cell m)에서 등치 영역을 뽑는다.
    등고선 위에서는 TIN 값이 정확히 그 표고라 원래 선을 따라간다.
    반환: ({level: 영역}, 바닥 레벨) — level 이상인 지면 영역.
    """
    import contourpy
    clip = box(*bbox)
    xs = np.arange(bbox[0], bbox[2] + cell, cell)
    ys = np.arange(bbox[1], bbox[3] + cell, cell)
    X, Y = np.meshgrid(xs, ys)
    Z = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
    if sigma > 0:
        # 끊긴 등고선 사이 TIN의 각진 면이 톱니처럼 보이지 않도록 지형면을 살짝 고르게
        from scipy.ndimage import gaussian_filter
        Zs = gaussian_filter(Z, sigma / cell, mode="nearest")
        w = np.zeros(X.shape)
        if keep is not None:
            # 사업지 레벨로 다시 만든 대지 안은 고르게 펴지 않는다 (단·옹벽 높이가 그대로 판이 되게)
            import shapely
            d = shapely.distance(keep, shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip(1.0 - d / 6.0, 0.0, 1.0)
        if keep_w is not None:
            # 계단식 대지(건물 터·논)도 평평한 단과 옹벽이 뭉개지지 않게
            w = np.maximum(w, keep_w(X, Y))
        Z = w * Z + (1 - w) * Zs if w.any() else Zs
    # 0.1m 단위 간격(0.3m 등)도 쓰므로 높이는 k번째 판 = base + k·간격 으로 계산하고 반올림한다
    # (간격을 계속 더하면 52.300000001 같은 오차가 쌓여 판 이름·높이가 어긋났다)
    base = round(math.floor(Z.min() / interval + 1e-9) * interval, 6)
    top = round(math.floor(Z.max() / interval + 1e-9) * interval, 6)
    gen = contourpy.contour_generator(X, Y, Z, fill_type="OuterOffset")
    regions = {base: clip}
    edge_w = 2 * smooth + cell
    edge_band = clip.difference(clip.buffer(-edge_w, join_style="mitre"))
    k = 1
    lv = round(base + interval, 6)
    while lv <= top + 1e-6:
        pts, offs = gen.filled(lv, np.inf)
        polys = []
        for P, O in zip(pts, offs):
            rings = [P[O[i]:O[i + 1]] for i in range(len(O) - 1)]
            if len(rings[0]) >= 4:
                polys.append(Polygon(rings[0], [r for r in rings[1:] if len(r) >= 4]))
        g = unary_union([make_valid(p) for p in polys]).intersection(clip)
        raw = g
        # 격자 계단 제거 + 레이저로 자르기 어려운 가는 부분·작은 조각 제거
        g = g.buffer(smooth, join_style="round").buffer(-2 * smooth, join_style="round") \
             .buffer(smooth, join_style="round").intersection(clip)
        # 모델 테두리 가까운 점은 다듬지 않고, 테두리 띠는 다듬기 전 윤곽으로 되돌린다.
        # (판이 테두리를 따라 길게 닿으면 다듬기가 모델 모서리를 큰 삼각형으로 깎아 내던 것)
        g = smooth_poly(g.simplify(cell * 0.5), chaikin_n, bbox, tol=edge_w).intersection(clip)
        g = unary_union([g, raw.intersection(edge_band)]).intersection(clip)
        g = unary_union([p for p in as_polys(g) if p.area >= min_area])
        if not g.is_empty:
            # 윗판은 반드시 아랫판 안에 있어야 쌓을 수 있다
            g = g.intersection(regions[max(regions)])
            # 겹친 판의 교집합에 선·점이 섞이면(GeometryCollection) 경계를 못 구한다 → 면만 남긴다
            ps = [p for p in as_polys(g) if p.area >= min_area]
            if not ps:
                break
            g = unary_union(ps)
            regions[lv] = g
        k += 1
        lv = round(base + k * interval, 6)
    return regions, base


def level_at(regions, pt):
    """점이 올라앉는 가장 높은 판. 윗판은 아랫판 안에 있으므로(겹쳐 쌓임) 이분 탐색으로 찾는다."""
    lv = list(regions)
    lo, hi = 0, len(lv) - 1          # lv[lo] 는 항상 점을 품는다(맨 아래 판 = 모델 전체)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if regions[lv[mid]].contains(pt):
            lo = mid
        else:
            hi = mid - 1
    return lv[lo]


def _dist_to_lines(lines, pts):
    """점마다 선까지 최단거리. 선을 짧은 토막으로 나눠 공간 색인으로 가장 가까운 토막만 재므로,
    긴 등고선(판이 많을 때)에서도 빠르다. shapely.distance(lines, pts) 와 같은 값."""
    import shapely
    segs = []
    for part in shapely.get_parts(lines):
        c = shapely.get_coordinates(part)
        if len(c) >= 2:
            segs.append(np.stack([c[:-1], c[1:]], axis=1))
    if not segs:
        return np.full(len(pts), np.inf)
    segs = shapely.linestrings(np.concatenate(segs))
    tree = shapely.STRtree(segs)
    idx, d = tree.query_nearest(pts, return_distance=True, all_matches=False)
    out = np.full(len(pts), np.inf)
    out[idx[0]] = d
    return out


def slope_heightfield(regions, bbox, cell=1.0, cap=60.0, return_levels=False):
    """계단 사이 높이차를 완만한 경사면으로 이은 높이 격자.

    두 등고선(아래 L, 위 L+간격) 사이의 점은 아래선까지 거리 dL, 위선까지
    거리 dU의 비율로 높이를 정한다: H = L + 간격 * dL / (dL + dU).
    그래서 경사가 두 등고선 사이 전체 폭에 고르게 퍼지고, 등고선 위에서는
    정확히 그 표고가 된다. 위 등고선이 없는 봉우리는 평평하게 둔다.
    반환: (X, Y, H)
    """
    import shapely
    clip = box(*bbox)
    edge = clip.exterior.buffer(0.05)
    xs = np.arange(bbox[0], bbox[2] + cell * 0.5, cell)
    ys = np.arange(bbox[1], bbox[3] + cell * 0.5, cell)
    X, Y = np.meshgrid(xs, ys)
    lv = list(regions)
    interval = lv[1] - lv[0] if len(lv) > 1 else 1.0
    # 모델 가장자리 점은 곡선 다듬기로 윤곽이 살짝 안쪽에 있을 수 있어 조금 들여서 판정
    Xc = np.clip(X, bbox[0] + 0.5, bbox[2] - 0.5)
    Yc = np.clip(Y, bbox[1] + 0.5, bbox[3] - 0.5)
    inside = []
    for L in lv:
        if L == lv[0]:
            inside.append(np.ones(X.shape, bool))
            continue
        # 윗판은 아랫판 안에 있으므로, 아랫판 안의 점만 시험한다(판이 많아도 빠름)
        prev = inside[-1]
        cur = np.zeros(X.shape, bool)
        if prev.any():
            shapely.prepare(regions[L])
            cur[prev] = shapely.intersects_xy(regions[L], Xc[prev], Yc[prev])
        inside.append(cur)
    lines = [regions[L].boundary.difference(edge) for L in lv]
    H = np.full(X.shape, float(lv[0]))
    for i, L in enumerate(lv):
        band = inside[i] & (~inside[i + 1] if i + 1 < len(lv) else True)
        if not band.any():
            continue
        pts = shapely.points(X[band], Y[band])
        dL = _dist_to_lines(lines[i], pts) if i > 0 and not lines[i].is_empty else np.full(len(pts), cap)
        if i + 1 < len(lv) and not lines[i + 1].is_empty:
            dU = _dist_to_lines(lines[i + 1], pts)
        else:
            dU = np.full(len(pts), np.inf)
        dL = np.minimum(np.nan_to_num(dL, nan=cap), cap)
        t = np.where(np.isinf(dU), 0.0, dL / np.maximum(dL + dU, 1e-9))
        H[band] = L + interval * t
    if return_levels:
        B = np.full(X.shape, float(lv[0]))
        for i, L in enumerate(lv):
            B[inside[i]] = L
        return X, Y, H, B
    return X, Y, H


def building_pads(X, Y, H, masses, apron=2.0):
    """건물 밑 대지를 평평한 터(pad)로 고르고, 둘레 apron(m)에서 경사면과 잇는다.

    경사면 위에 건물이 떠 있거나 한쪽이 파묻히지 않도록, 건물 바닥 높이를
    발자국 안 지형의 중앙값으로 정하고 그 높이로 대지를 깎거나 채운다.
    H를 그 자리에서 고치고, 각 건물의 pad 높이를 masses에 기록한다.
    """
    import shapely
    cell = X[0, 1] - X[0, 0]
    x0, y0 = X[0, 0], Y[0, 0]
    pad_mask = np.zeros(H.shape, bool)
    H0 = H.copy()
    # 큰 건물부터 → 작은 부속 건물이 큰 건물 터를 덮어쓰지 않게
    for m in sorted(masses, key=lambda m: -m["poly"].area):
        p = m["poly"]
        bx = p.buffer(apron).bounds
        i0, i1 = max(int((bx[1] - y0) / cell), 0), min(int((bx[3] - y0) / cell) + 2, H.shape[0])
        j0, j1 = max(int((bx[0] - x0) / cell), 0), min(int((bx[2] - x0) / cell) + 2, H.shape[1])
        if i0 >= i1 or j0 >= j1:
            continue
        xs, ys = X[i0:i1, j0:j1], Y[i0:i1, j0:j1]
        pts = shapely.points(xs, ys)
        d = shapely.distance(p, pts)
        inside = d == 0
        sub = H0[i0:i1, j0:j1]
        if inside.any():
            pad = float(np.median(sub[inside]))
        else:
            c = p.representative_point()
            pad = float(sub.ravel()[np.argmin(shapely.distance(c, pts).ravel())])
        already = pad_mask[i0:i1, j0:j1]
        if (already & inside).any():
            # 이미 다른 건물 터 위(붙은 건물·부속동)면 그 높이를 따른다
            pad = float(np.median(H[i0:i1, j0:j1][already & inside]))
        w = np.clip(d / apron, 0, 1)  # 0: 터, 1: 원래 경사
        Hs = H[i0:i1, j0:j1]
        blend = pad * (1 - w) + Hs * w
        ring = ~already & (d < apron)
        Hs[ring] = blend[ring]
        Hs[inside] = pad
        pad_mask[i0:i1, j0:j1] |= inside
        m["pad"] = pad
        if inside.any():
            m["cutfill"] = float(np.abs(sub[inside] - pad).max())
    return H


def surface_sampler(X, Y, H):
    from scipy.interpolate import RegularGridInterpolator
    f = RegularGridInterpolator((Y[:, 0], X[0]), H, bounds_error=False, fill_value=None)
    return lambda xs, ys: f(np.column_stack([ys, xs]))


def drape_line_3d(ls, hz, step=2.0):
    ls = ls.segmentize(step)
    xy = np.asarray(ls.coords)[:, :2]
    return LineString(np.column_stack([xy, hz(xy[:, 0], xy[:, 1]) + 0.03]))


def heightfield_mesh(X, Y, H, bottom, origin):
    """높이 격자를 바닥이 닫힌 솔리드 메시로."""
    ny, nx = H.shape
    idx = np.arange(nx * ny).reshape(ny, nx)
    top = np.column_stack([X.ravel() - origin[0], Y.ravel() - origin[1], H.ravel()])
    bot = top.copy(); bot[:, 2] = np.ravel(bottom) if np.ndim(bottom) else bottom
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = [np.column_stack([a, b, c]), np.column_stack([a, c, d])]
    n = nx * ny
    faces += [np.column_stack([a + n, c + n, b + n]), np.column_stack([a + n, d + n, c + n])]
    ring = np.concatenate([idx[0, :], idx[:, -1][1:], idx[-1, ::-1][1:], idx[::-1, 0][1:]])
    p, q = ring[:-1], ring[1:]
    faces += [np.column_stack([p, p + n, q]), np.column_stack([q, p + n, q + n])]
    m = trimesh.Trimesh(np.vstack([top, bot]), np.vstack(faces), process=True)
    trimesh.repair.fix_normals(m)
    return m


def simplify_solid(m, tol=0.01):
    """닫힌 지형 메시에서 평평하거나 고르게 기운 곳의 잔 삼각형을 합친다 (모양 차이 tol m 이하).

    1m 격자 그대로면 1000m 모델 지형 하나가 꼭짓점 200만 개라 Rhino 저장만 70초, GLB 85MB 가 된다.
    계단판·터처럼 평평한 곳이 많아 꼭짓점이 1/6~1/10 로 준다. 실패하면 원래 메시를 그대로 쓴다.
    """
    if len(m.vertices) < 20000:
        return m
    try:
        import manifold3d as mf
        M = _mf_from_trimesh(m)
        if M.status() != mf.Error.NoError:
            return m
        out = M.simplify(tol).to_mesh()
        v = np.asarray(out.vert_properties, dtype=float)[:, :3]
        s = trimesh.Trimesh(v, np.asarray(out.tri_verts, dtype=np.int64), process=False)
    except Exception:
        return m
    if len(s.faces) == 0 or not s.is_watertight:
        return m
    return s


# ---------------------------------------------------------------- 도로 드레이프
def drape_lines(lines, regions):
    """선형 요소를 각 계단 판 위로 나눠 올린다. 반환: [(LineString, level)]"""
    lv = list(regions)
    merged = unary_union([l for l in lines if not l.is_empty])
    out = []
    for i, L in enumerate(lv):
        band = regions[L]
        if i + 1 < len(lv):
            band = band.difference(regions[lv[i + 1]])
        part = merged.intersection(band)
        for g in getattr(part, "geoms", [part]):
            if isinstance(g, LineString) and g.length > 0.5:
                out.append((g, L))
    return out

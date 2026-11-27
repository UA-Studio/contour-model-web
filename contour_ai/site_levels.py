"""대지 안 지형을 사업지 레벨로: 레벨 보간과 경계 밖 지형과 섞기."""




def level_interpolator(levels, map_interp, site, log=print, tol=30.0, cover=None):
    """사업지 레벨로 만든 보간 함수. 수치지도 지형과 tol(m) 넘게 다른 숫자(치수·면적 글자 등)는 버린다."""
    import numpy as np
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
    lines, spots = levels
    P, Z = [], []
    for ls, z in lines:
        for c in ls.segmentize(2.0).coords:
            P.append(c[:2]); Z.append(z)
    for x, y, z in spots:
        P.append((x, y)); Z.append(z)
    if not P:
        return None
    import shapely
    P, Z = np.asarray(P, float), np.asarray(Z, float)
    # 대지 둘레 30m 밖 자료는 쓰지 않는다 (수치지도를 넓게 잘라 붙인 도면이면 대부분이 여기서 빠진다)
    around = shapely.contains_xy(site.buffer(30), P[:, 0], P[:, 1])
    P, Z = P[around], Z[around]
    if not len(P):
        log("   (사업지 도면의 레벨이 대지 둘레에 없어 수치지도 지형을 그대로 씁니다)")
        return None
    ok = np.abs(Z - map_interp(P)) <= tol
    if cover is not None:
        # 수치지도 자료가 없는 곳(넣은 도엽 밖)은 비교할 지형이 없으니 도면 레벨을 그대로 믿는다
        ok |= ~shapely.contains_xy(cover, P[:, 0], P[:, 1])
    if (~ok).any():
        log(f"   (사업지 레벨 가운데 {int((~ok).sum())}개는 지형과 {tol:g}m 넘게 달라 치수·면적 글자로 보고 뺐습니다)")
    P, Z = P[ok], Z[ok]
    if len(P) < 3:
        log("   (사업지 도면에서 쓸 만한 레벨을 3개 넘게 찾지 못해 수치지도 지형을 그대로 씁니다)")
        return None
    P, idx = np.unique(np.round(P, 3), axis=0, return_index=True)
    Z = Z[idx]
    try:
        lin = LinearNDInterpolator(P, Z)
    except Exception:
        lin = None
    near = NearestNDInterpolator(P, Z)

    def f(xy):
        xy = np.atleast_2d(np.asarray(xy, float))
        v = lin(xy) if lin is not None else np.full(len(xy), np.nan)
        bad = np.isnan(v)
        if bad.any():
            v[bad] = near(xy[bad])
        return v
    f.count = len(P)
    return f


def blended(site, site_f, map_f, blend=15.0):
    """대지 안은 사업지 레벨, 경계 밖 blend(m)까지는 섞고, 그 밖은 수치지도 지형"""
    import numpy as np
    import shapely
    inner = site
    shapely.prepare(inner)

    def f(xy):
        xy = np.atleast_2d(np.asarray(xy, float))
        out = map_f(xy)
        pts = shapely.points(xy[:, 0], xy[:, 1])
        d = shapely.distance(inner, pts)
        w = np.clip(1.0 - d / blend, 0.0, 1.0)
        m = w > 0
        if m.any():
            out[m] = w[m] * site_f(xy[m]) + (1 - w[m]) * out[m]
        return out
    return f

"""도면 좌표 → 수치지도 좌표: 도면에 남은 표고점과 도엽 표고점을 맞춰 축척·회전·이동을 찾는다."""
import re
import os


from shapely.geometry import box
from .site_read import NOT_LEVEL_RE, SiteError, _open_dxf


# ---------------------------------------------------------------- 도면 좌표 → 수치지도 좌표 (표고점 맞추기)
# 설계 사무소 배치도는 수치지도를 mm 로 키워 도면 원점에 옮겨 붙인 경우가 많다(실제 좌표가 아님).
# 도면에 수치지도 표고점 숫자(레이어 F002xxxx)가 남아 있으면, 넣은 도엽의 표고점과 높이·간격을 맞춰
# 축척·회전·이동(필요하면 좌우 뒤집기)을 찾는다.
SPOT_LAYER_RE = re.compile(r"^(?:N\dP_)?F002\d{4}$", re.I)


_AFFINE = {}


def _drawing_spots(path):
    """도면의 수치지도 표고점 숫자(F002 레이어)를 모은다. 블록 안(회전·축척된 지도 블록)까지 삽입 행렬로 펼친다."""
    import numpy as np
    from ezdxf.math import Matrix44
    doc = _open_dxf(path)
    out, syms, other = [], [], []

    def walk(entities, M, inherit, depth):
        for e in entities:
            t = e.dxftype()
            lay = str(e.dxf.get("layer", "0"))
            if lay == "0" and inherit:
                lay = inherit
            try:
                if t == "INSERT":
                    if SPOT_LAYER_RE.match(lay):
                        syms.append(tuple(M.transform(e.dxf.insert))[:2])
                    if depth < 6:
                        blk = doc.blocks.get(e.dxf.name)
                        if blk is not None and (depth == 0 or not SPOT_LAYER_RE.match(lay)):
                            walk(blk, e.matrix44() * M, lay, depth + 1)
                elif t in ("TEXT", "MTEXT"):
                    txt = re.sub(r"\\[A-Za-z][^;]*;|[{}]", "", str(e.dxf.text if t == "TEXT" else e.text)).strip()
                    if SPOT_LAYER_RE.match(lay) and re.fullmatch(r"-?\d{1,4}(?:\.\d{1,2})?", txt):
                        p = M.transform(e.dxf.insert)
                        out.append((float(p[0]), float(p[1]), float(txt)))
                    elif not NOT_LEVEL_RE.search(lay) and re.fullmatch(r"-?\d{1,4}\.\d", txt):
                        p = M.transform(e.dxf.insert)      # 다른 레이어로 옮겨진 표고점 숫자(예비)
                        other.append((float(p[0]), float(p[1]), float(txt)))
            except Exception:
                continue
    walk(doc.modelspace(), Matrix44(), None, 0)
    if len(out) < 3 and len(other) >= 4:
        # 수치지도 표고점 레이어가 없으면(지도를 다른 레이어로 옮겨 붙인 배치도) 소수점 한 자리 숫자를 예비 표고점으로 쓴다.
        # 이때는 맞추기에서 더 많은 점이 맞아야 인정한다(drawing_affine 의 spare).
        _drawing_spots.spare = True
        return np.array(other).reshape(-1, 3)
    _drawing_spots.spare = False
    D = np.array(out).reshape(-1, 3)
    # 수치지도 DXF 는 표고점 기호(블록)를 점 자리에 두고 숫자는 옆으로 비켜 쓴다(1:5000 은 +7m, −5m).
    # 기호가 있으면 숫자마다 가장 가까운 기호 자리를 쓴다(비켜 쓴 거리가 위치 오차로 남지 않게).
    sym = np.array(syms, float).reshape(-1, 2)
    if len(D) >= 3 and len(sym) >= 3:
        from scipy.spatial import cKDTree
        d, i = cKDTree(sym).query(D[:, :2])
        off = D[:, :2] - sym[i]
        med = np.median(off, axis=0)
        same = np.hypot(*(off - med).T) <= max(0.05 * np.hypot(*med), 1e-6 + 0.02 * np.ptp(D[:, :2], axis=0).max() * 0.01)
        if same.sum() >= 0.6 * len(D):
            D[:, :2] = D[:, :2] - med       # 모든 숫자를 같은 만큼 기호 자리로 되돌린다
    return D


def _map_spots(folder):
    import numpy as np
    import shapefile
    fn = os.path.join(folder, "N3P_F0020000")
    if not folder or not os.path.exists(fn + ".shp"):
        return np.zeros((0, 3))
    r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
    k = [f[0] for f in r.fields[1:]].index("수치") if "수치" in [f[0] for f in r.fields[1:]] else None
    if k is None:
        return np.zeros((0, 3))
    out = []
    for sr in r.iterShapeRecords():
        try:
            out.append((sr.shape.points[0][0], sr.shape.points[0][1], float(sr.record[k])))
        except Exception:
            continue
    return np.array(out).reshape(-1, 3)


def _similarity(P, Q):
    """P→Q 닮음 변환(축척·회전·이동) 최소제곱"""
    import numpy as np
    mp, mq = P.mean(0), Q.mean(0)
    A, B = P - mp, Q - mq
    U, S, Vt = np.linalg.svd(A.T @ B)
    d = np.sign(np.linalg.det(U @ Vt)) or 1.0
    Dm = np.diag([1.0, d])
    R = (U @ Dm @ Vt).T
    sc = (S * np.diag(Dm)).sum() / max((A ** 2).sum(), 1e-12)
    return sc, R, mq - sc * R @ mp


def drawing_affine(path, sheet, map_folder, log=print):
    """도면이 실제 좌표가 아니면 표고점으로 맞춘 변환을 돌려준다.
    반환: None(이미 실제 좌표이거나 DXF 가 아님) 또는 dict(m=shapely affine 6개 값, n=맞은 점 수, total, rms)"""
    import numpy as np
    if not str(path).lower().endswith(".dxf") or not sheet:
        return None
    key = (os.path.abspath(path), os.path.getmtime(path), map_folder, tuple(sheet))
    if key in _AFFINE:
        return _AFFINE[key]
    doc = _open_dxf(path)
    ext = None
    try:
        from ezdxf import bbox as _bb
        e = _bb.extents(doc.modelspace().query("LWPOLYLINE LINE POLYLINE"), fast=True)
        ext = (e.extmin.x, e.extmin.y, e.extmax.x, e.extmax.y) if e.has_data else None
    except Exception:
        pass
    sb = box(*sheet)
    D = _drawing_spots(path)
    M = _map_spots(map_folder)
    unit = {4: "mm", 5: "cm", 6: "m"}.get(int(doc.header.get("$INSUNITS", 0) or 0), "")
    if len(D) >= 3 and len(M) >= 3:
        # 표고점이 이미 제자리(수치지도 좌표)에 있으면 실제 좌표 도면
        from scipy.spatial import cKDTree
        dist, idx = cKDTree(M[:, :2]).query(D[:, :2])
        if ((dist < 2.0) & (np.abs(M[idx, 2] - D[:, 2]) <= 0.15)).sum() >= max(3, len(D) // 2):
            _AFFINE[key] = None
            return None
    real = ext is not None and max(ext[2] - ext[0], ext[3] - ext[1]) < 50_000 and (
        box(*ext).buffer(2000).intersects(sb) or _looks_real(ext))
    if ext is None or real:      # 실제 좌표 도면(도엽 밖이면 경계 읽기에서 '도엽 밖' 안내)
        _AFFINE[key] = None
        return None
    if len(D) < 3:
        raise SiteError("이 도면은 실제 좌표가 아니라 도면 자체 좌표" + (f"({unit} 단위)" if unit else "") +
                        "로 그려져 있어 지도 위 자리를 알 수 없습니다. 도면에 수치지도 표고점 숫자가 3개 이상 있으면 "
                        "자동으로 자리를 맞추는데, 이 도면에는 없습니다. CAD에서 도면을 실제 좌표(수치지도와 같은 좌표, m 단위)로 "
                        "옮겨 저장하거나, 연속지적도 경계 파일을 넣어 주세요.")
    if len(M) < 3:
        raise SiteError("이 도면은 도면 자체 좌표라 수치지도 표고점으로 자리를 맞춰야 하는데, 넣은 수치지도에 표고점 레이어(N3P_F0020000)가 없습니다.")
    from itertools import combinations
    from scipy.spatial import cKDTree
    tree = cKDTree(M[:, :2])
    byz = {}
    for i, z in enumerate(M[:, 2]):
        byz.setdefault(round(z, 1), []).append(i)
    best = None
    for i, j in combinations(range(len(D)), 2):
        dd = float(np.hypot(*(D[i, :2] - D[j, :2])))
        if dd <= 0:
            continue
        for a in byz.get(round(D[i, 2], 1), []):
            for b in byz.get(round(D[j, 2], 1), []):
                dm = float(np.hypot(*(M[a, :2] - M[b, :2])))
                if a == b or dm < 5 or not (1e-5 < dm / dd < 1e3):
                    continue
                for flip in (1.0, -1.0):
                    F = np.array([flip, 1.0])
                    sc, R, t = _similarity(D[[i, j], :2] * F, M[[a, b], :2])
                    T = (sc * (R @ (D[:, :2] * F).T)).T + t
                    dist, idx = tree.query(T)
                    ok = (dist < 2.0) & (np.abs(M[idx, 2] - D[:, 2]) <= 0.15)
                    if best is None or ok.sum() > best[0]:
                        best = (int(ok.sum()), flip, (sc, R, t))
        if best and best[0] == len(D):
            break
    spare = getattr(_drawing_spots, "spare", False)
    need = max(5, int(np.ceil(0.25 * len(D)))) if spare else max(3, int(np.ceil(0.5 * len(D))))
    if best is None or best[0] < need:
        raise SiteError("이 도면은 도면 자체 좌표" + (f"({unit} 단위)" if unit else "") + f"라 표고점 {len(D)}개로 자리를 맞추려 했지만, "
                        f"넣은 수치지도 도엽의 표고점과 {0 if best is None else best[0]}개만 맞았습니다. 대지가 들어 있는 도엽 zip을 넣었는지 확인해 주세요.")
    n, flip, (sc, R, t) = best
    F = np.array([flip, 1.0])
    for _ in range(3):      # 맞은 점 모두로 다시 맞추기
        T0 = (sc * (R @ (D[:, :2] * F).T)).T + t
        dist, idx = tree.query(T0)
        ok = (dist < 2.0) & (np.abs(M[idx, 2] - D[:, 2]) <= 0.15)
        sc, R, t = _similarity(D[ok, :2] * F, M[idx[ok], :2])
    T0 = (sc * (R @ (D[:, :2] * F).T)).T + t
    res = np.hypot(*(T0[ok] - M[tree.query(T0[ok])[1], :2]).T)
    A = sc * R @ np.diag(F)
    out = dict(m=[A[0, 0], A[0, 1], A[1, 0], A[1, 1], t[0], t[1]], n=int(ok.sum()), total=len(D),
               rms=float(np.sqrt((res ** 2).mean())), scale=float(sc), rot=float(np.degrees(np.arctan2(R[1, 0], R[0, 0]))),
               flip=flip < 0, unit=unit)
    log(f"   도면 좌표를 수치지도 표고점 {out['n']}/{out['total']}개로 맞췄습니다 (축척 ×{sc:.6g}, 회전 {out['rot']:.2f}°"
        + (", 좌우 뒤집힘" if out["flip"] else "") + f", 오차 {out['rms']:.2f} m)")
    _AFFINE[key] = out
    return out


def _looks_real(ext):
    """도면 범위가 국내 평면직각좌표(중부원점 등)·UTM-K·경위도처럼 보이면 True"""
    x0, y0, x1, y1 = ext
    if -180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90 and (x1 - x0) < 5:
        return True
    w = max(x1 - x0, y1 - y0)
    return w < 50_000 and (100_000 <= x0 <= 1_200_000 and 200_000 <= y0 <= 2_200_000)

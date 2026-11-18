"""사업 대지(사이트) 경계: 파일·직접 그린 점에서 읽고, 모델에서 대지 위를 비운다.

경계를 넣는 길은 세 가지다.
- 주소 → 브이월드 연속지적도 필지 (landlaw.lookup, 키 필요)
- 경계 파일: CAD(DXF) 닫힌 폴리선, SHP(연속지적도 등, zip 가능), GeoJSON
- 화면 지도에서 직접 찍은 꼭짓점

파일 좌표계는 .prj 가 있으면 그것을 쓰고, 없으면 국내에서 쓰는 좌표계를 차례로 넣어 보아
수치지도 도엽 안(또는 가장 가까이)에 떨어지는 것을 고른다.
"""
import json
import re
import os
import tempfile
import zipfile

from shapely import make_valid

from shapely.geometry import MultiPolygon, Point, Polygon, box, shape
from shapely.ops import transform, unary_union

# 국내 평면직각좌표: 중부·서부·동부·동해(2010), UTM-K, 옛 지적(Bessel) 중부·서부·동부, 경위도
CANDIDATE_EPSG = (5186, 5185, 5187, 5188, 5179, 5174, 5173, 5176, 5181, 5180, 5183, 2097, 4326)
MIN_AREA = 10.0          # ㎡: 이보다 작은 닫힌 도형은 경계로 보지 않는다(문자·기호)
MAX_AREA = 4_000_000.0   # ㎡: 2km × 2km 를 넘으면 대지가 아니라 도곽·테두리


class SiteError(ValueError):
    pass


def _polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon" and not p.is_empty]


def clean(g):
    """면만 남기고 정리. 반환: Polygon 또는 MultiPolygon (비면 None)"""
    ps = [p for p in _polys(make_valid(g)) if p.area >= MIN_AREA]
    if not ps:
        return None
    u = unary_union(ps).buffer(0)
    return u if not u.is_empty else None


def from_ring(ring):
    """화면에서 찍은 꼭짓점 [[x, y], …] (수치지도 좌표) → 대지 면"""
    pts = [(float(p[0]), float(p[1])) for p in ring or []]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        raise SiteError("대지 경계는 꼭짓점이 3개 이상이어야 합니다.")
    g = clean(Polygon(pts))
    if g is None:
        raise SiteError("찍은 점으로 면을 만들 수 없습니다(너무 작거나 선이 꼬였습니다).")
    return g


# ---------------------------------------------------------------- 파일 읽기
_DXF_CACHE = {}


def _open_dxf(path):
    """DXF 를 한 번만 연다 (경계·레벨을 따로 읽어도 같은 문서를 쓴다)"""
    import ezdxf
    key = (os.path.abspath(path), os.path.getmtime(path))
    if key not in _DXF_CACHE:
        try:
            doc = ezdxf.readfile(path)
        except Exception:
            from ezdxf import recover
            doc, _ = recover.readfile(path)
        _DXF_CACHE.clear()
        _DXF_CACHE[key] = doc
    return _DXF_CACHE[key]


def _read_dxf(path):
    doc = _open_dxf(path)
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        try:
            if t == "LWPOLYLINE":
                if abs(float(e.dxf.get("elevation", 0) or 0)) > 1e-6:
                    continue           # 높이가 있는 폴리선 = 등고선 → 경계 후보가 아님
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                closed = bool(e.closed)
            elif t == "POLYLINE":
                if any(abs(v.dxf.location.z) > 1e-6 for v in e.vertices):
                    continue
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                closed = bool(e.is_closed)
            elif t == "HATCH":
                for bp in e.paths:
                    vs = [(v[0], v[1]) for v in getattr(bp, "vertices", [])]
                    if len(vs) >= 3:
                        out.append((Polygon(vs), e.dxf.layer))
                continue
            else:
                continue
        except Exception:
            continue
        if len(pts) < 3:
            continue
        # 닫지 않고 끝점만 맞춘 폴리선도 받는다(끝이 전체 크기의 1% 안에서 만남)
        span = max(max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts))
        if closed or Point(pts[0]).distance(Point(pts[-1])) <= max(0.01, span * 0.01):
            out.append((Polygon(pts), e.dxf.layer))
    if not out:
        # 낱선(LINE)으로만 그린 경계: 이어 붙여 면으로
        from shapely.geometry import LineString
        from shapely.ops import polygonize
        segs = []
        for e in doc.modelspace().query("LINE"):
            a, b = e.dxf.start, e.dxf.end
            segs.append(LineString([(a.x, a.y), (b.x, b.y)]))
        out = [(p, "") for p in polygonize(unary_union(segs))] if segs else []
    return out, None


def _read_shp(base):
    import shapefile
    crs = None
    if os.path.exists(base + ".prj"):
        try:
            from pyproj import CRS
            crs = CRS.from_wkt(open(base + ".prj", encoding="utf-8", errors="replace").read())
        except Exception:
            crs = None
    out = []
    for enc in ("euc-kr", "utf-8"):
        try:
            r = shapefile.Reader(base, encoding=enc, encodingErrors="replace")
            names = [f[0] for f in r.fields[1:]]
            for sr in r.iterShapeRecords():
                g = shape(sr.shape.__geo_interface__)
                rec = dict(zip(names, sr.record))
                lab = str(rec.get("JIBUN") or rec.get("PNU") or rec.get("지번") or "")
                for p in _polys(make_valid(g)):
                    out.append((p, lab))
            break
        except UnicodeDecodeError:
            out = []
    return out, crs


def _read_geojson(path):
    j = json.load(open(path, encoding="utf-8-sig"))
    feats = j.get("features") if j.get("type") == "FeatureCollection" else [j]
    out = []
    for f in feats:
        geom = f.get("geometry", f) if isinstance(f, dict) else None
        if not geom or "coordinates" not in geom:
            continue
        for p in _polys(make_valid(shape(geom))):
            out.append((p, ""))
    crs = None
    name = ((j.get("crs") or {}).get("properties") or {}).get("name")
    if name:
        try:
            from pyproj import CRS
            crs = CRS.from_user_input(name)
        except Exception:
            crs = None
    return out, crs


def _read_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".zip":
        work = tempfile.mkdtemp(prefix="contour_site_")
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                name = info.filename
                try:      # 한글 파일 이름(cp949)으로 묶인 zip
                    name = name.encode("cp437").decode("cp949")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                name = os.path.basename(name)
                if not name or info.is_dir():
                    continue
                with z.open(info) as s, open(os.path.join(work, name), "wb") as d:
                    d.write(s.read())
        for want in (".shp", ".dxf", ".geojson", ".json"):
            hit = sorted(f for f in os.listdir(work) if f.lower().endswith(want))
            if hit:
                return _read_any(os.path.join(work, hit[0]))
        raise SiteError("zip 안에 경계 파일(SHP·DXF·GeoJSON)이 없습니다.")
    if ext == ".dxf":
        return _read_dxf(path)
    if ext == ".shp":
        return _read_shp(os.path.splitext(path)[0])
    if ext in (".geojson", ".json"):
        return _read_geojson(path)
    if ext == ".dwg":
        raise SiteError("DWG는 바로 읽지 못합니다. CAD에서 '다른 이름으로 저장 → DXF'로 바꿔 넣어 주세요.")
    raise SiteError(f"읽을 수 없는 경계 파일 형식입니다: {ext or path} (DXF·SHP·zip·GeoJSON)")


def _to_map(polys, src_crs, map_crs):
    from pyproj import Transformer
    t = Transformer.from_crs(src_crs, map_crs, always_xy=True)
    return [transform(lambda x, y, z=None: t.transform(x, y), p) for p in polys]


NAME_RE = re.compile(r"경계|바운더리|대지|지적|부지|사업|구역|SITE|BOUND|PARCEL|LOT", re.I)
# 수치지도 레이어 코드(1.0: A0013111, 2.0: N3A_B0010000) — 건물·도로 같은 지도 도형이지 대지 경계가 아니다
MAP_LAYER_RE = re.compile(r"^(?:N\d[ALPT]_)?[A-H]\d{7}$", re.I)


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


def load_boundary(path, crs_wkt, sheet=None, log=print, map_folder=None):
    """경계 파일 → 수치지도 좌표의 대지 면.

    map_folder: 넣은 수치지도 SHP 폴더. 도면이 실제 좌표가 아니면 그 표고점으로 자리를 맞춘다.

    sheet: 도엽 범위 (x0, y0, x1, y1). 좌표계를 모를 때 도엽 안에 떨어지는 좌표계를 고르는 데 쓴다.
    닫힌 도형이 여러 개면 도엽 안에 있는 것만 모아 하나의 대지로 본다(여러 필지).
    """
    from pyproj import CRS
    if not os.path.isfile(path):
        raise SiteError(f"대지 경계 파일을 찾지 못했습니다: {path}")
    from .sitefile import is_standard, read_standard
    if is_standard(path):
        # 표준 대지 파일(정리 화면에서 저장한 것): SITE_경계만, 이미 수치지도 좌표
        g = read_standard(path)["poly"]
        log(f"   대지 경계 파일: {os.path.basename(path)} · 표준 대지 파일 · {g.area:,.0f}㎡")
        _LAST_CRS[path] = CRS.from_wkt(crs_wkt)
        return g
    items, crs = _read_any(path)
    items = [(p, lab) for p, lab in items if not p.is_empty]
    aff = drawing_affine(path, sheet, map_folder, log) if map_folder else None
    if aff:
        from shapely.affinity import affine_transform
        items = [(affine_transform(p, aff["m"]), lab) for p, lab in items]
    # 수치지도를 잘라 붙인 도면: 지도 레이어(건물·도로 등) 도형은 경계 후보에서 뺀다
    own = [(p, lab) for p, lab in items if not MAP_LAYER_RE.match(str(lab or ""))]
    if own:
        items = own
    # 도곽: 도면 전체를 감싸는 네모는 다른 후보가 있으면 뺀다
    if len(items) > 1:
        from shapely.geometry import box as _bx
        ext = _bx(*unary_union([make_valid(p).envelope for p, _ in items]).bounds)
        frame = [i for i, (p, _) in enumerate(items)
                 if len(p.exterior.coords) <= 6 and p.area >= 0.98 * p.minimum_rotated_rectangle.area
                 and p.area >= 0.8 * ext.area]
        if frame and len(frame) < len(items):
            items = [it for i, it in enumerate(items) if i not in frame]
    polys = [p for p, _ in items]
    names = [bool(NAME_RE.search(str(lab))) for _, lab in items]
    if not polys:
        raise SiteError("경계 파일에서 닫힌 도형을 찾지 못했습니다. 대지 경계를 닫힌 폴리선으로 그려 주세요.")
    map_crs = CRS.from_wkt(crs_wkt)
    region = box(*sheet) if sheet else None

    def score(ps):
        """도엽 안에 든 넓이 비율(클수록 좋음), 안 닿으면 -거리"""
        u = unary_union([make_valid(p) for p in ps])
        if region is None:
            return 0.0
        if u.intersects(region):
            return u.intersection(region).area / max(u.area, 1e-9)
        return -u.distance(region)

    picked, label = None, None
    if crs is not None:
        cand = [(crs, crs.name)]
    elif aff:
        cand = [(map_crs, f"도면 좌표를 표고점 {aff['n']}개로 맞춤")]
    else:
        cand = [(map_crs, "수치지도와 같은 좌표")] + [(CRS.from_epsg(c), f"EPSG:{c}") for c in CANDIDATE_EPSG]
    best = None
    best_crs = {lab: c for c, lab in cand}
    for c, lab in cand:
        try:
            ps = polys if c == map_crs else _to_map(polys, c, map_crs)
        except Exception:
            continue
        if not ps or any(not all(map(lambda v: v == v and abs(v) < 1e9, p.bounds)) for p in ps):
            continue
        s = score(ps)
        if best is None or s > best[0] + 1e-6:
            best = (s, ps, lab)
        if s >= 0.999:
            break
    if best is None:
        raise SiteError("경계 파일의 좌표를 읽지 못했습니다.")
    s, picked, label = best
    if region is not None and s <= 0:
        far = -s
        raise SiteError("대지 경계가 넣은 수치지도 도엽 밖에 있습니다"
                        + (f"(도엽에서 약 {far / 1000:.1f}km)." if far < 5e5 else ".")
                        + " 대지가 들어 있는 도엽 zip을 넣거나, 경계 파일이 실제 좌표(수치지도와 같은 좌표)로 그려졌는지 확인해 주세요.")
    # 도곽·표제란 같은 큰 테두리와 문자 조각은 빼고, 도엽에 걸친 것만
    def usable(qs):
        return [p for q in qs for p in _polys(make_valid(q))
                if MIN_AREA <= p.area <= MAX_AREA and (region is None or p.intersects(region))]
    keep = usable(picked)
    if not keep:
        raise SiteError("경계 파일에서 대지로 볼 만한 닫힌 도형(10㎡~4㎢)을 찾지 못했습니다.")
    # 레이어 이름이 경계·대지·지적·SITE 인 도형이 있으면 그것만 (건물·포장 같은 다른 닫힌 선은 무시)
    keep_named = usable([q for q, n in zip(picked, names) if n])
    if keep_named:
        keep = keep_named
    # 한 도형이 다른 도형들을 모두 품고 있으면(대지 경계 안에 건물 배치 등) 가장 바깥 것이 대지
    keep.sort(key=lambda p: -p.area)
    outer = keep[0]
    if len(keep) > 1 and all(outer.buffer(0.5).contains(p) for p in keep[1:]):
        keep = [outer]
    g = clean(unary_union(keep))
    if g is None:
        raise SiteError("경계 파일의 도형을 면으로 만들지 못했습니다.")
    log(f"   대지 경계 파일: {os.path.basename(path)} · 도형 {len(keep)}개 · {g.area:,.0f}㎡ ({label})")
    _LAST_CRS[path] = best_crs[label]
    return g


_LAST_CRS = {}
NOT_LEVEL_RE = re.compile(r"층수|층|FLOOR|면적|AREA|치수|DIM|밀도|용적|건폐", re.I)
LEVEL_RE = re.compile(r"(?:^|[^\d.])(?:EL|FL|GL|SL|TL|BL|H)?\s*[=:]?\s*\+?\s*(-?\d{1,4}\.\d{1,3})(?![\d.])", re.I)


def _dxf_levels(path, kinds=None):
    """사업지 도면(DXF)의 레벨: 높이가 있는 등고선(폴리선·3D 폴리선·선), 표고점(POINT), 레벨 숫자 글자(TEXT·MTEXT·블록 속성).
    반환: (등고선 [(LineString, z)], 점 [(x, y, z)])"""
    from shapely.geometry import LineString
    doc = _open_dxf(path)
    lines, spots = [], []

    def num(txt):
        txt = re.sub(r"\\[A-Za-z][^;]*;|[{}]", "", str(txt or ""))   # MTEXT 서식 코드
        m = LEVEL_RE.search(txt)
        return float(m.group(1)) if m else None

    def text_pos(e):
        try:
            if e.dxftype() == "TEXT" and e.dxf.get("halign", 0) + e.dxf.get("valign", 0) > 0 and e.dxf.hasattr("align_point"):
                p = e.dxf.align_point
            else:
                p = e.dxf.insert
            return float(p[0]), float(p[1])
        except Exception:
            return None
    for e in doc.modelspace():
        t = e.dxftype()
        try:
            if t == "LWPOLYLINE":
                z = float(e.dxf.get("elevation", 0) or 0)
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                if abs(z) > 1e-6 and len(pts) >= 2:
                    if e.closed:
                        pts.append(pts[0])
                    lines.append((LineString(pts), z))
            elif t == "POLYLINE":
                vs = [v.dxf.location for v in e.vertices]
                zs = [v.z for v in vs]
                if len(vs) >= 2 and any(abs(z) > 1e-6 for z in zs):
                    if max(zs) - min(zs) < 1e-3:
                        pts = [(v.x, v.y) for v in vs] + ([(vs[0].x, vs[0].y)] if e.is_closed else [])
                        lines.append((LineString(pts), zs[0]))
                    else:
                        spots += [(v.x, v.y, v.z) for v in vs]
            elif t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                if abs(a.z) > 1e-6 and abs(b.z) > 1e-6:
                    if abs(a.z - b.z) < 1e-3:
                        lines.append((LineString([(a.x, a.y), (b.x, b.y)]), a.z))
                    else:
                        spots += [(a.x, a.y, a.z), (b.x, b.y, b.z)]
            elif t == "POINT":
                p = e.dxf.location
                if abs(p.z) > 1e-6:
                    spots.append((p.x, p.y, p.z))
            elif t in ("TEXT", "MTEXT"):
                if NOT_LEVEL_RE.search(str(e.dxf.layer)):
                    continue          # 층수·면적·치수 숫자는 레벨이 아니다
                v = num(e.dxf.text if t == "TEXT" else e.text)
                xy = text_pos(e)
                if v is not None and xy:
                    spots.append((xy[0], xy[1], v))
                    if kinds is not None and SPOT_LAYER_RE.match(str(e.dxf.layer)):
                        kinds.add(len(spots) - 1)      # 수치지도 표고점(원래 지반)
            elif t == "INSERT":
                p = e.dxf.insert
                if abs(p[2]) > 1e-6:          # 수치지도 표고점 기호처럼 삽입점에 높이가 있는 블록
                    spots.append((float(p[0]), float(p[1]), float(p[2])))
                    continue
                for att in e.attribs:
                    v = num(att.dxf.text)
                    if v is not None:
                        p = e.dxf.insert
                        spots.append((float(p[0]), float(p[1]), v))
                        break
        except Exception:
            continue
    return lines, spots


def load_site_file(path, crs_wkt, sheet=None, log=print, map_folder=None):
    """사업지 도면 → {poly: 대지 면, levels: (등고선, 점) 수치지도 좌표}. 레벨이 없으면 levels=None.
    닫힌 경계가 없고 레벨만 있으면 레벨 자료를 감싸는 면을 대지로 본다."""
    from pyproj import CRS
    lines, spots = ([], [])
    if not os.path.isfile(path):
        raise SiteError(f"대지 경계 파일을 찾지 못했습니다: {path}")
    from .sitefile import is_standard, read_standard
    if is_standard(path):
        got = read_standard(path)
        load_boundary(path, crs_wkt, sheet, log)
        got["standard"] = True
        log(f"   표준 대지 파일: 레벨판 {len(got['plates'])}개 · 레벨 점 {len(got['levels'][1]) if got['levels'] else 0}개"
            f" · 계획 등고선 {len(got['levels'][0]) if got['levels'] else 0}줄 · 원지형 유지 {len(got['keep'])}곳"
            f" · 계획 건물 {len(got['buildings'])}개 · 도로 {len(got['roads'])}줄")
        return got
    if path.lower().endswith(".dxf"):
        mapspot = set()
        lines, spots = _dxf_levels(path, mapspot)
        aff = drawing_affine(path, sheet, map_folder, log) if map_folder else None
        if aff:
            # 도면 좌표 → 수치지도 좌표 (레벨 숫자·등고선도 같은 변환으로)
            from shapely.affinity import affine_transform
            a, b, d, e, xo, yo = aff["m"]
            lines = [(affine_transform(ls, aff["m"]), z) for ls, z in lines]
            spots = [(a * x + b * y + xo, d * x + e * y + yo, z) for x, y, z in spots]
        if mapspot and sheet:
            # 도면에 옮겨 붙은 수치지도 표고점은 원래 지반 높이다. 도엽 안이면 수치지도에 이미 있으니
            # 계획 레벨로 쓰지 않는다(계획 GL 사이에 원래 산 높이가 섞이지 않게). 도엽 밖이면 지형 자료로 둔다.
            sb = box(*sheet)
            n0 = len(spots)
            spots = [p for i, p in enumerate(spots) if not (i in mapspot and sb.contains(Point(p[0], p[1])))]
            if len(spots) < n0:
                log(f"   도면의 수치지도 표고점 {n0 - len(spots)}개는 원래 지반이라 계획 레벨에서 뺐습니다")
    try:
        poly = load_boundary(path, crs_wkt, sheet, log, map_folder=map_folder)
        src = _LAST_CRS.get(path)
    except SiteError:
        if not (lines or spots):
            raise
        poly, src = None, None
    map_crs = CRS.from_wkt(crs_wkt)
    if (lines or spots) and src is None:
        # 경계 없이 레벨만: 좌표계는 레벨 자료가 도엽에 떨어지는 것으로
        from shapely.geometry import MultiPoint
        allp = [c for ls, _ in lines for c in ls.coords] + [(x, y) for x, y, _ in spots]
        hull = MultiPoint(allp).convex_hull
        best = None
        for c in [map_crs] + [CRS.from_epsg(e) for e in CANDIDATE_EPSG]:
            try:
                h = hull if c == map_crs else _to_map([hull], c, map_crs)[0]
            except Exception:
                continue
            sc = h.intersection(box(*sheet)).area / max(h.area, 1e-9) if sheet else 1.0
            if best is None or sc > best[0]:
                best = (sc, c, h)
        if best is None or best[0] <= 0:
            raise SiteError("사업지 도면의 레벨 자료가 넣은 수치지도 도엽 밖에 있습니다.")
        src = best[1]
        poly = clean(best[2].buffer(0))
        if poly is None or poly.area > MAX_AREA:
            raise SiteError("사업지 도면에서 대지 경계(닫힌 선)를 찾지 못했고, 레벨 숫자로 대지 범위를 잡을 수도 없습니다. "
                            "대지 경계를 닫힌 폴리선으로 그려 레이어 이름에 '경계'·'대지'·'SITE' 등을 넣어 주세요.")
        log(f"   사업지 도면에 닫힌 경계가 없어 레벨 자료를 감싸는 범위를 대지로 봅니다 ({poly.area:,.0f}㎡)")
    levels = None
    if lines or spots:
        from pyproj import Transformer
        from shapely.ops import transform as _tf
        t = None if src == map_crs else Transformer.from_crs(src, map_crs, always_xy=True)
        if t is not None:
            lines = [(_tf(lambda x, y, z=None: t.transform(x, y), ls), z) for ls, z in lines]
            spots = [(*t.transform(x, y), z) for x, y, z in spots]
        levels = (lines, spots)
        log(f"   사업지 도면 레벨: 높이 있는 등고선 {len(lines)}줄 · 레벨 점·글자 {len(spots)}개")
    return dict(poly=poly, levels=levels)


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


# ---------------------------------------------------------------- 대지 위 비우기
def inside_ratio(poly, site):
    """poly 넓이 가운데 대지 안에 든 비율"""
    a = poly.area
    return poly.intersection(site).area / a if a > 0 else 0.0


def split_masses(masses, site, ratio=0.5):
    """건물 매스를 (남길 것, 대지 위라 뺄 것)으로. 발자국의 절반 넘게 대지 안이면 뺀다.

    옆 대지 건물이 경계선을 조금 넘어온 것은 남긴다.
    """
    from shapely import prepare
    prepare(site)
    keep, gone = [], []
    for m in masses:
        p = m["poly"]
        (gone if p.intersects(site) and inside_ratio(p, site) > ratio else keep).append(m)
    return keep, gone


def cut_areas(areas, site, min_area=0.5):
    """{종류: 면} 에서 대지 부분을 뺀다. 빈 것은 지운다."""
    out = {}
    for k, g in areas.items():
        if g is None or not hasattr(g, "difference"):
            out[k] = g
            continue
        d = g.difference(site)
        ps = [p for p in _polys(d) if p.area >= min_area]
        if ps:
            out[k] = MultiPolygon(ps) if len(ps) > 1 else ps[0]
    return out


def cut_lines(lines, site):
    out = []
    for ls in lines:
        d = ls.difference(site)
        for g in getattr(d, "geoms", [d]):
            if not g.is_empty and g.geom_type == "LineString" and g.length > 0.2:
                out.append(g)
    return out


def outside(points, site, xy=lambda t: (t[0], t[1])):
    """점 목록에서 대지 밖 것만"""
    import numpy as np
    from shapely import contains_xy
    pts = list(points)
    if not pts:
        return pts
    P = np.array([xy(t) for t in pts], float)
    inn = contains_xy(site, P[:, 0], P[:, 1])
    return [t for t, k in zip(pts, inn) if not k]


def cut_road_info(info, site):
    """도로 디테일(노면표시·신호등·전봇대·정류장 등)에서 대지 안 것을 뺀다. 뺀 개수를 돌려준다."""
    n = 0
    if not info:
        return 0
    if info.get("marks"):
        keep = [t for t in info["marks"] if not (t[0].intersects(site) and inside_ratio(t[0], site) > 0.5)]
        n += len(info["marks"]) - len(keep)
        info["marks"] = keep
    for key in ("signals", "poles"):
        if info.get(key):
            keep = outside(info[key], site)
            n += len(info[key]) - len(keep)
            info[key] = keep
    if info.get("transit"):
        keep = [d for d in info["transit"] if not ("poly" in d and inside_ratio(d["poly"], site) > 0.5)]
        n += len(info["transit"]) - len(keep)
        info["transit"] = keep
    isl = info.get("islands")
    if isl is not None and not isl.is_empty:
        d = isl.difference(site)
        info["islands"] = d if not d.is_empty else None
    return n


def outline_mesh(site, zf, origin, width=0.7, lift=0.25, step=1.0):
    """대지 경계를 지형 위에 얹은 얇은 띠 메시(미리보기용). zf(x, y) → 지표 높이."""
    import numpy as np
    import trimesh
    ms = []
    for p in _polys(site):
        for ring in [p.exterior, *p.interiors]:
            n = max(4, int(ring.length / step))
            pts = np.array([ring.interpolate(i / n, normalized=True).coords[0][:2] for i in range(n)])
            d = np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)
            L = np.hypot(d[:, 0], d[:, 1]); L[L == 0] = 1
            nrm = np.column_stack([-d[:, 1], d[:, 0]]) / L[:, None] * width / 2
            z = np.array([float(zf(x, y)) for x, y in pts]) + lift
            a = np.column_stack([pts + nrm, z]); b = np.column_stack([pts - nrm, z])
            v = np.vstack([a, b, a + [0, 0, 0.15], b + [0, 0, 0.15]])
            f = []
            for i in range(n):
                j = (i + 1) % n
                for o1, o2 in ((2 * n, 3 * n), (0, n)):            # 윗면, 밑면
                    f += [[o1 + i, o2 + i, o1 + j], [o1 + j, o2 + i, o2 + j]]
                for o1, o2 in ((0, 2 * n), (n, 3 * n)):            # 옆면
                    f += [[o1 + i, o1 + j, o2 + i], [o1 + j, o2 + j, o2 + i]]
            v[:, 0] -= origin[0]; v[:, 1] -= origin[1]
            f = np.array(f)
            # 얇은 띠라 어느 쪽에서 봐도 보이게 앞뒤 두 면으로
            ms.append(trimesh.Trimesh(v, np.vstack([f, f[:, ::-1]]), process=False))
    return trimesh.util.concatenate(ms) if ms else None

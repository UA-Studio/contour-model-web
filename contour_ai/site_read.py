"""대지 경계 파일 읽기: DXF 닫힌 폴리선, SHP(zip 가능), GeoJSON, 직접 찍은 꼭짓점."""
import json
import re
import os
import tempfile
import zipfile

from shapely import make_valid

from shapely.geometry import Point, Polygon, shape
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


NOT_LEVEL_RE = re.compile(r"층수|층|FLOOR|면적|AREA|치수|DIM|밀도|용적|건폐", re.I)


LEVEL_RE = re.compile(r"(?:^|[^\d.])(?:EL|FL|GL|SL|TL|BL|H)?\s*[=:]?\s*\+?\s*(-?\d{1,4}\.\d{1,3})(?![\d.])", re.I)

"""대지 경계·레벨 불러오기: 좌표계 고르기(load_boundary), 도면 레벨 읽기, 사업지 파일 전체(load_site_file)."""
import re
import os

from shapely import make_valid

from shapely.geometry import Point, box
from shapely.ops import unary_union
from .site_read import (CANDIDATE_EPSG, LEVEL_RE, MAP_LAYER_RE, NOT_LEVEL_RE, MAX_AREA, MIN_AREA, NAME_RE, SiteError, _open_dxf, _polys,
                        _read_any, _to_map, clean)
from .site_align import SPOT_LAYER_RE, drawing_affine


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

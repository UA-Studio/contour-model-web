"""대지 파일 정리: 사무소마다 다른 배치도·사업지 도면(DXF)을 '표준 대지 파일'로 바꾼다.

표준 대지 파일 = DXF 하나 (수치지도와 같은 실제 좌표, 단위 m) + 같은 이름의 .json(정보)
  SITE_경계         닫힌 선. 대지 경계 (여러 개면 여러 필지)
  SITE_레벨판       닫힌 선, 폴리선 높이(elevation) = 계획 레벨. 판 안은 그 높이로 평평하게
  SITE_레벨점       높이 있는 점(POINT z). 'GL 58.0' 같은 계획 레벨 숫자
  SITE_계획등고선   높이 있는 폴리선
  SITE_원지형유지   닫힌 선. 깎거나 쌓지 않고 원래 지형 그대로 둘 곳
  SITE_건물         닫힌 선. 계획 건물 외곽 (모델에는 그 자리 판 높이에 선으로)
  SITE_도로         선. 단지 안 도로·램프 (모델에 선으로)

정리 순서: 원본 DXF → 레이어별 도형·글자 모으기(블록 안까지, 좌우 뒤집힌 블록도 행렬로 정확히) →
좌표 맞추기(실제 좌표면 그대로, 도면 좌표면 수치지도 표고점으로) → 레이어 역할 추측 → (화면에서 확인·수정) → 저장.
"""
import json
import os
import re
import time

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from .site import (LEVEL_RE, MAP_LAYER_RE, NAME_RE, NOT_LEVEL_RE, SPOT_LAYER_RE, SiteError, _open_dxf, clean,
                   drawing_affine)

ROLES = ("경계", "레벨판", "레벨점", "계획등고선", "원지형유지", "건물", "도로", "무시")
STD_LAYER = {r: f"SITE_{r}" for r in ROLES if r != "무시"}
VERSION = 1

BLD_RE = re.compile(r"건물|건축|주동|동\b|BLD|BUILD|OUTLINE|외곽|매스|MASS|기숙사|상가|카페|근생", re.I)
ROAD_RE = re.compile(r"도로|ROAD|램프|RAMP|ELE\d|진입|차로|통로|주차|PARK", re.I)
KEEP_RE = re.compile(r"유지|보존|원지형|존치|KEEP|산림|자연", re.I)
PLATE_RE = re.compile(r"레벨판|판|PAD|PLATE|단지\s*레벨|LEVEL|조성|계획고", re.I)
SKIP_RE = re.compile(r"TEXT|TXT|DIM|치수|해치|HATCH|SYM|기호|FORM|도곽|표제|Defpoints|범례|LEGEND|^0$", re.I)


def is_standard(path):
    """표준 대지 파일인지 (SITE_경계 레이어가 있는 DXF)"""
    if not str(path).lower().endswith(".dxf"):
        return False
    try:
        doc = _open_dxf(path)
        return STD_LAYER["경계"] in doc.layers and len(doc.modelspace().query(f'*[layer=="{STD_LAYER["경계"]}"]')) > 0
    except Exception:
        return False


# ---------------------------------------------------------------- 원본 모으기
def _flatten(doc):
    """모형 공간 도형을 블록 안까지 펼쳐 (레이어, 종류, 좌표 배열(n×3), 닫힘, 글자) 로 낸다.
    블록은 삽입 행렬을 직접 곱한다(ezdxf virtual_entities 가 좌우 뒤집힌 중첩 블록을 엉뚱한 곳에 두는 문제 회피)."""
    from ezdxf.math import Matrix44
    out = []

    def pts_of(e, M):
        t = e.dxftype()
        if t == "LWPOLYLINE":
            el = float(e.dxf.get("elevation", 0) or 0)
            P = np.array([(p[0], p[1], el) for p in e.get_points("xy")], float)
            return P, bool(e.closed)
        if t == "POLYLINE":
            P = np.array([tuple(v.dxf.location) for v in e.vertices], float)
            return P, bool(e.is_closed)
        if t == "LINE":
            return np.array([tuple(e.dxf.start), tuple(e.dxf.end)], float), False
        return None, False

    def walk(entities, M, inherit, depth):
        for e in entities:
            t = e.dxftype()
            lay = e.dxf.get("layer", "0")
            if lay == "0" and inherit:
                lay = inherit
            try:
                if t == "INSERT":
                    if depth > 6:
                        continue
                    blk = doc.blocks.get(e.dxf.name)
                    if blk is None:
                        continue
                    walk(blk, e.matrix44() * M, lay, depth + 1)
                    for att in e.attribs:
                        p = np.array(M.transform(att.dxf.insert))
                        out.append((att.dxf.get("layer", lay), "TEXT", p[None, :], False, att.dxf.text))
                elif t in ("LWPOLYLINE", "POLYLINE", "LINE"):
                    P, closed = pts_of(e, M)
                    if P is None or len(P) < 2:
                        continue
                    P = np.array(list(M.transform_vertices(P.tolist())), float)
                    out.append((lay, t, P, closed, None))
                elif t in ("TEXT", "MTEXT"):
                    txt = e.dxf.text if t == "TEXT" else e.text
                    ins = e.dxf.insert
                    if t == "TEXT" and e.dxf.get("halign", 0) + e.dxf.get("valign", 0) > 0 and e.dxf.hasattr("align_point"):
                        ins = e.dxf.align_point
                    p = np.array(M.transform(ins))
                    out.append((lay, "TEXT", p[None, :], False, txt))
                elif t == "POINT":
                    p = np.array(M.transform(e.dxf.location))
                    out.append((lay, "POINT", p[None, :], False, None))
            except Exception:
                continue
    walk(doc.modelspace(), Matrix44(), None, 0)
    return out


UNIT_RE = re.compile(r"m2|㎡|m²|평|%|\d\s*[xX×]\s*\d|mm|cm|km|kg|톤|원|세대|실|대$", re.I)


def _level_of(txt):
    """'EL+178.00', 'GL 58.0', '165.5' 같은 레벨 숫자. 면적(30.60 m2)·치수(4500 X 6800)·비율은 아님."""
    if txt is None or UNIT_RE.search(str(txt)):
        return None
    t = re.sub(r"\\[A-Za-z][^;]*;|[{}]", "", str(txt or ""))
    m = LEVEL_RE.search(t)
    return float(m.group(1)) if m else None


def _to_map_fn(path, crs_wkt, sheet, map_folder, log):
    """원본 좌표 → 수치지도 좌표 함수와 설명"""
    from pyproj import CRS, Transformer
    from .site import _LAST_CRS, load_boundary
    aff = drawing_affine(path, sheet, map_folder, log) if map_folder else None
    if aff:
        a, b, d, e, xo, yo = aff["m"]
        return (lambda X, Y: (a * X + b * Y + xo, d * X + e * Y + yo)), \
            f"도면 좌표{'(' + aff['unit'] + ')' if aff.get('unit') else ''} → 수치지도 표고점 {aff['n']}/{aff['total']}개로 맞춤 (오차 {aff['rms']:.1f} m)", aff
    # 실제 좌표 도면: 경계 읽기와 같은 방법으로 좌표계를 정한다(대지 경계 후보가 없으면 수치지도 좌표로 본다)
    src = None
    try:
        load_boundary(path, crs_wkt, sheet, log=lambda *_: None, map_folder=map_folder)
        src = _LAST_CRS.get(path)
    except SiteError:
        pass
    map_crs = CRS.from_wkt(crs_wkt)
    if src is None or src == map_crs:
        return (lambda X, Y: (X, Y)), "실제 좌표 (수치지도와 같은 좌표)", None
    tr = Transformer.from_crs(src, map_crs, always_xy=True)
    return (lambda X, Y: tr.transform(X, Y)), f"실제 좌표 ({src.name} → 수치지도 좌표)", None


def _guess(name, st):
    """레이어 이름과 내용으로 역할을 추측한다"""
    if MAP_LAYER_RE.match(name):
        return "무시"            # 수치지도에서 옮겨 온 지도 도형(원래 지형·건물)은 수치지도에 이미 있다
    if NAME_RE.search(name) and st["closed"]:
        return "경계"
    if KEEP_RE.search(name) and st["closed"]:
        return "원지형유지"
    if st["closed_z"] >= max(1, st["closed"] // 2) and not st["open_z"]:
        return "레벨판"
    if st["open_z"] >= 3 and st["open_z"] >= st["open"] // 2:
        return "계획등고선"
    if st["levels"] >= 1 and not NOT_LEVEL_RE.search(name) and st["levels"] >= max(1, 0.3 * st["texts"]):
        return "레벨점"
    if PLATE_RE.search(name) and st["closed"]:
        return "레벨판"
    if BLD_RE.search(name) and st["closed"]:
        return "건물"
    if ROAD_RE.search(name) and (st["open"] or st["closed"]):
        return "도로"
    return "무시"


def analyze(path, crs_wkt, sheet, map_folder=None, log=print):
    """원본 도면을 읽어 레이어 목록·역할 추측·미리보기 도형을 돌려준다(화면용)."""
    if not str(path).lower().endswith(".dxf"):
        raise SiteError("대지 파일 정리는 DXF만 됩니다. DWG는 CAD에서 DXF로 저장해 넣어 주세요.")
    doc = _open_dxf(path)
    items = _flatten(doc)
    to_map, how, aff = _to_map_fn(path, crs_wkt, sheet, map_folder, log)
    layers = {}
    geoms = []
    for lay, t, P, closed, txt in items:
        st = layers.setdefault(lay, dict(closed=0, closed_z=0, open=0, open_z=0, texts=0, levels=0, points=0, area=0.0,
                                         values=[]))
        X, Y = to_map(P[:, 0], P[:, 1])
        X, Y = np.asarray(X, float), np.asarray(Y, float)
        z = P[:, 2] if P.shape[1] > 2 else np.zeros(len(P))
        if t == "TEXT":
            st["texts"] += 1
            v = _level_of(txt)
            if v is not None and not SPOT_LAYER_RE.match(lay):
                st["levels"] += 1
                st["values"].append(v)
                geoms.append(dict(layer=lay, kind="level", xy=[[float(X[0]), float(Y[0])]], z=v))
            continue
        if t == "POINT":
            st["points"] += 1
            if abs(z[0]) > 1e-6:
                st["levels"] += 1
                geoms.append(dict(layer=lay, kind="level", xy=[[float(X[0]), float(Y[0])]], z=float(z[0])))
            continue
        hasz = bool(np.abs(z).max() > 1e-6) and float(np.ptp(z)) < 1e-3
        span = max(np.ptp(X), np.ptp(Y))
        closed = closed or (len(P) >= 4 and np.hypot(X[0] - X[-1], Y[0] - Y[-1]) <= max(0.01, span * 0.01))
        if closed and len(P) >= 3:
            st["closed"] += 1
            st["closed_z"] += hasz
            try:
                st["area"] = max(st["area"], abs(Polygon(np.column_stack([X, Y])).area))
            except Exception:
                pass
        else:
            st["open"] += 1
            st["open_z"] += hasz
        geoms.append(dict(layer=lay, kind="closed" if closed else "line",
                          xy=np.round(np.column_stack([X, Y]), 2).tolist(), z=float(z[0]) if hasz else None))
    # 수치지도 표고점(원래 지반 높이)을 옮겨 적은 숫자는 계획 레벨이 아니다
    from .site import _map_spots
    MS = _map_spots(map_folder) if map_folder else np.zeros((0, 3))
    if len(MS):
        from scipy.spatial import cKDTree
        tree = cKDTree(MS[:, :2])
        for g in geoms:
            if g["kind"] == "level":
                ii = tree.query_ball_point(g["xy"][0], 12.0)
                if any(abs(MS[i, 2] - g["z"]) <= 0.15 for i in ii):
                    layers[g["layer"]]["map_spots"] = layers[g["layer"]].get("map_spots", 0) + 1
    out = []
    for name, st in sorted(layers.items(), key=lambda kv: (-kv[1]["area"], kv[0])):
        if not (st["closed"] or st["open"] or st["levels"]):
            continue
        vals = sorted(set(round(v, 2) for v in st["values"]))
        role = _guess(name, st)
        if role == "레벨점" and st.get("map_spots", 0) >= 0.5 * st["levels"]:
            role = "무시"          # 원래 지반 표고점
        out.append(dict(name=name, role=role, map_spots=st.get("map_spots", 0), closed=st["closed"], closed_z=st["closed_z"], open=st["open"],
                        open_z=st["open_z"], levels=st["levels"], area=round(st["area"], 1),
                        values=vals[:12] + (["…"] if len(vals) > 12 else [])))
    # 경계는 한 레이어만 추측: 이름이 맞는 레이어 중 가장 큰 닫힌 도형이 다른 경계 후보를 품으면 그것
    bnd = [L for L in out if L["role"] == "경계"]
    if len(bnd) > 1:
        bnd.sort(key=lambda L: -L["area"])
        for L in bnd[1:]:
            L["role"] = "무시"
    return dict(how=how, fit=aff, layers=out, geoms=_thin(geoms), file=os.path.basename(path))


def _thin(geoms, limit=60000):
    """미리보기용으로 꼭짓점을 줄인다(화면이 무거워지지 않게)"""
    total = sum(len(g["xy"]) for g in geoms)
    if total <= limit:
        return geoms
    tol = 0.5
    out = []
    for g in geoms:
        if len(g["xy"]) > 8:
            ls = LineString(g["xy"]).simplify(tol)
            g = dict(g, xy=[list(c) for c in ls.coords])
        out.append(g)
    return out


# ---------------------------------------------------------------- 표준 파일 쓰기
def _poly(xy):
    try:
        p = Polygon(xy)
        return clean(p.buffer(0)) if not p.is_valid else p
    except Exception:
        return None


def build_standard(analysis, roles, log=print):
    """analyze 결과 + 레이어 역할 {레이어: 역할} → 표준 도형 묶음"""
    by = {r: [] for r in ROLES}
    for g in analysis["geoms"]:
        r = roles.get(g["layer"], "무시")
        by[r].append(g)
    bnd = [p for g in by["경계"] if g["kind"] == "closed" for p in [_poly(g["xy"])] if p is not None and p.area >= 10]
    if not bnd:
        raise SiteError("'경계' 역할을 준 레이어에 닫힌 선이 없습니다. 대지 경계가 그려진 레이어를 '경계'로 골라 주세요.")
    bnd.sort(key=lambda p: -p.area)
    if len(bnd) > 1 and all(bnd[0].buffer(0.5).contains(p) for p in bnd[1:]):
        bnd = bnd[:1]                      # 가장 바깥 선이 나머지를 품으면 그것만 (필지 안의 선들은 대지가 아님)
    site = unary_union(bnd)
    # 대지에서 멀리 떨어진 도형(도면 옆에 복사해 둔 다른 안, 범례 등)은 뺀다
    near = site.buffer(max(200.0, 0.5 * np.sqrt(site.area)))
    far = 0
    for r in ROLES:
        if r in ("경계", "무시"):
            continue
        keep_ = []
        for g in by[r]:
            if near.intersects(LineString(g["xy"]) if len(g["xy"]) > 1 else Point(g["xy"][0])):
                keep_.append(g)
            else:
                far += 1
        by[r] = keep_
    if far:
        log(f"   대지에서 멀리 떨어진 도형 {far}개는 뺐습니다 (도면 옆에 따로 그린 것)")
    # 레벨 숫자: 레벨점 역할 레이어의 숫자 글자·높이 점
    pts = [(g["xy"][0][0], g["xy"][0][1], g["z"]) for g in by["레벨점"] if g["kind"] == "level"]
    # 레벨판: 폴리선 높이가 있으면 그것, 없으면 판 안 레벨 숫자(여럿이면 가장 많이 나온 값)
    plates, no_level = [], 0
    for g in by["레벨판"]:
        if g["kind"] != "closed":
            continue
        p = _poly(g["xy"])
        if p is None or p.area < 1:
            continue
        z = g["z"]
        if z is None:
            inside = [round(v, 2) for x, y, v in pts if p.buffer(0.5).contains(Point(x, y))]
            if inside:
                vals, cnt = np.unique(inside, return_counts=True)
                z = float(vals[cnt.argmax()])
        if z is None:
            no_level += 1
            continue
        plates.append((p, float(z)))
    if no_level:
        log(f"   레벨판 {no_level}개는 높이(폴리선 높이나 판 안 레벨 숫자)가 없어 뺐습니다")
    lines = [(LineString(g["xy"]), g["z"]) for g in by["계획등고선"] if g["kind"] != "level" and g["z"] is not None and len(g["xy"]) >= 2]
    keep = [p for g in by["원지형유지"] if g["kind"] == "closed" for p in [_poly(g["xy"])] if p is not None]
    blds = [p for g in by["건물"] if g["kind"] == "closed" for p in [_poly(g["xy"])] if p is not None and p.area >= 1]
    # 열린 선 조각으로 그린 건물 외곽: 선을 이어 생기는 닫힌 면도 건물로
    opn = [LineString(g["xy"]) for g in by["건물"] if g["kind"] == "line" and len(g["xy"]) >= 2]
    if opn:
        from shapely.ops import polygonize
        blds += [p for p in polygonize(unary_union(opn)) if p.area >= 1]
    if blds:     # 방·코어처럼 잘게 나뉜 외곽선은 붙은 것끼리 합쳐 건물 하나로
        u = unary_union([p.buffer(0.3, join_style=2) for p in blds]).buffer(-0.3, join_style=2)
        blds = [Polygon(g.exterior) for g in getattr(u, "geoms", [u]) if g.area >= 10]
    roads = [LineString(g["xy"]) for g in by["도로"] if g["kind"] != "level" and len(g["xy"]) >= 2]
    return dict(site=site, plates=plates, points=pts, lines=lines, keep=keep, buildings=blds, roads=roads)


def write_standard(std, out_path, analysis=None, roles=None, source=None, log=print):
    """표준 대지 파일(DXF + .json)을 쓴다. 반환: 정보 dict"""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6           # m
    colors = {"경계": 5, "레벨판": 4, "레벨점": 4, "계획등고선": 8, "원지형유지": 3, "건물": 7, "도로": 9}
    for r, name in STD_LAYER.items():
        doc.layers.add(name, color=colors[r])
    ms = doc.modelspace()

    def ring(p, layer, z=0.0):
        for g in (p.geoms if isinstance(p, MultiPolygon) else [p]):
            ms.add_lwpolyline([c[:2] for c in g.exterior.coords[:-1]], close=True,
                              dxfattribs=dict(layer=layer, elevation=z))
    for p in (std["site"].geoms if isinstance(std["site"], MultiPolygon) else [std["site"]]):
        ring(p, STD_LAYER["경계"])
    for p, z in std["plates"]:
        ring(p, STD_LAYER["레벨판"], z)
        c = p.representative_point()
        ms.add_text(f"EL {z:g}", height=2.0, dxfattribs=dict(layer=STD_LAYER["레벨판"])).set_placement((c.x, c.y))
    for x, y, z in std["points"]:
        ms.add_point((x, y, z), dxfattribs=dict(layer=STD_LAYER["레벨점"]))
        ms.add_text(f"{z:g}", height=1.5, dxfattribs=dict(layer=STD_LAYER["레벨점"])).set_placement((x + 0.8, y + 0.8))
    for ls, z in std["lines"]:
        ms.add_lwpolyline(list(ls.coords), dxfattribs=dict(layer=STD_LAYER["계획등고선"], elevation=z))
    for p in std["keep"]:
        ring(p, STD_LAYER["원지형유지"])
    for p in std["buildings"]:
        ring(p, STD_LAYER["건물"])
    for ls in std["roads"]:
        ms.add_lwpolyline([c[:2] for c in ls.coords], dxfattribs=dict(layer=STD_LAYER["도로"]))
    doc.saveas(out_path)
    info = dict(format="콘타 표준 대지 파일", version=VERSION, made=time.strftime("%Y-%m-%d %H:%M"),
                source=source, coords=(analysis or {}).get("how"), roles=roles or {},
                counts=dict(경계=len(getattr(std["site"], "geoms", [std["site"]])), 레벨판=len(std["plates"]),
                            레벨점=len(std["points"]), 계획등고선=len(std["lines"]), 원지형유지=len(std["keep"]),
                            건물=len(std["buildings"]), 도로=len(std["roads"])),
                site_area_m2=round(std["site"].area, 1),
                plate_levels=sorted({round(z, 2) for _, z in std["plates"]}))
    json.dump(info, open(os.path.splitext(out_path)[0] + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"   표준 대지 파일을 저장했습니다: {os.path.basename(out_path)} (대지 {info['site_area_m2']:,.0f}㎡, 레벨판 {len(std['plates'])}개)")
    return info


# ---------------------------------------------------------------- 표준 파일 읽기
def read_standard(path):
    """표준 대지 파일 → dict(poly, levels, plates, keep, buildings, roads) (수치지도 좌표)"""
    doc = _open_dxf(path)
    ms = doc.modelspace()
    get = {r: list(ms.query(f'*[layer=="{name}"]')) for r, name in STD_LAYER.items()}

    def polys(es):
        out = []
        for e in es:
            if e.dxftype() == "LWPOLYLINE" and len(e) >= 3:
                p = _poly([(q[0], q[1]) for q in e.get_points("xy")])
                if p is not None:
                    out.append((p, float(e.dxf.get("elevation", 0) or 0)))
        return out
    site = clean(unary_union([p for p, _ in polys(get["경계"])]))
    if site is None or site.is_empty:
        raise SiteError("표준 대지 파일의 SITE_경계 레이어에 닫힌 선이 없습니다.")
    plates = polys(get["레벨판"])
    spots = [tuple(e.dxf.location) for e in get["레벨점"] if e.dxftype() == "POINT"]
    lines = [(LineString([(q[0], q[1]) for q in e.get_points("xy")]), float(e.dxf.get("elevation", 0) or 0))
             for e in get["계획등고선"] if e.dxftype() == "LWPOLYLINE" and len(e) >= 2]
    keep = [p for p, _ in polys(get["원지형유지"])]
    blds = [p for p, _ in polys(get["건물"])]
    roads = [LineString([(q[0], q[1]) for q in e.get_points("xy")]) for e in get["도로"]
             if e.dxftype() == "LWPOLYLINE" and len(e) >= 2]
    levels = (lines, spots) if (lines or spots) else None
    return dict(poly=site, levels=levels, plates=plates, keep=keep, buildings=blds, roads=roads)


def plan_function(map_f, plates=(), keep=(), base=None):
    """레벨판(판 안 = 그 높이)·원지형유지(원래 지형)를 반영한 대지 높이 함수.
    base: 레벨점·등고선으로 만든 대지 높이 함수(없으면 원래 지형). 판 사이 빈 곳은 base."""
    import shapely
    base = base or map_f
    P = [(p, z) for p, z in plates]
    for p, _ in P:
        shapely.prepare(p)
    K = list(keep)
    for p in K:
        shapely.prepare(p)

    def f(xy):
        xy = np.atleast_2d(np.asarray(xy, float))
        z = base(xy).astype(float)
        for p, z0 in P:
            m = shapely.contains_xy(p, xy[:, 0], xy[:, 1])
            z[m] = z0
        for p in K:
            m = shapely.contains_xy(p, xy[:, 0], xy[:, 1])
            if m.any():
                z[m] = map_f(xy[m])
        return z
    return f


def plate_points(plates, step=3.0):
    """레벨판 테두리를 높이 점으로 (판 사이 빈 곳을 판 높이에 맞춰 잇는 데 쓴다)"""
    pts = []
    for p, z in plates:
        for g in (p.geoms if isinstance(p, MultiPolygon) else [p]):
            ring = g.exterior
            n = max(4, int(ring.length / step))
            for d in np.linspace(0, ring.length, n, endpoint=False):
                c = ring.interpolate(d)
                pts.append((c.x, c.y, z))
    return pts

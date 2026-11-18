"""여러 도엽을 한 장으로 잇기.

국토정보플랫폼 수치지도는 도엽마다 따로 받는다. 사이트가 도엽 가장자리에 있으면 이웃 도엽까지 함께
써야 하므로, 도엽 폴더들을 레이어별로 합쳐 하나의 SHP 폴더를 만든다.

- 같은 레이어는 모양·속성을 그대로 이어 붙인다(속성 칸은 모든 도엽의 합집합).
- 도엽 경계에서 잘린 면(건물·도로·보도 등)은 경계를 맞대고 있는 조각끼리 다시 하나로 붙인다.
  건물은 붙인 뒤 층수가 큰 쪽, 속성은 넓은 조각의 것을 쓴다.
- 좌표계가 다른 도엽(원점이 다른 경계 지역)은 첫 도엽의 좌표계로 바꿔 넣는다.
- sheets.json 에 도엽마다 범위를 적어 화면 개관도에 경계선을 그린다.
"""
import glob
import json
import os

import shapefile

ENC = dict(encoding="euc-kr", encodingErrors="replace")
POINT, LINE, POLY = 1, 3, 5


def _base_type(t):
    return {0: 0, 1: 1, 11: 1, 21: 1, 8: 8, 18: 8, 28: 8, 3: 3, 13: 3, 23: 3, 5: 5, 15: 5, 25: 5}.get(t, t)


def _extent(folder):
    xs0, ys0, xs1, ys1 = [], [], [], []
    for f in glob.glob(os.path.join(folder, "*.shp")):
        try:
            r = shapefile.Reader(f[:-4], **ENC)
            if len(r):
                b = r.bbox
                xs0.append(b[0]); ys0.append(b[1]); xs1.append(b[2]); ys1.append(b[3])
            r.close()
        except Exception:
            continue
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _crs(folder):
    from pyproj import CRS
    prj = os.path.join(folder, "N3A_B0010000.prj")
    try:
        return CRS.from_wkt(open(prj, encoding="utf-8", errors="ignore").read())
    except Exception:
        return CRS.from_epsg(5186)


def _parts(shape):
    pts = [tuple(p[:2]) for p in shape.points]
    if not pts:
        return []
    idx = (list(shape.parts) or [0]) + [len(pts)]
    return [pts[a:b] for a, b in zip(idx[:-1], idx[1:]) if b > a]


def _to_value(v, ftype):
    if v is None:
        return None
    if ftype in ("N", "F"):
        if isinstance(v, (int, float)):
            return v
        try:
            return float(str(v).strip())
        except ValueError:
            return None
    return v


def _join_cut_polys(items, extents, log, layer):
    """도엽 경계에서 잘린 면 조각을 다시 붙인다. items: [(sheet, rings, rec)] → 같은 형식."""
    from shapely.geometry import Polygon, box
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    edges = [box(*e).exterior for e in extents]
    geoms, near = [], []
    for k, (si, rings, rec) in enumerate(items):
        try:
            g = Polygon(rings[0], rings[1:]) if len(rings[0]) >= 3 else None
            if g is not None and not g.is_valid:
                g = g.buffer(0)
        except Exception:
            g = None
        geoms.append(g)
        if g is not None and not g.is_empty and g.distance(edges[si]) < 0.05:
            near.append(k)
    if not near:
        return items, 0
    tree = STRtree([geoms[k] for k in near])
    parent = {k: k for k in near}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, k in enumerate(near):
        gk = geoms[k]
        for j in tree.query(gk.buffer(0.05)):
            m = near[j]
            if m <= k or items[m][0] == items[k][0]:
                continue
            shared = gk.boundary.intersection(geoms[m].buffer(0.05)).length
            if shared > 0.3:
                parent[find(m)] = find(k)
    groups = {}
    for k in near:
        groups.setdefault(find(k), []).append(k)
    out, drop, joined = [], set(), 0
    for root, ks in groups.items():
        if len(ks) < 2:
            continue
        u = unary_union([geoms[k].buffer(0.02, join_style=2) for k in ks]).buffer(-0.02, join_style=2)
        if u.geom_type != "Polygon" or u.is_empty:
            continue
        big = max(ks, key=lambda k: geoms[k].area)
        rec = dict(items[big][2])
        if "층수" in rec:
            fl = [items[k][2].get("층수") for k in ks]
            fl = [f for f in fl if isinstance(f, (int, float))]
            if fl:
                rec["층수"] = max(fl)
        u = orient(u, sign=-1.0)     # SHP: 바깥 고리 시계 방향
        rings = [list(u.exterior.coords)] + [list(h.coords) for h in u.interiors]
        out.append((items[big][0], rings, rec))
        drop.update(ks)
        joined += len(ks) - 1
    return [it for k, it in enumerate(items) if k not in drop] + out, joined


def merge_sheets(folders, out, log=print):
    """도엽 폴더 여러 개 → out 폴더 하나(같은 레이어 이름). 반환: out"""
    from pyproj import Transformer
    os.makedirs(out, exist_ok=True)
    crs0 = _crs(folders[0])
    tf = []
    for f in folders:
        c = _crs(f)
        tf.append(None if c.equals(crs0, ignore_axis_order=True) else Transformer.from_crs(c, crs0, always_xy=True))
    extents = []
    for f, t in zip(folders, tf):
        e = _extent(f)
        if t:
            xs, ys = t.transform([e[0], e[2], e[0], e[2]], [e[1], e[1], e[3], e[3]])
            e = (min(xs), min(ys), max(xs), max(ys))
        extents.append(e)
    names = sorted({os.path.basename(p)[:-4] for f in folders for p in glob.glob(os.path.join(f, "*.shp"))})
    joined_total = 0
    for name in names:
        fields, ftypes, items, stype = [], {}, [], None
        for si, f in enumerate(folders):
            fn = os.path.join(f, name)
            if not os.path.exists(fn + ".shp"):
                continue
            try:
                r = shapefile.Reader(fn, **ENC)
            except Exception as e:
                log(f"   (도엽 {si + 1}의 {name} 레이어를 읽지 못해 건너뜀: {e})")
                continue
            for fd in r.fields[1:]:
                fname, ft, size, dec = fd[0], fd[1], int(fd[2]), int(fd[3])
                if fname not in ftypes:
                    ftypes[fname] = [ft, size, dec]
                    fields.append(fname)
                else:
                    ftypes[fname][1] = max(ftypes[fname][1], size)
            for sr in r.iterShapeRecords():
                bt = _base_type(sr.shape.shapeType)
                if bt == 0:
                    continue
                stype = stype or bt
                parts = _parts(sr.shape)
                if not parts:
                    continue
                if tf[si]:
                    parts = [list(zip(*tf[si].transform([p[0] for p in pp], [p[1] for p in pp]))) for pp in parts]
                items.append((si, parts, sr.record.as_dict()))
            r.close()
        if stype is None:
            stype = POINT if name.startswith("N3P") else LINE if name.startswith("N3L") else POLY
        if stype == POLY and len(folders) > 1:
            items, j = _join_cut_polys(items, extents, log, name)
            joined_total += j
        w = shapefile.Writer(os.path.join(out, name), shapeType=stype, **ENC)
        for fname in fields:
            ft, size, dec = ftypes[fname]
            w.field(fname, ft, size=min(size, 254), decimal=dec)
        for si, parts, rec in items:
            if stype == POINT:
                w.point(*parts[0][0])
            elif stype == LINE:
                w.line([list(p) for p in parts])
            elif stype == POLY:
                w.poly([list(p) for p in parts])
            else:
                continue
            w.record(*[_to_value(rec.get(fn), ftypes[fn][0]) for fn in fields])
        w.close()
        open(os.path.join(out, name + ".prj"), "w", encoding="utf-8").write(crs0.to_wkt("WKT1_ESRI"))
        open(os.path.join(out, name + ".cpg"), "w").write("EUC-KR")
    json.dump(dict(sheets=[dict(name=os.path.basename(f), bbox=list(e)) for f, e in zip(folders, extents)]),
              open(os.path.join(out, "sheets.json"), "w", encoding="utf-8"), ensure_ascii=False)
    moved = sum(1 for t in tf if t)
    log(f"   도엽 {len(folders)}장을 이어 붙였습니다 (레이어 {len(names)}개, 경계에서 잘린 면 {joined_total}곳 다시 붙임"
        + (f", 좌표계가 다른 도엽 {moved}장 변환" if moved else "") + ")")
    return out

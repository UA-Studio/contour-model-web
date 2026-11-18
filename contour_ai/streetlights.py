"""가로등·보안등.

출처 순서 (앞쪽이 우선, 6m 안에 이미 있으면 뒤쪽은 버림)
1. 자료 파일: 위도·경도가 있는 CSV/XLSX (전국보안등정보표준데이터, 지자체 가로등 현황 등).
   내장 자료(data/korea_streetlights.npz)가 있으면 그것도 쓴다.
2. 수치지도 점 레이어 중 명칭·구분·종류가 가로등/보안등인 것 (1:1000 도엽에 가끔 있음).
3. 추정: 수치지도 도로 중심선의 도로폭으로 등급을 나눠 도로안전시설 지침의 보통 배치를 따른다.
     폭 20m 이상  양쪽 마주 보기, 35m 간격, 높이 10m
     폭 12~20m    양쪽 엇갈림,   30m 간격, 높이 9m
     폭 6~12m     한쪽,          30m 간격, 높이 8m
     폭 6m 미만   골목 보안등 한쪽, 40m 간격, 높이 6m
   기둥은 차도 경계(연석)에서 0.6m 바깥 보도 위, 팔은 차도 쪽.
   추정한 것은 '가로등::추정' 레이어에 따로 둔다.

반환 형식: [(x, y, 높이, 팔 방향(라디안), 종류, 출처)]  종류 = "가로등" | "보안등"
"""
import glob
import math
import os

import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.ops import linemerge, unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "korea_streetlights.npz")
KEYS = ("가로등", "보안등", "조명등", "등주", "가로조명")
CLASSES = [  # (최소 폭, 양쪽 방식, 간격, 높이, 종류)
    (20.0, "both", 35.0, 10.0, "가로등"),
    (12.0, "stagger", 30.0, 9.0, "가로등"),
    (6.0, "one", 30.0, 8.0, "가로등"),
    (0.0, "one", 40.0, 6.0, "보안등"),
]
MIN_GAP = 6.0


# ---------------------------------------------------------------- 1. 자료 파일
def _lonlat_cols(cols):
    low = {c: str(c).strip().lower() for c in cols}
    lat = next((c for c, l in low.items() if l in ("위도", "lat", "latitude", "y좌표(위도)") or "위도" in l), None)
    lon = next((c for c, l in low.items() if l in ("경도", "lon", "lng", "longitude", "x좌표(경도)") or "경도" in l), None)
    return lat, lon


def read_table(path):
    import pandas as pd
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, encoding="cp949", encoding_errors="replace", low_memory=False)


def table_lonlat(path):
    """위도·경도 칸이 있는 표 → (lon, lat, 종류) 배열. 설치개수가 2 이상이면 같은 자리 한 개로."""
    df = read_table(path)
    lat, lon = _lonlat_cols(df.columns)
    if lat is None or lon is None:
        raise SystemExit(f"{os.path.basename(path)}: 위도·경도 칸을 찾지 못했습니다 (칸 이름: {', '.join(map(str, df.columns[:12]))})")
    la = np.asarray(pd_num(df[lat]), float)
    lo = np.asarray(pd_num(df[lon]), float)
    ok = (la > 33) & (la < 39) & (lo > 124) & (lo < 132)
    name = next((c for c in df.columns if "보안등" in str(c) or "구분" in str(c) or "종류" in str(c)), None)
    sec = "보안" in os.path.basename(path) or any("보안등" in str(c) for c in df.columns)
    kind = np.array(["보안등" if sec else "가로등"] * len(df), object)
    if name is not None:
        kind = np.where(df[name].astype(str).str.contains("보안").values, "보안등", kind)
    return lo[ok], la[ok], kind[ok]


def pd_num(s):
    import pandas as pd
    return pd.to_numeric(s, errors="coerce")


def build_dataset(path, out=DATA):
    """전국 자료 파일 → 내장 자료(npz). python -m contour_ai.streetlights 파일.csv"""
    lo, la, kind = table_lonlat(path)
    np.savez_compressed(out, lon=np.round(lo * 1e7).astype(np.int32), lat=np.round(la * 1e7).astype(np.int32),
                        kind=(kind == "보안등").astype(np.uint8), source=os.path.basename(path))
    print(f"{len(lo)}개 → {out}")
    return out


def known_lights(bbox, crs_wkt, files=()):
    """내장 자료 + 사용자 파일에서 bbox 안 등. 반환 [(x, y, 종류, 출처)]"""
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:4326", crs_wkt, always_xy=True)
    out = []
    srcs = []
    if os.path.exists(DATA):
        d = np.load(DATA)
        srcs.append((d["lon"] / 1e7, d["lat"] / 1e7, np.where(d["kind"] == 1, "보안등", "가로등"),
                     f"내장 자료({d['source']})"))
    for f in files or ():
        lo, la, kind = table_lonlat(f)
        srcs.append((lo, la, kind, os.path.basename(f)))
    pad = 0.01
    for lo, la, kind, lab in srcs:
        # 대충 경위도 범위로 먼저 줄이고 변환
        inv = Transformer.from_crs(crs_wkt, "EPSG:4326", always_xy=True)
        bx, by = inv.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
        m = (lo > min(bx) - pad) & (lo < max(bx) + pad) & (la > min(by) - pad) & (la < max(by) + pad)
        if not m.any():
            continue
        x, y = tf.transform(lo[m], la[m])
        for xi, yi, k in zip(x, y, kind[m]):
            if bbox[0] <= xi <= bbox[2] and bbox[1] <= yi <= bbox[3]:
                out.append((float(xi), float(yi), str(k), lab))
    return out


# ---------------------------------------------------------------- 2. 수치지도
def mapped_lights(folder, bbox):
    import shapefile
    out = []
    for f in sorted(glob.glob(os.path.join(folder, "N3P_*.shp"))):
        try:
            r = shapefile.Reader(f[:-4], encoding="euc-kr", encodingErrors="replace")
            keys = [x[0] for x in r.fields[1:] if x[0] in ("명칭", "구분", "종류", "용도")]
            if not keys:
                r.close(); continue
            for sr in r.iterShapeRecords():
                rec = sr.record.as_dict()
                txt = " ".join(str(rec.get(k) or "") for k in keys)
                if not any(w in txt for w in KEYS) or not sr.shape.points:
                    continue
                x, y = sr.shape.points[0][:2]
                if bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
                    out.append((x, y, "보안등" if "보안" in txt else "가로등", "수치지도"))
            r.close()
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- 3. 추정
def _centerlines(folder, bbox):
    import shapefile
    from shapely.geometry import shape
    fn = os.path.join(folder, "N3L_A0020000")
    if not os.path.exists(fn + ".shp"):
        return []
    clip = box(*bbox)
    r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
    out = []
    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict()
        try:
            w = float(rec.get("도로폭") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if w <= 0:
            try:
                w = 3.2 * float(rec.get("차로수") or 1) + 1.0
            except (TypeError, ValueError):
                w = 4.0
        try:
            g = shape(sr.shape.__geo_interface__)
        except Exception:
            continue
        if g.intersects(clip):
            out.append((g.intersection(clip), w))
    r.close()
    return out


class _Curb:
    """차도 경계까지 거리 재기 (경계를 짧은 선분으로 쪼개 공간 색인)."""

    def __init__(self, road):
        from shapely.strtree import STRtree
        self.segs = []
        if road is not None and not road.is_empty:
            for ring in _rings(road):
                c = np.asarray(ring.coords)
                for i in range(0, len(c) - 1, 8):
                    self.segs.append(LineString(c[i:i + 9]))
        self.tree = STRtree(self.segs) if self.segs else None

    def hit(self, p, n, reach):
        """p 에서 n 방향으로 reach 안 첫 경계까지 거리 (없으면 None)."""
        if self.tree is None:
            return None
        ray = LineString([p, (p[0] + n[0] * reach, p[1] + n[1] * reach)])
        best = None
        for i in self.tree.query(ray):
            x = ray.intersection(self.segs[i])
            for q in getattr(x, "geoms", [x]):
                if q.is_empty:
                    continue
                q = q if q.geom_type == "Point" else Point(q.coords[0])
                d = math.hypot(q.x - p[0], q.y - p[1])
                if d > 0.8 and (best is None or d < best):
                    best = d
        return best


def _rings(g):
    for p in getattr(g, "geoms", [g]):
        if p.geom_type == "Polygon":
            yield p.exterior
            yield from p.interiors


def estimate_lights(folder, bbox, road=None, buildings=None):
    """도로 중심선 + 도로폭으로 가로등 자리를 추정. 반환 [(x, y, 종류, "추정", 팔 방향)]"""
    from shapely import contains_xy, prepare
    lines = _centerlines(folder, bbox)
    curb = _Curb(road)
    if buildings is not None and not buildings.is_empty:
        blk = buildings.buffer(0.4)
        prepare(blk)
    else:
        blk = None
    road_in = None
    if road is not None and not road.is_empty:
        road_in = road.buffer(-0.1)       # 차도 한가운데(중앙분리대·교통섬 쪽으로 잘못 잰 경우)에는 세우지 않음
        prepare(road_in)
    out = []
    for ci, (wmin, mode, sp, h, kind) in enumerate(CLASSES):
        wmax = CLASSES[ci - 1][0] if ci else 1e9
        group = [g for g, w in lines if wmin <= w < wmax]
        if not group:
            continue
        widths = [w for g, w in lines if wmin <= w < wmax]
        half = float(np.median(widths)) / 2
        u_ = unary_union(group)
        merged = linemerge(u_) if u_.geom_type == "MultiLineString" else u_
        for ln in getattr(merged, "geoms", [merged]):
            if ln.geom_type != "LineString" or ln.length < sp * 0.5:
                continue
            n = max(1, int(ln.length // sp))
            start = (ln.length - (n - 1) * sp) / 2 if n > 1 else ln.length / 2
            for j in range(n):
                s = start + j * sp
                a, b = ln.interpolate(max(s - 1, 0)), ln.interpolate(min(s + 1, ln.length))
                tx, ty = b.x - a.x, b.y - a.y
                L = math.hypot(tx, ty) or 1.0
                nx, ny = -ty / L, tx / L
                c = ln.interpolate(s)
                sides = (1, -1) if mode == "both" else ((1,) if j % 2 == 0 else (-1,)) if mode == "stagger" else (1,)
                for sd in sides:
                    for side in (sd, -sd) if mode == "one" else (sd,):
                        d = curb.hit((c.x, c.y), (nx * side, ny * side), half + 6.0)
                        d = (half if d is None or d > half * 1.8 + 2 else d)
                        d = d - 0.3 if kind == "보안등" else d + 0.6
                        x, y = c.x + nx * side * d, c.y + ny * side * d
                        if not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                            continue
                        if blk is not None and contains_xy(blk, x, y):
                            continue   # 건물에 닿으면 반대쪽(한쪽 방식)으로
                        if road_in is not None and contains_xy(road_in, x, y):
                            continue
                        out.append((x, y, kind, "추정", math.atan2(-ny * side, -nx * side), h))
                        break
    return out


def _arm_dir(x, y, center_tree, centers):
    if center_tree is None:
        return 0.0
    i = center_tree.nearest(Point(x, y))
    from shapely.ops import nearest_points
    q = nearest_points(centers[i], Point(x, y))[0]
    return math.atan2(q.y - y, q.x - x)


def place_lights(folder, bbox, crs_wkt=None, road=None, buildings=None, files=(), estimate=True, log=print, extra=()):
    """세 출처를 합친다. 반환 [(x, y, 높이, 팔 방향, 종류, 출처)]"""
    from scipy.spatial import cKDTree
    from shapely.strtree import STRtree
    lines = [g for g, _ in _centerlines(folder, bbox)]
    centers = [p for g in lines for p in getattr(g, "geoms", [g]) if p.geom_type == "LineString"]
    ctree = STRtree(centers) if centers else None
    real = []
    if crs_wkt:
        try:
            real = known_lights(bbox, crs_wkt, files)
        except SystemExit as e:
            log(f"   가로등 자료 파일을 읽지 못함: {e}")
    mapped = mapped_lights(folder, bbox)
    if extra:   # API(생활안전지도 보안등) — 자료 파일과 겹치면(3m 안) 파일 쪽을 쓴다
        from scipy.spatial import cKDTree as _T
        have = _T([(t[0], t[1]) for t in real]) if real else None
        for x, y in extra:
            if have is None or have.query((x, y))[0] > 3.0:
                real.append((x, y, "보안등", "생활안전지도"))
    out = []
    for lab in dict.fromkeys(t[3] for t in real):
        log(f"   {lab}에서 등 {sum(t[3] == lab for t in real)}개")
    for x, y, kind, src in real + mapped:
        h = 6.0 if kind == "보안등" else 9.0
        out.append((x, y, h, _arm_dir(x, y, ctree, centers), kind, src))
    if mapped:
        log(f"   수치지도에서 등 {len(mapped)}개")
    est = estimate_lights(folder, bbox, road, buildings) if estimate else []
    if out and est:
        tree = cKDTree([(o[0], o[1]) for o in out])
        d, _ = tree.query([(e[0], e[1]) for e in est])
        # 실제 자료가 있는 동네(반경 60m 안에 실측 등)는 추정을 넣지 않는다
        near = tree.query_ball_point([(e[0], e[1]) for e in est], 60.0)
        est = [e for e, dd, nb in zip(est, d, near) if dd > MIN_GAP and not nb]
    # 추정끼리 교차로에서 너무 붙은 것 정리
    kept = []
    if est:
        P = np.array([(e[0], e[1]) for e in est])
        tr = cKDTree(P)
        alive = np.ones(len(est), bool)
        for i in range(len(est)):
            if not alive[i]:
                continue
            for j in tr.query_ball_point(P[i], MIN_GAP):
                if j > i:
                    alive[j] = False
            kept.append(est[i])
    for x, y, kind, src, ang, h in kept:
        out.append((x, y, h, ang, kind, src))
    if kept:
        log(f"   자료가 없는 길에 도로폭 기준으로 등 {len(kept)}개 추정 배치 (레이어 '가로등::추정')")
    return out


# ---------------------------------------------------------------- 모양
def light_parts(h, kind):
    """원점 바닥, 팔은 +x. 반환 [(trimesh, 부위)] — 기둥·팔·등기구."""
    import trimesh
    r = 0.09 if kind == "가로등" else 0.07
    arm = 1.6 if kind == "가로등" else 0.8
    pole = trimesh.creation.cylinder(radius=r, height=h, sections=8)
    pole.apply_translation([0, 0, h / 2])
    a = trimesh.creation.box([arm, 0.08, 0.08])
    a.apply_translation([arm / 2, 0, h - 0.15])
    lamp = trimesh.creation.box([0.7 if kind == "가로등" else 0.45, 0.28, 0.14])
    lamp.apply_translation([arm, 0, h - 0.27])
    return [(pole, "기둥"), (a, "팔"), (lamp, "등기구")]


def light_mesh(h, kind):
    import trimesh
    return trimesh.util.concatenate([m for m, _ in light_parts(h, kind)])


def placed_mesh(x, y, z, h, ang, kind, origin=(0, 0)):
    import trimesh
    m = light_mesh(h, kind)
    m.apply_transform(trimesh.transformations.rotation_matrix(ang, [0, 0, 1]))
    m.apply_translation([x - origin[0], y - origin[1], z])
    return m


if __name__ == "__main__":
    import sys
    build_dataset(sys.argv[1])

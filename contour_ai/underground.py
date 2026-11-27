"""땅속 시설: 지하보도·지하철 연결통로(서울 도보 네트워크)와 지하철역(서울 역사마스터).

수치지도에는 땅속 시설이 없다. 서울 열린데이터광장 키가 있으면
- 도보 네트워크 링크 중 TNL(지하보도)·SBWY_NTW(지하철 연결통로) 표시가 있는 선을 받아 땅 아래 통로로,
- 역사마스터의 역 위치·노선을 받아 대합실·승강장 덩어리로 그린다.
역 본체의 정확한 크기·방향은 공개 자료가 없어 추정한다(연결통로가 퍼진 방향 → 없으면 위 도로 방향).
결과는 Rhino 레이어 '지하::…' 로만 나가고(STL·레이저 도면 제외), 지형에 가려 있으니 Rhino 에서 지형을 숨겨 본다.
"""
import math

import numpy as np
import trimesh
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

RGB = {"line": (70, 90, 150), "tunnel": (120, 160, 205), "subway": (95, 135, 200), "concourse": (150, 170, 215), "platform": (80, 110, 180)}
LAYER = {"line": "지하::지하철 선로(OSM)", "tunnel": "지하::지하보도", "subway": "지하::지하철 연결통로",
         "concourse": "지하::지하철역::대합실(추정)", "platform": "지하::지하철역::승강장(추정)"}
# 땅 밑 깊이(m): (윗면, 바닥)
DEPTH = {"line": (14.0, 21.0), "tunnel": (2.5, 5.5), "subway": (4.0, 7.0), "concourse": (3.0, 9.0), "platform": (13.0, 19.0)}


def collect(api, bbox, crs_wkt, log=print):
    """키가 있으면 {tunnel, subway: [LineString], stations: [(x, y, 이름, 노선)]}. 없으면 None."""
    if not api or not api.get("seoul_key"):
        return None
    from . import crosswalk_api as ca
    out = {"tunnel": [], "subway": [], "stations": []}
    links = ca._LINK_MEMO.get((tuple(bbox), api["seoul_key"]))
    if links is None:
        try:
            links = ca.seoul_links(api["seoul_key"], bbox, crs_wkt, api.get("seoul_service"), api.get("seoul_code"),
                                   api.get("cache_dir"), log)
        except Exception as e:
            log(f"   ({e})")
            links = {}
    out["tunnel"] = links.get("tunnel", [])
    out["subway"] = links.get("subway", [])
    try:
        out["stations"] = ca.seoul_stations(api["seoul_key"], bbox, crs_wkt, None, api.get("cache_dir"), log)
    except Exception as e:
        log(f"   ({e})")
    if any(out.values()):
        log(f"   땅속 시설: 지하보도 {len(out['tunnel'])}, 지하철 통로 {len(out['subway'])}, 역 {len(out['stations'])}")
    return out


def _slab(poly, z_top, z_bot):
    if poly is None or poly.is_empty or z_top - z_bot <= 0:
        return None
    parts = []
    for p in getattr(poly, "geoms", [poly]):
        if p.geom_type == "Polygon" and p.area > 0.5:
            m = trimesh.creation.extrude_polygon(p, z_top - z_bot)
            m.apply_translation([0, 0, z_bot])
            parts.append(m)
    return trimesh.util.concatenate(parts) if parts else None


def _axis(geom):
    """도형의 긴 방향 단위 벡터와 길이."""
    r = geom.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    L = float(np.linalg.norm(e1))
    return (e1 / L if L > 0 else np.array([1.0, 0.0])), L


def _box(c, u, L, W):
    v = np.array([-u[1], u[0]])
    pts = [c - u * L / 2 - v * W / 2, c + u * L / 2 - v * W / 2, c + u * L / 2 + v * W / 2, c - u * L / 2 + v * W / 2]
    from shapely.geometry import Polygon
    return Polygon(pts)


def _zmin(geom, zground, step=10.0):
    """도형 위 땅 높이 가운데 가장 낮은 값. 비탈에서 긴 대합실·선로가 낮은 쪽 땅 위로 튀어나오지 않게 (v2.4.1)."""
    pts = [geom.representative_point()]
    for g in getattr(geom, "geoms", [geom]):
        ring = g.exterior if g.geom_type == "Polygon" else g
        n = max(2, int(ring.length // step) + 1)
        pts += [ring.interpolate(t, normalized=True) for t in np.linspace(0, 1, n)]
    return min(zground(q.x, q.y) for q in pts)


def meshes(ug, zground, road=None, clip=None):
    """[(메시, 레이어, 색, 이름)] — 월드 좌표. zground(x, y) = 그 자리 땅 높이."""
    out = []
    if not ug:
        return out
    for kind, w in (("tunnel", 4.0), ("subway", 5.0)):
        top, bot = DEPTH[kind]
        for ln in ug.get(kind, []):
            g = ln if clip is None else ln.intersection(clip)
            if g.is_empty:
                continue
            for seg in getattr(g, "geoms", [g]):
                if seg.geom_type != "LineString" or seg.length < 1:
                    continue
                fp = seg.buffer(w / 2, cap_style=2, join_style=2)
                z0 = _zmin(fp, zground)
                m = _slab(fp, z0 - top, z0 - bot)
                if m is not None:
                    out.append((m, LAYER[kind], RGB[kind], "지하보도" if kind == "tunnel" else "지하철 연결통로"))
    # 지하철 선로(OSM, v2.3): 복선 터널 폭 10m, 승강장 아래 깊이
    lines = ug.get("lines") or []
    for ln in lines:
        g = ln if clip is None else ln.intersection(clip)
        for seg in getattr(g, "geoms", [g]):
            if g.is_empty or seg.geom_type != "LineString" or seg.length < 2:
                continue
            # 40m 마디마다 그 마디의 가장 낮은 땅 기준 (긴 선로가 비탈 낮은 쪽에서 튀어나오지 않게, 높은 쪽은 너무 깊지 않게)
            from shapely.ops import substring
            n = max(1, int(math.ceil(seg.length / 40.0)))
            top, bot = DEPTH["line"]
            parts = []
            for k in range(n):
                piece = substring(seg, seg.length * k / n, seg.length * (k + 1) / n)
                fp = piece.buffer(5.0, cap_style=2, join_style=2)
                z0 = _zmin(fp, zground)
                pm = _slab(fp, z0 - top, z0 - bot)
                if pm is not None:
                    parts.append(pm)
            if parts:
                out.append((trimesh.util.concatenate(parts), LAYER["line"], RGB["line"],
                            "지하철 선로 터널 (© OpenStreetMap contributors)"))
    subs = unary_union([ln.buffer(3.0) for ln in ug.get("subway", [])]) if ug.get("subway") else None
    for x, y, name, line in ug.get("stations", []):
        p = Point(x, y)
        u, L = None, 0.0
        near_ln = [ln for ln in lines if ln.distance(p) < 120]
        if near_ln:                                   # 선로가 있으면 역은 선로 방향 (v2.3)
            ln = min(near_ln, key=lambda q: q.distance(p))
            s0 = ln.project(p)
            a1, a2 = ln.interpolate(max(s0 - 15, 0)), ln.interpolate(min(s0 + 15, ln.length))
            uu = np.array([a2.x - a1.x, a2.y - a1.y])
            if np.linalg.norm(uu) > 1:
                u, L = uu / np.linalg.norm(uu), 180.0
                q = ln.interpolate(s0)
                x, y, p = q.x, q.y, Point(q.x, q.y)  # 역 상자를 선로 위로
        if u is None and subs is not None and not subs.is_empty:
            near = subs.intersection(p.buffer(220))
            if not near.is_empty and near.area > 50:
                u, L = _axis(near)
        if u is None and road is not None and not road.is_empty:
            near = road.intersection(p.buffer(70))
            if not near.is_empty and near.area > 50:
                u, _ = _axis(near)
        if u is None:
            u = np.array([1.0, 0.0])
        c = np.array([x, y])
        z0 = zground(x, y)
        label = f"{name}역 ({line})" if name and not name.endswith("역") else f"{name} ({line})"
        conc = _box(c, u, min(200.0, max(90.0, L)), 24.0)
        plat = _box(c, u, 180.0, 12.0)
        z0 = min(z0, _zmin(conc if clip is None else (conc.intersection(clip) if conc.intersects(clip) else conc), zground))
        for kind, poly in (("concourse", conc), ("platform", plat)):
            if clip is not None:
                poly = poly.intersection(clip)
            top, bot = DEPTH[kind]
            m = _slab(poly, z0 - top, z0 - bot)
            if m is not None:
                out.append((m, LAYER[kind], RGB[kind], f"{label} {'대합실' if kind == 'concourse' else '승강장'}(추정)"))
    return out

"""건축물이 아닌 구조물: 고가차도, 보행육교, 교량.

수치지도 면 레이어에서 읽는다 (로드뷰·3D 자료 없이).
  N3A_A0090000 입체교차부  구분=고가차도 → 상판 + 교각 (지하차도는 건너뜀)
  N3A_A0063321 보행육교   높이 칸 → 상판 + 기둥
  N3A_A0070000 교량       하천을 건너는 다리 → 양 끝 높이를 잇는 상판 + 교각 (땅이 꺼진 곳만)

상판 높이: 면의 긴 축을 따라 양 끝 지반 높이를 잇는 선 위에,
  고가차도는 가운데 부분을 형하고 5m + 상판 1.5m 만큼 띄우고 양 끝 진입부(최대 70m)는 땅에서 올라오게,
  육교는 주변 가장 높은 지반 + 높이(없으면 4.5m) 로 평평하게.
상판은 긴 축에 직각인 3m 띠로 잘라 띠마다 돌출한 뒤 높이를 올린다(굽은 면도 매끈하게).
반환: [(Trimesh(월드 좌표), 종류, 이름)]
"""
import math
import os

import numpy as np
import trimesh
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

DECK = {"고가도로": 1.5, "육교": 0.6, "교량": 1.0}
PIER_STEP = {"고가도로": 25.0, "육교": 12.0, "교량": 20.0}


def _read(folder, name):
    import shapefile
    from shapely.geometry import shape
    fn = os.path.join(folder, name)
    if not os.path.exists(fn + ".shp"):
        return []
    r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
    out = []
    for sr in r.iterShapeRecords():
        try:
            g = shape(sr.shape.__geo_interface__).buffer(0)
        except Exception:
            continue
        out.append((g, sr.record.as_dict()))
    r.close()
    return out


def _axis(poly):
    r = poly.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    L = float(np.linalg.norm(e1))
    u = e1 / max(L, 1e-9)
    ctr = np.asarray(r.centroid.coords[0])
    return ctr - u * L / 2, u, L


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _deck(poly, ground, kind, height=None):
    """상판 + 교각 메시. ground(xy 배열) → 지반 높이."""
    p0, u, L = _axis(poly)
    if L < 6:
        return None
    v = np.array([-u[1], u[0]])

    def uu(xy):
        return np.clip(((np.asarray(xy) - p0) @ u) / L, 0, 1)
    pts = np.asarray(poly.exterior.segmentize(2.0).coords)[:-1]
    g = ground(pts)
    t = uu(pts)
    end0 = np.nanmedian(g[t < 0.08]) if (t < 0.08).any() else np.nanmin(g)
    end1 = np.nanmedian(g[t > 0.92]) if (t > 0.92).any() else np.nanmin(g)
    T = DECK[kind]
    if kind == "고가도로":
        H = (height if height and height > 3 else 5.0) + T
        r = min(0.35, 70.0 / L)

        def top(s):
            ramp = np.clip(np.minimum(s, 1 - s) / r, 0, 1)
            ramp = ramp * ramp * (3 - 2 * ramp)
            return end0 + (end1 - end0) * s + H * ramp
    elif kind == "육교":
        H = (height if height and height > 2 else 4.5) + T
        flat = float(np.nanmax(g)) + H

        def top(s):
            return np.full_like(np.asarray(s, float), flat)
    else:
        def top(s):
            return end0 + (end1 - end0) * s + 0.2
    parts = []
    n = max(1, int(math.ceil(L / 3.0)))
    for k in range(n):
        s0, s1 = p0 + u * (L * k / n), p0 + u * (L * (k + 1) / n)
        strip = Polygon([s0 - v * 1e4, s1 - v * 1e4, s1 + v * 1e4, s0 + v * 1e4])
        piece = poly.intersection(strip)
        for q in getattr(piece, "geoms", [piece]):
            if q.geom_type != "Polygon" or q.area < 0.05:
                continue
            try:
                m = trimesh.creation.extrude_polygon(q, T)
            except Exception:
                continue
            V = m.vertices.copy()
            V[:, 2] += top(uu(V[:, :2])) - T
            parts.append(trimesh.Trimesh(V, m.faces, process=False))
    # 교각: 긴 축 위 일정 간격, 상판 밑이 땅에서 2m 넘게 뜬 곳만
    piers = []
    step = PIER_STEP[kind]
    for s_m in np.arange(step / 2, L, step):
        c = p0 + u * s_m
        if not poly.contains(Point(c)):
            continue
        cross = LineString([c - v * 200, c + v * 200]).intersection(poly)
        w = cross.length if not cross.is_empty else 4.0
        zt = float(top(np.array([s_m / L]))[0]) - T
        zg = float(ground(np.array([c]))[0])
        if not np.isfinite(zg) or zt - zg < 2.0:
            continue
        if kind == "육교":
            for off in (-w / 2 + 0.4, w / 2 - 0.4):
                q = c + v * off
                col = trimesh.creation.cylinder(radius=0.25, height=zt - zg + 1, sections=10)
                col.apply_translation([q[0], q[1], (zt + zg - 1) / 2])
                piers.append(col)
            continue
        pw = min(max(w * 0.45, 1.5), 8.0)
        bx = trimesh.creation.box([1.4, pw, zt - zg + 1.5])
        rot = math.atan2(u[1], u[0])
        bx.apply_transform(trimesh.transformations.rotation_matrix(rot, [0, 0, 1]))
        bx.apply_translation([c[0], c[1], (zt + zg - 1.5) / 2])
        piers.append(bx)
        # 교각 머리(코핑): 상판 폭에 가깝게
        cap = trimesh.creation.box([1.8, min(w * 0.85, pw + 4), 1.0])
        cap.apply_transform(trimesh.transformations.rotation_matrix(rot, [0, 0, 1]))
        cap.apply_translation([c[0], c[1], zt - 0.5])
        piers.append(cap)
    deck = _weld(trimesh.util.concatenate(parts)) if parts else None
    pier = trimesh.util.concatenate(piers) if piers else None
    return deck, pier


def _weld(m):
    """띠 사이 맞닿은 면을 지우고 하나로 잇는다 (렌더에 줄무늬가 안 생기게)."""
    m = trimesh.Trimesh(m.vertices, m.faces, process=False)
    m.merge_vertices(digits_vertex=4)
    key = np.sort(m.faces, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    m.update_faces(cnt[inv.ravel()] == 1)
    m.remove_unreferenced_vertices()
    from .mesh_shading import crisp_mesh
    return crisp_mesh(m)


def load_structures(folder, bbox, ground, log=print):
    """반환 ([(Trimesh, 종류, 이름)], 상판으로 바꾼 교량 면(길에서 뺄 것))"""
    clip = box(*bbox)
    todo = []
    for g, rec in _read(folder, "N3A_A0090000"):
        if "고가" in str(rec.get("구분") or ""):
            todo.append((g, "고가도로", rec.get("명칭") or "고가차도", _num(rec.get("높이"))))
    for g, rec in _read(folder, "N3A_A0063321"):
        todo.append((g, "육교", rec.get("명칭") or "보행육교", _num(rec.get("높이"))))
    for g, rec in _read(folder, "N3A_A0070000"):
        todo.append((g, "교량", rec.get("명칭") or str(rec.get("종류") or "교량"), None))
    out, replaced = [], []
    for g, kind, name, h in todo:
        if not g.intersects(clip):
            continue
        g = g.intersection(clip)
        for p in getattr(g, "geoms", [g]):
            if p.geom_type != "Polygon" or p.area < 10:
                continue
            if kind == "교량":
                # 땅이 실제로 꺼진(하천·골짜기) 다리만 띄운다
                p0, u, L = _axis(p)
                mid = ground(np.array([p0 + u * L * s for s in (0.35, 0.5, 0.65)]))
                ends = ground(np.array([p0 + u * L * 0.03, p0 + u * L * 0.97]))
                if not (np.nanmean(ends) - np.nanmin(mid) > 1.5):
                    continue
            try:
                res = _deck(p.simplify(0.2), ground, kind, h)
            except Exception as e:
                log(f"   ({name} 구조물을 만들지 못함: {e})")
                continue
            if not res:
                continue
            deck, pier = res
            if deck is not None:
                out.append((deck, kind, name))
                if kind == "교량":
                    replaced.append(p)
            if pier is not None:
                out.append((pier, "교각", name))
    if out:
        cnt = {}
        for _, k, _n in out:
            cnt[k] = cnt.get(k, 0) + 1
        log("   구조물: " + ", ".join(f"{k} {v}" for k, v in cnt.items()))
    return out, (unary_union(replaced) if replaced else None)


STRUCT_RGB = {"고가도로": (176, 174, 168), "육교": (184, 182, 176), "교량": (176, 174, 168), "교각": (160, 158, 152)}

"""나무·숲: 항공사진·위성영상 녹지 면, 숲 나무 채우기, 가로수 대장·항공사진 가로수.

이 파일만 고치면 나무만 바뀝니다.
"""
import math
import os

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from .mapdata import _crs_wkt
from .terrain import level_at


def _green_areas(a, path_areas, masses, bbox, log, pg):
    """숲·조경 녹지 = 수치지도 식생 면 + 항공사진 수관 + 위성 NDVI 를 겹친 것. 반환 {"forest","garden"}"""
    from .vegetation import aerial_green_areas, combine_greens, green_areas, map_green_areas
    pg.step("5.4")
    blocked = unary_union([m["poly"].buffer(1.5) for m in masses] +
                          [g.buffer(0.5) for g in path_areas.values()])
    sources, bare, ae = [], None, None

    def ha(d):
        return (d["forest"].area + d["garden"].area) / 1e4
    try:
        mp = map_green_areas(a.src, bbox)
        bare = mp.pop("bare")
        if ha(mp) > 0:
            sources.append(mp)
            log(f"   수치지도 식생 면: 숲 {mp['forest'].area / 1e4:.2f}ha · 과수원 등 {mp['garden'].area / 1e4:.2f}ha")
    except Exception as e:
        log(f"   (수치지도 식생 면을 읽지 못함: {e})")
    if a.aerial:
        try:
            ae = aerial_green_areas(a.aerial, bbox, preview_path=os.path.join(a.out, "aerial_green.png"))
            log(f"   항공사진 판독: 나무 수관 {ae['canopy'].area / 1e4:.2f}ha(나무 {len(ae['crowns'])}그루 자리) · "
                f"풀·잔디 {ae['grass'].area / 1e4:.2f}ha → aerial_green.png")
            # 사진이 있으면 사진이 기준: 수치지도·위성(10m) 녹지는 사진에서 수관이 보이는 곳 둘레(3m)만 남기고, 풀로 판독된 곳은 뺀다
            keep = ae["canopy"].buffer(3.0)
            sources = [{k: v.intersection(keep).difference(ae["grass"]) for k, v in src.items()} for src in sources]
            sources.append({"forest": ae["forest"], "garden": ae["garden"]})
        except Exception as e:
            ae = None
            log(f"   (항공사진에서 숲을 찾지 못함: {e})")
    if a.ndvi:
        try:
            nd = green_areas(a.ndvi, bbox, Polygon())
            if ae is not None:
                keep = ae["canopy"].buffer(3.0)
                nd = {k: v.intersection(keep).difference(ae["grass"]) for k, v in nd.items()}
            sources.append(nd)
            log(f"   위성 식생지수: 숲 {nd['forest'].area / 1e4:.2f}ha · 조경 {nd['garden'].area / 1e4:.2f}ha")
        except Exception as e:
            log(f"   (위성 녹지 자료를 읽지 못함: {e})")
    if not sources:
        log("   숲을 찾을 자료가 없습니다 (위성영상 받기 실패, 브이월드 키 없음, 수치지도 식생 면 없음)")
        return {"forest": Polygon(), "garden": Polygon()}
    out = combine_greens(sources, blocked, bare)
    if ae is not None:
        out["grass"] = ae["grass"].difference(blocked).difference(unary_union([out["forest"], out["garden"]]))
        zone = unary_union([out["forest"], out["garden"]]).buffer(2.0)
        from shapely import contains_xy, prepare
        prepare(zone); prepare(blocked)
        cr = ae["crowns"]
        if cr:
            P = np.array([(x, y) for x, y, _ in cr])
            ok = contains_xy(zone, P[:, 0], P[:, 1]) & ~contains_xy(blocked, P[:, 0], P[:, 1])
            out["crowns"] = [c for c, k in zip(cr, ok) if k]
    log(f"   나무를 채울 녹지: 숲 {out['forest'].area / 1e4:.2f}ha · 조경 {out['garden'].area / 1e4:.2f}ha"
        + (f" · 나무 없는 풀밭 {out['grass'].area / 1e4:.2f}ha" if out.get("grass") is not None else ""))
    pg.step("5.5")
    return out


def _green_trees(a, geom, regions, avoid, log):
    from .vegetation import FOREST, GARDEN, scatter_trees, spacing_for
    out = []
    if geom.get("crowns"):
        # 항공사진에서 찾은 수관 하나하나에 나무를 세운다(격자로 흩뿌리지 않음)
        from shapely import contains_xy, prepare
        fz = geom["forest"].buffer(2.0)
        prepare(fz)
        cr = sorted(geom["crowns"], key=lambda c: -c[2])[:a.max_forest_trees]
        av = np.asarray([(t[0], t[1]) for t in avoid]) if len(avoid) else None
        if av is not None:
            from scipy.spatial import cKDTree
            d, _ = cKDTree(av).query([(x, y) for x, y, _ in cr])
            cr = [c for c, dd in zip(cr, d) if dd > 2.5]
        P = np.array([(x, y) for x, y, _ in cr]) if cr else np.zeros((0, 2))
        inf = contains_xy(fz, P[:, 0], P[:, 1]) if len(P) else []
        for (x, y, r), f_ in zip(cr, inf):
            out.append((x, y, level_at(regions, Point(x, y)), float(np.clip(r, 1.2, 6.0)), "forest" if f_ else "garden"))
        log(f"   녹지 나무 {len(out)}그루 — 항공사진 수관 자리 그대로 (숲 {sum(t[4] == 'forest' for t in out)} · 조경 {sum(t[4] == 'garden' for t in out)})")
        return out
    fill = {k: geom[k] for k in ("forest", "garden")}
    if getattr(a, "chm", None):
        # 항공사진이 없으면 Meta 1m 나무 높이 지도에서 나무 꼭대기를 찾아 한 그루씩 세운다.
        # 높이 지도는 몇 년 전 영상이라, 지금 위성이 녹지라고 한 곳(forest·garden) 안의 나무만 쓴다.
        try:
            from shapely import contains_xy, prepare
            from .canopy_height import trees_from_chm
            zone = unary_union([fill["forest"], fill["garden"]]).buffer(1.0)
            ct, cover = trees_from_chm(a.chm, zone, max_trees=a.max_forest_trees)
            if avoid:
                from scipy.spatial import cKDTree
                d, _ = cKDTree(np.asarray([(t[0], t[1]) for t in avoid])).query([(t[0], t[1]) for t in ct]) if ct else ([], None)
                ct = [t for t, dd in zip(ct, d) if dd > 2.5]
            fz = fill["forest"].buffer(2.0)
            prepare(fz)
            P = np.array([(t[0], t[1]) for t in ct]) if ct else np.zeros((0, 2))
            inf = contains_xy(fz, P[:, 0], P[:, 1]) if len(P) else []
            for (x, y, r, h), f_ in zip(ct, inf):
                out.append((x, y, level_at(regions, Point(x, y)), r, "forest" if f_ else "garden"))
            hs = [t[3] for t in ct]
            log(f"   나무 높이 지도(Meta 1m)에 맞춰 {len(out)}그루 (높이에 따라 간격·크기)"
                + (f" (높이 중간값 {np.median(hs):.0f}m, 큰 나무 {np.percentile(hs, 90):.0f}m)" if hs else ""))
            # 높이 지도에 나무가 없는 녹지(영상 뒤에 자란 숲 등)만 아래에서 흩뿌려 채운다
            fill = {k: v.difference(cover.buffer(2.0)) for k, v in fill.items()}
        except Exception as e:
            log(f"   (나무 높이 지도를 쓰지 못해 숲은 고르게 채웁니다: {type(e).__name__}: {e})")
    for key, cfg in (("forest", FOREST), ("garden", GARDEN)):
        sp = cfg["spacing"]
        if key == "forest":
            sp = spacing_for(fill[key].area, sp, max(1000, a.max_forest_trees - len(out)))
            if sp > cfg["spacing"] + 0.05:
                log(f"   숲이 넓어 나무 간격을 {cfg['spacing']:g}m → {sp:.1f}m 로 넓혔습니다 (숲 나무 최대 {a.max_forest_trees}그루)")
        for x, y, r in scatter_trees(fill[key], sp, cfg["r"], seed=1 if key == "forest" else 2,
                                     avoid=avoid + out):
            out.append((x, y, level_at(regions, Point(x, y)), r, key))
    log(f"   녹지 나무 {len(out)}그루 (숲 {sum(t[4] == 'forest' for t in out)} · 조경 {sum(t[4] == 'garden' for t in out)})")
    return out


def _aerial_accuracy(out, found, reg_pts, walk, log, tol=3.0):
    """항공사진으로 찾은 나무를 가로수 대장(정답지)과 맞대 본다 → aerial_accuracy.json/csv.

    찾을 수 있는 곳(보도 둘레 3m)에 있는 대장 나무만 센다. 3m 안에서 1:1로 짝지으면 '맞힘'."""
    import csv
    import json as _json
    from scipy.spatial import cKDTree
    zone = walk.buffer(3.0)
    truth = [p for p in reg_pts if zone.contains(Point(p))]
    det = [(t[0], t[1]) for t in found]
    pairs, used = [], set()
    if truth and det:
        tt = cKDTree(truth)
        cand = []
        for i, d in enumerate(det):
            for j in tt.query_ball_point(d, tol):
                cand.append((math.dist(d, truth[j]), i, j))
        di = set()
        for dd, i, j in sorted(cand):
            if i not in di and j not in used:
                di.add(i); used.add(j); pairs.append((i, j, dd))
    hit = len(pairs)
    res = dict(대장_나무=len(truth), 사진_찾음=len(det), 맞힘=hit,
               놓침=len(truth) - hit, 잘못_잡음=len(det) - hit,
               찾은_비율=round(hit / len(truth), 3) if truth else None,
               맞은_비율=round(hit / len(det), 3) if det else None,
               평균_위치오차_m=round(sum(p[2] for p in pairs) / hit, 2) if hit else None,
               기준=f"대장 나무와 {tol:.0f}m 안에서 1:1로 짝지으면 맞힘. 보도 둘레 3m 안의 대장 나무만 셈.")
    with open(os.path.join(out, "aerial_accuracy.json"), "w", encoding="utf-8") as f:
        _json.dump(res, f, ensure_ascii=False, indent=1)
    hit_i = {i for i, _, _ in pairs}
    hit_j = {j for _, j, _ in pairs}
    with open(os.path.join(out, "aerial_accuracy.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["x", "y", "구분"])
        w.writerows([(round(x, 2), round(y, 2), "맞힘(사진)" if i in hit_i else "잘못 잡음(사진)") for i, (x, y) in enumerate(det)])
        w.writerows([(round(x, 2), round(y, 2), "놓침(대장)") for j, (x, y) in enumerate(truth) if j not in hit_j])
    if truth:
        log(f"   항공사진 정확도(가로수 대장 기준): 대장 {len(truth)}그루 중 {hit}그루 찾음({res['찾은_비율']:.0%}), "
            f"사진에서 잡은 {len(det)}개 중 {len(det) - hit}개는 대장에 없음 → aerial_accuracy.json")
    return res


def _real_street_trees(a, path_areas, masses, regions, bbox, log, pg):
    """가로수 = 가로수 대장(서울시·산림청 실제 위치·수종) + 수치지도 표시 + 항공사진에서 확인된 나머지.

    일정 간격으로 지어내지 않는다. 반환: ([(x, y, z, 수관 반지름, "street")], [(x, y, 수종, 출처)])
    """
    from shapely.ops import nearest_points
    from .aerial import detect_street_trees, mapped_trees, preview
    from .tree_models import remember
    from .tree_registry import crown_r, trees_in
    walk = unary_union([path_areas[k] for k in ("sidewalk", "island") if k in path_areas])
    walk_in = walk.buffer(-0.4) if not walk.is_empty else walk
    known = []   # (x, y, r, 수종, 출처)
    crs_wkt = _crs_wkt(a)
    if crs_wkt:
        reg = trees_in(bbox, crs_wkt)
        for lab in dict.fromkeys(t[4] for t in reg):
            log(f"   {lab}에서 {sum(t[4] == lab for t in reg)}그루")
        for x, y, sp, dbh, lab in reg:
            p = Point(x, y)
            if not walk.is_empty and not walk.contains(p):
                q = nearest_points(walk_in if not walk_in.is_empty else walk, p)[0]
                if q.distance(p) < 3.5:   # 연석에 걸친 좌표는 보도 안으로
                    x, y = q.x, q.y
            known.append((x, y, crown_r(sp, dbh), sp, lab))
            remember(x, y, sp)
    for x, y in mapped_trees(a.src, bbox):
        if all((x - k[0]) ** 2 + (y - k[1]) ** 2 > 9 for k in known):
            known.append((x, y, 2.5, "", "수치지도"))
    pg.step("5.2")
    found = []
    if a.aerial and not walk.is_empty:
        blds = unary_union([m["poly"] for m in masses]) if masses else None
        found, info = detect_street_trees(a.aerial, walk, path_areas.get("road"), blds)
        reg_pts = [(k[0], k[1]) for k in known if k[4] != "수치지도"]
        if reg_pts:
            try:
                _aerial_accuracy(a.out, found, reg_pts, walk, log)
            except Exception as e:
                log(f"   (항공사진 정확도 확인표 생략: {e})")
        # 이미 아는 나무 자리(4m 안)는 빼고 대장에 없는 나무만 보충
        found = [t for t in found if all((t[0] - k[0]) ** 2 + (t[1] - k[1]) ** 2 > 16 for k in known)]
        log(f"   항공사진({info['source']})에서 대장에 없는 가로수 {len(found)}그루 더 확인")
        try:
            preview(os.path.join(a.out, "aerial_trees.png"), info, found, walk, [(k[0], k[1]) for k in known])
        except Exception as e:
            log(f"   (항공사진 확인 그림 생략: {e})")
    elif not known:
        log("   이 범위에는 가로수 대장·수치지도 가로수가 없고 항공사진도 없어 가로수를 넣지 않습니다")
    pg.step("5.3")
    rows = [(x, y, sp, src, r) for x, y, r, sp, src in known] + [(x, y, "", "항공사진", r) for x, y, r in found]
    trees = [(x, y, level_at(regions, Point(x, y)), r, "street") for x, y, r, *_ in known]
    trees += [(x, y, level_at(regions, Point(x, y)), r, "street") for x, y, r in found]
    try:
        import csv
        with open(os.path.join(a.out, "street_trees.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["x", "y", "수종", "출처", "수관반지름(m)"])
            w.writerows([(round(x, 2), round(y, 2), sp, src, round(r, 2)) for x, y, sp, src, r in rows])
    except Exception:
        pass
    return trees

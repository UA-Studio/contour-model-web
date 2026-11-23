"""road_details(): 모든 출처를 모아 우선순위대로 정리 (정밀도로지도 반영 포함)."""
import math
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from .centerlines import ISLANDS, _centerlines
from .lanes import _zebra, estimate_marks, real_crosswalks
from .aerial import aerial_marks, aerial_zebras
from .junctions import chain_links, dedupe_bands, junction_cores, snap_crossings
from .street_furniture import estimate_poles, estimate_signals
from .islands import hd_islands, islands_at_points, islands_from_aerial
from .parking import parking_lots, parking_marks
from .data_files import read_files



CW_RANK = {"항공사진": 0, "자료": 0, "서울도보네트워크": 1, "생활안전지도": 1, "추정": 2}


def tidy_crosswalks(marks, islands, log=print):
    """복잡한 교차로의 횡단보도 정리.
    1) 교통섬(수치지도·추정 모두) 위로는 줄무늬를 긋지 않는다 — 횡단보도는 섬 가장자리에서 끊기고 섬 건너편에서 이어진다.
    2) 두 횡단보도가 겹치면 믿을 만한 쪽(자료·API > 항공사진 > 추정)만 남긴다.
    3) 잘리고 남은 짧은 토막(줄무늬 4개 미만, 약 3.5m 미만)은 지운다 — 섬 너머로 삐져나온 꼬리."""
    cw = [(g, s_) for g, k, s_ in marks if k == "횡단보도"]
    rest = [m for m in marks if m[1] != "횡단보도"]
    if not cw:
        return marks
    cut = islands.buffer(0.3) if islands is not None and not islands.is_empty else None
    pieces = []
    for g, s_ in cw:
        if cut is not None:
            g = g.difference(cut)
        for q in getattr(g, "geoms", [g]):
            if q.geom_type == "Polygon" and q.area > 0.2:
                pieces.append((q, s_))
    # 같은 출처 줄무늬끼리 이웃(간격 0.45m, 0.6m 안)으로 묶어 한 횡단보도 조각으로
    from shapely.strtree import STRtree
    geoms = [q for q, _ in pieces]
    tree = STRtree(geoms)
    parent = list(range(len(pieces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, (q, s_) in enumerate(pieces):
        for j in tree.query(q.buffer(0.7)):
            j = int(j)
            if j != i and pieces[j][1] == s_ and geoms[j].distance(q) < 0.6:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(pieces)):
        groups.setdefault(find(i), []).append(i)
    grp = sorted(groups.values(), key=lambda ix: (CW_RANK.get(pieces[ix[0]][1], 2), -len(ix)))
    taken, out, n_short, n_ovl = None, [], 0, 0
    for ix in grp:
        if len(ix) < 4:
            n_short += 1
            continue
        area = unary_union([geoms[i] for i in ix])
        hull = area.minimum_rotated_rectangle
        if taken is not None and hull.intersects(taken):
            # 더 믿을 만한 횡단보도와 겹침: 겹치는 줄무늬만 빼고, 남은 게 짧으면 통째로 뺀다
            keep = [i for i in ix if not geoms[i].intersects(taken)]
            if len(keep) < 4:
                n_ovl += 1
                continue
            if len(keep) < len(ix):
                n_ovl += 1
            ix = keep
            hull = unary_union([geoms[i] for i in ix]).minimum_rotated_rectangle
        out += [(geoms[i], "횡단보도", pieces[i][1]) for i in ix]
        taken = hull.buffer(0.3) if taken is None else unary_union([taken, hull.buffer(0.3)])
    if n_short or n_ovl:
        log(f"   횡단보도 정리: 섬 위 줄무늬는 끊고, 짧은 토막 {n_short}개 빼고, 겹친 {n_ovl}곳 정리")
    return rest + out



def road_details(folder, bbox, crs_wkt, road, buildings=None, aerial_path=None, files=(), lights=(),
                 log=print, keep_out=None, walk=None, islands=None, api=None, hd_dir=None, dst_epsg=5186):
    """정밀도로지도 폴더(hd_dir)가 있으면 그 범위의 노면표시는 정밀도로지도가 으뜸이고, 항공사진·추정은
    정밀도로지도가 없는 곳(골목 등)만 채운다.
    반환 dict(marks=[(Polygon, 종류, 출처)], signals=[(x, y, 방향, 종류, 출처, 팔)],
                 poles=[(x, y, 출처)], lots=[Polygon])"""
    from ..streetlights import _Curb
    lines = _centerlines(folder, bbox)
    data = read_files(files, bbox, crs_wkt, log)
    curb = _Curb(road)
    marks, crossings = [], []
    am = []
    if aerial_path and road is not None and not road.is_empty:
        try:
            am = aerial_marks(aerial_path, road, log=log)
        except Exception as e:
            log(f"   (항공사진 노면표시를 찾지 못함: {type(e).__name__}: {e})")
    ISLANDS["geom"] = islands
    core = junction_cores(lines, road)
    inferred = Polygon()
    # 공공 API 횡단보도(서울 도보 네트워크 → 생활안전지도 지도) — 위치는 가장 믿고, 모양은 그 자리 길에 맞춘다
    api_bands, api_src = [], None
    api = api or {}
    if api.get("seoul_key"):
        try:
            from ..crosswalk_api import seoul_crosswalks
            links = seoul_crosswalks(api["seoul_key"], bbox, crs_wkt, api.get("seoul_service"),
                                     api.get("seoul_code"), api.get("cache_dir"), log)
            chains, isl_pts = chain_links(links, walk, road)
            if isl_pts:
                isl_new = islands_at_points(isl_pts, chains, road, walk)
                if islands is not None and not islands.is_empty:
                    isl_new = isl_new.difference(islands.buffer(1.0))
                if not isl_new.is_empty:
                    inferred = unary_union([inferred, isl_new])
                    log(f"   도보 네트워크: 횡단보도가 차도 한가운데에서 이어지는 {len(isl_pts)}곳에 교통섬 추정")
            if not inferred.is_empty:          # 섬을 건너는 횡단보도가 섬에 닿도록 먼저 넣는다
                ISLANDS["geom"] = unary_union([g for g in (islands, inferred) if g is not None and not g.is_empty])
            walk_api = unary_union([walk, inferred]) if (walk is not None and not inferred.is_empty) else walk
            api_bands = snap_crossings(chains, lines, road, walk_api, core, log)
            api_src = "서울도보네트워크"
        except Exception as e:
            log(f"   ({e})")
    if not api_bands and api.get("safemap_key"):
        try:
            from ..crosswalk_api import bands_from_polys, safemap_crosswalks
            polys = safemap_crosswalks(api["safemap_key"], bbox, crs_wkt, api.get("safemap_layer"),
                                       api.get("safemap_styles") or "", api.get("cache_dir"), log)
            chains = [LineString([b[3], b[4]]) for b in bands_from_polys(polys)]
            api_bands = snap_crossings(chains, lines, road, walk, core, log)
            api_src = "생활안전지도"
        except Exception as e:
            log(f"   ({e})")
    # 항공사진: 차도 안 화단 = 교통섬
    if aerial_path and road is not None and not road.is_empty:
        try:
            ia = islands_from_aerial(aerial_path, road, walk)
            known = unary_union([g for g in (islands, inferred) if g is not None and not g.is_empty] or [Polygon()])
            ia = unary_union([p for p in getattr(ia, "geoms", [ia]) if p.geom_type == "Polygon"
                              and p.intersection(known.buffer(1.0)).area < 0.3 * p.area] or [Polygon()])
            if not ia.is_empty:
                inferred = unary_union([inferred, ia])
                log(f"   항공사진: 차도 안 화단 {len(getattr(ia, 'geoms', [ia]))}곳을 교통섬으로")
        except Exception as e:
            log(f"   (항공사진에서 교통섬을 찾지 못함: {type(e).__name__}: {e})")
    if not inferred.is_empty:
        islands = unary_union([g for g in (islands, inferred) if g is not None and not g.is_empty])
        ISLANDS["geom"] = islands
        walk = unary_union([walk, inferred]) if walk is not None else inferred
    # 자료 파일 → API → 항공사진 판독 순으로 띠를 모으고, 같은 횡단보도는 하나만
    bands = []
    # 항공사진 줄무늬에서 직접 읽은 횡단보도를 가장 먼저 믿는다 (실제로 칠해진 자리·각도)
    zb = []
    if aerial_path and road is not None and not road.is_empty:
        try:
            zb = aerial_zebras(aerial_path, road, walk, log)
        except Exception as e:
            log(f"   (항공사진 횡단보도 줄무늬를 읽지 못함: {type(e).__name__}: {e})")
    bands += [(b, "항공사진") for b in zb]
    zb_ids = {id(b) for b in zb}
    for x, y, w, length in data["crosswalk"]:
        hit = real_crosswalks([(x, y, w, length)], lines, road, walk, bands_only=True)
        bands += [(b, "자료") for b in hit]
    bands += [(b, api_src) for b in api_bands]
    aer = None
    if am:
        from ..marking_assets import read_aerial
        aer = read_aerial(am, lines, log)
        for x, y, w, length in aer["crosswalks"]:
            hit = real_crosswalks([(x, y, w, length)], lines, road, walk, bands_only=True)
            bands += [(b, "항공사진") for b in hit]
    inner = core.buffer(-4.0) if not core.is_empty else None
    # 교차로 한가운데 횡단보도는 버린다 — 단, 사진에 실제로 칠해진 것(섬과 섬을 잇는 횡단보도)은 둔다
    bands = [(b, s_) for b, s_ in bands if inner is None or id(b) in zb_ids or not inner.contains(b[0].centroid)]
    kept = dedupe_bands([b for b, _ in bands])
    srcs = {id(b): s_ for b, s_ in bands}
    road_in_ = road.buffer(-0.25)
    marks, rc = [], []
    for b in kept:
        band, u, nrm, e0, e1 = b
        isl_cut = inferred.buffer(0.3) if not inferred.is_empty else None
        for g, k in _zebra(band, u, nrm, road_in_):
            if isl_cut is not None:
                g = g.difference(isl_cut)
            if not g.is_empty and g.area > 0.2:
                marks.append((g, k, srcs.get(id(b), "자료")))
        c = (np.asarray(e0) + np.asarray(e1)) / 2
        rc.append((c[0], c[1], nrm, float(np.linalg.norm(np.asarray(e1) - np.asarray(e0))), np.asarray(e0), np.asarray(e1)))
    if len(kept) < len(bands):
        log(f"   같은 자리 횡단보도 {len(bands) - len(kept)}개 겹침 정리")
    em, ec, appr = estimate_marks(lines, road, (), log, walk, lane_obs=aer["lanes"] if aer else None,
                                  islands=islands, real_bands=kept, core=core)
    marks += em
    marks = tidy_crosswalks(marks, islands, log)
    # 교통섬 유도선·빗금은 횡단보도·정지선과 겹치지 않게
    cwz = [g for g, k, _ in marks if k in ("횡단보도", "정지선")]
    if cwz:
        cwz = unary_union(cwz).buffer(0.6)
        marks = [((g.difference(cwz) if k in ("유도선", "안전지대") else g), k, s_) for g, k, s_ in marks]
        marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty and g.area > 0.1]
    crossings = rc + ec
    # 신호등
    sig = [(x, y, 0.0, k, "자료", 4.0 if k == "차량" else 0.0) for x, y, k in data["signal"]]
    if not sig:
        sig = estimate_signals(crossings, appr, curb, buildings, road)
    else:
        from shapely.strtree import STRtree
        cl = [d["line"] for d in lines]
        tr = STRtree(cl) if cl else None
        out = []
        for x, y, a, k, s, arm in sig:
            if tr is not None:
                from shapely.ops import nearest_points
                q = nearest_points(cl[tr.nearest(Point(x, y))], Point(x, y))[0]
                a = math.atan2(q.y - y, q.x - x)
            out.append((x, y, a, k, s, arm))
        sig = out
    sig = [s for s in sig if bbox[0] <= s[0] <= bbox[2] and bbox[1] <= s[1] <= bbox[3]]
    # 전봇대
    poles = [(x, y, "자료") for x, y in data["pole"]]
    if not poles:
        poles = [(x, y, "추정") for x, y in estimate_poles(lines, curb, bbox, buildings, lights, road=road)]
    # 정밀도로지도: 덮는 곳은 그것으로 바꾸고, 사진과 비교해 바뀐 곳은 알림
    hd_flags, hd_report = [], None
    if hd_dir != "" and road is not None and not road.is_empty:      # None = 흔한 자리에서 자동으로 찾기, "" = 쓰지 않음
        try:
            marks, sig, hd_flags, hd_report, hd_isl = _apply_hdmap(hd_dir, bbox, road, walk, islands, zb, marks, sig,
                                                                   dst_epsg, log)
            if hd_isl is not None and not hd_isl.is_empty:
                inferred = unary_union([inferred, hd_isl])
                marks = [(g.difference(hd_isl), k, s_) for g, k, s_ in marks]
                marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty and g.area > 0.05]
        except Exception as e:
            log(f"   (정밀도로지도를 쓰지 못함: {type(e).__name__}: {e})")
    # 주차장
    lots = parking_lots(folder, bbox, buildings)
    for lot in lots:
        marks += [(g, k, "추정") for g, k in parking_marks(lot)]
    if keep_out is not None and not keep_out.is_empty:      # 중앙정류장 승강장 밑 페인트는 지운다
        marks = [(g.difference(keep_out), k, s_) for g, k, s_ in marks]
        marks = [(g, k, s_) for g, k, s_ in marks if not g.is_empty]
    cnt = {}
    for _, k, s in marks:
        cnt[k] = cnt.get(k, 0) + 1
    log("   노면표시: " + (", ".join(f"{k} {v}" for k, v in cnt.items()) or "없음")
        + f" · 신호등 {len(sig)}개 · 전봇대 {len(poles)}개 · 주차장 {len(lots)}곳")
    return dict(marks=marks, signals=sig, poles=poles, lots=lots, busstops=data["busstop"],
                islands_inferred=inferred, core=core, hd_flags=hd_flags, hd_report=hd_report)


def _apply_hdmap(hd_dir, bbox, road, walk, islands, zebras, marks, sig, dst_epsg, log):
    from .. import hdmap_align, hdmap_reader
    feats = hdmap_reader.load(hd_dir, bbox, dst_epsg, log)
    paint_kinds = ("차선", "중앙선", "차도경계", "주정차금지선", "버스전용차선", "정지선", "횡단보도", "화살표", "안전지대", "유도선", "노면문자", "정차금지지대", "자전거횡단도", "기타노면")
    feats = [f for f in feats if f["kind"] in paint_kinds]
    if not any(f["kind"] in paint_kinds for f in feats):
        return marks, sig, [], None, None
    res = hdmap_align.align(feats, road, walk, islands=islands, aerial_zebras=list(zebras or []),
                            src_epsg=hdmap_reader.SRC_EPSG, dst_epsg=dst_epsg, log=log)
    if res.get("failed"):
        return marks, sig, [], res["report"], None
    fx = res["features"]
    hd_isl = hd_islands(fx, road, islands, log, walk=walk)
    paint = hdmap_reader.to_paint([f for f in fx if f["kind"] in paint_kinds], road, log)
    # 정밀도로지도가 덮는 곳 = 그 자료의 표시(가장자리선 빼고) 둘레 8m 안의 차도. 거기의 사진·추정 표시는 뺀다.
    cover = unary_union([f["geom"].buffer(8.0) for f in fx if f["kind"] in paint_kinds if f["kind"] != "차도경계"])
    cover = cover.buffer(15.0).buffer(-15.0).intersection(road)     # 교차로 한가운데(표시 없는 곳)도 덮은 것으로
    keep = [(g, k, s_) for g, k, s_ in marks if g.intersection(cover).area < 0.5 * max(g.area, 1e-9)]
    log(f"   정밀도로지도로 바꿈: 표시 {len(paint)}개 (사진·추정 표시 {len(marks) - len(keep)}개 대신)")
    # 신호등: 정밀도로지도 점은 차도 위 신호등 '머리' 자리라 기둥 자리로 바로 쓰기 어려움 → 지금은 기존 추정 유지
    return keep + paint, sig, res["flags"], res["report"], hd_isl

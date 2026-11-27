"""도로 디테일: 노면표시(차선·중앙선·정지선·횡단보도), 신호등, 전봇대, 주차 구획.

인도는 이미 수치지도 보도 면(N3A_A0033320)을 연석 높이만큼 올려 만든다.

출처 (앞쪽이 우선)
- 노면표시
  1. 항공사진(브이월드 영상·정사영상)이 있으면 차도 안의 흰색·노란색 페인트를 찾아 그대로 면으로 (레이어 '...::항공사진')
  2. 횡단보도 자료 파일 (전국횡단보도표준데이터: 위도·경도·횡단보도폭·연장)
  3. 추정: 수치지도 도로중심선(N3L_A0020000)의 차로수·분리대유무로 중앙선(노랑)·차선(흰 점선),
     차로 2개 이상 길끼리 만나는 교차로마다 횡단보도와 정지선
- 신호등
  1. 신호등 자료 파일 (전국신호등표준데이터: 위도·경도·신호등구분)
  2. 추정: 횡단보도 양 끝에 보행 신호등, 교차로 진입로마다 건너편 오른쪽 모서리에 차량 신호등(팔 달린 기둥)
- 전봇대
  1. 위치 자료 파일 (위도·경도 칸, 파일 이름이나 칸에 '전주'·'전봇대')
  2. 추정: 폭 12m 미만 생활도로에만 한쪽 30m 간격 (큰길은 서울 대부분 지중화) — 보안등 5m 안에서는 생략
     한전 전주 좌표는 공개 자료가 없다(전주번호만 공개).
- 주차장: 수치지도 주차장 면(N3A_C0430000) 안에 2.5×5.0m 구획 + 6m 통로 줄을 긴 축 방향으로 깐다(추정).

반환은 2D 도형과 점이고, 높이는 contour_model 이 붙인다.
"""
import math

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union


# 기능별 파일에서 가져옴. 예전처럼 road_detail 에서 바로 불러도 동작하도록 그대로 다시 내보낸다.
from .road_sources import _dedupe, _f, _kind_of_table, read_files  # noqa: F401
from .road_network import (_approach_dir, _arm_end, arm_frame, _carriage, _centerlines,
                           chain_links, _curb_pair, junction_cores, LANE_W, _mouth, _nodes,
                           _offset_strips, _ransac_line, _road_dir_at, _section, _split_dual,
                           _strip)  # noqa: F401
from .road_crosswalks import (aerial_zebras, _all_walk, _crossing, CW_RANK, dedupe_bands,
                              _is_island, ISLANDS, real_crosswalks, snap_crossings, _span,
                              tidy_crosswalks, _zebra)  # noqa: F401
from .road_marks import aerial_marks, arm_lanes, estimate_marks, PAINT, _stop_and_arrows  # noqa: F401
from .road_islands import (_apply_hdmap, crosswalk_islands, hd_islands, islands_at_points,
                           islands_from_aerial)  # noqa: F401
from .road_parking import parking_lots, parking_marks  # noqa: F401
from .road_fixtures import (estimate_poles, estimate_signals, FIX_RGB, flat_mesh, placed,
                            pole_parts, signal_parts)  # noqa: F401

def road_details(folder, bbox, crs_wkt, road, buildings=None, aerial_path=None, files=(), lights=(),
                 log=print, keep_out=None, walk=None, islands=None, api=None, hd_dir=None, dst_epsg=5186):
    """정밀도로지도 폴더(hd_dir)가 있으면 그 범위의 노면표시는 정밀도로지도가 으뜸이고, 항공사진·추정은
    정밀도로지도가 없는 곳(골목 등)만 채운다.
    반환 dict(marks=[(Polygon, 종류, 출처)], signals=[(x, y, 방향, 종류, 출처, 팔)],
                 poles=[(x, y, 출처)], lots=[Polygon])"""
    from .streetlights import _Curb
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
            from .crosswalk_api import seoul_crosswalks
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
            from .crosswalk_api import bands_from_polys, safemap_crosswalks
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
        from .marking_assets import read_aerial
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


# ---------------------------------------------------------------- 확인 그림
SRC_COLOR = {"정밀도로지도": "#0a8f5a", "서울도보네트워크": "#1f6fd1", "생활안전지도": "#1f6fd1", "자료": "#1f6fd1", "항공사진": "#d1501f",
             "추정": "#222222"}


def marks_check(path, road, walk, marks, islands=None, inferred=None, bbox=None, title="", core=None):
    """노면표시 확인 그림: 출처별 색(파랑 = 공공 API·자료, 주황 = 항공사진, 검정 = 추정),
    중앙선은 노랑, 수치지도 교통섬은 회색, 추정 교통섬은 빨간 테두리."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.plotting import plot_polygon

    def polys(g):
        if g is None or g.is_empty:
            return []
        return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"]
    fig, ax = plt.subplots(figsize=(12, 12), dpi=130)
    clip = box(*bbox) if bbox else None
    for g, fc in ((road, "#8a8a8a"), (walk, "#e4e2dc"), (islands, "#b8b4a8")):
        if g is not None and clip is not None:
            g = g.intersection(clip)
        for p in polys(g):
            plot_polygon(p, ax=ax, add_points=False, facecolor=fc, edgecolor="none")
    for p in polys(core):
        plot_polygon(p, ax=ax, add_points=False, facecolor="none", edgecolor="#2a9d5c", linewidth=1.0, linestyle="--")
    for p in polys(inferred):
        plot_polygon(p, ax=ax, add_points=False, facecolor="#c9c3b3", edgecolor="#e01010", linewidth=1.2)
    for g, k, s in marks:
        fc = "#f2c230" if k in ("중앙선", "주정차금지선") else ("#2860c4" if k == "버스전용차선" else SRC_COLOR.get(s, "#222222"))
        if k in ("유도선", "안전지대"):
            fc = "#ffffff"
        for p in polys(g if clip is None else g.intersection(clip)):
            plot_polygon(p, ax=ax, add_points=False, facecolor=fc, edgecolor="none")
    if bbox:
        ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color="#0a8f5a", label="HD map"), Patch(color="#1f6fd1", label="API / data file"), Patch(color="#d1501f", label="aerial photo"),
                       Patch(color="#222222", label="estimated"), Patch(color="#f2c230", label="center line"),
                       Patch(facecolor="#c9c3b3", edgecolor="#e01010", label="inferred island"),
                       Patch(facecolor="none", edgecolor="#2a9d5c", linestyle="--", label="junction core")],
              loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)

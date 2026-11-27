"""모델 만들기 4단계: 길·면, 나무, 가로등, 도로 디테일, 거리 실황."""
import os

import numpy as np
import trimesh
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from .progress import low_trees

from .mapdata import _crs_wkt
from .geom_util import _shifted
from .terrain import (drape_lines, level_at)
from .greens import _green_areas, _green_trees, _real_street_trees
from .preview import _plan_greens, _plan_paths, _plan_sheets, _plan_trees


def _paths(st):
    """4단계: 길 면(차도·보도·교통섬), 정류장·지하철 출입구, 고가·교량."""
    from . import site as site_mod
    a, bbox, log, origin, pg = st.a, st.bbox, st.log, st.origin, st.pg
    # SHP: 길을 종류별 면으로 표현, 선형 도로는 면이 없는 곳(단지 내 도로경계)만
    from .paths_trees import drape_areas, load_paths
    pg.step("4.1")
    st.path_areas, edge_lines = load_paths(a.src, bbox)
    if st.wipe is not None:
        before = sum(g.area for g in st.path_areas.values())
        st.path_areas = site_mod.cut_areas(st.path_areas, st.wipe)
        edge_lines = site_mod.cut_lines(edge_lines, st.wipe)
        st.cleared["길(㎡)"] = round(before - sum(g.area for g in st.path_areas.values()))
    if a.road_detail and a.detail >= 2 and st.path_areas.get("road") is not None:
        # 정류장 지붕·지하철 출입구 캐노피는 수치지도에 '무벽건물'로 있다 → 건물에서 빼서 교통시설로
        from .road_network import _centerlines, _nodes
        from .transit import classify
        try:
            walk_t = unary_union([g for k, g in st.path_areas.items() if k in ("sidewalk", "island") and g is not None])
            cl = _centerlines(a.src, bbox)
            nd = [p for p, e in _nodes(cl) if len(e) >= 3 and max(cl[i]["w"] for i, _ in e) >= 8]
            st.transit_items = classify(st.masses, st.path_areas["road"], walk_t, nd, log)
            for d in st.transit_items:
                d["road"] = st.path_areas["road"]
        except Exception as e:
            log(f"   (정류장·지하철 출입구를 찾지 못함: {type(e).__name__}: {e})")
    if a.structures:
        from .structures import load_structures
        try:
            st.structs, bridged = load_structures(a.src, bbox, lambda xy: st.interp(np.asarray(xy, float)), log)
            if bridged is not None and "road" in st.path_areas:
                st.path_areas["road"] = st.path_areas["road"].difference(bridged)   # 다리 위 길은 상판이 대신한다
        except Exception as e:
            log(f"   (고가도로·교량을 만들지 못함: {type(e).__name__}: {e})")
        if pg.live and st.structs:
            pg.part("struct", [(_shifted(mm, origin), (176, 174, 168)) for mm, k, n in st.structs],
                    info=f"구조물 {len(st.structs)}개")
    pg.step("4.2")
    st.areas = drape_areas(st.path_areas, st.regions)
    pg.step("4.3", x=f"길 면 {len(st.areas)}개")
    st.edges = drape_lines(edge_lines, st.regions)
    st.edge_lines = edge_lines
    if pg.live:
        pg.plan("r", bbox, lambda ax: (_plan_sheets(ax, st.regions), _plan_paths(ax, st.areas)))
    st.draped = []


def _trees(st):
    """5단계: 가로수와 숲·정원 나무."""
    from .paths_trees import drape_areas
    from . import site as site_mod
    a, bbox, log, pg = st.a, st.bbox, st.log, st.pg
    pg.step("5.1")
    if not a.no_trees:
        st.trees = _real_street_trees(a, st.path_areas, st.masses, st.regions, bbox, log, pg)
        if st.wipe is not None:
            n0 = len(st.trees); st.trees = site_mod.outside(st.trees, st.wipe)
            st.cleared["나무"] = n0 - len(st.trees)
    if a.green:
        green_geom = _green_areas(a, st.path_areas, st.masses, bbox, log, pg)
        if st.wipe is not None:
            crowns = green_geom.pop("crowns", None)
            green_geom = site_mod.cut_areas(green_geom, st.wipe, min_area=0.0)
            for k in ("forest", "garden"):
                green_geom.setdefault(k, Polygon())
            if crowns:
                green_geom["crowns"] = site_mod.outside(crowns, st.wipe)
        st.green_geom = {k: v for k, v in green_geom.items() if k in ("forest", "garden", "grass") and v is not None}
        st.trees += _green_trees(a, green_geom, st.regions, st.trees, log)
        if st.wipe is not None:
            n0 = len(st.trees); st.trees = site_mod.outside(st.trees, st.wipe)
            st.cleared["나무"] = st.cleared.get("나무", 0) + n0 - len(st.trees)
        st.greens = drape_areas({k: v for k, v in green_geom.items() if k in ("forest", "garden", "grass") and v is not None}, st.regions)


def _lights(st):
    """5단계: 가로등."""
    from . import site as site_mod
    a, bbox, log = st.a, st.bbox, st.log
    if a.lights:
        from .streetlights import place_lights
        api_lights = []
        if getattr(a, "safemap_key", None):
            try:
                from .crosswalk_api import safemap_lights
                api_lights = safemap_lights(a.safemap_key, bbox, _crs_wkt(a), a.safemap_light_layer, log=log)
            except Exception as e:
                log(f"   ({e})")
        try:
            blds = unary_union([m["poly"] for m in st.masses]) if st.masses else None
            st.lights = [(x, y, level_at(st.regions, Point(x, y)), h, ang, kind, src)
                      for x, y, h, ang, kind, src in place_lights(
                          a.src, bbox, _crs_wkt(a), st.path_areas.get("road"), blds, a.light_files, log=log,
                          extra=api_lights)]
            if st.wipe is not None:
                n0 = len(st.lights); st.lights = site_mod.outside(st.lights, st.wipe)
                st.cleared["가로등"] = n0 - len(st.lights)
        except Exception as e:
            log(f"   (가로등을 넣지 못함: {type(e).__name__}: {e})")


def _road_detail(st):
    """5단계: 도로 디테일(노면표시·횡단보도·신호등·전봇대·교통섬·땅속 시설)."""
    a, bbox, log = st.a, st.bbox, st.log
    if a.road_detail and a.detail >= 2:
        from .road_detail import road_details
        from .transit import stops_from_file
        try:
            blds_rd = unary_union([m["poly"] for m in st.masses]) if st.masses else None
            plats = [d["poly"].buffer(0.6, join_style=2) for d in st.transit_items if d["kind"] == "중앙버스정류장"]
            plats = [unary_union(plats).buffer(5.0, join_style=2).buffer(-5.0, join_style=2)] if plats else []
            st.road_info = road_details(a.src, bbox, _crs_wkt(a), st.path_areas.get("road"), blds_rd, a.aerial,
                                     a.road_files, [(t[0], t[1]) for t in st.lights], log,
                                     keep_out=unary_union(plats) if plats else None,
                                     walk=unary_union([g for k, g in st.path_areas.items()
                                                       if k in ("sidewalk", "island", "stairs") and g is not None]),
                                     islands=st.path_areas.get("island"),
                                     api=dict(seoul_key=a.seoul_key, safemap_key=a.safemap_key,
                                              seoul_service=a.seoul_service, safemap_layer=a.safemap_layer,
                                              seoul_code=a.seoul_code, cache_dir=a.cache_dir),
                                     hd_dir=a.hd_dir)
            if not st.road_info.get("busstops") and a.data_key:      # 자료 파일이 없으면 공공데이터포털에서 (v2.3)
                from .busstop_api import fetch as fetch_stops
                try:
                    st.road_info["busstops"] = fetch_stops(a.data_key, bbox, _crs_wkt(a), a.cache_dir, log)
                except Exception as e:
                    log(f"   (버스 정류장을 받지 못함: {type(e).__name__}: {e})")
            st.transit_items += stops_from_file(st.road_info.get("busstops") or [], st.path_areas.get("road"),
                                             st.transit_items, log)
            st.osm = None
            if a.osm:                       # OpenStreetMap 지하철 출입구·선로·역 (v2.3)
                from .osm_transit import entrances_to_items, fetch as fetch_osm
                try:
                    st.osm = fetch_osm(bbox, _crs_wkt(a), a.cache_dir, log)
                except Exception as e:
                    log(f"   ({e})")
                if st.osm:
                    st.transit_items += entrances_to_items(st.osm["entrances"], st.path_areas.get("road"),
                                                           st.transit_items, log, blds=blds_rd)
            if st.lights and st.transit_items:       # 가로등이 정류장 지붕·출입구 캐노피를 뚫지 않게 (v2.4.1)
                from .transit import clear_lights
                st.lights = clear_lights(st.lights, st.transit_items,
                                         lambda x, y: level_at(st.regions, Point(x, y)), log)
            st.road_info["transit"] = st.transit_items
            if st.wipe is not None:
                st.road_info["islands_inferred"] = (st.road_info["islands_inferred"].difference(st.wipe)
                                                 if st.road_info.get("islands_inferred") is not None else None)
            inf = st.road_info.get("islands_inferred")
            isl0 = st.path_areas.get("island")
            st.road_info["islands"] = unary_union([g for g in (isl0, inf) if g is not None and not g.is_empty]) \
                if (inf is not None and not inf.is_empty) else isl0
            try:     # 확인 그림: 노면표시 출처별 색 + 교차로 면 + 추정 교통섬
                from .road_detail import marks_check
                marks_check(os.path.join(a.out, "road_marks_check.png"), st.path_areas.get("road"),
                            unary_union([g for k, g in st.path_areas.items() if k in ("sidewalk", "stairs") and g is not None]),
                            st.road_info["marks"], isl0, inf, bbox, core=st.road_info.get("core"))
            except Exception as e:
                log(f"   (노면표시 확인 그림을 못 만듦: {e})")
            try:
                from .underground import collect
                ug = collect(dict(seoul_key=a.seoul_key, seoul_service=a.seoul_service, seoul_code=a.seoul_code,
                                  cache_dir=a.cache_dir), bbox, _crs_wkt(a), log)
                osm = getattr(st, "osm", None)
                if osm and (osm["lines"] or osm["stations"]):
                    ug = ug or {"tunnel": [], "subway": [], "stations": []}
                    ug["lines"] = [ln for ln, under, _ in osm["lines"] if under]
                    if not ug.get("stations"):
                        ug["stations"] = osm["stations"]
                        ug["stations_src"] = "OSM"
                if ug:
                    st.road_info["underground"] = dict(ug, road=st.path_areas.get("road"), clip=st.clip)
            except Exception as e:
                log(f"   (땅속 시설을 넣지 못함: {type(e).__name__}: {e})")
        except Exception as e:
            log(f"   (도로 디테일을 넣지 못함: {type(e).__name__}: {e})")


def _streets_live(st):
    """5단계: 만드는 중 화면에 나무·가로등 보내기."""
    bbox, origin, pg = st.bbox, st.origin, st.pg
    pg.step("5.6", x=f"나무 {len(st.trees)}그루")
    if pg.live and st.trees:
        pg.part("tree", low_trees(st.trees, origin), info=f"나무 {len(st.trees)}그루")
    if pg.live and st.lights:
        from .streetlights import placed_mesh
        pg.part("light", [(trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin)
                                                     for x, y, z, h, ang, kind, _ in st.lights]), (90, 90, 96))],
                info=f"가로등 {len(st.lights)}개")
        pg.plan("tr", bbox, lambda ax: (_plan_sheets(ax, st.regions), _plan_greens(ax, st.greens),
                                        _plan_paths(ax, st.areas), _plan_trees(ax, st.trees)))

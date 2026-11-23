"""모델 만들기 전체 흐름 build(): 입력 → 지형 → 건물·길·나무 → 저장."""
import json
import math
import os
from collections import defaultdict
import numpy as np
import trimesh
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from ..progress import extrude, fill, low_trees
from .build_steps import DEFAULTS, _crs_wkt, _green_areas, _green_trees, _pad_parcels, _real_street_trees
from .export_3dm import _add_site, write_3dm
from .terrain import _dist_to_lines, as_polys, breakline_points, build_interpolator, building_pads, level_at, slope_heightfield, terrace_regions
from .live_preview import _live_details, _live_input, _live_sheet, _live_tin, _mass_meshes, _plan_greens, _plan_paths, _plan_sheets, _plan_trees, _preview_plan
from .export_stl import _road_meshes, write_stl
from .mesh_tools import _shifted
from .buildings import building_masses
from .surface import drape_area_solids, drape_line_3d, drape_lines, heightfield_mesh, simplify_solid, split_inlays, surface_sampler
from .inputs import load_dxf, load_shp
from .export_laser import write_laser



def build(src, bbox, out="out", log=print, **kw):
    """수치지도(SHP 폴더 또는 DXF)에서 콘타 모델 전체를 만든다. 반환: 요약 dict."""
    from types import SimpleNamespace
    from ..progress import Progress
    pg = kw.pop("progress", None) or Progress()   # 화면의 생성 과정 실황 (없으면 조용히)
    a = SimpleNamespace(**{**DEFAULTS, **kw})
    a.src, a.out, a.bbox = src, out, bbox
    os.makedirs(a.out, exist_ok=True)
    from ..tree_models import SPECIES
    SPECIES.clear()
    from ..facade import clear_cache
    clear_cache()
    bbox = tuple(a.bbox)
    min_area = a.min_area if a.min_area is not None else (2.0 * a.scale / 1000) ** 2 * 4  # 모형상 약 4mm²
    log("1/6 수치지도 읽는 중")
    pg.step("1.1")
    data = load_shp(a.src, bbox) if os.path.isdir(a.src) else load_dxf(a.src, bbox)
    origin = (bbox[0], bbox[1])
    clip = box(*bbox)
    if pg.live:
        _live_input(pg, data, bbox, origin, a.interval)
    pg.steps("1.2", "1.3")
    log("2/6 등고선 보간, 계단판 만드는 중")
    pg.steps("2.1", "2.2")
    interp = build_interpolator(data["contours"], data["spots"])
    breaks = [b for b in data.get("breaks", ()) if b[0].intersects(clip.buffer(30))] if a.breaklines else []
    if breaks:
        bp = breakline_points(breaks, interp)
        interp = build_interpolator(data["contours"], data["spots"], extra=bp)
        nk = defaultdict(int)
        for b in breaks:
            nk["옹벽" if b[3] > 0.3 else b[1] or "기타"] += 1
        log("   지형 꺾임선: " + " · ".join(f"{k} {v}줄" for k, v in nk.items()) + f" (높이 점 {len(bp[1]):,}개)")
    sigma, smooth, chaikin_n = a.sigma, a.smooth, 3
    if a.terrain_fit == "exact":
        # 등고선 그대로: 절토면 계단·옹벽처럼 등고선 사이가 좁게 꺾이는 곳을 뭉개지 않게 다듬기를 줄인다.
        # 판 윤곽은 레이저로 자를 수 있는 만큼만(축척 1:1000 에서 0.5m = 0.5mm) 다듬는다.
        sigma, smooth, chaikin_n = min(a.sigma, 0.5), min(a.smooth, 0.5), 1
        log("   지형: 등고선 그대로 (다듬기 최소)")
    if a.dem is not None:
        # 수치표고모델이 있으면 그 높이를 쓰고, 자료가 비는 곳만 등고선으로. 격자 자료라 고르게 펴는 정도도 줄인다
        from ..dem import merged_interpolator
        interp = merged_interpolator(a.dem, interp)
        sigma = min(a.sigma, 1.0)
    # 계단식 대지: 비탈의 건물 터·논을 평평한 단으로, 단 사이는 옹벽처럼 (수치지도 건물·옹벽·담·길·논)
    pads = None
    if a.pads and (data.get("buildings") or data.get("paddies")):
        from ..pads import build_pads
        site_p = (a.site or {}).get("poly")
        uses_site = site_p is not None and not getattr(a, "site_keep_terrain", False) and (
            a.site_clear or a.site.get("levels") or a.site.get("plates") or a.site.get("keep"))
        try:
            bl = [b[0] for b in data["buildings"]]
            barriers = [b[0] for b in data.get("breaks", ())] + list(data.get("walls", ()))
            if not os.path.isdir(a.src):
                barriers += list(data.get("roads", ()))      # DXF: 길은 선뿐이라 선을 막는 선으로
            roads_u = unary_union(data["road_areas"]) if data.get("road_areas") else None
            pcl = _pad_parcels(a, bbox, log)
            pads = build_pads(bl, interp, bbox, barriers=barriers, roads=roads_u, paddies=data.get("paddies", ()),
                              exclude=site_p if uses_site else None, parcels=pcl, log=log)
        except Exception as e:
            log(f"   (계단식 대지를 만들지 못함: {type(e).__name__}: {e})")
            pads = None
        if pads is not None:
            interp = pads.wrap(interp)
    # 사업지 도면의 레벨: 대지 안 지형을 그 레벨로 다시 만들고, 경계 밖 15m 까지 수치지도 지형과 섞는다
    level_site = None
    orig_interp = interp      # 대지 분석(경사·절토·성토)에서 '원래 지형'으로 쓴다
    plates = (a.site or {}).get("plates") or []
    keep_areas = (a.site or {}).get("keep") or []
    if a.site and getattr(a, "site_keep_terrain", False) and (a.site.get("levels") or plates or keep_areas):
        log("   대지 안 원래 지형 유지: 도면의 레벨·레벨판은 지형에 쓰지 않고 경계선만 지형에 눌러 표시합니다")
    elif a.site and (a.site.get("levels") or plates or keep_areas) and a.site.get("poly") is not None \
            and a.site["poly"].intersects(clip):
        from ..site import blended, level_interpolator
        lv = a.site.get("levels")
        if plates:
            # 레벨판 테두리를 높이 점으로 더해 판 사이 빈 곳이 판 높이를 잇게 한다
            from ..sitefile import plate_points
            lines_, spots_ = lv or ([], [])
            lv = (lines_, list(spots_) + plate_points(plates))
        # 표준 대지 파일은 사용자가 역할을 확인한 자료라 지형과 많이 달라도(깊은 절토) 버리지 않는다
        tol = 1e4 if a.site.get("standard") else 30.0
        sf = level_interpolator(lv, interp, a.site["poly"], log, tol=tol, cover=clip) if lv and (lv[0] or lv[1]) else None
        n_pts = getattr(sf, "count", 0) if sf is not None else 0
        if plates or keep_areas:
            from ..sitefile import plan_function
            sf = plan_function(interp, plates, keep_areas, base=sf)
        if sf is not None:
            interp = blended(a.site["poly"], sf, interp)
            level_site = a.site["poly"]
            log(f"   대지 안 지형을 사업지 레벨로 다시 만들었습니다 (레벨 점 {n_pts:,}개"
                + (f" · 레벨판 {len(plates)}개" if plates else "") + (f" · 원지형 유지 {len(keep_areas)}곳" if keep_areas else "") + ")")
    pg.step("2.3")
    if pg.live:
        _live_tin(pg, interp, bbox, origin)
    regions, zmin = terrace_regions(data["contours"], interp, bbox, a.interval, min_area,
                                    smooth=smooth, sigma=sigma, chaikin_n=chaikin_n, keep=level_site,
                                    keep_w=pads.keep_weight if pads is not None else None)
    lv = list(regions)
    pg.step("2.4", x=f"EL {lv[0]:g}부터 {a.interval:g}m마다 {len(lv)}단")
    # 판이 많으면(예: 1m 간격 수백 장) 화면 실황은 최대 30묶음으로 나눠 보낸다.
    # 판마다 3D 부품·평면 그림을 만들면 그림 그리는 시간이 판 수의 제곱으로 늘어난다.
    stride = max(1, math.ceil(len(lv) / 30))
    for i, L in enumerate(lv):
        pg.step("2.5", r=i, n=len(lv), x=f"판 {i + 1} / {len(lv)} · EL {L:g}")
        if pg.live and (i % stride == stride - 1 or i == len(lv) - 1):
            _live_sheet(pg, regions, i - i % stride, bbox, origin, j=i + 1, key=i // stride)
    pg.steps("2.6", "2.7")
    log("3/6 건물·길·가로수 배치 중")
    pg.steps("3.1", "3.2")
    masses = building_masses(data["buildings"], regions, clip, a.floor_height)
    # 사업 대지: 경계를 표시하고, 고르면 대지 위(건물·길·나무·시설물)를 비운다
    site_poly = a.site.get("poly") if a.site else None
    if site_poly is not None and (site_poly.is_empty or not site_poly.intersects(clip)):
        log("   (대지가 모델 범위 밖에 있어 경계를 넣지 않습니다)")
        site_poly = None
    wipe = site_poly if (site_poly is not None and a.site_clear) else None
    cleared = {}
    if wipe is not None:
        from .. import site as site_mod
        masses, gone = site_mod.split_masses(masses, wipe)
        cleared["건물"] = len(gone)
        log(f"   대지 위 비우기: 건물 {len(gone)}동을 뺐습니다 (대지 {wipe.area:,.0f}㎡)")
    register = None
    if a.vworld_key and a.data_key and masses:
        # 건축물대장: 실제 높이·층수·준공연도 (키는 사용자 것, 실패해도 수치지도 층수로 계속)
        from ..publicdata import enrich
        crs_wkt = a.crs_wkt
        if not crs_wkt and os.path.isdir(a.src):
            from ..pipeline import read_crs
            crs_wkt = read_crs(a.src).to_wkt()
        cache = a.cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
        register = enrich(masses, bbox, crs_wkt, a.vworld_key, a.data_key, a.vworld_domain, cache,
                          a.floor_height, log)
    if a.detail <= 1:
        for m in masses:
            m["lod"] = 1
    if a.facade and a.detail >= 2 and masses:
        from ..facade import assign
        assign(masses, a.aerial, log)
        cx, cy = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        if a.site and a.site.get("poly") is not None:
            cx, cy = a.site["poly"].centroid.x, a.site["poly"].centroid.y
        r = a.facade_radius
        n = 0
        for m in masses:
            c = m["poly"].centroid
            if (a.detail >= 3 and (r is None or math.hypot(c.x - cx, c.y - cy) <= r)
                    and m.get("layer") not in ("무벽건물", "공사중건물")):
                m["facade"]["windows"] = True; n += 1
        if a.detail >= 3:
            log(f"   창문을 낼 건물 {n}동" + (f" (가운데에서 {r:g}m 안)" if r else ""))
    pg.step("3.3", x=f"건물 {len(masses)}동")
    if pg.live:
        pg.part("mass", _mass_meshes(masses, origin), info=f"건물 {len(masses)}동")
        pg.plan("b", bbox, lambda ax: (_plan_sheets(ax, regions),
                                       fill(ax, [m["poly"] for m in masses], "white", "#333", 0.3, 3)))
    pg.step("3.4")
    areas, edges, trees, greens, lights, structs = [], [], [], [], [], []
    road_info = None
    transit_items = []
    if os.path.isdir(a.src):
        # SHP: 길을 종류별 면으로 표현, 선형 도로는 면이 없는 곳(단지 내 도로경계)만
        from ..paths_trees import drape_areas, load_paths
        pg.step("4.1")
        path_areas, edge_lines = load_paths(a.src, bbox)
        if wipe is not None:
            before = sum(g.area for g in path_areas.values())
            path_areas = site_mod.cut_areas(path_areas, wipe)
            edge_lines = site_mod.cut_lines(edge_lines, wipe)
            cleared["길(㎡)"] = round(before - sum(g.area for g in path_areas.values()))
        if a.road_detail and a.detail >= 2 and path_areas.get("road") is not None:
            # 정류장 지붕·지하철 출입구 캐노피는 수치지도에 '무벽건물'로 있다 → 건물에서 빼서 교통시설로
            from ..road_detail import _centerlines, _nodes
            from ..transit import classify
            try:
                walk_t = unary_union([g for k, g in path_areas.items() if k in ("sidewalk", "island") and g is not None])
                cl = _centerlines(a.src, bbox)
                nd = [p for p, e in _nodes(cl) if len(e) >= 3 and max(cl[i]["w"] for i, _ in e) >= 8]
                transit_items = classify(masses, path_areas["road"], walk_t, nd, log)
                for d in transit_items:
                    d["road"] = path_areas["road"]
            except Exception as e:
                log(f"   (정류장·지하철 출입구를 찾지 못함: {type(e).__name__}: {e})")
        if a.structures:
            from ..structures import load_structures
            try:
                structs, bridged = load_structures(a.src, bbox, lambda xy: interp(np.asarray(xy, float)), log)
                if bridged is not None and "road" in path_areas:
                    path_areas["road"] = path_areas["road"].difference(bridged)   # 다리 위 길은 상판이 대신한다
            except Exception as e:
                log(f"   (고가도로·교량을 만들지 못함: {type(e).__name__}: {e})")
            if pg.live and structs:
                pg.part("struct", [(_shifted(mm, origin), (176, 174, 168)) for mm, k, n in structs],
                        info=f"구조물 {len(structs)}개")
        pg.step("4.2")
        areas = drape_areas(path_areas, regions)
        pg.step("4.3", x=f"길 면 {len(areas)}개")
        edges = drape_lines(edge_lines, regions)
        if pg.live:
            pg.plan("r", bbox, lambda ax: (_plan_sheets(ax, regions), _plan_paths(ax, areas)))
        draped = []
        pg.step("5.1")
        if not a.no_trees:
            trees = _real_street_trees(a, path_areas, masses, regions, bbox, log, pg)
            if wipe is not None:
                n0 = len(trees); trees = site_mod.outside(trees, wipe)
                cleared["나무"] = n0 - len(trees)
        if a.green:
            green_geom = _green_areas(a, path_areas, masses, bbox, log, pg)
            if wipe is not None:
                crowns = green_geom.pop("crowns", None)
                green_geom = site_mod.cut_areas(green_geom, wipe, min_area=0.0)
                for k in ("forest", "garden"):
                    green_geom.setdefault(k, Polygon())
                if crowns:
                    green_geom["crowns"] = site_mod.outside(crowns, wipe)
            trees += _green_trees(a, green_geom, regions, trees, log)
            if wipe is not None:
                n0 = len(trees); trees = site_mod.outside(trees, wipe)
                cleared["나무"] = cleared.get("나무", 0) + n0 - len(trees)
            greens = drape_areas({k: v for k, v in green_geom.items() if k in ("forest", "garden", "grass") and v is not None}, regions)
        if a.lights:
            from ..streetlights import place_lights
            api_lights = []
            if getattr(a, "safemap_key", None):
                try:
                    from ..crosswalk_api import safemap_lights
                    api_lights = safemap_lights(a.safemap_key, bbox, _crs_wkt(a), a.safemap_light_layer, log=log)
                except Exception as e:
                    log(f"   ({e})")
            try:
                blds = unary_union([m["poly"] for m in masses]) if masses else None
                lights = [(x, y, level_at(regions, Point(x, y)), h, ang, kind, src)
                          for x, y, h, ang, kind, src in place_lights(
                              a.src, bbox, _crs_wkt(a), path_areas.get("road"), blds, a.light_files, log=log,
                              extra=api_lights)]
                if wipe is not None:
                    n0 = len(lights); lights = site_mod.outside(lights, wipe)
                    cleared["가로등"] = n0 - len(lights)
            except Exception as e:
                log(f"   (가로등을 넣지 못함: {type(e).__name__}: {e})")
        if a.road_detail and a.detail >= 2:
            from ..road_detail import road_details
            from ..transit import stops_from_file
            try:
                blds_rd = unary_union([m["poly"] for m in masses]) if masses else None
                plats = [d["poly"].buffer(0.6, join_style=2) for d in transit_items if d["kind"] == "중앙버스정류장"]
                plats = [unary_union(plats).buffer(5.0, join_style=2).buffer(-5.0, join_style=2)] if plats else []
                road_info = road_details(a.src, bbox, _crs_wkt(a), path_areas.get("road"), blds_rd, a.aerial,
                                         a.road_files, [(t[0], t[1]) for t in lights], log,
                                         keep_out=unary_union(plats) if plats else None,
                                         walk=unary_union([g for k, g in path_areas.items()
                                                           if k in ("sidewalk", "island", "stairs") and g is not None]),
                                         islands=path_areas.get("island"),
                                         api=dict(seoul_key=a.seoul_key, safemap_key=a.safemap_key,
                                                  seoul_service=a.seoul_service, safemap_layer=a.safemap_layer,
                                                  seoul_code=a.seoul_code, cache_dir=a.cache_dir),
                                         hd_dir=a.hd_dir)
                transit_items += stops_from_file(road_info.get("busstops") or [], path_areas.get("road"),
                                                 transit_items, log)
                road_info["transit"] = transit_items
                if wipe is not None:
                    road_info["islands_inferred"] = (road_info["islands_inferred"].difference(wipe)
                                                     if road_info.get("islands_inferred") is not None else None)
                inf = road_info.get("islands_inferred")
                isl0 = path_areas.get("island")
                road_info["islands"] = unary_union([g for g in (isl0, inf) if g is not None and not g.is_empty]) \
                    if (inf is not None and not inf.is_empty) else isl0
                try:     # 확인 그림: 노면표시 출처별 색 + 교차로 면 + 추정 교통섬
                    from ..road_detail import marks_check
                    marks_check(os.path.join(a.out, "road_marks_check.png"), path_areas.get("road"),
                                unary_union([g for k, g in path_areas.items() if k in ("sidewalk", "stairs") and g is not None]),
                                road_info["marks"], isl0, inf, bbox, core=road_info.get("core"))
                except Exception as e:
                    log(f"   (노면표시 확인 그림을 못 만듦: {e})")
                try:
                    from ..underground import collect
                    ug = collect(dict(seoul_key=a.seoul_key, seoul_service=a.seoul_service, seoul_code=a.seoul_code,
                                      cache_dir=a.cache_dir), bbox, _crs_wkt(a), log)
                    if ug:
                        road_info["underground"] = dict(ug, road=path_areas.get("road"), clip=box(*bbox))
                except Exception as e:
                    log(f"   (땅속 시설을 넣지 못함: {type(e).__name__}: {e})")
            except Exception as e:
                log(f"   (도로 디테일을 넣지 못함: {type(e).__name__}: {e})")
        pg.step("5.6", x=f"나무 {len(trees)}그루")
        if pg.live and trees:
            pg.part("tree", low_trees(trees, origin), info=f"나무 {len(trees)}그루")
        if pg.live and lights:
            from ..streetlights import placed_mesh
            pg.part("light", [(trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin)
                                                         for x, y, z, h, ang, kind, _ in lights]), (90, 90, 96))],
                    info=f"가로등 {len(lights)}개")
            pg.plan("tr", bbox, lambda ax: (_plan_sheets(ax, regions), _plan_greens(ax, greens),
                                            _plan_paths(ax, areas), _plan_trees(ax, trees)))
    else:
        draped = drape_lines(data["roads"], regions)
    terrain_mesh = skp_terrain = None
    print_field = None           # 3D 프린트 STL 용 높이 격자 (경사면일 때)
    skp_solids = []
    trees_lv, edges_lv, areas_lv, lights_lv = trees, edges, areas, lights  # 레이저 판은 항상 계단 레벨 기준
    # 차도·자전거도로는 계단판 한 장 두께로 끼워 넣고(판은 그만큼 파냄), 나머지 길은 판 위에 얇게
    pg.step("4.4")
    inlays, areas = split_inlays(areas)
    if pg.live and inlays and a.terrain != "slope":
        from ..paths_trees import PATH_STYLE
        pg.part("inlay", [(extrude(p, L - a.interval + 0.03, L + 0.03, origin), PATH_STYLE[k][1])
                          for p, L, k in inlays], info=f"차도 조각 {len(inlays)}개")
    area_meshes = []
    if a.terrain in ("slope", "combo"):
        log("4/6 경사면·건물 터 만드는 중")
        pg.step("6.1")
        X, Y, H, B = slope_heightfield(regions, bbox, return_levels=True)
        if breaks and a.dem is None:
            # 꺾임선 둘레(8m 안)는 꺾임선이 들어간 보간면을 그대로, 20m 밖은 원래 방식(등고선 사이를 고르게), 그 사이는 섞는다
            import shapely
            Hb = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
            dist = _dist_to_lines(shapely.multilinestrings([b[0] for b in breaks]),
                                  shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip((20.0 - dist) / 12.0, 0.0, 1.0)
            H = w * Hb + (1 - w) * H
        if a.dem is not None:
            # 경사면도 등고선 사이를 잇는 대신 DEM 높이 그대로 (절토면 계단·옹벽 같은 꺾임이 살아난다)
            H = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
        elif level_site is not None:
            # 대지 안 경사면도 사업지 레벨 그대로, 경계 밖 15m 에서 원래 경사면으로 넘어간다
            import shapely
            Hl = interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
            d = shapely.distance(level_site, shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip(1.0 - d / 15.0, 0.0, 1.0)
            H = w * Hl + (1 - w) * H
        if pads is not None:
            # 경사면에도 같은 평평한 단: 터 안은 터 높이, 둘레는 옹벽(거의 수직)·1:1 비탈로 경사면과 잇는다.
            # 대지 레벨을 쓰는 대지 안은 터를 만들지 않았으므로 그대로다.
            H = pads.apply(np.column_stack([X.ravel(), Y.ravel()]), H.ravel()).reshape(X.shape)
        pg.step("6.2")
        H = building_pads(X, Y, H, masses)
        print_field = (X, Y, H, B)
        if pg.live:
            pg.plan("sl", bbox, lambda ax: ax.imshow(H, origin="lower", cmap="terrain",
                                                     extent=(bbox[0], bbox[2], bbox[1], bbox[3])))
        lv = list(regions)
        if a.terrain == "combo":
            # 계단판 윗면에서 경사면까지만 채우는 얹힘 매스 (윗면은 계단과 겹쳐 깜빡이지 않게 5cm 띄움)
            terrain_mesh = heightfield_mesh(X, Y, H + 0.05, np.minimum(B, H), origin)
        else:
            terrain_mesh = heightfield_mesh(X, Y, H, lv[0] - (lv[1] - lv[0]) * 2, origin)
        terrain_mesh = simplify_solid(terrain_mesh)
        hz = surface_sampler(X, Y, H)
        # 곡면 대지 위에는 길 전체를 두께 있는 한 층으로 (보도·교통섬은 연석 높이만큼 더)
        area_meshes = drape_area_solids(areas_lv, hz, origin, a.road_thickness)
        if a.sketchup:
            # 스케치업용은 가볍게: 대지 격자 2m, 길 면 세분 8m
            X2, Y2, H2, B2 = X[::2, ::2], Y[::2, ::2], H[::2, ::2], B[::2, ::2]
            skp_terrain = (heightfield_mesh(X2, Y2, H2 + 0.05, np.minimum(B2, H2), origin) if a.terrain == "combo"
                           else heightfield_mesh(X2, Y2, H2, lv[0] - (lv[1] - lv[0]) * 2, origin))
            skp_terrain = simplify_solid(skp_terrain)
            skp_solids = drape_area_solids(areas_lv, hz, origin, a.road_thickness, max_edge=8.0)
        areas = []
        edges = [(drape_line_3d(ls, hz), L) for ls, L in edges]
        from ..paths_trees import PATH_STYLE
        on_walk = a.road_thickness + PATH_STYLE["sidewalk"][2]   # 가로수는 보도 층 위에 선다
        trees = [(x, y, float(hz([x], [y])[0]) + (on_walk if k == "street" else 0.0), r, k)
                 for x, y, z, r, k in trees]
        lights = [(x, y, float(hz([x], [y])[0]) + (on_walk if kind == "가로등" else a.road_thickness), h, ang, kind, src)
                  for x, y, z, h, ang, kind, src in lights]
        pg.step("6.3")
        for m in masses:
            h = m.get("height", m["top"] - level_at(regions, m["poly"].representative_point()))
            pad = m.get("pad")
            if pad is None:
                c = m["poly"].representative_point(); pad = float(hz([c.x], [c.y])[0])
            m["level"] = m["base"]
            # 바닥은 터에 살짝 묻어 틈이 없게, 다만 가장 낮은 판보다 깊이는 묻지 않는다
            m["base"], m["top"] = max(pad - 0.3, m["level"]), pad + h
        if pg.live:
            pg.part("mass", _mass_meshes(masses, origin), info="터 위에 다시 앉힘")
        pg.step("6.4")
        if pg.live:
            s = slice(None, None, 3)   # 실황용은 3m 격자로 가볍게
            Hs, Bs = H[s, s], B[s, s]
            pg.part("slope", heightfield_mesh(X[s, s], Y[s, s], Hs + 0.05,
                                              np.minimum(Bs, Hs) if a.terrain == "combo" else lv[0] - a.interval * 2,
                                              origin),
                    replaces=["sheet_*", "inlay"] if a.terrain == "slope" else [])
        pg.step("4.5")
        if pg.live and areas_lv:
            sol = skp_solids or drape_area_solids(areas_lv, hz, origin, a.road_thickness, max_edge=8.0)
            pg.part("path", [(m, PATH_STYLE[k][1]) for m, k in sol], info=f"길 층 {len(sol)}개")
        pg.step("6.5")
        if pg.live and trees:
            pg.part("tree", low_trees(trees, origin), info="곡면 위로 올림")
        if pg.live and lights:
            from ..streetlights import placed_mesh
            pg.part("light", [(trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin)
                                                         for x, y, z, h, ang, kind, _ in lights]), (90, 90, 96))])
    pg.step("4.6")
    if pg.live:
        _live_details(pg, masses, a.floor_height, origin)
    if wipe is not None:
        if road_info:
            cleared["도로시설"] = site_mod.cut_road_info(road_info, wipe)
        log("   대지 위 비우기: " + " · ".join(f"{k} {v:,}" for k, v in cleared.items() if v) if any(cleared.values())
            else "   대지 위 비우기: 대지 안에 뺄 것이 없었습니다")
    site_mesh = None
    if site_poly is not None:
        try:
            from ..site import outline_mesh
            if a.terrain in ("slope", "combo"):
                site_mesh = outline_mesh(site_poly.intersection(clip), lambda x, y: float(hz([x], [y])[0]) + a.road_thickness, origin)
            else:
                site_mesh = outline_mesh(site_poly.intersection(clip), lambda x, y: level_at(regions, Point(x, y)), origin)
            if pg.live and site_mesh is not None:
                pg.part("site", [(site_mesh, (37, 99, 235))], info="대지 경계")
        except Exception as e:
            log(f"   (대지 경계 띠를 만들지 못함: {e})")
    roadbits = _road_meshes(road_info, hz if a.terrain in ("slope", "combo") else None, regions,
                            a.road_thickness) if road_info else []
    if pg.live and roadbits:
        pg.part("roadx", [(_shifted(m, origin), rgb) for m, lay, rgb, nm in roadbits],
                info=f"도로 디테일 {len(roadbits)}묶음")
    combo = a.terrain == "combo"
    log("5/6 Rhino(3dm)·STL·레이저 도면 쓰는 중")
    pg.steps("8.1", "8.2")

    def save_tick(kind):
        """저장 단계 세부 진행: 화면 왼쪽 '지금 하는 일' 줄과 진행 막대를 움직인다."""
        def t(label, r=None, n=None, est=None):
            pg.note("sub", k=5, file=kind, x=label, r=r, n=n, est=est)
        return t
    site_summary = None

    def add_site(f):
        nonlocal site_summary
        try:
            site_summary = _add_site(f, dict(a.site, poly=site_poly, regions=regions, keep_terrain=getattr(a, "site_keep_terrain", False)), interp, origin, log,
                                     step=1.0 if level_site is not None else None)
            site_summary.update(area=round(site_poly.area, 1), cleared=cleared if wipe is not None else None)
        except Exception as e:
            log(f"   (대지 경계·일조 사선을 넣지 못함: {e})")
    write_3dm(os.path.join(a.out, "model.3dm"), regions, masses, draped, origin, areas, trees, edges,
              terrain_mesh, area_meshes, combo, a.floor_height, inlays, lights, structs,
              "species" if a.detail >= 2 else "simple", roadbits,
              before_write=add_site if site_poly is not None else None, tick=save_tick("Rhino"))
    pg.step("8.3")
    write_stl(os.path.join(a.out, "model.stl"), regions, masses, origin, areas, trees, terrain_mesh,
              area_meshes, combo, a.floor_height, inlays, lights, structs,
              "species" if a.detail >= 2 else "simple", roadbits, site_mesh=site_mesh, tick=save_tick("STL"),
              scale=a.scale, field=print_field,
              site_poly=site_poly.intersection(clip) if site_poly is not None else None)
    if a.sketchup:
        pg.step("8.4")
        pg.note("sub", k=5, file="SketchUp", x="스케치업 파일")
        from ..skp_export import write_sketchup
        site_lines = []
        if site_poly is not None:
            rings = [LineString(r.coords) for g in as_polys(site_poly) for r in [g.exterior, *g.interiors]]
            site_lines = [(f"대지 경계 · 판 EL {L:g}", np.array([(x, y, L + 0.02) for x, y, *_ in ls.coords]))
                          for ls, L in drape_lines(rings, regions)]
        write_sketchup(a.out, regions, masses, origin, skp_terrain, skp_solids, inlays, areas, trees,
                       combo, a.floor_height, lights, structs, "species" if a.detail >= 2 else "simple", roadbits,
                       site_lines=site_lines, log=log)
    pg.steps("9.1", "9.2", "9.3")
    pg.note("explode")
    pg.step("9.4")
    pg.note("sub", k=5, file="레이저", x="레이저 판 DXF")
    sheets = write_laser(os.path.join(a.out, "laser"), regions, masses, draped, origin, a.scale,
                         areas_lv, trees_lv, edges_lv, a.tree_hole, greens, lights_lv,
                         site=site_poly.intersection(clip) if site_poly is not None else None)
    for i, f in enumerate(sheets):
        pg.step("9.5", r=i, n=len(sheets), x=os.path.basename(str(f)))
    summary = dict(levels=list(regions), sheets=len(sheets), buildings=len(masses),
                   road_segments=len(draped) + len(edges), path_areas=len(areas) + len(area_meshes),
                   road_inlays=len(inlays), road_thickness_m=a.road_thickness,
                   building_uses=dict(sorted({u: sum(1 for m in masses if m.get("use") == u)
                                              for u in {m.get("use") for m in masses}}.items(),
                                             key=lambda kv: -kv[1])),
                   building_register=register, site=site_summary,
                   pads=pads.stats if pads is not None else None,
                   buildings_by_decade=dict(sorted({f"{m['year'] // 10 * 10}년대": sum(
                       1 for n in masses if n.get("year", 0) // 10 == m["year"] // 10)
                       for m in masses if m.get("year")}.items())),
                   structures={k: sum(1 for _, k2, _n in structs if k2 == k) for k in dict.fromkeys(s_[1] for s_ in structs)},
                   lights=len(lights), lights_by_source={s_: sum(1 for t in lights if t[6] == s_)
                                                         for s_ in dict.fromkeys(t[6] for t in lights)},
                   trees=len(trees), trees_by_kind={k: sum(1 for t in trees if t[4] == k) for k in ("street", "forest", "garden")},
                   terrain=a.terrain,
                   building_pad_cutfill_max_m=round(max([m.get("cutfill", 0) for m in masses] or [0]), 2),
                   building_pad_cutfill_median_m=round(float(np.median([m.get("cutfill", 0) for m in masses] or [0])), 2),
                   contours=len(data["contours"]),
                   model_size_mm=[(bbox[2] - bbox[0]) * 1000 / a.scale, (bbox[3] - bbox[1]) * 1000 / a.scale])
    if site_poly is not None and getattr(a, "analysis", True):
        # 대지 분석: 원래 지형 경사, 레벨 계획이 있으면 절토·성토 (그림 2장 + 숫자 표)
        try:
            from ..analysis import site_analysis
            summary["analysis"] = site_analysis(a.out, bbox, site_poly, orig_interp,
                                                interp if level_site is not None else None,
                                                slope_limit=getattr(a, "slope_limit", 17.5), log=log)
        except Exception as e:  # 분석 실패는 모델에 영향 없음
            log(f"   (대지 분석을 만들지 못했습니다: {type(e).__name__}: {e})")
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    clear_cache()
    try:
        _preview_plan(os.path.join(a.out, "preview_plan.png"), bbox, regions, masses,
                      areas_lv, trees_lv, greens, lights_lv, site=site_poly)
    except Exception as e:  # 미리보기 실패는 모델에 영향 없음
        log(f"   (평면 미리보기 생략: {e})")
    if pg.live and os.path.exists(os.path.join(a.out, "preview_plan.png")):
        import shutil
        name = pg._name("fin", "png")
        shutil.copy(os.path.join(a.out, "preview_plan.png"), os.path.join(pg.live, name))
        pg.note("plan", key="fin", url="/live/" + name)
    pg.note("done")
    log("6/6 완료")
    return summary

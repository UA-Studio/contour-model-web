"""수치지형도(DXF) -> 건축 콘타 모델 (Rhino 3DM, STL, 레이저 커팅용 층별 DXF).

사용 예:
    python contour_model.py map.dxf --bbox 200900 553600 201400 554100 \
        --scale 1000 --sheet 5 --out out/
"""
import argparse
import json
import math
import os
from collections import defaultdict
from types import SimpleNamespace

import numpy as np
import trimesh
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from .progress import extrude, fill, low_trees

# 기능별 파일에서 가져옴. 예전처럼 contour_model 에서 바로 불러도 동작하도록 그대로 다시 내보낸다.
from .mapdata import (BUILDING_PREFIX, CONTOUR_LAYERS, _crs_wkt, load_dxf, load_shp, _pad_parcels,
                      ROAD_PREFIXES, SPOT_LAYERS, WALL_PREFIX, WATER_PREFIX)  # noqa: F401
from .geom_util import (as_polys, chaikin, cut_regions, _mf_extrude, _mf_from_trimesh, _shift,
                        _shifted, smooth_poly)  # noqa: F401
from .mesh_shading import crisp_mesh, _split_sharp, _with_ground_normals  # noqa: F401
from .terrain import (breakline_points, build_interpolator, building_pads, _dist_to_lines,
                      drape_line_3d, drape_lines, heightfield_mesh, level_at, simplify_solid,
                      slope_heightfield, surface_sampler, terrace_regions)  # noqa: F401
from .drape import (drape_area_meshes, drape_area_solids, _drape_solid_tiles, _grid_triangulate,
                    INLAY_KINDS, _road_meshes, ROAD_THICKNESS, split_inlays)  # noqa: F401
from .buildings import (building_masses, FALLBACK_FLOORS, FLOOR_HEIGHT, ROLE_KO, SOURCE_KO,
                        USE_COLORS)  # noqa: F401
from .out_rhino import (_add_site, _RH_COUNT, _rh_curve, _rh_extrusion, _rh_mesh, write_3dm,
                        _write_estimate, WRITE_SEC_PER_OBJ, WRITE_SEC_PER_VERT)  # noqa: F401
from .out_stl import (print_solid, SITE_GROOVE_D, SITE_GROOVE_W, stl_check, STL_SEC_PER_FACE,
                      STL_SEC_PER_FACE_JOIN, _unpinch, write_stl)  # noqa: F401
from .out_laser import write_laser  # noqa: F401
from .greens import _aerial_accuracy, _green_areas, _green_trees, _real_street_trees  # noqa: F401
from .live_view import _live_details, _live_input, _live_sheet, _live_tin, _mass_meshes  # noqa: F401
from .preview import _plan_greens, _plan_paths, _plan_sheets, _plan_trees, _preview_plan  # noqa: F401

# ---------------------------------------------------------------- 메인
DEFAULTS = dict(shape="square", outputs=None, site=None, site_clear=False, site_keep_terrain=False, dem=None, terrain_fit="smooth", breaklines=False, pads=True, parcel_file=None, scale=1000, interval=5, min_area=None, floor_height=FLOOR_HEIGHT, sigma=4.0, smooth=1.5,
                terrain="combo", no_trees=False, ndvi=None, chm=None, aerial=None, crs_wkt=None, vworld_key=None,
                vworld_domain="", data_key=None, cache_dir=None, tree_spacing=8.0, tree_hole=1.0,
                road_thickness=ROAD_THICKNESS, sketchup=True, green=True, lights=True, light_files=(),
                max_forest_trees=30000, facade=True, facade_radius=None, detail=3,
                structures=True, road_detail=True, road_files=(), seoul_key=None, safemap_key=None,
                seoul_service=None, safemap_layer=None, safemap_light_layer=None, seoul_code=None, hd_dir=None, osm=True)


def build(src, bbox, out="out", log=print, **kw):
    """수치지도(SHP 폴더 또는 DXF)에서 콘타 모델 전체를 만든다. 반환: 요약 dict."""
    st = SimpleNamespace()     # 단계 사이에 넘기는 값 (st.masses = 건물, st.regions = 계단판 …)
    _prepare(st, src, bbox, out, log, kw)
    a, data, pg = st.a, st.data, st.pg
    _heights(st)
    _pads(st)
    _site_levels(st)
    _sheets(st)
    _buildings(st)
    _building_details(st)
    pg.step("3.4")
    st.areas, st.edges, st.trees, st.greens, st.lights, st.structs = [], [], [], [], [], []
    st.path_areas, st.green_geom, st.edge_lines = {}, {}, []
    st.road_info = None
    st.transit_items = []
    if os.path.isdir(a.src):
        _paths(st)
        _trees(st)
        _lights(st)
        _road_detail(st)
        _streets_live(st)
        _circle_cut(st)
    else:
        st.draped = drape_lines(data["roads"], st.regions)
    _inlays(st)
    _slope(st)
    _site_mark(st)
    _circle_meshes(st)
    _save(st)
    _summary(st)
    return st.summary


# ---------------------------------------------------------------- 모델 만들기 단계
# build() 가 아래 단계를 차례로 부른다. 단계마다 고칠 곳이 한 함수 안에 모여 있어, 한 단계를 고쳐도 다른 단계는 그대로다.
# 단계 사이에 넘기는 값은 st 에 담는다 (st.regions = 계단판, st.masses = 건물, st.trees = 나무 …).

def _prepare(st, src, bbox, out, log, kw):
    """1단계: 설정을 모으고 수치지도(SHP·DXF)를 읽는다."""
    from types import SimpleNamespace
    from .progress import Progress
    pg = kw.pop("progress", None) or Progress()   # 화면의 생성 과정 실황 (없으면 조용히)
    a = SimpleNamespace(**{**DEFAULTS, **kw})
    a.src, a.out, a.bbox = src, out, bbox
    # 받을 파일 (v2.2): None = 전부. 3D 보기(GLB)·요약·단면 자료는 늘 만든다
    a.want = (lambda k: True) if a.outputs is None else (lambda k, o=frozenset(a.outputs): k in o)
    if not a.want("sketchup"):
        a.sketchup = False
    os.makedirs(a.out, exist_ok=True)
    from .tree_models import SPECIES
    SPECIES.clear()
    from .facade import clear_cache
    clear_cache()
    bbox = tuple(a.bbox)
    min_area = a.min_area if a.min_area is not None else (2.0 * a.scale / 1000) ** 2 * 4  # 모형상 약 4mm²
    log("1/6 수치지도 읽는 중")
    pg.step("1.1")
    data = load_shp(a.src, bbox) if os.path.isdir(a.src) else load_dxf(a.src, bbox)
    origin = (bbox[0], bbox[1])
    st.circle = None
    if a.shape == "circle":                  # 원형 범위 (v2.2): 네모 안에 꼭 맞는 원
        from .circle_range import make_circle
        st.circle = make_circle(bbox)
        log(f"   범위: 지름 {min(bbox[2] - bbox[0], bbox[3] - bbox[1]):g}m 원")
    clip = st.circle if st.circle is not None else box(*bbox)
    if pg.live:
        _live_input(pg, data, bbox, origin, a.interval)
    pg.steps("1.2", "1.3")
    st.a, st.bbox, st.clip, st.data, st.log, st.min_area, st.origin, st.pg = a, bbox, clip, data, log, min_area, origin, pg


def _heights(st):
    """2단계: 등고선·표고점 → 높이 보간 (꺾임선, '등고선 그대로', DEM)."""
    a, clip, data, log, pg = st.a, st.clip, st.data, st.log, st.pg
    log("2/6 등고선 보간, 계단판 만드는 중")
    pg.steps("2.1", "2.2")
    st.interp = build_interpolator(data["contours"], data["spots"])
    st.breaks = [b for b in data.get("breaks", ()) if b[0].intersects(clip.buffer(30))] if a.breaklines else []
    if st.breaks:
        bp = breakline_points(st.breaks, st.interp)
        st.interp = build_interpolator(data["contours"], data["spots"], extra=bp)
        nk = defaultdict(int)
        for b in st.breaks:
            nk["옹벽" if b[3] > 0.3 else b[1] or "기타"] += 1
        log("   지형 꺾임선: " + " · ".join(f"{k} {v}줄" for k, v in nk.items()) + f" (높이 점 {len(bp[1]):,}개)")
    st.sigma, st.smooth, st.chaikin_n = a.sigma, a.smooth, 3
    if a.terrain_fit == "exact":
        # 등고선 그대로: 절토면 계단·옹벽처럼 등고선 사이가 좁게 꺾이는 곳을 뭉개지 않게 다듬기를 줄인다.
        # 판 윤곽은 레이저로 자를 수 있는 만큼만(축척 1:1000 에서 0.5m = 0.5mm) 다듬는다.
        st.sigma, st.smooth, st.chaikin_n = min(a.sigma, 0.5), min(a.smooth, 0.5), 1
        log("   지형: 등고선 그대로 (다듬기 최소)")
    if a.dem is not None:
        # 수치표고모델이 있으면 그 높이를 쓰고, 자료가 비는 곳만 등고선으로. 격자 자료라 고르게 펴는 정도도 줄인다
        from .dem import merged_interpolator
        st.interp = merged_interpolator(a.dem, st.interp)
        st.sigma = min(a.sigma, 1.0)


def _pads(st):
    """2단계: 계단식 대지 — 비탈의 건물 터·논을 평평한 단으로."""
    a, bbox, data, log = st.a, st.bbox, st.data, st.log
    # 계단식 대지: 비탈의 건물 터·논을 평평한 단으로, 단 사이는 옹벽처럼 (수치지도 건물·옹벽·담·길·논)
    st.pads = None
    st.parcels = []
    if a.pads and (data.get("buildings") or data.get("paddies")):
        from .pads import build_pads
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
            st.parcels = pcl                             # 2D 지형도·다이어그램의 지적 경계 (v2.2)
            st.pads = build_pads(bl, st.interp, bbox, barriers=barriers, roads=roads_u, paddies=data.get("paddies", ()),
                              exclude=site_p if uses_site else None, parcels=pcl, log=log)
        except Exception as e:
            log(f"   (계단식 대지를 만들지 못함: {type(e).__name__}: {e})")
            st.pads = None
        if st.pads is not None:
            st.interp = st.pads.wrap(st.interp)


def _site_levels(st):
    """2단계: 사업지 도면의 레벨·레벨판으로 대지 안 지형을 다시 만든다."""
    a, clip, log = st.a, st.clip, st.log
    # 사업지 도면의 레벨: 대지 안 지형을 그 레벨로 다시 만들고, 경계 밖 15m 까지 수치지도 지형과 섞는다
    st.level_site = None
    st.orig_interp = st.interp      # 대지 분석(경사·절토·성토)에서 '원래 지형'으로 쓴다
    plates = (a.site or {}).get("plates") or []
    keep_areas = (a.site or {}).get("keep") or []
    if a.site and getattr(a, "site_keep_terrain", False) and (a.site.get("levels") or plates or keep_areas):
        log("   대지 안 원래 지형 유지: 도면의 레벨·레벨판은 지형에 쓰지 않고 경계선만 지형에 눌러 표시합니다")
    elif a.site and (a.site.get("levels") or plates or keep_areas) and a.site.get("poly") is not None \
            and a.site["poly"].intersects(clip):
        from .site import blended, level_interpolator
        lv = a.site.get("levels")
        if plates:
            # 레벨판 테두리를 높이 점으로 더해 판 사이 빈 곳이 판 높이를 잇게 한다
            from .sitefile import plate_points
            lines_, spots_ = lv or ([], [])
            lv = (lines_, list(spots_) + plate_points(plates))
        # 표준 대지 파일은 사용자가 역할을 확인한 자료라 지형과 많이 달라도(깊은 절토) 버리지 않는다
        tol = 1e4 if a.site.get("standard") else 30.0
        sf = level_interpolator(lv, st.interp, a.site["poly"], log, tol=tol, cover=clip) if lv and (lv[0] or lv[1]) else None
        n_pts = getattr(sf, "count", 0) if sf is not None else 0
        if plates or keep_areas:
            from .sitefile import plan_function
            sf = plan_function(st.interp, plates, keep_areas, base=sf)
        if sf is not None:
            st.interp = blended(a.site["poly"], sf, st.interp)
            st.level_site = a.site["poly"]
            log(f"   대지 안 지형을 사업지 레벨로 다시 만들었습니다 (레벨 점 {n_pts:,}개"
                + (f" · 레벨판 {len(plates)}개" if plates else "") + (f" · 원지형 유지 {len(keep_areas)}곳" if keep_areas else "") + ")")


def _sheets(st):
    """2단계: 계단판(층) 면 만들기."""
    a, bbox, data, min_area, origin, pg = st.a, st.bbox, st.data, st.min_area, st.origin, st.pg
    pg.step("2.3")
    if pg.live:
        _live_tin(pg, st.interp, bbox, origin)
    st.regions, zmin = terrace_regions(data["contours"], st.interp, bbox, a.interval, min_area,
                                    smooth=st.smooth, sigma=st.sigma, chaikin_n=st.chaikin_n, keep=st.level_site,
                                    keep_w=st.pads.keep_weight if st.pads is not None else None)
    if st.circle is not None:
        from .circle_range import cut_regions as circle_regions
        st.regions = circle_regions(st.regions, st.circle, min_area)
    lv = list(st.regions)
    pg.step("2.4", x=f"EL {lv[0]:g}부터 {a.interval:g}m마다 {len(lv)}단")
    # 판이 많으면(예: 1m 간격 수백 장) 화면 실황은 최대 30묶음으로 나눠 보낸다.
    # 판마다 3D 부품·평면 그림을 만들면 그림 그리는 시간이 판 수의 제곱으로 늘어난다.
    stride = max(1, math.ceil(len(lv) / 30))
    for i, L in enumerate(lv):
        pg.step("2.5", r=i, n=len(lv), x=f"판 {i + 1} / {len(lv)} · EL {L:g}")
        if pg.live and (i % stride == stride - 1 or i == len(lv) - 1):
            _live_sheet(pg, st.regions, i - i % stride, bbox, origin, j=i + 1, key=i // stride)
    pg.steps("2.6", "2.7")


def _buildings(st):
    """3단계: 건물 매스를 판 위에 앉히고, 대지 위 비우기를 고르면 대지 안 건물을 뺀다."""
    a, clip, data, log, pg = st.a, st.clip, st.data, st.log, st.pg
    log("3/6 건물·길·가로수 배치 중")
    pg.steps("3.1", "3.2")
    st.masses = building_masses(data["buildings"], st.regions, clip, a.floor_height)
    # 사업 대지: 경계를 표시하고, 고르면 대지 위(건물·길·나무·시설물)를 비운다
    st.site_poly = a.site.get("poly") if a.site else None
    if st.site_poly is not None and (st.site_poly.is_empty or not st.site_poly.intersects(clip)):
        log("   (대지가 모델 범위 밖에 있어 경계를 넣지 않습니다)")
        st.site_poly = None
    st.wipe = st.site_poly if (st.site_poly is not None and a.site_clear) else None
    st.cleared = {}
    if st.wipe is not None:
        from . import site as site_mod
        st.masses, gone = site_mod.split_masses(st.masses, st.wipe)
        st.cleared["건물"] = len(gone)
        log(f"   대지 위 비우기: 건물 {len(gone)}동을 뺐습니다 (대지 {st.wipe.area:,.0f}㎡)")


def _building_details(st):
    """3단계: 건축물대장 높이·층수, 디테일 수준, 외관·창문."""
    a, bbox, log, origin, pg = st.a, st.bbox, st.log, st.origin, st.pg
    st.register = None
    if a.vworld_key and a.data_key and st.masses:
        # 건축물대장: 실제 높이·층수·준공연도 (키는 사용자 것, 실패해도 수치지도 층수로 계속)
        from .publicdata import enrich
        crs_wkt = a.crs_wkt
        if not crs_wkt and os.path.isdir(a.src):
            from .pipeline import read_crs
            crs_wkt = read_crs(a.src).to_wkt()
        cache = a.cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
        st.register = enrich(st.masses, bbox, crs_wkt, a.vworld_key, a.data_key, a.vworld_domain, cache,
                          a.floor_height, log)
    if a.detail <= 1:
        for m in st.masses:
            m["lod"] = 1
    if a.facade and a.detail >= 2 and st.masses:
        from .facade import assign
        assign(st.masses, a.aerial, log)
        cx, cy = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        if a.site and a.site.get("poly") is not None:
            cx, cy = a.site["poly"].centroid.x, a.site["poly"].centroid.y
        r = a.facade_radius
        n = 0
        for m in st.masses:
            c = m["poly"].centroid
            if (a.detail >= 3 and (r is None or math.hypot(c.x - cx, c.y - cy) <= r)
                    and m.get("layer") not in ("무벽건물", "공사중건물")):
                m["facade"]["windows"] = True; n += 1
        if a.detail >= 3:
            log(f"   창문을 낼 건물 {n}동" + (f" (가운데에서 {r:g}m 안)" if r else ""))
    pg.step("3.3", x=f"건물 {len(st.masses)}동")
    if pg.live:
        pg.part("mass", _mass_meshes(st.masses, origin), info=f"건물 {len(st.masses)}동")
        pg.plan("b", bbox, lambda ax: (_plan_sheets(ax, st.regions),
                                       fill(ax, [m["poly"] for m in st.masses], "white", "#333", 0.3, 3)))


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
                                                           st.transit_items, log)
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


def _inlays(st):
    """4단계: 차도·자전거도로를 계단판에 끼워 넣을 조각으로 나눈다."""
    a, origin, pg = st.a, st.origin, st.pg
    st.terrain_mesh = st.skp_terrain = None
    st.print_field = None           # 3D 프린트 STL 용 높이 격자 (경사면일 때)
    st.skp_solids = []
    st.trees_lv, st.edges_lv, st.areas_lv, st.lights_lv = st.trees, st.edges, st.areas, st.lights  # 레이저 판은 항상 계단 레벨 기준
    # 차도·자전거도로는 계단판 한 장 두께로 끼워 넣고(판은 그만큼 파냄), 나머지 길은 판 위에 얇게
    pg.step("4.4")
    st.inlays, st.areas = split_inlays(st.areas)
    if pg.live and st.inlays and a.terrain != "slope":
        from .paths_trees import PATH_STYLE
        pg.part("inlay", [(extrude(p, L - a.interval + 0.03, L + 0.03, origin), PATH_STYLE[k][1])
                          for p, L, k in st.inlays], info=f"차도 조각 {len(st.inlays)}개")
    st.area_meshes = []


def _slope(st):
    """6단계: 경사면(곡면 대지)과 건물 터, 길·나무·가로등을 곡면 위로 올리기."""
    a, bbox, log, origin, pg = st.a, st.bbox, st.log, st.origin, st.pg
    if a.terrain in ("slope", "combo"):
        log("4/6 경사면·건물 터 만드는 중")
        pg.step("6.1")
        X, Y, H, B = slope_heightfield(st.regions, bbox, return_levels=True)
        if st.breaks and a.dem is None:
            # 꺾임선 둘레(8m 안)는 꺾임선이 들어간 보간면을 그대로, 20m 밖은 원래 방식(등고선 사이를 고르게), 그 사이는 섞는다
            import shapely
            Hb = st.interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
            dist = _dist_to_lines(shapely.multilinestrings([b[0] for b in st.breaks]),
                                  shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip((20.0 - dist) / 12.0, 0.0, 1.0)
            H = w * Hb + (1 - w) * H
        if a.dem is not None:
            # 경사면도 등고선 사이를 잇는 대신 DEM 높이 그대로 (절토면 계단·옹벽 같은 꺾임이 살아난다)
            H = st.interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
        elif st.level_site is not None:
            # 대지 안 경사면도 사업지 레벨 그대로, 경계 밖 15m 에서 원래 경사면으로 넘어간다
            import shapely
            Hl = st.interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)
            d = shapely.distance(st.level_site, shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
            w = np.clip(1.0 - d / 15.0, 0.0, 1.0)
            H = w * Hl + (1 - w) * H
        if st.pads is not None:
            # 경사면에도 같은 평평한 단: 터 안은 터 높이, 둘레는 옹벽(거의 수직)·1:1 비탈로 경사면과 잇는다.
            # 대지 레벨을 쓰는 대지 안은 터를 만들지 않았으므로 그대로다.
            H = st.pads.apply(np.column_stack([X.ravel(), Y.ravel()]), H.ravel()).reshape(X.shape)
        pg.step("6.2")
        H = building_pads(X, Y, H, st.masses)
        st.print_field = (X, Y, H, B)
        if pg.live:
            pg.plan("sl", bbox, lambda ax: ax.imshow(H, origin="lower", cmap="terrain",
                                                     extent=(bbox[0], bbox[2], bbox[1], bbox[3])))
        lv = list(st.regions)
        if a.terrain == "combo":
            # 계단판 윗면에서 경사면까지만 채우는 얹힘 매스 (윗면은 계단과 겹쳐 깜빡이지 않게 5cm 띄움)
            st.terrain_mesh = heightfield_mesh(X, Y, H + 0.05, np.minimum(B, H), origin)
        else:
            st.terrain_mesh = heightfield_mesh(X, Y, H, lv[0] - (lv[1] - lv[0]) * 2, origin)
        st.terrain_mesh = simplify_solid(st.terrain_mesh)
        st.hz = surface_sampler(X, Y, H)
        # 곡면 대지 위에는 길 전체를 두께 있는 한 층으로 (보도·교통섬은 연석 높이만큼 더)
        st.area_meshes = drape_area_solids(st.areas_lv, st.hz, origin, a.road_thickness)
        if a.sketchup:
            # 스케치업용은 가볍게: 대지 격자 2m, 길 면 세분 8m
            X2, Y2, H2, B2 = X[::2, ::2], Y[::2, ::2], H[::2, ::2], B[::2, ::2]
            st.skp_terrain = (heightfield_mesh(X2, Y2, H2 + 0.05, np.minimum(B2, H2), origin) if a.terrain == "combo"
                           else heightfield_mesh(X2, Y2, H2, lv[0] - (lv[1] - lv[0]) * 2, origin))
            st.skp_terrain = simplify_solid(st.skp_terrain)
            st.skp_solids = drape_area_solids(st.areas_lv, st.hz, origin, a.road_thickness, max_edge=8.0)
        st.areas = []
        st.edges = [(drape_line_3d(ls, st.hz), L) for ls, L in st.edges]
        from .paths_trees import PATH_STYLE
        on_walk = a.road_thickness + PATH_STYLE["sidewalk"][2]   # 가로수는 보도 층 위에 선다
        st.trees = [(x, y, float(st.hz([x], [y])[0]) + (on_walk if k == "street" else 0.0), r, k)
                 for x, y, z, r, k in st.trees]
        st.lights = [(x, y, float(st.hz([x], [y])[0]) + (on_walk if kind == "가로등" else a.road_thickness), h, ang, kind, src)
                  for x, y, z, h, ang, kind, src in st.lights]
        pg.step("6.3")
        for m in st.masses:
            h = m.get("height", m["top"] - level_at(st.regions, m["poly"].representative_point()))
            pad = m.get("pad")
            if pad is None:
                c = m["poly"].representative_point(); pad = float(st.hz([c.x], [c.y])[0])
            m["level"] = m["base"]
            # 바닥은 터에 살짝 묻어 틈이 없게, 다만 가장 낮은 판보다 깊이는 묻지 않는다
            m["base"], m["top"] = max(pad - 0.3, m["level"]), pad + h
        if pg.live:
            pg.part("mass", _mass_meshes(st.masses, origin), info="터 위에 다시 앉힘")
        pg.step("6.4")
        if pg.live:
            s = slice(None, None, 3)   # 실황용은 3m 격자로 가볍게
            Hs, Bs = H[s, s], B[s, s]
            pg.part("slope", heightfield_mesh(X[s, s], Y[s, s], Hs + 0.05,
                                              np.minimum(Bs, Hs) if a.terrain == "combo" else lv[0] - a.interval * 2,
                                              origin),
                    replaces=["sheet_*", "inlay"] if a.terrain == "slope" else [])
        pg.step("4.5")
        if pg.live and st.areas_lv:
            sol = st.skp_solids or drape_area_solids(st.areas_lv, st.hz, origin, a.road_thickness, max_edge=8.0)
            pg.part("path", [(m, PATH_STYLE[k][1]) for m, k in sol], info=f"길 층 {len(sol)}개")
        pg.step("6.5")
        if pg.live and st.trees:
            pg.part("tree", low_trees(st.trees, origin), info="곡면 위로 올림")
        if pg.live and st.lights:
            from .streetlights import placed_mesh
            pg.part("light", [(trimesh.util.concatenate([placed_mesh(x, y, z, h, ang, kind, origin)
                                                         for x, y, z, h, ang, kind, _ in st.lights]), (90, 90, 96))])


def _circle_cut(st):
    """원형 범위 (v2.2): 원 밖 나무·가로등·정류장·구조물·도로 디테일·길 면을 뺀다 (판 위 길 면은 이미 원 안)."""
    if st.circle is None:
        return
    from . import site as site_mod
    from .circle_range import keep_points, outside_of
    c, out = st.circle, outside_of(st.circle, st.bbox)
    st.trees = keep_points(st.trees, c)
    st.lights = keep_points(st.lights, c)
    st.transit_items[:] = [d for d in st.transit_items
                           if (d.get("poly") is not None and d["poly"].intersects(c))
                           or (d.get("poly") is None and d.get("c") is not None and c.contains(Point(*d["c"][:2])))]
    st.structs = [s for s in st.structs if c.intersects(box(*s[0].bounds[0][:2], *s[0].bounds[1][:2]))]
    st.path_areas = site_mod.cut_areas(st.path_areas, out)
    st.edge_lines = site_mod.cut_lines(st.edge_lines, out)
    if st.green_geom:
        st.green_geom = site_mod.cut_areas(st.green_geom, out, min_area=0.0)
    if st.road_info:
        site_mod.cut_road_info(st.road_info, out)


def _circle_meshes(st):
    """원형 범위 (v2.2): 경사면 지형은 원기둥으로 자르고, 도로 디테일·구조물 메시는 원 밖 면을 뺀다."""
    if st.circle is None:
        return
    from .circle_range import clip_mesh_faces, cylinder_cut
    c, o = st.circle, st.origin
    if st.terrain_mesh is not None:
        st.terrain_mesh = cylinder_cut(st.terrain_mesh, c, o)
    if st.skp_terrain is not None:
        st.skp_terrain = cylinder_cut(st.skp_terrain, c, o)
    st.roadbits = [(m2, lay, rgb, nm) for m, lay, rgb, nm in st.roadbits if (m2 := clip_mesh_faces(m, c)) is not None]
    st.structs = [(m2, k, n) for m, k, n in st.structs if (m2 := clip_mesh_faces(m, c)) is not None]


def _site_mark(st):
    """6단계: 대지 비우기 정리, 대지 경계 띠, 도로 디테일 3D."""
    from . import site as site_mod
    a, clip, log, origin, pg = st.a, st.clip, st.log, st.origin, st.pg
    pg.step("4.6")
    if pg.live:
        _live_details(pg, st.masses, a.floor_height, origin)
    if st.wipe is not None:
        if st.road_info:
            st.cleared["도로시설"] = site_mod.cut_road_info(st.road_info, st.wipe)
        log("   대지 위 비우기: " + " · ".join(f"{k} {v:,}" for k, v in st.cleared.items() if v) if any(st.cleared.values())
            else "   대지 위 비우기: 대지 안에 뺄 것이 없었습니다")
    st.site_mesh = None
    if st.site_poly is not None:
        try:
            from .site import outline_mesh
            if a.terrain in ("slope", "combo"):
                st.site_mesh = outline_mesh(st.site_poly.intersection(clip), lambda x, y: float(st.hz([x], [y])[0]) + a.road_thickness, origin)
            else:
                st.site_mesh = outline_mesh(st.site_poly.intersection(clip), lambda x, y: level_at(st.regions, Point(x, y)), origin)
            if pg.live and st.site_mesh is not None:
                pg.part("site", [(st.site_mesh, (37, 99, 235))], info="대지 경계")
        except Exception as e:
            log(f"   (대지 경계 띠를 만들지 못함: {e})")
    st.roadbits = _road_meshes(st.road_info, st.hz if a.terrain in ("slope", "combo") else None, st.regions,
                            a.road_thickness) if st.road_info else []
    if pg.live and st.roadbits:
        pg.part("roadx", [(_shifted(m, origin), rgb) for m, lay, rgb, nm in st.roadbits],
                info=f"도로 디테일 {len(st.roadbits)}묶음")
    st.combo = a.terrain == "combo"


def _zones(st):
    """영역별 지형(v2.2)에 쓸 면: 대지 · 물 · 도로 · 보도 · 녹지 (실제 좌표)."""
    pa = getattr(st, "path_areas", None) or {}
    u = lambda gs: unary_union([g for g in gs if g is not None and not g.is_empty]) if gs else None  # noqa: E731
    return {"대지": st.site_poly, "물": u(st.data.get("water_areas", [])),
            "도로": u([pa.get("road"), pa.get("bike")]),
            "보도": u([pa.get("sidewalk"), pa.get("island"), pa.get("stairs")]),
            "녹지": u(list((getattr(st, "green_geom", None) or {}).values()))}


def _zone_parts(st):
    """영역별 지형 메시 [(이름, 메시)] (곡면 지형일 때만). 실패해도 모델은 그대로."""
    if st.terrain_mesh is None or getattr(st, "hz", None) is None:
        return None
    try:
        from .terrain_zones import zone_meshes
        st.zone_parts = zone_meshes(_zones(st), st.clip, st.hz, st.origin)
        return st.zone_parts
    except Exception as e:
        st.log(f"   (영역별 지형을 만들지 못함: {type(e).__name__}: {e})")
        return None


def _save(st):
    """7단계: Rhino(3dm) · STL · 스케치업 · 레이저 판 DXF 저장."""
    a, clip, log, origin, pg = st.a, st.clip, st.log, st.origin, st.pg
    log("5/6 Rhino(3dm)·STL·레이저 도면 쓰는 중")
    pg.steps("8.1", "8.2")

    def save_tick(kind):
        """저장 단계 세부 진행: 화면 왼쪽 '지금 하는 일' 줄과 진행 막대를 움직인다."""
        def t(label, r=None, n=None, est=None):
            pg.note("sub", k=5, file=kind, x=label, r=r, n=n, est=est)
        return t
    st.site_summary = None

    def add_site(f):
        try:
            st.site_summary = _add_site(f, dict(a.site, poly=st.site_poly, regions=st.regions, keep_terrain=getattr(a, "site_keep_terrain", False)), st.interp, origin, log,
                                     step=1.0 if st.level_site is not None else None)
            st.site_summary.update(area=round(st.site_poly.area, 1), cleared=st.cleared if st.wipe is not None else None)
        except Exception as e:
            log(f"   (대지 경계·일조 사선을 넣지 못함: {e})")
    if a.want("rhino"):
        write_3dm(os.path.join(a.out, "model.3dm"), st.regions, st.masses, st.draped, origin, st.areas, st.trees, st.edges,
                  st.terrain_mesh, st.area_meshes, st.combo, a.floor_height, st.inlays, st.lights, st.structs,
                  "species" if a.detail >= 2 else "simple", st.roadbits,
                  before_write=add_site if st.site_poly is not None else None, tick=save_tick("Rhino"),
                  zones=_zone_parts(st))
    pg.step("8.3")
    write_stl(os.path.join(a.out, "model.stl"), st.regions, st.masses, origin, st.areas, st.trees, st.terrain_mesh,
              st.area_meshes, st.combo, a.floor_height, st.inlays, st.lights, st.structs,
              "species" if a.detail >= 2 else "simple", st.roadbits, site_mesh=st.site_mesh, tick=save_tick("STL"),
              scale=a.scale, field=st.print_field,
              site_poly=st.site_poly.intersection(clip) if st.site_poly is not None else None, circle=st.circle,
              stl=a.want("print"))
    if a.sketchup:
        pg.step("8.4")
        pg.note("sub", k=5, file="SketchUp", x="스케치업 파일")
        from .skp_export import write_sketchup
        site_lines = []
        if st.site_poly is not None:
            rings = [LineString(r.coords) for g in as_polys(st.site_poly) for r in [g.exterior, *g.interiors]]
            site_lines = [(f"대지 경계 · 판 EL {L:g}", np.array([(x, y, L + 0.02) for x, y, *_ in ls.coords]))
                          for ls, L in drape_lines(rings, st.regions)]
        write_sketchup(a.out, st.regions, st.masses, origin, st.skp_terrain, st.skp_solids, st.inlays, st.areas, st.trees,
                       st.combo, a.floor_height, st.lights, st.structs, "species" if a.detail >= 2 else "simple", st.roadbits,
                       site_lines=site_lines, log=log)
    pg.steps("9.1", "9.2", "9.3")
    pg.note("explode")
    pg.step("9.4")
    pg.note("sub", k=5, file="레이저", x="레이저 판 DXF")
    st.sheets = [] if not a.want("laser") else write_laser(os.path.join(a.out, "laser"), st.regions, st.masses, st.draped, origin, a.scale,
                         st.areas_lv, st.trees_lv, st.edges_lv, a.tree_hole, st.greens, st.lights_lv,
                         site=st.site_poly.intersection(clip) if st.site_poly is not None else None)
    for i, f in enumerate(st.sheets):
        pg.step("9.5", r=i, n=len(st.sheets), x=os.path.basename(str(f)))
    # 2D 지형도(CAD 평면 DXF) + 사이트 다이어그램(SVG): 등고선은 계단판 경계 그대로 (v2.2)
    pg.note("sub", k=5, file="2D", x="2D 지형도·다이어그램")
    from .out_plan import write_plan_outputs
    st.plan_info = write_plan_outputs(a.out, st, log) if a.want("plan") else None
    try:                                   # 항공사진 지형 OBJ·GLB (v2.2): 항공사진이 있을 때만
        from .out_plan import _aerial
        from .photo_terrain import write_photo_terrain
        ae = _aerial(st)
        if ae is not None and a.want("photo"):
            hz = st.hz if getattr(st, "hz", None) is not None else (lambda x, y: st.interp(np.column_stack([x, y])))
            write_photo_terrain(a.out, st.clip, hz, st.origin, ae, log)
    except Exception as e:
        log(f"   (항공사진 지형을 만들지 못함: {type(e).__name__}: {e})")
    try:                                   # 지형 단면 재료: 화면에서 선을 그으면 바로 단면을 만든다 (v2.2)
        from .section import save_section_data
        save_section_data(a.out, st)
    except Exception as e:
        log(f"   (단면 재료를 저장하지 못함: {type(e).__name__}: {e})")


def _summary(st):
    """8단계: 요약(summary.json), 대지 분석, 평면 확인 그림."""
    from .facade import clear_cache
    a, bbox, data, log, pg = st.a, st.bbox, st.data, st.log, st.pg
    st.summary = dict(osm=bool(getattr(st, "osm", None)), levels=list(st.regions), sheets=len(st.sheets) or len(st.regions), buildings=len(st.masses),
                   road_segments=len(st.draped) + len(st.edges), path_areas=len(st.areas) + len(st.area_meshes),
                   road_inlays=len(st.inlays), road_thickness_m=a.road_thickness,
                   building_uses=dict(sorted({u: sum(1 for m in st.masses if m.get("use") == u)
                                              for u in {m.get("use") for m in st.masses}}.items(),
                                             key=lambda kv: -kv[1])),
                   building_register=st.register, site=st.site_summary,
                   pads=st.pads.stats if st.pads is not None else None,
                   buildings_by_decade=dict(sorted({f"{m['year'] // 10 * 10}년대": sum(
                       1 for n in st.masses if n.get("year", 0) // 10 == m["year"] // 10)
                       for m in st.masses if m.get("year")}.items())),
                   structures={k: sum(1 for _, k2, _n in st.structs if k2 == k) for k in dict.fromkeys(s_[1] for s_ in st.structs)},
                   lights=len(st.lights), lights_by_source={s_: sum(1 for t in st.lights if t[6] == s_)
                                                         for s_ in dict.fromkeys(t[6] for t in st.lights)},
                   trees=len(st.trees), trees_by_kind={k: sum(1 for t in st.trees if t[4] == k) for k in ("street", "forest", "garden")},
                   terrain=a.terrain,
                   building_pad_cutfill_max_m=round(max([m.get("cutfill", 0) for m in st.masses] or [0]), 2),
                   building_pad_cutfill_median_m=round(float(np.median([m.get("cutfill", 0) for m in st.masses] or [0])), 2),
                   contours=len(data["contours"]),
                   model_size_mm=[(bbox[2] - bbox[0]) * 1000 / a.scale, (bbox[3] - bbox[1]) * 1000 / a.scale])
    if st.site_poly is not None and getattr(a, "analysis", True):
        # 대지 분석: 원래 지형 경사, 레벨 계획이 있으면 절토·성토 (그림 2장 + 숫자 표)
        try:
            from .analysis import site_analysis
            st.summary["analysis"] = site_analysis(a.out, bbox, st.site_poly, st.orig_interp,
                                                st.interp if st.level_site is not None else None,
                                                slope_limit=getattr(a, "slope_limit", 17.5), log=log)
        except Exception as e:  # 분석 실패는 모델에 영향 없음
            log(f"   (대지 분석을 만들지 못했습니다: {type(e).__name__}: {e})")
    json.dump(st.summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    clear_cache()
    try:
        _preview_plan(os.path.join(a.out, "preview_plan.png"), bbox, st.regions, st.masses,
                      st.areas_lv, st.trees_lv, st.greens, st.lights_lv, site=st.site_poly)
    except Exception as e:  # 미리보기 실패는 모델에 영향 없음
        log(f"   (평면 미리보기 생략: {e})")
    if pg.live and os.path.exists(os.path.join(a.out, "preview_plan.png")):
        import shutil
        name = pg._name("fin", "png")
        shutil.copy(os.path.join(a.out, "preview_plan.png"), os.path.join(pg.live, name))
        pg.note("plan", key="fin", url="/live/" + name)
    pg.note("done")
    log("6/6 완료")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="수치지도 DXF 파일 또는 수치지도 2.0 SHP 폴더")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, help="xmin ymin xmax ymax (m)")
    ap.add_argument("--scale", type=float, default=1000, help="모형 축척 분모 (1:scale)")
    ap.add_argument("--interval", type=float, default=5, help="계단 간격(m), 원본 등고선 간격의 배수")
    ap.add_argument("--min-area", type=float, default=None, help="버릴 작은 조각 면적(m²)")
    ap.add_argument("--floor-height", type=float, default=FLOOR_HEIGHT)
    ap.add_argument("--sigma", type=float, default=4.0, help="지형 고르기 정도(m). 0이면 원자료 그대로")
    ap.add_argument("--smooth", type=float, default=1.5, help="등고선 곡선 다듬기 반경(m)")
    ap.add_argument("--terrain", choices=["combo", "step", "slope"], default="combo")
    ap.add_argument("--no-trees", action="store_true", help="가로수 생략")
    ap.add_argument("--ndvi", help="식생지수 파일. 주면 녹지를 나무로 채움")
    ap.add_argument("--tree-spacing", type=float, default=8.0)
    ap.add_argument("--tree-hole", type=float, default=1.0)
    ap.add_argument("--hd-dir", help="정밀도로지도 zip 들을 모아 둔 폴더(노면표시를 이것으로 그림)")
    ap.add_argument("--out", default="out")
    a = vars(ap.parse_args())
    src, bbox, out = a.pop("src"), tuple(a.pop("bbox")), a.pop("out")
    print(json.dumps(build(src, bbox, out, **a), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

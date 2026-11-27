"""모델 만들기 5~6단계: 길 면 얹기, 경사 지형, 원형 범위 자르기, 대지 표시, 영역별 지형."""
import numpy as np
import trimesh
from shapely.geometry import Point, box
from shapely.ops import unary_union

from .progress import extrude, low_trees

from .geom_util import _shifted
from .terrain import (building_pads, _dist_to_lines, drape_line_3d, heightfield_mesh,
                      level_at, simplify_solid, slope_heightfield, surface_sampler)
from .drape import (drape_area_solids, _road_meshes, split_inlays)
from .live_view import _live_details, _mass_meshes


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

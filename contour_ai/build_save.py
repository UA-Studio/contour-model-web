"""모델 만들기 7~8단계: 파일 저장(Rhino·STL·스케치업·레이저·2D·사진 지형·단면)과 요약."""
import json
import os

import numpy as np

from .out_rhino import _add_site_3dm, write_3dm
from .out_stl import write_stl
from .out_laser import write_laser
from .preview import _preview_plan
from .build_surface import _zone_parts
from .output_kinds import OUTPUT_NAMES


def _save(st):
    """7단계: Rhino(3dm) · STL · 스케치업 · 레이저 판 DXF 저장."""
    a, clip, log, origin, pg = st.a, st.clip, st.log, st.origin, st.pg
    log("5/6 " + "·".join(v for k, v in OUTPUT_NAMES.items() if a.want(k)) + " 파일 쓰는 중")
    pg.steps("8.1", "8.2")

    def save_tick(kind):
        """저장 단계 세부 진행: 화면 왼쪽 '지금 하는 일' 줄과 진행 막대를 움직인다."""
        def t(label, r=None, n=None, est=None):
            pg.note("sub", k=5, file=kind, x=label, r=r, n=n, est=est)
        return t
    st.site_summary, site_items = None, []
    if st.site_poly is not None:
        # 대지 표시 도형은 형식과 상관없이 한 번 만들어 Rhino·스케치업에 같이 넣는다 (site_overlay.py)
        try:
            from .site_overlay import site_overlay
            site_items, st.site_summary = site_overlay(
                dict(a.site, poly=st.site_poly, regions=st.regions, keep_terrain=getattr(a, "site_keep_terrain", False)),
                st.interp, log, step=1.0 if st.level_site is not None else None)
            st.site_summary.update(area=round(st.site_poly.area, 1), cleared=st.cleared if st.wipe is not None else None)
        except Exception as e:
            log(f"   (대지 경계·일조 사선을 넣지 못함: {e})")
    if a.want("rhino"):
        write_3dm(os.path.join(a.out, "model.3dm"), st.regions, st.masses, st.draped, origin, st.areas, st.trees, st.edges,
                  st.terrain_mesh, st.area_meshes, st.combo, a.floor_height, st.inlays, st.lights, st.structs,
                  "species" if a.detail >= 2 else "simple", st.roadbits,
                  before_write=(lambda f: _add_site_3dm(f, site_items, origin)) if site_items else None, tick=save_tick("Rhino"),
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
        write_sketchup(a.out, st.regions, st.masses, origin, st.skp_terrain, st.skp_solids, st.inlays, st.areas, st.trees,
                       st.combo, a.floor_height, st.lights, st.structs, "species" if a.detail >= 2 else "simple", st.roadbits,
                       site_items=site_items, log=log)
    pg.steps("9.1", "9.2", "9.3")
    pg.note("explode")
    pg.step("9.4")
    if a.want("laser"):
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
    st.summary = dict(outputs=sorted(a.outs), osm=bool(getattr(st, "osm", None)), levels=list(st.regions), sheets=len(st.sheets) or len(st.regions), buildings=len(st.masses),
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

"""수치지형도(DXF) -> 건축 콘타 모델 (Rhino 3DM, STL, 레이저 커팅용 층별 DXF).

사용 예:
    python contour_model.py map.dxf --bbox 200900 553600 201400 554100 \
        --scale 1000 --sheet 5 --out out/
"""
# 입구: 코드는 기능별 파일에 있다. 예전처럼 contour_model 에서 불러도 동작하도록 이름만 다시 내보낸다.
# 여기에 코드를 쓰지 말 것 (CLAUDE.md).
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
from .out_rhino import (_add_site_3dm, _RH_COUNT, _rh_curve, _rh_extrusion, _rh_mesh, write_3dm,
                        _write_estimate, WRITE_SEC_PER_OBJ, WRITE_SEC_PER_VERT)  # noqa: F401
from .out_stl import (print_solid, SITE_GROOVE_D, SITE_GROOVE_W, stl_check, STL_SEC_PER_FACE,
                      STL_SEC_PER_FACE_JOIN, _unpinch, write_stl)  # noqa: F401
from .out_laser import write_laser  # noqa: F401
from .greens import _aerial_accuracy, _green_areas, _green_trees, _real_street_trees  # noqa: F401
from .live_view import _live_details, _live_input, _live_sheet, _live_tin, _mass_meshes  # noqa: F401
from .preview import _plan_greens, _plan_paths, _plan_sheets, _plan_trees, _preview_plan  # noqa: F401
from .output_kinds import OUTPUT_NAMES, OUTPUTS_DEFAULT, _outputs  # noqa: F401
from .build_defaults import DEFAULTS, FINE_TRIM_RADIUS  # noqa: F401
from .build_prepare import _heights, _pads, _prepare, _sheets, _site_levels  # noqa: F401
from .build_buildings import _building_details, _buildings  # noqa: F401
from .build_streets import _lights, _paths, _road_detail, _streets_live, _trees  # noqa: F401
from .build_surface import (_circle_cut, _circle_meshes, _inlays, _site_mark, _slope, _zone_parts,  # noqa: F401
                            _zones)
from .build_save import _save, _summary  # noqa: F401
from .build_flow import build, main  # noqa: F401
from .progress import extrude, fill, low_trees  # noqa: F401


if __name__ == "__main__":
    main()

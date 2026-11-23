"""수치지형도(DXF) -> 건축 콘타 모델 (Rhino 3DM, STL, 레이저 커팅용 층별 DXF).

사용 예:
    python contour_model.py map.dxf --bbox 200900 553600 201400 554100         --scale 1000 --sheet 5 --out out/

이 파일은 입구입니다. 실제 코드는 model/ 폴더에 기능별로 나뉘어 있습니다.
"""
from .model.layers import (  # noqa: F401
    BUILDING_PREFIX,
    CONTOUR_LAYERS,
    FALLBACK_FLOORS,
    FLOOR_HEIGHT,
    INLAY_KINDS,
    ROAD_PREFIXES,
    ROAD_THICKNESS,
    ROLE_KO,
    SOURCE_KO,
    SPOT_LAYERS,
    USE_COLORS,
    WALL_PREFIX,
    WATER_PREFIX,
)
from .model.inputs import (  # noqa: F401
    load_dxf,
    load_shp,
)
from .model.terrain import (  # noqa: F401
    _dist_to_lines,
    as_polys,
    breakline_points,
    build_interpolator,
    building_pads,
    chaikin,
    level_at,
    slope_heightfield,
    smooth_poly,
    terrace_regions,
)
from .model.surface import (  # noqa: F401
    _drape_solid_tiles,
    _grid_triangulate,
    cut_regions,
    drape_area_meshes,
    drape_area_solids,
    drape_line_3d,
    drape_lines,
    heightfield_mesh,
    simplify_solid,
    split_inlays,
    surface_sampler,
)
from .model.buildings import (  # noqa: F401
    building_masses,
)
from .model.export_3dm import (  # noqa: F401
    STL_SEC_PER_FACE,
    STL_SEC_PER_FACE_JOIN,
    WRITE_SEC_PER_OBJ,
    WRITE_SEC_PER_VERT,
    _RH_COUNT,
    _add_site,
    _rh_curve,
    _rh_extrusion,
    _rh_mesh,
    _write_estimate,
    write_3dm,
)
from .model.mesh_tools import (  # noqa: F401
    _mf_from_trimesh,
    _shift,
    _shifted,
    _split_sharp,
    _with_ground_normals,
    crisp_mesh,
)
from .model.export_stl import (  # noqa: F401
    SITE_GROOVE_D,
    SITE_GROOVE_W,
    _mf_extrude,
    _road_meshes,
    _unpinch,
    print_solid,
    stl_check,
    write_stl,
)
from .model.export_laser import (  # noqa: F401
    write_laser,
)
from .model.build_steps import (  # noqa: F401
    DEFAULTS,
    _aerial_accuracy,
    _crs_wkt,
    _green_areas,
    _green_trees,
    _pad_parcels,
    _real_street_trees,
)
from .model.build import (  # noqa: F401
    build,
)
from .model.live_preview import (  # noqa: F401
    _live_details,
    _live_input,
    _live_sheet,
    _live_tin,
    _mass_meshes,
    _plan_greens,
    _plan_paths,
    _plan_sheets,
    _plan_trees,
    _preview_plan,
)
import argparse
import json


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

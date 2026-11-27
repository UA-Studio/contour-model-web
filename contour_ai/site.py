"""사업 대지(사이트) 경계: 파일·직접 그린 점에서 읽고, 모델에서 대지 위를 비운다.

경계를 넣는 길은 세 가지다.
- 주소 → 브이월드 연속지적도 필지 (landlaw.lookup, 키 필요)
- 경계 파일: CAD(DXF) 닫힌 폴리선, SHP(연속지적도 등, zip 가능), GeoJSON
- 화면 지도에서 직접 찍은 꼭짓점

파일 좌표계는 .prj 가 있으면 그것을 쓰고, 없으면 국내에서 쓰는 좌표계를 차례로 넣어 보아
수치지도 도엽 안(또는 가장 가까이)에 떨어지는 것을 고른다.
"""
# 입구: 코드는 기능별 파일(site_*.py)에 있다. 예전처럼 site 에서 불러도 동작하도록 이름만 다시 내보낸다.
# 여기에 코드를 쓰지 말 것 (CLAUDE.md).
from .site_read import (  # noqa: F401
    CANDIDATE_EPSG, MIN_AREA, MAX_AREA, SiteError, _polys, clean, from_ring, _DXF_CACHE, _open_dxf,
    _read_dxf, _read_shp, _read_geojson, _read_any, _to_map, NAME_RE, MAP_LAYER_RE, NOT_LEVEL_RE,
    LEVEL_RE)
from .site_align import (  # noqa: F401
    SPOT_LAYER_RE, _AFFINE, _drawing_spots, _map_spots, _similarity, drawing_affine, _looks_real)
from .site_load import (load_boundary, _LAST_CRS, _dxf_levels, load_site_file)  # noqa: F401
from .site_levels import (level_interpolator, blended)  # noqa: F401
from .site_cut import (  # noqa: F401
    inside_ratio, split_masses, cut_areas, cut_lines, outside, cut_road_info, outline_mesh)

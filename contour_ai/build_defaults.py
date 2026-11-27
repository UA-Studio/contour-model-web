"""모델 만들기 기본 설정값 (공용: build() 의 모든 단계가 같이 쓴다)."""
from .drape import ROAD_THICKNESS
from .buildings import FLOOR_HEIGHT


DEFAULTS = dict(shape="square", outputs=None, site=None, site_clear=False, site_keep_terrain=False, dem=None, terrain_fit="smooth", breaklines=False, pads=True, parcel_file=None, scale=1000, interval=5, min_area=None, floor_height=FLOOR_HEIGHT, sigma=4.0, smooth=1.5,
                terrain="combo", no_trees=False, ndvi=None, chm=None, aerial=None, crs_wkt=None, vworld_key=None,
                vworld_domain="", data_key=None, cache_dir=None, tree_spacing=8.0, tree_hole=1.0,
                road_thickness=ROAD_THICKNESS, sketchup=True, green=True, lights=True, light_files=(),
                max_forest_trees=30000, facade=True, facade_radius=None, detail=3,
                structures=True, road_detail=True, road_files=(), seoul_key=None, safemap_key=None,
                seoul_service=None, safemap_layer=None, safemap_light_layer=None, seoul_code=None, hd_dir=None, osm=True)


FINE_TRIM_RADIUS = 250.0     # 창틀·창살·창턱을 만들 범위(가운데에서, m) (v2.4)

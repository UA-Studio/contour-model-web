"""모델 만들기 전체 흐름 build() 와 명령줄 실행 main(). 단계는 build_*.py 에 있다."""
import argparse
import json
import os
from types import SimpleNamespace

from .terrain import drape_lines
from .buildings import FLOOR_HEIGHT
from .build_prepare import _heights, _pads, _prepare, _sheets, _site_levels
from .build_buildings import _building_details, _buildings
from .build_streets import _lights, _paths, _road_detail, _streets_live, _trees
from .build_surface import _circle_cut, _circle_meshes, _inlays, _site_mark, _slope
from .build_save import _save, _summary


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

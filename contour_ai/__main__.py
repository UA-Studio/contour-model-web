"""python -m contour_ai              → 화면(브라우저) 열기
python -m contour_ai 수치지도.zip --name 삼선푸르지오 --size 500   → 명령줄 실행
"""
import argparse
import json
import sys

from .pipeline import run


def main():
    if len(sys.argv) == 1 or sys.argv[1] in ("gui", "--gui"):
        from .app import main as gui
        return gui()
    if sys.argv[1] == "serve":              # 공개 웹 서비스 (Docker 안에서 CONTOUR_SERVER=1)
        from . import app
        return app.serve()
    ap = argparse.ArgumentParser(prog="contour_ai", description="수치지도 2.0 SHP zip → 건축 콘타 모델")
    ap.add_argument("source", nargs="+", help="국토정보플랫폼 수치지도 2.0(SHP) zip 또는 압축 푼 폴더. 이웃 도엽은 여러 개를 이어서 적으면 한 장으로 잇는다")
    ap.add_argument("--out", default="결과", help="결과 폴더")
    g = ap.add_argument_group("모델 범위 (하나만 고르면 됨, 없으면 도엽 가운데)")
    g.add_argument("--name", help="중심으로 삼을 건물·지명 이름 (예: 삼선푸르지오, 한성대학교)")
    g.add_argument("--latlon", nargs=2, type=float, metavar=("위도", "경도"))
    g.add_argument("--xy", nargs=2, type=float, metavar=("X", "Y"), help="수치지도 좌표(m)")
    g.add_argument("--size", nargs="+", type=float, default=[500], help="범위 크기(m). 한 값=정사각형, 두 값=가로 세로")
    m = ap.add_argument_group("모형")
    m.add_argument("--scale", type=float, default=1000, help="축척 분모 (1:1000)")
    m.add_argument("--interval", type=float, default=5, help="판 한 장 높이(m). 1:1000에서 5m = 5mm 판")
    m.add_argument("--terrain", choices=["combo", "step", "slope"], default="combo",
                   help="combo=계단판+경사 매스(기본), step=계단판만, slope=경사면만")
    m.add_argument("--floor-height", type=float, default=3.3)
    m.add_argument("--no-green", action="store_true", help="위성영상 녹지 나무 생략(인터넷 없이)")
    m.add_argument("--no-trees", action="store_true", help="가로수 생략")
    m.add_argument("--vworld-key", help="브이월드 인증키. 주면 항공사진에서 실제 가로수를 찾음")
    m.add_argument("--vworld-domain", default="", help="인증키를 받을 때 등록한 서비스 주소(필요할 때만)")
    m.add_argument("--data-key", help="공공데이터포털 인증키(일반 인증키). 브이월드 키와 함께 주면 건축물대장 높이·층수·준공연도를 씀")
    m.add_argument("--site", help="검토할 대지 주소. 브이월드 키와 함께 주면 토지이용계획으로 법규 1차 검토(법규검토.md)와 대지 경계·정북일조 사선을 넣음")
    m.add_argument("--site-file", help="대지 경계 파일(DXF 닫힌 폴리선, SHP·zip, GeoJSON). 모델에 경계를 넣고 대지를 가운데로 잡음")
    m.add_argument("--dem", nargs="*", default=[], help="수치표고모델(DEM) 파일·zip·폴더. 5m 이하 격자면 등고선 대신 이 높이로 지형을 만듦")
    m.add_argument("--exact-terrain", action="store_true", help="등고선 그대로: 지형 다듬기를 줄여 절토면·옹벽 같은 꺾임을 살림")
    m.add_argument("--no-pads", action="store_true", help="계단식 대지 끄기: 비탈의 건물 터·논을 평평한 단으로 만들지 않고 등고선 보간 그대로")
    m.add_argument("--parcel-file", help="연속지적도 SHP(zip·폴더). 계단식 대지가 필지 경계대로 단을 나눔 (없으면 브이월드 키로 받음)")
    m.add_argument("--site-clear", action="store_true", help="대지 위의 건물·길·나무·시설물을 빼고 빈 터로")
    m.add_argument("--site-keep-terrain", action="store_true", help="대지 안 원래 지형 유지: 도면 레벨을 쓰지 않고 경계선만 지형에 눌러 표시")
    m.add_argument("--aerial", help="좌표가 들어 있는 항공사진 파일(GeoTIFF 등). 브이월드 대신 이 영상으로 가로수를 찾음")
    m.add_argument("--year", type=int, help="위성영상 연도 (기본: 최근 여름)")
    m.add_argument("--tree-hole", type=float, default=1.0, help="레이저 나무 구멍 지름(mm)")
    m.add_argument("--road-thickness", type=float, default=1.0, help="곡면 대지 위 길 한 층 두께(m). 1:1000에서 1m=1mm")
    m.add_argument("--no-lights", action="store_true", help="가로등·보안등 생략")
    m.add_argument("--lights-file", nargs="*", default=[], help="가로등·보안등 위치 파일(위도·경도 칸이 있는 CSV/XLSX)")
    m.add_argument("--no-road-detail", action="store_true", help="차선·횡단보도·신호등·전봇대·주차 구획 생략")
    m.add_argument("--road-files", nargs="*", default=[],
                   help="도로시설 위치 파일(위도·경도 CSV/XLSX): 전국횡단보도표준데이터, 전국신호등표준데이터, 전주 위치 등")
    m.add_argument("--seoul-key", help="서울 열린데이터광장 인증키 (도보 네트워크의 횡단보도)")
    m.add_argument("--safemap-key", help="생활안전지도 오픈API 인증키 (횡단보도 지도)")
    m.add_argument("--seoul-service", help="도보 네트워크 Open API 서비스명 (비우면 후보를 차례로)")
    m.add_argument("--safemap-layer", help="생활안전지도 횡단보도 layername (비우면 후보를 차례로)")
    m.add_argument("--seoul-code", help="도보 네트워크에서 횡단보도를 뜻하는 값(연결 시험의 값 분포 참고)")
    m.add_argument("--safemap-light-layer", help="생활안전지도 보안등 layername (비우면 후보를 차례로)")
    m.add_argument("--detail", type=int, choices=[1, 2, 3], default=3,
                   help="건물 디테일: 1=매스만, 2=지붕·옥탑·재료 레이어, 3=창문까지(기본)")
    m.add_argument("--no-structures", action="store_true", help="고가도로·육교·교량 생략")
    m.add_argument("--no-facade", action="store_true", help="창문·재료 레이어 생략(매스만)")
    m.add_argument("--facade-radius", type=float, help="이 반경(m) 안 건물만 창문을 냄 (기본: 전부)")
    m.add_argument("--no-sketchup", action="store_true", help="스케치업용 파일 생략")
    a = ap.parse_args()
    size = a.size[0] if len(a.size) == 1 else tuple(a.size[:2])
    s = run(a.source if len(a.source) > 1 else a.source[0], a.out, name=a.name, latlon=a.latlon, xy=a.xy, size=size, green=not a.no_green,
            year=a.year, scale=a.scale, interval=a.interval, terrain=a.terrain, floor_height=a.floor_height,
            no_trees=a.no_trees, tree_hole=a.tree_hole, road_thickness=a.road_thickness,
            sketchup=not a.no_sketchup, vworld_key=a.vworld_key, vworld_domain=a.vworld_domain, data_key=a.data_key, site_address=a.site, site_file=a.site_file, site_clear=a.site_clear, site_keep_terrain=a.site_keep_terrain, dem_files=a.dem or None, terrain_fit="exact" if a.exact_terrain else "smooth", pads=not a.no_pads, parcel_file=a.parcel_file,
            aerial_file=a.aerial, lights=not a.no_lights, facade=not a.no_facade, detail=a.detail, structures=not a.no_structures, facade_radius=a.facade_radius, light_files=tuple(a.lights_file),
            road_detail=not a.no_road_detail, road_files=tuple(a.road_files),
            seoul_key=a.seoul_key, safemap_key=a.safemap_key, seoul_service=a.seoul_service, safemap_layer=a.safemap_layer,
            safemap_light_layer=a.safemap_light_layer, seoul_code=a.seoul_code)
    print(json.dumps(s, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

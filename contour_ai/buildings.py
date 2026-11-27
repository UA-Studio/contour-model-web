"""건물 매스: 층수 → 높이, 판 위에 앉히기, 용도별 색·부위 이름.

이 파일만 고치면 '건물 덩어리'만 바뀝니다 (창문·지붕 디테일은 building_detail.py).
"""
from shapely.geometry import Point

from .geom_util import as_polys
from .terrain import level_at


# 층수 데이터가 없을 때 쓰는 임시 층수 (레이어별 추정)
FALLBACK_FLOORS = {
    "B0014111": 2, "B0014112": 4, "B0014113": 12, "B0014114": 2,
    "B0014115": 2, "B0014116": 3, "B0014117": 2, "B0014118": 1,
}
FALLBACK_FLOORS.update({"일반주택": 2, "연립주택": 4, "아파트": 12, "주택외건물": 3,
                        "무벽건물": 1, "가건물": 1, "공사중건물": 2, "온실": 1})
FLOOR_HEIGHT = 3.3
SOURCE_KO = {"data": "수치지도", "estimate": "추정", "register": "건축물대장(높이)", "register_floors": "건축물대장(층수)"}

# 건물 용도별 Rhino 레이어 색 (화면 구분용, 렌더 재질과 무관)
USE_COLORS = {"주택": (246, 241, 226), "근린생활시설": (242, 220, 196), "교육연구시설": (205, 222, 240),
              "종교시설": (228, 210, 236), "업무시설": (208, 216, 232), "의료시설": (238, 206, 206),
              "숙박시설": (240, 228, 204), "노유자(노인및어린이)시설": (216, 236, 206),
              "문화및집회시설": (236, 216, 236), "자동차관련시설": (214, 214, 204), "기타시설": (228, 228, 226)}
ROLE_KO = {"body": "본체", "roof": "지붕", "parapet": "파라펫", "penthouse": "옥탑", "canopy": "캐노피",
           "column": "캐노피", "frame": "공사중", "floorline": "층선"}


# ---------------------------------------------------------------- 건물
def building_masses(buildings, regions, clip, floor_height=FLOOR_HEIGHT):
    """건물 매스. 바닥은 발자국 아래 가장 낮은 판 윗면에서 평평하게 끝난다(그보다 깊이 묻지 않음).

    경사지에 걸친 건물은 위쪽 판들을 지나가므로, 레이저 판에서 그 자리를 잘라 낸다
    (through = 건물이 지나가는 판 높이들). 바닥이 평평해야 매스 하나로 잘라 끼울 수 있다.
    """
    lv = list(regions)
    out = []
    for item in buildings:
        pg, layer, n_floors, name = item[:4]
        attrs = {k: v for k, v in (item[4] if len(item) > 4 else {}).items() if str(v).strip()}
        pg = pg.intersection(clip)
        for p in as_polys(pg):
            if p.area < 4:
                continue
            samples = [Point(c) for c in p.exterior.coords[:-1]] + [p.representative_point()]
            lvs = [level_at(regions, s) for s in samples]
            base = min(lvs)
            ground = level_at(regions, p.representative_point())
            source = "data" if n_floors else "estimate"
            floors = n_floors or FALLBACK_FLOORS.get(layer, 2)
            # 무벽건물(캐노피 등)은 낮게
            h = 3.0 if layer == "무벽건물" else floors * floor_height
            out.append(dict(poly=p, layer=layer, base=base, top=ground + h,
                            through=[L for L in lv if base < L <= max(lvs)], height=h,
                            floors=floors, source=source, name=name, attrs=attrs,
                            use=attrs.get("용도") or "용도미상"))
    return out

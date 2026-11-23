"""건물 덩어리(층수·높이) 만들기."""
from shapely.geometry import Point
from .layers import FALLBACK_FLOORS, FLOOR_HEIGHT
from .terrain import as_polys, level_at



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

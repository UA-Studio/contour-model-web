"""레이저 재단용 층별 DXF 판."""
import os
from collections import defaultdict
import ezdxf
from .layers import INLAY_KINDS
from .terrain import as_polys



# ---------------------------------------------------------------- 출력: 레이저 DXF
def write_laser(out_dir, regions, masses, draped, origin, scale, areas=(), trees=(),
                edges=(), tree_hole=1.0, greens=(), lights=(), site=None):
    """판마다 DXF 한 장. 단위 mm.

    CUT=절단, CUT_ROAD=도로 자리 절단(판에서 파내고, 같은 선으로 도로 조각을 다른 재료에서 잘라 끼움),
    ENGRAVE=선 각인(윗판 위치, 도로경계, 건물 자리),
    RASTER=면 각인(보도·교통섬, HATCH 채움), HATCH_STAIRS=계단 빗금,
    TREE=가로수 꽂을 구멍(지름 tree_hole mm), TREE_GREEN=녹지 나무 구멍, GREEN=녹지 경계 각인,
    LIGHT=가로등 자리 작은 구멍(지름 0.5mm, 핀을 꽂거나 각인).
    """
    from ..paths_trees import PATH_STYLE
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        if old.startswith("sheet_") and old.endswith(".dxf"):
            os.remove(os.path.join(out_dir, old))
    k = 1000.0 / scale  # m -> 모형 mm
    lv = list(regions)
    by_level = defaultdict(list)
    through = defaultdict(list)   # 건물이 뚫고 지나가는 판: 그 판에서 발자국 자리를 잘라 낸다
    for m in masses:
        by_level[m.get("level", m["base"])].append(m["poly"])
        b0 = m.get("level", m["base"])   # 레이저 판은 계단 기준 바닥으로
        for L in m.get("through", ()):
            if b0 < L:
                through[L].append(m["poly"])
    roads = defaultdict(list)
    for ls, L in draped:
        roads[L].append(ls)

    area_by = defaultdict(list)
    for p, L, kind in areas:
        area_by[L].append((p, kind))
    green_by = defaultdict(list)
    for p, L, kind in greens:
        green_by[L].append((p, kind))
    edge_by = defaultdict(list)
    for ls, L in edges:
        edge_by[L].append(ls)
    tree_by = defaultdict(list)
    for x, y, z, r, kind in trees:
        tree_by[z].append((x, y, kind))
    light_by = defaultdict(list)
    for x, y, z, *_ in lights:
        light_by[z].append((x, y))

    def tx(coords):
        return [((x - origin[0]) * k, (y - origin[1]) * k) for x, y in coords]
    files = []
    for i, L in enumerate(lv):
        doc = ezdxf.new("R2010"); doc.units = ezdxf.units.MM
        doc.layers.add("CUT", color=1); doc.layers.add("ENGRAVE", color=5)
        doc.layers.add("LABEL", color=3); doc.layers.add("RASTER", color=8)
        doc.layers.add("HATCH_STAIRS", color=4); doc.layers.add("TREE", color=6)
        doc.layers.add("TREE_GREEN", color=3); doc.layers.add("GREEN", color=3); doc.layers.add("GRASS", color=51)
        doc.layers.add("CUT_ROAD", color=30); doc.layers.add("LIGHT", color=2)
        if site is not None:
            doc.layers.add("SITE", color=140)
        msp = doc.modelspace()
        for p in as_polys(regions[L]):
            for ring in [p.exterior, *p.interiors]:
                msp.add_lwpolyline(tx(ring.coords), close=True, dxfattribs={"layer": "CUT"})
        if i + 1 < len(lv):
            for p in as_polys(regions[lv[i + 1]]):
                msp.add_lwpolyline(tx(p.exterior.coords), close=True, dxfattribs={"layer": "ENGRAVE"})
        for ls in roads[L]:
            msp.add_lwpolyline(tx(ls.coords), dxfattribs={"layer": "ENGRAVE"})
        for p in by_level[L]:
            msp.add_lwpolyline(tx(p.exterior.coords), close=True, dxfattribs={"layer": "ENGRAVE"})
        for p in through[L]:
            for q in as_polys(p.intersection(regions[L])):
                if q.area > 0.5:
                    msp.add_lwpolyline(tx(q.exterior.coords), close=True, dxfattribs={"layer": "CUT"})
        for ls in edge_by[L]:
            msp.add_lwpolyline(tx(ls.coords), dxfattribs={"layer": "ENGRAVE"})
        for p, kind in area_by[L]:
            mode = PATH_STYLE[kind][3]
            if kind in INLAY_KINDS:
                # 도로는 이 판에서 잘라 내고, 같은 선으로 다른 재료(회색 판)에서 도로 조각을 잘라 끼운다
                for ring in [p.exterior, *p.interiors]:
                    msp.add_lwpolyline(tx(ring.coords), close=True, dxfattribs={"layer": "CUT_ROAD"})
                continue
            if mode == "outline":
                for ring in [p.exterior, *p.interiors]:
                    msp.add_lwpolyline(tx(ring.coords), close=True, dxfattribs={"layer": "ENGRAVE"})
                continue
            lay = "RASTER" if mode == "raster" else "HATCH_STAIRS"
            h = msp.add_hatch(dxfattribs={"layer": lay})
            if mode == "hatch":
                h.set_pattern_fill("ANSI31", scale=0.4)
            h.paths.add_polyline_path(tx(p.exterior.coords), is_closed=True, flags=1)
            for ring in p.interiors:
                h.paths.add_polyline_path(tx(ring.coords), is_closed=True)
        for x, y, kind in tree_by[L]:
            msp.add_circle(tx([(x, y)])[0], tree_hole / 2,
                           dxfattribs={"layer": "TREE" if kind == "street" else "TREE_GREEN"})
        for x, y in light_by[L]:
            msp.add_circle(tx([(x, y)])[0], 0.25, dxfattribs={"layer": "LIGHT"})
        for p, kind in green_by[L]:
            for ring in [p.exterior, *p.interiors]:
                msp.add_lwpolyline(tx(ring.coords), close=True,
                                   dxfattribs={"layer": "GRASS" if kind == "grass" else "GREEN"})
        if site is not None:
            # 대지 경계: 이 판 윗면에 드러나는 부분만 각인(윗판에 덮이는 곳은 빼고)
            show = regions[L].difference(regions[lv[i + 1]]) if i + 1 < len(lv) else regions[L]
            for sp in as_polys(site):
                for ring in [sp.exterior, *sp.interiors]:
                    seg = ring.intersection(show)
                    for ls in getattr(seg, "geoms", [seg]):
                        if ls.geom_type == "LineString" and ls.length > 0.3:
                            msp.add_lwpolyline(tx(ls.coords), dxfattribs={"layer": "SITE"})
        rp = regions[L].representative_point()
        msp.add_text(f"{i:02d}  EL {L:g}", height=4, dxfattribs={"layer": "LABEL"}).set_placement(tx(rp.coords)[0])
        fn = os.path.join(out_dir, f"sheet_{i:02d}_EL{L:g}.dxf")
        doc.saveas(fn); files.append(fn)
    return files

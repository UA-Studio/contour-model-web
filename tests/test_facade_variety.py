"""v2.3 입면 다양화 시험: 대장 속성으로 입면 유형이 갈리고, 같은 조건이라도 건물마다 조금씩 다르며, 필로티·상가층이 생김."""
from shapely.geometry import box

from contour_ai import facade


def _m(x, attrs, layer="일반주택", floors=5, fu=None, w=12, d=10):
    m = dict(poly=box(x, 0, x + w, d), base=0.0, top=floors * 3.0, height=floors * 3.0, floors=floors,
             layer=layer, use="", attrs=dict(attrs), floor_h=3.0)
    if fu:
        m["floor_uses"] = fu
    return m


def test_대장_속성으로_입면_유형이_갈림():
    ms = [_m(0, {"대장_주용도": "공동주택", "대장_기타용도": "도시형생활주택(원룸형)", "대장_사용승인일": "20150301"}),
          _m(20, {"대장_주용도": "제2종근린생활시설", "대장_사용승인일": "20100101", "대장_구조": "철근콘크리트구조"}),
          _m(40, {"대장_주용도": "종교시설", "대장_구조": "철근콘크리트구조"}, floors=2),
          _m(60, {"대장_주용도": "단독주택", "대장_구조": "목구조", "대장_지붕": "기와"}, floors=1),
          _m(80, {}, layer="아파트", floors=15, w=60, d=12)]
    facade.assign(ms, log=lambda *a: None)
    types = [m["facade"]["type"] for m in ms]
    assert types[:3] == ["도시형생활주택", "근생 꼬마빌딩", "종교·문화"]
    assert ms[3].get("pitched") and ms[3]["facade"]["wall"].startswith("목재")
    assert types[4] == "판상형 아파트"


def test_같은_조건이라도_건물마다_다르고_다시_만들면_같음():
    attrs = {"대장_주용도": "공동주택", "대장_기타용도": "다세대주택", "대장_사용승인일": "20180101"}
    ms = [_m(i * 20, attrs) for i in range(12)]
    facade.assign(ms, log=lambda *a: None)
    walls = {m["facade"]["wall"] for m in ms}
    jits = {m["facade"]["jitter"] for m in ms}
    assert len(walls) >= 3 and len(jits) == 12
    again = [_m(i * 20, attrs) for i in range(12)]
    facade.assign(again, log=lambda *a: None)
    assert [m["facade"]["wall"] for m in again] == [m["facade"]["wall"] for m in ms]


def test_필로티는_1층_벽이_없고_기둥이_생김():
    attrs = {"대장_주용도": "공동주택", "대장_기타용도": "다세대주택", "대장_사용승인일": "20180101"}
    pil = _m(0, attrs, fu={1: "주차장 필로티", 2: "다세대주택", 3: "다세대주택", 4: "다세대주택", 5: "다세대주택"})
    plain = _m(0, attrs)
    facade.assign([pil, plain], log=lambda *a: None)
    assert pil["facade"]["pilotis"] and not plain["facade"]["pilotis"]
    facade.clear_cache()
    a = facade.facade_meshes(pil)
    b = facade.facade_meshes(plain)
    wa = a[pil["facade"]["wall"]]
    wb = b[plain["facade"]["wall"]]
    # 필로티: 1층 높이(0~3m) 사이 벽 면적이 기둥만큼만 남음
    def low_area(mesh):
        tri = mesh.triangles
        low = (tri[:, :, 2].max(axis=1) <= 2.9) & (tri[:, :, 2].min(axis=1) >= 0.1)
        return mesh.area_faces[low].sum()
    assert low_area(wa) < 0.5 * low_area(wb)


def test_층별_용도가_근생이면_그_층에_상가_유리():
    attrs = {"대장_주용도": "공동주택", "대장_기타용도": "다세대주택", "대장_사용승인일": "20050101"}
    m = _m(0, attrs, fu={1: "제2종근린생활시설", 2: "제2종근린생활시설", 3: "다세대주택", 4: "다세대주택", 5: "다세대주택"})
    facade.assign([m], log=lambda *a: None)
    assert m["facade"]["shop_floors"] == [0, 1]
    ops = facade._openings(12, 3.0, 3.0, 15, m["facade"]["window"], 1, {0, 1})
    assert ops and all(o[3] - o[2] > 2.0 for o in ops)       # 2층도 큰 유리


def test_평지붕_파라펫은_마감면부터_1_2m():
    from contour_ai.building_detail import PARAPET_H, ROOF_DECK, building_parts
    flat = _m(0, {"대장_주용도": "업무시설"}, layer="일반건물", floors=4)
    pitched = _m(20, {}, layer="일반주택", floors=1)
    pp = [p for p in building_parts(flat) if p[0] == "parapet"]
    assert pp and all(abs(p[4] - (flat["top"] + ROOF_DECK + 1.2)) < 1e-9 and p[3] == flat["top"] for p in pp)
    assert PARAPET_H >= 1.2
    assert not [p for p in building_parts(pitched) if p[0] == "parapet"]


def _apt_with_shops(x=0.0, fh=2.5, floors=20):
    """층고가 낮은(대장 높이÷층수 2.5m) 판상형 아파트, 1~3층 상가 — 상가 큰 유리가 위층 창과 겹치던 조건."""
    attrs = {"대장_주용도": "공동주택", "대장_기타용도": "아파트", "대장_사용승인일": "20150101"}
    m = _m(x, attrs, layer="아파트", floors=floors, w=60, d=12,
           fu={1: "제1종근린생활시설", 2: "제2종근린생활시설", 3: "판매시설"})
    m["top"] = m["height"] = floors * fh
    m["floor_h"] = fh
    return m


def test_창_구멍끼리_겹치거나_닿지_않음():
    """구멍이 겹치면 Rhino 가 벽 판을 그리지 못해 벽이 사라지고 창 테두리·파라펫만 떠 보였다 (v2.3.1 신고)."""
    import itertools
    for fh in (2.3, 2.4, 2.5, 2.6, 2.8, 3.0):
        m = _apt_with_shops(fh=fh)
        facade.assign([m], log=lambda *a: None)
        for style in facade.WINDOW:
            m["facade"]["window"] = style
            for lay in facade.layout(m):
                for A, t, nrm, L, ops in lay["edges"]:
                    assert ops or L < 2.0
                    for a, b in itertools.combinations(ops, 2):
                        apart = a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2]
                        assert apart, (style, fh, a, b)
                    for o in ops:
                        assert 0 < o[0] < o[1] < L and lay["zwall"] < o[2] < o[3] < lay["top"]


def test_셋백_건물_벽이_두_벌_생기지_않음(tmp_path):
    """층별개요 셋백(단 2개) 건물: Rhino 외벽·유리가 단마다 한 번씩만."""
    import rhino3dm
    from shapely.geometry import box as _box
    from contour_ai.out_rhino import write_3dm
    m = _apt_with_shops(fh=2.8, floors=10)
    m.update(source="register", tiers=[(m["poly"], 0, 6, 10), (_box(0, 0, 40, 12), 6, 10, 10)])
    facade.assign([m], log=lambda *a: None)
    m["facade"]["windows"] = True
    facade.clear_cache()
    p = tmp_path / "t.3dm"
    write_3dm(str(p), {}, [m], [], (0.0, 0.0))
    f = rhino3dm.File3dm.Read(str(p))
    walls = [o for o in f.Objects if o.Attributes.GetUserString("부위") == "외벽"
             and isinstance(o.Geometry, rhino3dm.Extrusion)]
    edges = sum(len(lay["edges"]) for lay in facade.layout(m))
    bottoms = 2   # 단마다 바닥 판 하나
    assert len(walls) == edges + bottoms
    # 창 구멍은 각 벽 판 높이 안에 (땅 높이만큼 위로 뜨지 않음)
    for o in walls:
        g = o.Geometry
        ob = g.Profile3d(0, 0.0).GetBoundingBox()
        for k in range(1, g.ProfileCount):
            hb = g.Profile3d(k, 0.0).GetBoundingBox()
            assert ob.Min.Z < hb.Min.Z < hb.Max.Z < ob.Max.Z
    assert max(o.Geometry.GetBoundingBox().Max.Z for o in walls) <= m["top"] + 0.01

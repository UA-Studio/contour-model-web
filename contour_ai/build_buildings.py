"""모델 만들기 3단계: 건물 매스 앉히기와 건물 디테일(입면·창문)."""
import math
import os

from .progress import fill

from .buildings import building_masses
from .live_view import _mass_meshes
from .preview import _plan_sheets
from .build_defaults import FINE_TRIM_RADIUS


def _buildings(st):
    """3단계: 건물 매스를 판 위에 앉히고, 대지 위 비우기를 고르면 대지 안 건물을 뺀다."""
    a, clip, data, log, pg = st.a, st.clip, st.data, st.log, st.pg
    log("3/6 건물·길·가로수 배치 중")
    pg.steps("3.1", "3.2")
    st.masses = building_masses(data["buildings"], st.regions, clip, a.floor_height)
    # 사업 대지: 경계를 표시하고, 고르면 대지 위(건물·길·나무·시설물)를 비운다
    st.site_poly = a.site.get("poly") if a.site else None
    if st.site_poly is not None and (st.site_poly.is_empty or not st.site_poly.intersects(clip)):
        log("   (대지가 모델 범위 밖에 있어 경계를 넣지 않습니다)")
        st.site_poly = None
    st.wipe = st.site_poly if (st.site_poly is not None and a.site_clear) else None
    st.cleared = {}
    if st.wipe is not None:
        from . import site as site_mod
        st.masses, gone = site_mod.split_masses(st.masses, st.wipe)
        st.cleared["건물"] = len(gone)
        log(f"   대지 위 비우기: 건물 {len(gone)}동을 뺐습니다 (대지 {st.wipe.area:,.0f}㎡)")


def _building_details(st):
    """3단계: 건축물대장 높이·층수, 디테일 수준, 외관·창문."""
    a, bbox, log, origin, pg = st.a, st.bbox, st.log, st.origin, st.pg
    st.register = None
    if a.vworld_key and a.data_key and st.masses:
        # 건축물대장: 실제 높이·층수·준공연도 (키는 사용자 것, 실패해도 수치지도 층수로 계속)
        from .publicdata import enrich
        crs_wkt = a.crs_wkt
        if not crs_wkt and os.path.isdir(a.src):
            from .pipeline import read_crs
            crs_wkt = read_crs(a.src).to_wkt()
        cache = a.cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
        st.register = enrich(st.masses, bbox, crs_wkt, a.vworld_key, a.data_key, a.vworld_domain, cache,
                          a.floor_height, log)
    if a.detail <= 1:
        for m in st.masses:
            m["lod"] = 1
    if a.facade and a.detail >= 2 and st.masses:
        from .facade import assign
        assign(st.masses, a.aerial, log)
        cx, cy = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        if a.site and a.site.get("poly") is not None:
            cx, cy = a.site["poly"].centroid.x, a.site["poly"].centroid.y
        r = a.facade_radius
        n = 0
        for m in st.masses:
            c = m["poly"].centroid
            if (a.detail >= 3 and (r is None or math.hypot(c.x - cx, c.y - cy) <= r)
                    and m.get("layer") not in ("무벽건물", "공사중건물")):
                m["facade"]["windows"] = True; n += 1
                # 창틀·창살·창턱은 가운데 250m 안 건물만(먼 건물은 유리·간판만): 1500m 에서 파일·시간이 크게 늘지 않게
                m["facade"]["fine"] = math.hypot(c.x - cx, c.y - cy) <= FINE_TRIM_RADIUS
        if a.detail >= 3:
            nf_ = sum(1 for m in st.masses if (m.get("facade") or {}).get("fine"))
            log(f"   창틀·창턱까지 만들 건물 {nf_}동 (가운데에서 {FINE_TRIM_RADIUS:g}m 안)")
            log(f"   창문을 낼 건물 {n}동" + (f" (가운데에서 {r:g}m 안)" if r else ""))
    pg.step("3.3", x=f"건물 {len(st.masses)}동")
    if pg.live:
        pg.part("mass", _mass_meshes(st.masses, origin), info=f"건물 {len(st.masses)}동")
        pg.plan("b", bbox, lambda ax: (_plan_sheets(ax, st.regions),
                                       fill(ax, [m["poly"] for m in st.masses], "white", "#333", 0.3, 3)))

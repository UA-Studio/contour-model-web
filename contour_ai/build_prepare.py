"""모델 만들기 1~2단계: 설정 모으기·수치지도 읽기, 높이 보간, 계단식 대지, 사업지 레벨, 계단판."""
import math
import os
from collections import defaultdict

from shapely.geometry import box
from shapely.ops import unary_union

from .mapdata import (load_dxf, load_shp, _pad_parcels)
from .terrain import (breakline_points, build_interpolator, terrace_regions)
from .live_view import _live_input, _live_sheet, _live_tin
from .build_defaults import DEFAULTS
from .output_kinds import _outputs


def _prepare(st, src, bbox, out, log, kw):
    """1단계: 설정을 모으고 수치지도(SHP·DXF)를 읽는다."""
    from types import SimpleNamespace
    from .progress import Progress
    pg = kw.pop("progress", None) or Progress()   # 화면의 생성 과정 실황 (없으면 조용히)
    a = SimpleNamespace(**{**DEFAULTS, **kw})
    a.src, a.out, a.bbox = src, out, bbox
    # 받을 파일: None = 기본(OUTPUTS_DEFAULT, 스케치업만). 3D 보기(GLB)·요약·단면 자료는 늘 만든다
    a.outs = frozenset(_outputs(a))
    a.want = a.outs.__contains__
    a.sketchup = a.want("sketchup")
    os.makedirs(a.out, exist_ok=True)
    from .tree_models import SPECIES
    SPECIES.clear()
    from .facade import clear_cache
    clear_cache()
    bbox = tuple(a.bbox)
    min_area = a.min_area if a.min_area is not None else (2.0 * a.scale / 1000) ** 2 * 4  # 모형상 약 4mm²
    log("1/6 수치지도 읽는 중")
    pg.step("1.1")
    data = load_shp(a.src, bbox) if os.path.isdir(a.src) else load_dxf(a.src, bbox)
    origin = (bbox[0], bbox[1])
    st.circle = None
    if a.shape == "circle":                  # 원형 범위 (v2.2): 네모 안에 꼭 맞는 원
        from .circle_range import make_circle
        st.circle = make_circle(bbox)
        log(f"   범위: 지름 {min(bbox[2] - bbox[0], bbox[3] - bbox[1]):g}m 원")
    clip = st.circle if st.circle is not None else box(*bbox)
    if pg.live:
        _live_input(pg, data, bbox, origin, a.interval)
    pg.steps("1.2", "1.3")
    st.a, st.bbox, st.clip, st.data, st.log, st.min_area, st.origin, st.pg = a, bbox, clip, data, log, min_area, origin, pg


def _heights(st):
    """2단계: 등고선·표고점 → 높이 보간 (꺾임선, '등고선 그대로', DEM)."""
    a, clip, data, log, pg = st.a, st.clip, st.data, st.log, st.pg
    log("2/6 등고선 보간, 계단판 만드는 중")
    pg.steps("2.1", "2.2")
    st.interp = build_interpolator(data["contours"], data["spots"])
    st.breaks = [b for b in data.get("breaks", ()) if b[0].intersects(clip.buffer(30))] if a.breaklines else []
    if st.breaks:
        bp = breakline_points(st.breaks, st.interp)
        st.interp = build_interpolator(data["contours"], data["spots"], extra=bp)
        nk = defaultdict(int)
        for b in st.breaks:
            nk["옹벽" if b[3] > 0.3 else b[1] or "기타"] += 1
        log("   지형 꺾임선: " + " · ".join(f"{k} {v}줄" for k, v in nk.items()) + f" (높이 점 {len(bp[1]):,}개)")
    st.sigma, st.smooth, st.chaikin_n = a.sigma, a.smooth, 3
    if a.terrain_fit == "exact":
        # 등고선 그대로: 절토면 계단·옹벽처럼 등고선 사이가 좁게 꺾이는 곳을 뭉개지 않게 다듬기를 줄인다.
        # 판 윤곽은 레이저로 자를 수 있는 만큼만(축척 1:1000 에서 0.5m = 0.5mm) 다듬는다.
        st.sigma, st.smooth, st.chaikin_n = min(a.sigma, 0.5), min(a.smooth, 0.5), 1
        log("   지형: 등고선 그대로 (다듬기 최소)")
    if a.dem is not None:
        # 수치표고모델이 있으면 그 높이를 쓰고, 자료가 비는 곳만 등고선으로. 격자 자료라 고르게 펴는 정도도 줄인다
        from .dem import merged_interpolator
        st.interp = merged_interpolator(a.dem, st.interp)
        st.sigma = min(a.sigma, 1.0)


def _pads(st):
    """2단계: 계단식 대지 — 비탈의 건물 터·논을 평평한 단으로."""
    a, bbox, data, log = st.a, st.bbox, st.data, st.log
    # 계단식 대지: 비탈의 건물 터·논을 평평한 단으로, 단 사이는 옹벽처럼 (수치지도 건물·옹벽·담·길·논)
    st.pads = None
    st.parcels = []
    if a.pads and (data.get("buildings") or data.get("paddies")):
        from .pads import build_pads
        site_p = (a.site or {}).get("poly")
        uses_site = site_p is not None and not getattr(a, "site_keep_terrain", False) and (
            a.site_clear or a.site.get("levels") or a.site.get("plates") or a.site.get("keep"))
        try:
            bl = [b[0] for b in data["buildings"]]
            barriers = [b[0] for b in data.get("breaks", ())] + list(data.get("walls", ()))
            if not os.path.isdir(a.src):
                barriers += list(data.get("roads", ()))      # DXF: 길은 선뿐이라 선을 막는 선으로
            roads_u = unary_union(data["road_areas"]) if data.get("road_areas") else None
            pcl = _pad_parcels(a, bbox, log)
            st.parcels = pcl                             # 2D 지형도·다이어그램의 지적 경계 (v2.2)
            st.pads = build_pads(bl, st.interp, bbox, barriers=barriers, roads=roads_u, paddies=data.get("paddies", ()),
                              exclude=site_p if uses_site else None, parcels=pcl, log=log)
        except Exception as e:
            log(f"   (계단식 대지를 만들지 못함: {type(e).__name__}: {e})")
            st.pads = None
        if st.pads is not None:
            st.interp = st.pads.wrap(st.interp)


def _site_levels(st):
    """2단계: 사업지 도면의 레벨·레벨판으로 대지 안 지형을 다시 만든다."""
    a, clip, log = st.a, st.clip, st.log
    # 사업지 도면의 레벨: 대지 안 지형을 그 레벨로 다시 만들고, 경계 밖 15m 까지 수치지도 지형과 섞는다
    st.level_site = None
    st.orig_interp = st.interp      # 대지 분석(경사·절토·성토)에서 '원래 지형'으로 쓴다
    plates = (a.site or {}).get("plates") or []
    keep_areas = (a.site or {}).get("keep") or []
    if a.site and getattr(a, "site_keep_terrain", False) and (a.site.get("levels") or plates or keep_areas):
        log("   대지 안 원래 지형 유지: 도면의 레벨·레벨판은 지형에 쓰지 않고 경계선만 지형에 눌러 표시합니다")
    elif a.site and (a.site.get("levels") or plates or keep_areas) and a.site.get("poly") is not None \
            and a.site["poly"].intersects(clip):
        from .site import blended, level_interpolator
        lv = a.site.get("levels")
        if plates:
            # 레벨판 테두리를 높이 점으로 더해 판 사이 빈 곳이 판 높이를 잇게 한다
            from .sitefile import plate_points
            lines_, spots_ = lv or ([], [])
            lv = (lines_, list(spots_) + plate_points(plates))
        # 표준 대지 파일은 사용자가 역할을 확인한 자료라 지형과 많이 달라도(깊은 절토) 버리지 않는다
        tol = 1e4 if a.site.get("standard") else 30.0
        sf = level_interpolator(lv, st.interp, a.site["poly"], log, tol=tol, cover=clip) if lv and (lv[0] or lv[1]) else None
        n_pts = getattr(sf, "count", 0) if sf is not None else 0
        if plates or keep_areas:
            from .sitefile import plan_function
            sf = plan_function(st.interp, plates, keep_areas, base=sf)
        if sf is not None:
            st.interp = blended(a.site["poly"], sf, st.interp)
            st.level_site = a.site["poly"]
            log(f"   대지 안 지형을 사업지 레벨로 다시 만들었습니다 (레벨 점 {n_pts:,}개"
                + (f" · 레벨판 {len(plates)}개" if plates else "") + (f" · 원지형 유지 {len(keep_areas)}곳" if keep_areas else "") + ")")


def _sheets(st):
    """2단계: 계단판(층) 면 만들기."""
    a, bbox, data, min_area, origin, pg = st.a, st.bbox, st.data, st.min_area, st.origin, st.pg
    pg.step("2.3")
    if pg.live:
        _live_tin(pg, st.interp, bbox, origin)
    st.regions, zmin = terrace_regions(data["contours"], st.interp, bbox, a.interval, min_area,
                                    smooth=st.smooth, sigma=st.sigma, chaikin_n=st.chaikin_n, keep=st.level_site,
                                    keep_w=st.pads.keep_weight if st.pads is not None else None)
    if st.circle is not None:
        from .circle_range import cut_regions as circle_regions
        st.regions = circle_regions(st.regions, st.circle, min_area)
    lv = list(st.regions)
    pg.step("2.4", x=f"EL {lv[0]:g}부터 {a.interval:g}m마다 {len(lv)}단")
    # 판이 많으면(예: 1m 간격 수백 장) 화면 실황은 최대 30묶음으로 나눠 보낸다.
    # 판마다 3D 부품·평면 그림을 만들면 그림 그리는 시간이 판 수의 제곱으로 늘어난다.
    stride = max(1, math.ceil(len(lv) / 30))
    for i, L in enumerate(lv):
        pg.step("2.5", r=i, n=len(lv), x=f"판 {i + 1} / {len(lv)} · EL {L:g}")
        if pg.live and (i % stride == stride - 1 or i == len(lv) - 1):
            _live_sheet(pg, st.regions, i - i % stride, bbox, origin, j=i + 1, key=i // stride)
    pg.steps("2.6", "2.7")

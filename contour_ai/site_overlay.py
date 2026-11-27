"""검토 대지 표시(경계선·레벨 등고선·레벨판·원지형 유지·계획 건물·계획 도로·정북일조 사선)를
파일 형식과 상관없는 도형 목록으로 만든다. Rhino(3dm)·스케치업 쓰기가 같은 목록을 받아 각자 형식으로 넣는다.

반환 항목(dict):
  layer  "대지::경계" 처럼 '::' 로 나눈 레이어(스케치업 태그) 경로
  rgb    레이어 색
  name   개체 이름
  kind   "curve" → xyz (N×3, 모델 좌표 = 원점 이동 전)  /  "mesh" → mesh (Trimesh, 모델 좌표)
  attrs  개체 정보(이름 → 값, 글자)
"""
import numpy as np
from shapely.geometry import LineString, Point

RED, BLUE, GRAY, DARK, ORANGE = (220, 60, 40), (40, 90, 220), (90, 90, 90), (30, 30, 30), (240, 150, 60)


def _xyz(line, z=None):
    """LineString → N×3. z 를 주면 그 높이로, 아니면 선의 z 그대로."""
    c = np.asarray(line.coords, float)
    if z is not None or c.shape[1] < 3:
        c = np.column_stack([c[:, :2], np.full(len(c), 0.0 if z is None else float(z))])
    return c


def site_overlay(site, interp, log=print, step=None):
    """site = {poly(모델 좌표), review, regions, plates, keep, buildings, roads, keep_terrain}
    → (항목 목록, 요약 dict). 요약은 summary.json 의 site 칸."""
    from .geom_util import as_polys
    from .terrain import drape_lines
    from .landlaw import envelope_mesh
    poly, rv = site["poly"], site.get("review") or {}
    items = []

    def add(layer, rgb, name, kind, geom, **attrs):
        items.append(dict(layer=layer, rgb=rgb, name=name, kind=kind,
                          **({"xyz": geom} if kind == "curve" else {"mesh": geom}),
                          attrs={k: str(v) for k, v in attrs.items()}))

    xs = np.linspace(poly.bounds[0], poly.bounds[2], 12)
    ys = np.linspace(poly.bounds[1], poly.bounds[3], 12)
    pts = [(x, y) for x in xs for y in ys if poly.contains(Point(x, y))] or [tuple(poly.representative_point().coords[0])]
    ground = float(np.nanmean(interp(np.array(pts))))

    info = {"면적(㎡)": f"{poly.area:.1f}", "PNU": str(rv.get("pnu") or "")}
    for k, ko in (("zone", "용도지역"), ("bcr", "건폐율(%)"), ("far", "용적률(%)"), ("area", "대지면적(㎡)")):
        if rv.get(k) is not None:
            info[ko] = round(rv[k], 1) if isinstance(rv[k], float) else rv[k]
    for g in as_polys(poly):
        add("대지::경계", RED, f"대지 경계 {rv.get('address', '')}".strip(), "curve", _xyz(g.exterior, ground + 0.05), **info)
    # 경계선을 지형에 눌러 붙인 선: 계단판마다 그 판 윗면 높이에(레이저 SITE 각인과 같은 선), 그리고 매끈한 지형 위에
    regions = site.get("regions")
    n_dr = 0
    if regions:
        rings = [r for g in as_polys(poly) for r in [g.exterior, *g.interiors]]
        for ls, L in drape_lines([LineString(r.coords) for r in rings], regions):
            add("대지::경계(계단판 위)", RED, f"대지 경계 · 판 EL {L:g}", "curve", _xyz(ls, L + 0.02)); n_dr += 1
    for g in as_polys(poly):
        for r in [g.exterior, *g.interiors]:
            seg = np.asarray(LineString(r.coords).segmentize(1.0).coords)[:, :2]
            add("대지::경계(지형 위)", RED, "대지 경계 · 지형 위", "curve",
                np.column_stack([seg, interp(seg) + 0.02]))
    log(f"   대지 경계선을 지형에 눌러 붙였습니다 (대지::경계(계단판 위) {n_dr}조각 · 대지::경계(지형 위))")
    if step:
        # 대지 안을 사업지 레벨로 다시 그린 등고선 (step m 간격, 각 선은 제 높이에)
        import contourpy
        b = poly.bounds
        gx = np.arange(b[0] - 1, b[2] + 1.5, 0.5); gy = np.arange(b[1] - 1, b[3] + 1.5, 0.5)
        GX, GY = np.meshgrid(gx, gy)
        GZ = interp(np.column_stack([GX.ravel(), GY.ravel()])).reshape(GX.shape)
        gen = contourpy.contour_generator(GX, GY, GZ)
        n = 0
        for z in np.arange(np.ceil(np.nanmin(GZ) / step) * step, np.nanmax(GZ) + 1e-6, step):
            for c in gen.lines(z - 0.01):     # 평평한 단(레벨 = z)에서 선이 부서지지 않게 살짝 아래를 딴다
                if len(c) < 2:
                    continue
                g2 = LineString(c).intersection(poly)
                for part in getattr(g2, "geoms", [g2]):
                    if isinstance(part, LineString) and part.length > 0.5:
                        add("대지::레벨 등고선", BLUE, f"EL {z:g}", "curve", _xyz(part, z)); n += 1
        log(f"   대지 안 레벨 등고선 {n}줄을 넣었습니다 ({step:g}m 간격, 대지::레벨 등고선)")
    # 표준 대지 파일의 레벨판·원지형 유지·계획 건물·도로 (선으로, 그 자리 계획 높이에)
    plates, keep, blds, roads = (site.get(k) or [] for k in ("plates", "keep", "buildings", "roads"))
    if site.get("keep_terrain"):
        plates, keep = [], []      # 원래 지형 유지: 계획 레벨판은 넣지 않는다(건물·도로 선은 원래 지형 위에)
    for p, z in plates:
        for g in as_polys(p):
            add("대지::레벨판", BLUE, f"레벨판 EL {z:g}", "curve", _xyz(g.exterior, z + 0.05),
                **{"계획 레벨": f"{z:g}", "면적(㎡)": f"{g.area:.1f}"})
    for p in keep:
        for g in as_polys(p):
            c = np.asarray(g.exterior.coords)[:, :2]
            add("대지::원지형 유지", GRAY, "원지형 유지", "curve", np.column_stack([c, interp(c) + 0.05]))
    for i, p in enumerate(blds):
        for g in as_polys(p):
            c = g.representative_point()
            z = float(interp(np.array([[c.x, c.y]]))[0])
            add("대지::계획 건물", DARK, f"계획 건물 {i + 1} (EL {z:.1f})", "curve", _xyz(g.exterior, z + 0.1))
    for ls in roads:
        c = np.asarray(ls.segmentize(2.0).coords)[:, :2]
        add("대지::계획 도로", (120, 120, 120), "계획 도로·램프", "curve", np.column_stack([c, interp(c) + 0.1]))
    if plates or keep or blds or roads:
        log(f"   표준 대지 파일 도형을 넣었습니다 (대지:: 레벨판 {len(plates)} · 원지형 유지 {len(keep)} · "
            f"계획 건물 {len(blds)} · 계획 도로 {len(roads)})")
    env = None
    if rv.get("sunlight"):
        env = envelope_mesh(poly, ground)
        if env is not None:
            add("대지::정북일조 사선(참고)", ORANGE, "정북일조 사선: 높이 10m 이하 1.5m, 초과 부분 높이의 1/2", "mesh", env)
    log(f"   대지 경계" + (" + 정북일조 사선을" if env is not None else "를") + f" 넣었습니다 (기준 지표면 EL {ground:.1f})")
    return items, dict(address=rv.get("address"), pnu=rv.get("pnu"), ground=round(ground, 2), envelope=env is not None)

"""대지 파일 읽기와 '대지 파일 정리' 화면: 레이어 목록·역할 추정·미리보기.
"""
import re

from .app_lazy import pipeline


def site_info(src, path=None, ring=None):
    """대지 경계(파일 또는 화면에서 찍은 점) → 화면에 그릴 테두리와 넓이. src: _sources() 항목"""
    from shapely.geometry import box
    from .site import from_ring, load_boundary
    crs = pipeline.read_crs(src["folder"])
    sheet = pipeline.sheet_bounds(src["folder"])
    poly = from_ring(ring) if ring else load_boundary(path, crs.to_wkt(), sheet, log=lambda *_: None, map_folder=src["folder"])
    polys = list(getattr(poly, "geoms", [poly]))
    fit = None
    if path and not ring:
        from .site import drawing_affine
        aff = drawing_affine(path, sheet, src["folder"], log=lambda *_: None)   # 위에서 한 번 계산해 둔 값
        if aff:
            fit = f"도면 좌표{'(' + aff['unit'] + ')' if aff.get('unit') else ''}를 수치지도 표고점 {aff['n']}/{aff['total']}개로 맞췄어요 (오차 {aff['rms']:.1f} m)"
    b = poly.bounds
    sb = box(*sheet)
    out_pct = round(100 * (1 - poly.intersection(sb).area / max(poly.area, 1e-9)))
    # 대지가 도엽 밖으로 나가는 쪽과, 그쪽 1:5000 도엽 번호(파일 이름에 8자리 도엽 번호가 있을 때)
    dirs = [d for d, hit in (("북", b[3] > sheet[3] + 1), ("남", b[1] < sheet[1] - 1),
                             ("동", b[2] > sheet[2] + 1), ("서", b[0] < sheet[0] - 1)) if hit]
    need = []
    m = re.search(r"(?<!\d)(\d{5})(\d{3})(?!\d)", str(src.get("file") or ""))
    if m and dirs:
        n = int(m.group(2))
        if 1 <= n <= 100:
            r, c = divmod(n - 1, 10)
            for d in dirs:
                rr, cc = r + {"북": -1, "남": 1}.get(d, 0), c + {"동": 1, "서": -1}.get(d, 0)
                if 0 <= rr < 10 and 0 <= cc < 10:
                    need.append(f"{m.group(1)}{rr * 10 + cc + 1:03d}")
    return dict(path=path, rings=[[[round(x, 2), round(y, 2)] for x, y, *_ in g.exterior.coords] for g in polys],
                xy=[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2], out_pct=out_pct, out_dirs=dirs, need=need, area=round(poly.area, 1), size=[round(b[2] - b[0], 1), round(b[3] - b[1], 1)],
                bounds=list(b), in_sheet=bool(box(*sheet).contains(poly)), parts=len(polys), fit=fit,
                dxf=bool(path and str(path).lower().endswith(".dxf") and not ring),
                standard=bool(path and not ring and _is_std(path)))


def _is_std(path):
    from .sitefile import is_standard
    return is_standard(path)


PREP = {}      # 대지 파일 경로 → 정리 분석 결과(저장할 때 다시 쓴다)


def site_prep(src, path):
    """대지 파일 정리 화면용: 레이어 목록·역할 추측·미리보기 도형"""
    from . import sitefile
    crs = pipeline.read_crs(src["folder"])
    sheet = pipeline.sheet_bounds(src["folder"])
    an = sitefile.analyze(path, crs.to_wkt(), sheet, map_folder=src["folder"], log=lambda *_: None)
    PREP[(src["folder"], path)] = an
    return dict(an, fit=None, roles=list(sitefile.ROLES), standard=sitefile.is_standard(path))

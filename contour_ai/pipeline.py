"""수치지도 2.0 SHP zip 하나로 콘타 모델 전체를 만드는 실행 흐름.

zip 풀기 → 좌표계·도엽 범위 확인 → 모델 범위(중심·크기) 정하기 →
위성영상으로 녹지 판정(선택) → 지형·길·건물·나무 → Rhino/STL/레이저/미리보기
"""
import glob
import json
import os
import re
import shutil
import tempfile
import time
import zipfile

import numpy as np

from .contour_model import build

BUILDING_SHP = "N3A_B0010000"


# ---------------------------------------------------------------- 입력 준비
def _fix_name(info):
    """zip 안 한글 파일명(cp949) 복원."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except Exception:
        return info.filename


def _extract(zpath, root, depth=0):
    """zip 을 root 아래에 폴더 구조대로 푼다. 안의 zip(도엽 묶음)은 각자 하위 폴더로 3단계까지 푼다."""
    with zipfile.ZipFile(zpath) as z:
        for k, info in enumerate(z.infolist()):
            name = _fix_name(info).replace("\\", "/")      # 윈도우식 경로 구분자도 처리
            parts = [x for x in name.split("/") if x not in ("", ".")]
            if info.is_dir() or name.endswith("/") or ".." in parts or not parts:
                continue
            dest = os.path.join(root, *parts)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if dest.lower().endswith(".zip") and depth < 3:
                try:
                    _extract(dest, os.path.splitext(dest)[0] + f"_z{k}", depth + 1)
                except zipfile.BadZipFile:
                    pass
                os.remove(dest)


def _normalize(root):
    """레이어 파일 이름을 표준형(N3A_B0010000.shp)으로 맞춘다: 대소문자, 꼬리표가 붙은 이름."""
    for d, _, fs in os.walk(root):
        _normalize_dir(d, fs)


def _normalize_dir(root, files):
    for fn in files:
        stem, ext = os.path.splitext(fn)
        if ext.lower() not in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            continue
        m = re.search(r"N([123])([APL]_[A-Z]\d{7})", stem.upper())
        if not m:
            continue
        # 1:1000(N1)·1:2500(N2) 수치지도도 레이어 구성·속성이 같아 표준 이름(N3…)으로 맞춘다
        std = "N3" + m.group(2) + ext.lower()
        if fn != std and not os.path.exists(os.path.join(root, std)):
            os.rename(os.path.join(root, fn), os.path.join(root, std))


def _describe(root):
    files = [f for _, _, fs in os.walk(root) for f in fs]
    exts = {}
    for f in files:
        e = os.path.splitext(f)[1].lower() or "(확장자 없음)"
        exts[e] = exts.get(e, 0) + 1
    kinds = ", ".join(f"{k} {v}개" for k, v in sorted(exts.items(), key=lambda kv: -kv[1])[:6]) or "파일 없음"
    shp = sorted(f for f in files if f.lower().endswith(".shp"))[:5]
    hint = ""
    if exts.get(".dxf") or exts.get(".dwg"):
        hint = " DXF/DWG 판이 들어 있습니다. 국토정보플랫폼에서 'SHP' 형식으로 다시 받아 주세요."
    elif exts.get(".ngi") or exts.get(".nda"):
        hint = " NGI 판이 들어 있습니다. 국토정보플랫폼에서 'SHP' 형식으로 다시 받아 주세요."
    return f"zip 안 내용: {kinds}" + (f" (예: {', '.join(shp)})" if shp else "") + "." + hint


def _sheet_folders(root):
    """root 아래에서 도엽 폴더(건물 레이어가 든 폴더)를 모두 찾는다. 똑같은 도엽이 두 번 들어 있으면 하나만."""
    import shapefile
    out, seen = [], set()
    for d, _, fs in sorted(os.walk(root)):
        if BUILDING_SHP + ".shp" not in fs:
            continue
        r = shapefile.Reader(os.path.join(d, BUILDING_SHP), encoding="euc-kr", encodingErrors="replace")
        key = (tuple(round(v, 1) for v in r.bbox), len(r))
        r.close()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def prepare_source(path, work_dir, log=print):
    """zip·폴더 하나 또는 여러 개를 받아 SHP가 든 폴더 경로를 돌려준다.

    도엽이 여러 장이면(여러 zip, 또는 zip 안의 여러 도엽) 한 장으로 이어 붙인 폴더를 만든다.
    """
    paths = [path] if isinstance(path, (str, os.PathLike)) else list(path)
    if len(paths) == 1 and os.path.isdir(paths[0]) and os.path.exists(os.path.join(paths[0], BUILDING_SHP + ".shp")):
        if not os.path.exists(os.path.join(paths[0], "sheets.json")):
            log(f"   SHP 폴더: {paths[0]}")
        return paths[0]
    raw = os.path.join(work_dir, "raw")
    if os.path.isdir(raw):
        shutil.rmtree(raw)
    os.makedirs(raw)
    for k, p in enumerate(paths):
        dest = os.path.join(raw, f"in{k}")
        if os.path.isdir(p):
            shutil.copytree(p, dest)
        else:
            try:
                _extract(p, dest)
            except zipfile.BadZipFile:
                raise SystemExit(f"zip 파일을 열 수 없습니다: {os.path.basename(p)}. 내려받기가 끝까지 됐는지, 압축 파일이 맞는지 확인해 주세요.")
    _normalize(raw)
    sheets = _sheet_folders(raw)
    if not sheets:
        raise SystemExit("zip 안에서 수치지도 2.0 건물 레이어(N3A_B0010000.shp)를 찾지 못했습니다. "
                         "국토정보플랫폼의 '수치지도 2.0(SHP)' 파일인지 확인해 주세요. " + _describe(raw))
    if len(sheets) == 1:
        folder = sheets[0]
        log(f"   SHP 폴더: {folder} (레이어 {len(glob.glob(os.path.join(folder, '*.shp')))}개)")
        return folder
    from .sheets import merge_sheets
    folder = os.path.join(work_dir, "shp")
    merge_sheets(sheets, folder, log)
    return folder


def read_crs(folder):
    from pyproj import CRS
    prj = os.path.join(folder, BUILDING_SHP + ".prj")
    if os.path.exists(prj):
        try:
            crs = CRS.from_wkt(open(prj, encoding="utf-8", errors="ignore").read())
            epsg = crs.to_epsg(min_confidence=50)
            return CRS.from_epsg(epsg) if epsg else crs
        except Exception:
            pass
    return CRS.from_epsg(5186)


def sheet_bounds(folder):
    import shapefile
    return tuple(shapefile.Reader(os.path.join(folder, BUILDING_SHP), encoding="euc-kr", encodingErrors="replace").bbox)


def find_by_name(folder, name):
    """건물 이름·지명으로 위치 찾기. 반환: (x, y, 찾은 이름들) 또는 None."""
    import shapefile
    from shapely.geometry import shape
    from shapely.ops import unary_union
    hits, names = [], set()
    for layer, fields in (("N3A_B0010000", ("명칭", "주기")), ("N3P_H0040000", ("명칭",)),
                          ("N3A_C0430000", ("명칭",)), ("N3A_A0070000", ("명칭",))):
        fn = os.path.join(folder, layer)
        if not os.path.exists(fn + ".shp"):
            continue
        r = shapefile.Reader(fn, encoding="euc-kr", encodingErrors="replace")
        idx = [i for i, f in enumerate(r.fields[1:]) if f[0] in fields]
        for sr in r.iterShapeRecords():
            vals = [str(sr.record[i]) for i in idx]
            if any(name.replace(" ", "") in v.replace(" ", "") for v in vals if v):
                hits.append(shape(sr.shape.__geo_interface__))
                names.update(v for v in vals if v)
    if not hits:
        return None
    c = unary_union(hits).centroid
    return c.x, c.y, sorted(names)[:8]


def center_point(folder, crs, name=None, latlon=None, xy=None, log=print):
    if xy:
        return tuple(xy)
    if latlon:
        from pyproj import Transformer
        t = Transformer.from_crs(4326, crs, always_xy=True)
        return t.transform(latlon[1], latlon[0])
    if name:
        r = find_by_name(folder, name)
        if r is None:
            raise SystemExit(f"'{name}'을(를) 이 도엽에서 찾지 못했습니다. 건물 이름이나 동 이름을 바꿔 보세요.")
        log(f"   '{name}' 위치: {', '.join(r[2])}")
        return r[0], r[1]
    b = sheet_bounds(folder)
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def model_bbox(center, size, sheet):
    """중심과 크기(m)로 범위를 만들되, 도엽 밖으로 나가면 안쪽으로 밀어 넣는다."""
    w, h = (size, size) if np.isscalar(size) else size
    # 도엽보다 큰 범위는 도엽 크기로 줄인다(1:1000 도엽은 약 440×550m)
    w, h = min(w, sheet[2] - sheet[0]), min(h, sheet[3] - sheet[1])
    x0 = min(max(center[0] - w / 2, sheet[0]), sheet[2] - w)
    y0 = min(max(center[1] - h / 2, sheet[1]), sheet[3] - h)
    return (round(x0, 1), round(y0, 1), round(x0 + w, 1), round(y0 + h, 1))


def to_latlon(crs, x, y):
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(x, y)
    return lat, lon


def sentinel_tile(lat, lon):
    """위경도 → Sentinel-2 타일 이름(MGRS 100km 칸, 예: 서울 52SCG)."""
    from pyproj import Transformer
    zone = int((lon + 180) // 6) + 1
    band = "CDEFGHJKLMNPQRSTUVWXX"[int((lat + 80) // 8)]
    e, n = Transformer.from_crs(4326, (32600 if lat >= 0 else 32700) + zone, always_xy=True).transform(lon, lat)
    col = ("STUVWXYZ", "ABCDEFGH", "JKLMNPQR")[zone % 3][int(e // 100000) - 1]
    row = "ABCDEFGHJKLMNPQRSTUV"[(int(n // 100000) + (5 if zone % 2 == 0 else 0)) % 20]
    return f"{zone:02d}{band}{col}{row}"


# ---------------------------------------------------------------- 실행
def site_review(address, crs, vworld_key, vworld_domain="", cache_dir=None, out_dir=None, log=print, raise_errors=False):
    """주소 → 대지 법규 1차 검토. out_dir 에 법규검토.md·json 을 쓴다. 반환: {poly, review} 또는 None"""
    from .landlaw import lookup, review, site_polygon, to_markdown
    from .publicdata import ApiError
    if not vworld_key:
        log("   (대지 법규 검토는 브이월드 인증키가 있어야 합니다. 건너뜀)")
        return None
    cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
    try:
        raw = lookup(address, vworld_key, vworld_domain, cache_dir)
        rv = review(raw)
        poly = site_polygon(raw, crs.to_wkt())
    except (ApiError, OSError, ValueError, KeyError) as e:
        if raise_errors:
            raise ValueError(f"대지 법규 검토 실패: {e}")
        log(f"   (대지 법규 검토 실패: {e})")
        return None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, "법규검토.md"), "w", encoding="utf-8").write(to_markdown(rv))
        json.dump(dict(review=rv, raw=raw), open(os.path.join(out_dir, "법규검토.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    log(f"   대지 법규 검토: {rv['address']} · {rv.get('zone') or '용도지역 미상'} · 확인할 것 {len(rv['checks'])}가지")
    return dict(poly=poly, review=rv)


def run(source, out_dir, name=None, latlon=None, xy=None, size=500, green=True, year=None,
        cache_dir=None, log=print, vworld_key=None, vworld_domain="", aerial_file=None, data_key=None,
        site_address=None, site_file=None, site_ring=None, dem_files=None, **opts):
    """source(zip 또는 SHP 폴더) → out_dir 에 모델 일체. 반환: 요약 dict."""
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="contour_ai_")   # 결과 폴더에는 압축 푼 SHP를 남기지 않는다
    log("0/6 입력 확인 중")
    from .progress import Progress
    pg = opts.setdefault("progress", None) or Progress()
    pg.step("0.1")
    folder = prepare_source(source, work, log)
    pg.step("0.2")
    pg.step("0.3")
    crs = read_crs(folder)
    sheet = sheet_bounds(folder)
    pg.step("0.4")
    site = None
    if site_address:
        site = site_review(site_address, crs, vworld_key, vworld_domain, cache_dir, out_dir, log)
    if site_file or site_ring:
        # 경계 파일·직접 그린 경계가 있으면 그것이 대지 경계(주소로 받은 법규 검토는 그대로 붙인다)
        from .site import SiteError, from_ring, load_site_file
        try:
            levels = None
            if site_ring:
                poly = from_ring(site_ring)
                log(f"   화면에서 그린 대지 경계: 꼭짓점 {len(site_ring)}개 · {poly.area:,.0f}㎡")
            else:
                # 사업지 도면: 경계와 함께 레벨(높이 있는 등고선·레벨 글자)도 읽어 대지 안 지형을 다시 만든다
                got = load_site_file(site_file, crs.to_wkt(), sheet, log, map_folder=folder)
                poly, levels = got["poly"], got["levels"]
            site = dict(poly=poly, levels=levels, review=(site or {}).get("review"))
            if not site_ring:
                # 표준 대지 파일: 레벨판·원지형 유지·계획 건물·도로
                for k in ("plates", "keep", "buildings", "roads", "standard"):
                    if got.get(k):
                        site[k] = got[k]
        except SiteError as e:
            raise SystemExit(str(e))
    if site:
        from shapely.geometry import box as _box
        if not (name or latlon or xy):
            b = site["poly"].bounds          # 무게중심이 아니라 범위 가운데: 긴 대지가 한쪽으로 잘리지 않게
            xy = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            log("   대지를 모델 가운데로 잡았습니다")
        if not _box(*sheet).contains(site["poly"]):
            log("   (주의: 대지가 넣은 수치지도 도엽 밖으로 나갑니다. 그 도엽 zip도 함께 넣어 주세요)")
    center = center_point(folder, crs, name, latlon, xy, log)
    bbox = model_bbox(center, size, sheet)
    want = (size, size) if np.isscalar(size) else tuple(size)
    if bbox[2] - bbox[0] < want[0] - 1 or bbox[3] - bbox[1] < want[1] - 1:
        log(f"   도엽이 작아 범위를 {bbox[2] - bbox[0]:.0f} × {bbox[3] - bbox[1]:.0f} m 로 줄였습니다 (도엽 하나 안에서만 만들 수 있습니다)")
    pg.step("0.5", x=f"{bbox[2] - bbox[0]:.0f} × {bbox[3] - bbox[1]:.0f} m")
    if site:
        from shapely.geometry import box as _box
        inside = site["poly"].intersection(_box(*bbox)).area / max(site["poly"].area, 1e-9)
        if inside < 0.999:
            log(f"   (주의: 대지의 {int(inside * 100)}%만 모델 범위에 들어옵니다. 범위를 키우거나 대지 쪽으로 옮겨 주세요)")
    lat, lon = to_latlon(crs, (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    log(f"   좌표계 {crs.name}, 모델 범위 {bbox} (중심 {lat:.5f}N, {lon:.5f}E)")

    if dem_files:
        from .dem import DemError, load_dem
        try:
            dem = load_dem(dem_files, bbox, crs.to_wkt(), log=log, work=work)
            if dem.cell > 10:
                # 90m DEM 같은 거친 자료는 수치지도 등고선보다 뭉툭하다 → 쓰지 않는다
                log(f"   (DEM 격자가 {dem.cell:g}m라 수치지도 등고선보다 거칠어 쓰지 않습니다. 5m 이하 DEM을 넣어 주세요)")
            else:
                opts["dem"] = dem
        except DemError as e:
            raise SystemExit(str(e))

    ndvi = None
    if green:
        try:
            from .vegetation import fetch_ndvi
            tile = sentinel_tile(lat, lon)
            yr = year or (time.localtime().tm_year - (1 if time.localtime().tm_mon < 10 else 0))
            cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
            os.makedirs(cache_dir, exist_ok=True)
            key = f"ndvi_{tile}_{yr}_{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}.npz"
            ndvi = os.path.join(cache_dir, key)
            pg.step("0.6")
            if not os.path.exists(ndvi):
                log(f"   위성영상(Sentinel-2 {tile}, {yr}년 5~9월)으로 녹지 찾는 중 — 처음 한 번만 1~3분")
                fetch_ndvi(bbox, tile, yr, out=ndvi, crs=crs.to_wkt(), log=log)
            else:
                log("   저장해 둔 위성영상 녹지 자료 사용")
        except SystemExit as e:
            log(f"   녹지 자료를 만들지 못해 녹지 나무는 생략합니다: {e}")
            ndvi = None
        except Exception as e:
            log(f"   위성영상을 받지 못해 녹지 나무는 생략합니다 (인터넷 연결 확인): {e}")
            ndvi = None

    aerial = None
    if (not opts.get("no_trees") or green or opts.get("facade", True)) and (aerial_file or vworld_key):
        # 가로수·숲 확인용 고화질 항공사진 (모델 격자로 바꿔 저장해 두고 다시 씀)
        from .aerial import fetch_vworld, load_image
        cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".contour_ai_cache")
        os.makedirs(cache_dir, exist_ok=True)
        tag = f"{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}"
        try:
            if aerial_file:
                aerial = os.path.join(cache_dir, f"aerial_file_{tag}_{int(os.path.getmtime(aerial_file))}.npz")
                if not os.path.exists(aerial):
                    log(f"   항공사진 파일 읽는 중: {os.path.basename(aerial_file)}")
                    load_image(aerial_file, bbox, crs.to_wkt(), aerial)
            else:
                aerial = os.path.join(cache_dir, f"aerial_vworld_{tag}.npz")
                if not os.path.exists(aerial):
                    fetch_vworld(bbox, crs.to_wkt(), vworld_key, aerial, domain=vworld_domain, log=log,
                                 cache_dir=cache_dir)
                else:
                    log("   저장해 둔 항공사진 사용")
        except SystemExit as e:
            log(f"   항공사진을 쓰지 못해 가로수는 수치지도에 있는 것만 넣습니다: {e}")
            aerial = None
        except Exception as e:
            log(f"   항공사진을 쓰지 못해 가로수는 수치지도에 있는 것만 넣습니다: {type(e).__name__}: {e}")
            aerial = None

    summary = build(folder, bbox, out_dir, log=log, ndvi=ndvi, aerial=aerial, crs_wkt=crs.to_wkt(), site=site, green=green,
                    vworld_key=vworld_key, vworld_domain=vworld_domain, data_key=data_key, cache_dir=cache_dir, **opts)
    summary.update(source=os.path.basename(str(source)) if isinstance(source, str) else [os.path.basename(str(x)) for x in source], center_latlon=[round(lat, 6), round(lon, 6)],
                   bbox=list(bbox), crs=crs.name, green=bool(summary.get("trees_by_kind", {}).get("forest") or summary.get("trees_by_kind", {}).get("garden")),
                   satellite=bool(ndvi), aerial_trees=bool(aerial), seconds=round(time.time() - t0, 1))
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    shutil.rmtree(work, ignore_errors=True)
    t = round(summary["seconds"])
    log(f"끝났습니다 ({f'{t // 60}분 {t % 60}초' if t >= 60 else f'{t}초'}). 결과 폴더: {out_dir}")
    return summary

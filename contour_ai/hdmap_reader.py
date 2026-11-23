"""정밀도로지도(국토지리정보원) 폴더 읽기.

사용자가 내려받은 zip 들을 한 폴더에 모아 두면, 프로그램은 압축을 풀지 않고 zip 안의 shp 를 바로 읽는다.
  - zip 안 구조(서울_여의도 예):  SEC01_여의도/HDMap_UTMK_정표고/A1_NODE.shp, A2_LINK.shp, B2_SURFACELINEMARK.shp …
    같은 자료가 'HDMap_UTM52N_타원체고' 'HDMap_UTMK_타원체고' 와 '01.품질검사 데이터' 아래에도 있는데,
    수치지도와 높이 기준이 같은 'UTMK_정표고'(EPSG:5179) 하나만 쓰고 품질검사 폴더는 건너뛴다.
  - 처음 한 번 각 zip 의 범위(shp 머리글의 bbox)를 재서 색인 파일(.contour_ai_hdindex.json)로 저장해 두고,
    다음부터는 모형 범위에 걸치는 zip·구역만 연다. 자치구 파일과 자율주행 시범지구 파일이 겹치면 같은 표시는 하나만.

자료는 사용자 PC 에서만 읽는다(약관: 목적 외 사용·재배포·국외 반출 금지). 프로그램 묶음에 넣지 않는다.

반환 형식은 hdmap_align.align 이 받는 [dict(kind, geom, attrs)].
"""
import io
import json
import os
import re
import zipfile

import numpy as np
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box, shape

WANT_DIR = "HDMap_UTMK_정표고"
SRC_EPSG = 5179
LAYERS = ("A2_LINK", "A3_DRIVEWAYSECTION", "B2_SURFACELINEMARK", "B3_SURFACEMARK",
          "C1_TRAFFICLIGHT", "C4_SPEEDBUMP", "B1_SAFETYSIGN", "C3_VEHICLEPROTECTIONSAFETY", "C6_POSTPOINT")
READ_LAYERS = ("B2_SURFACELINEMARK", "B3_SURFACEMARK")     # 지금 모형에 쓰는 층(나머지는 나중에)
INDEX_NAME = ".contour_ai_hdindex.json"

# ---------------------------------------------------------------- 속성 코드 → 종류
# 정밀도로지도 제작 규정의 코드표. 여의도 B2 dbf(2026-11-04 사용자 화면)로 필드 이름 ID·AdminCode·Type·Kind·R_linkID·L_linkID
# 와 B2 값 501/503/505/506/515/599, Type 111/112/211/212 를 확인. 그 밖의 값은 아직 짐작.
# B2_SURFACELINEMARK.Kind
LINE_KIND = {
    "501": "중앙선", "5011": "중앙선",       # 중앙선, 가변 중앙선
    "502": "차선",                           # 유턴구역선
    "503": "차선", "504": "버스전용차선", "505": "차도경계",   # 차선, 버스전용, 길가장자리구역선
    "506": "차선", "515": "주정차금지선",      # 진로변경제한선, 주정차금지선(가장자리 황색) — 여의도 B2 에서 확인(515=111 황색실선)
    "525": "유도선", "530": "정지선", "531": "안전지대", "535": "차선",
    "599": "기타노면",
}
# B2_SURFACELINEMARK.Type  — 백의 자리 색(1 황 2 백 3 청), 십의 자리 겹(1 단 2 겹), 일의 자리 모양(1 실 2 점 3 좌점혼 4 우점혼)
COLOR = {"1": "황색", "2": "백색", "3": "청색"}
# B3_SURFACEMARK.Kind
MARK_KIND = {
    # 여의도 B3 dbf(2026-11-04)에서 본 값: 524 5321 534 5371 5372 5373 5381 5382 5383 5391 5392 5431 5432 …, Type 1·5
    # 번호는 도로교통법 시행규칙 별표6 노면표시 번호와 같은 체계(532 횡단보도, 534 자전거횡단도, 537~539 진행방향 …)
    "5321": "횡단보도", "532": "횡단보도", "533": "횡단보도",
    "534": "자전거횡단도",
    "524": "정차금지지대",
    "5371": "화살표", "5372": "화살표", "5373": "화살표", "5374": "화살표", "5379": "화살표",
    "5381": "화살표", "5382": "화살표", "5383": "화살표", "5391": "화살표", "5392": "화살표",
    "531": "안전지대",
}
ARROW_WORD = {"5371": "직진", "5372": "좌회전", "5373": "우회전", "5374": "직진좌회전", "5379": "직진우회전",
              "5381": "좌우회전", "5382": "유턴", "5383": "직진유턴", "5391": "좌회전유턴", "5392": "우회전유턴"}


def _fixname(info):
    """윈도우에서 만든 zip 은 한글 이름이 cp949 인데 표시 플래그가 없어서 깨져 읽힌다."""
    n = info.filename
    if info.flag_bits & 0x800:
        return n
    try:
        return n.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return n


def _shp_bbox(raw):
    """shp 머리글 100바이트 안의 범위 (xmin, ymin, xmax, ymax)."""
    import struct
    return struct.unpack("<4d", raw[36:68])


def _sections(z):
    """zip 안의 'UTMK_정표고' 폴더들(품질검사 폴더 제외) → {폴더: {층 이름: zip 안 경로 접두}}"""
    out = {}
    for info in z.infolist():
        n = _fixname(info).replace("\\", "/")
        if "품질검사" in n:
            continue
        parts = n.split("/")
        if len(parts) < 2 or parts[-2] != WANT_DIR:
            continue
        stem, ext = os.path.splitext(parts[-1])
        if ext.lower() not in (".shp", ".shx", ".dbf", ".cpg", ".prj"):
            continue
        d = "/".join(parts[:-1])
        out.setdefault(d, {}).setdefault(stem, {})[ext.lower()] = info.filename
    return out


def build_index(folder, log=print):
    """폴더 안 zip 들의 구역 범위 색인. 바뀐 zip 만 다시 잰다."""
    ip = os.path.join(folder, INDEX_NAME)
    try:
        old = json.load(open(ip, encoding="utf-8"))
    except Exception:
        old = {}
    new, changed = {}, 0
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".zip"):
            continue
        p = os.path.join(folder, fn)
        st = os.stat(p)
        key = f"{st.st_size}:{int(st.st_mtime)}"
        if fn in old and old[fn].get("key") == key:
            new[fn] = old[fn]
            continue
        changed += 1
        secs = []
        try:
            with zipfile.ZipFile(p) as z:
                for d, layers in _sections(z).items():
                    bb = None
                    for name, files in layers.items():
                        if ".shp" not in files:
                            continue
                        with z.open(files[".shp"]) as f:
                            b = _shp_bbox(f.read(100))
                        if not np.all(np.isfinite(b)) or b[2] <= b[0]:
                            continue
                        bb = b if bb is None else (min(bb[0], b[0]), min(bb[1], b[1]), max(bb[2], b[2]), max(bb[3], b[3]))
                    if bb:
                        secs.append(dict(dir=d, bbox=bb, layers={k: v for k, v in layers.items() if ".shp" in v}))
        except zipfile.BadZipFile:
            log(f"   정밀도로지도: {fn} 은 zip 이 깨져 있어 건너뜀")
        new[fn] = dict(key=key, sections=secs)
    if changed or set(old) != set(new):
        try:
            json.dump(new, open(ip, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass
        n = sum(len(v["sections"]) for v in new.values())
        log(f"   정밀도로지도 색인: zip {len(new)}개, 구역 {n}개 (새로 잰 zip {changed}개)")
    return new


def _to_target_bbox(bbox, dst_epsg=5186):
    """모형 범위(수치지도 좌표) → 정밀도로지도 좌표(UTM-K) 범위"""
    from pyproj import Transformer
    tr = Transformer.from_crs(dst_epsg, SRC_EPSG, always_xy=True)
    xs, ys = [], []
    for x in np.linspace(bbox[0], bbox[2], 5):
        for y in np.linspace(bbox[1], bbox[3], 5):
            a, b = tr.transform(x, y)
            xs.append(a); ys.append(b)
    return min(xs), min(ys), max(xs), max(ys)


def _read_layer(z, files, qbox):
    import shapefile
    enc = "cp949"
    if ".cpg" in files:
        try:
            c = z.read(files[".cpg"]).decode("ascii", "ignore").strip().lower()
            enc = "utf-8" if "utf" in c else ("cp949" if ("euc" in c or "949" in c) else (c or enc))
        except Exception:
            pass
    shp = io.BytesIO(z.read(files[".shp"]))
    shx = io.BytesIO(z.read(files[".shx"])) if ".shx" in files else None
    dbf = io.BytesIO(z.read(files[".dbf"]))
    r = shapefile.Reader(shp=shp, shx=shx, dbf=dbf, encoding=enc, encodingErrors="replace")
    names = [f[0] for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        s = sr.shape
        if s.shapeType == 0:
            continue
        if hasattr(s, "bbox"):
            b = s.bbox
            if b[2] < qbox[0] or b[0] > qbox[2] or b[3] < qbox[1] or b[1] > qbox[3]:
                continue
        else:
            x, y = s.points[0][:2]
            if not (qbox[0] <= x <= qbox[2] and qbox[1] <= y <= qbox[3]):
                continue
        try:
            g = shape(s.__geo_interface__)
        except Exception:
            continue
        g = _drop_z(g)
        if g.is_empty:
            continue
        out.append((g, dict(zip(names, sr.record))))
    return out


def _drop_z(g):
    from shapely import force_2d
    return force_2d(g)


def _code(v):
    if v is None:
        return ""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def _classify(layer, g, a):
    """(kind, attrs) 또는 None"""
    A = {k.lower(): v for k, v in a.items()}
    kind = _code(A.get("kind"))
    typ = _code(A.get("type"))
    if layer == "B2_SURFACELINEMARK":
        k = LINE_KIND.get(kind)
        if k is None:
            k = "기타노면"
        col = COLOR.get(typ[:1], "백색") if len(typ) >= 3 else ("황색" if k == "중앙선" else "백색")
        double = len(typ) >= 3 and typ[1] == "2"
        dash = len(typ) >= 3 and typ[2] in "234"
        return k, dict(code=kind, type=typ, color=col, double=double, dashed=dash, mixed=typ[2:3] in ("3", "4"))
    if layer == "B3_SURFACEMARK":
        k = MARK_KIND.get(kind)
        if k is None:
            k = _guess_mark(g)
        return k, dict(code=kind, type=typ, arrow=ARROW_WORD.get(kind))
    if layer == "A3_DRIVEWAYSECTION":
        return "차도면", dict(code=kind)
    if layer == "A2_LINK":
        return "차로중심", dict(code=kind, lanes=A.get("laneno") or A.get("lane"))
    if layer == "C1_TRAFFICLIGHT":
        return "신호등", dict(code=kind, type=typ)
    if layer == "C4_SPEEDBUMP":
        return "과속방지턱", dict(code=kind)
    if layer == "B1_SAFETYSIGN":
        return "표지", dict(code=kind, type=typ)
    if layer == "C3_VEHICLEPROTECTIONSAFETY":
        return "방호울타리", dict(code=kind, type=typ)
    if layer == "C6_POSTPOINT":
        return "지주", dict(code=kind, type=typ)
    return None


def _guess_mark(g):
    """코드표에 없는 면 표시: 모양으로 짐작(길쭉한 큰 사각형 = 횡단보도, 가늘고 5m 안팎 = 화살표)."""
    if g.geom_type not in ("Polygon", "MultiPolygon"):
        return "기타노면"
    r = g.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    a, b = sorted([np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[1])])
    fill = g.area / max(r.area, 1e-6)
    if a >= 2.5 and b >= 4.0 and fill > 0.8:
        return "횡단보도"
    if 3.0 <= b <= 7.0 and a <= 1.6:
        return "화살표"
    return "기타노면"


def find_default_dir():
    """폴더를 따로 정하지 않았을 때: 흔한 자리에서 '정밀도로지도' zip 이 든 폴더를 찾는다(한 단계 아래까지).
    찾으면 그 경로를 돌려준다. 사용자 PC 에 이미 받아 둔 자료를 쓰는 것이고, 프로그램에는 자료를 넣지 않는다."""
    home = os.path.expanduser("~")
    roots = [os.path.join(home, d) for d in ("Downloads", "다운로드", "Desktop", "바탕 화면", "Documents", "문서")]
    roots += [os.path.join(home, "contour_ai_data", "정밀도로지도")]
    if os.name == "nt":
        roots += [f"{c}:\\" for c in "DEFGH" if os.path.isdir(f"{c}:\\")]
    pat = re.compile(r"정밀도로지도.*\.zip$", re.I)

    def has(d):
        try:
            return any(pat.search(f) for f in os.listdir(d))
        except OSError:
            return False
    for r in roots:
        if not os.path.isdir(r):
            continue
        if has(r):
            return r
        try:
            subs = [os.path.join(r, d) for d in os.listdir(r)]
        except OSError:
            continue
        for d in subs:
            if os.path.isdir(d) and has(d):
                return d
    return None


def load(folder, bbox, dst_epsg=5186, log=print, margin=20.0):
    """모형 범위의 정밀도로지도 표시 → [dict(kind, geom, attrs)] (좌표는 아직 UTM-K, align 이 바꿈)"""
    if not folder:
        folder = find_default_dir()
        if folder:
            log(f"   정밀도로지도 폴더를 찾음: {folder}")
    if not folder or not os.path.isdir(folder):
        log("   정밀도로지도 폴더가 없어 항공사진·추정으로 노면표시를 그림 "
            "(국토지리정보원 map.ngii.go.kr 에서 정밀도로지도를 받아 한 폴더에 두면 더 정확해짐)")
        return []
    idx = build_index(folder, log)
    q = _to_target_bbox((bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin), dst_epsg)
    qb = box(*q)
    feats, seen, used = [], set(), []
    for fn, ent in idx.items():
        for sec in ent["sections"]:
            if not box(*sec["bbox"]).intersects(qb):
                continue
            used.append(f"{fn}:{sec['dir'].split('/')[0]}")
            try:
                with zipfile.ZipFile(os.path.join(folder, fn)) as z:
                    # 색인에 shp 만 적었으므로 같은 폴더의 다른 확장자 파일을 다시 찾는다
                    files = _sections(z).get(sec["dir"], {})
                    for layer in READ_LAYERS:
                        if layer not in files or ".dbf" not in files[layer]:
                            continue
                        for g, a in _read_layer(z, files[layer], q):
                            c = _classify(layer, g, a)
                            if c is None:
                                continue
                            kind, attrs = c
                            # 자치구 파일과 시범지구 파일이 겹치면 같은 표시가 두 번 — 종류+대표점+길이로 하나만
                            p = g.representative_point()
                            key = (kind, round(p.x, 1), round(p.y, 1), round(g.length, 0))
                            if key in seen:
                                continue
                            seen.add(key)
                            attrs["layer"] = layer
                            attrs["raw"] = {k: _code(v) for k, v in a.items()}
                            feats.append(dict(kind=kind, geom=g, attrs=attrs))
            except Exception as e:
                log(f"   정밀도로지도: {fn} 읽기 실패 ({type(e).__name__}: {e})")
    if used:
        cnt = {}
        for f in feats:
            cnt[f["kind"]] = cnt.get(f["kind"], 0) + 1
        log(f"   정밀도로지도: {len(used)}개 구역에서 " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())))
    else:
        log("   정밀도로지도: 이 범위를 담은 파일이 폴더에 없음 (항공사진·추정으로 그림)")
    return feats


# ---------------------------------------------------------------- 표시 → 칠 면
def to_paint(features, road, log=print):
    """맞춘 정밀도로지도 표시 → [(Polygon, 종류, '정밀도로지도')] (road_details 의 marks 형식)"""
    from shapely.ops import substring
    from .marking_assets import SPEC, arrow
    from .road_crosswalks import _zebra
    out = []
    w = SPEC["차선_폭"]
    lanes = [f["geom"] for f in features if f["kind"] in ("차선", "중앙선", "차도경계", "버스전용차선")]
    for f in features:
        k, g, a = f["kind"], f["geom"], f.get("attrs", {})
        if k in ("차선", "중앙선", "차도경계", "주정차금지선", "버스전용차선", "유도선", "기타노면") and "Line" in g.geom_type:
            col = a.get("color")
            if k == "중앙선":
                name = "중앙선"
            elif k == "버스전용차선" or col == "청색":
                name = "버스전용차선"
            elif col == "황색":
                name = "주정차금지선"        # 가장자리 황색 실선·점선
            elif k == "차도경계":
                name = "가장자리선"
            else:
                name = "차선"
            for ln in getattr(g, "geoms", [g]):
                offs = [-(w + SPEC["중앙선_겹간격"]) / 2, (w + SPEC["중앙선_겹간격"]) / 2] if a.get("double") else [0.0]
                for k_, o in enumerate(offs):
                    L = ln if o == 0 else ln.offset_curve(o)
                    if L.is_empty or L.length < 0.3:
                        continue
                    # 겹선 좌·우 점혼선: 한쪽만 점선
                    dashed = a.get("dashed") and (not a.get("mixed") or (k_ == 0) == (a.get("type", "")[2:3] == "3"))
                    pieces = [L]
                    if dashed:
                        pieces = [substring(L, s, min(s + SPEC["차선_칠"], L.length))
                                  for s in np.arange(0, L.length, SPEC["차선_칠"] + SPEC["차선_틈"])]
                    for p in pieces:
                        if p.length > 0.2:
                            out.append((p.buffer(w / 2, cap_style=2), name, "정밀도로지도"))
        elif k == "정지선" and "Line" in g.geom_type:
            out.append((g.buffer(SPEC["정지선_폭"] / 2, cap_style=2), "정지선", "정밀도로지도"))
        elif k == "횡단보도" and g.geom_type in ("Polygon", "MultiPolygon"):
            for pg in getattr(g, "geoms", [g]):
                out += [(b, kk, "정밀도로지도") for b, kk in _zebra_of(pg, lanes, road, _zebra)]
        elif k == "화살표" and g.geom_type in ("Polygon", "MultiPolygon"):
            out.append((g, "화살표", "정밀도로지도"))          # 정밀도로지도는 화살표 모양 자체를 담고 있음
        elif k == "정차금지지대" and g.geom_type in ("Polygon", "MultiPolygon"):
            # 노란 테두리만(면 전체를 칠하지 않음)
            ring = g.difference(g.buffer(-0.3))
            if not ring.is_empty:
                out.append((ring, "주정차금지선", "정밀도로지도"))
        elif k == "자전거횡단도" and g.geom_type in ("Polygon", "MultiPolygon"):
            ring = g.difference(g.buffer(-0.3))
            if not ring.is_empty:
                out.append((ring, "노면표시", "정밀도로지도"))
        elif k in ("안전지대", "노면문자", "기타노면") and g.geom_type in ("Polygon", "MultiPolygon"):
            # 코드표에 없는 면: 글자·기호처럼 작으면 모양 그대로, 크면 테두리만
            if k == "안전지대" or g.area < 15:
                out.append((g, "노면표시" if k == "기타노면" else k, "정밀도로지도"))
            else:
                ring = g.difference(g.buffer(-0.3))
                if not ring.is_empty:
                    out.append((ring, "노면표시", "정밀도로지도"))
    return out


def _zebra_of(pg, lanes, road, zebra):
    """횡단보도 바깥선 → 규격 줄무늬. 줄 방향(u)은 가까운 차선 방향, 없으면 사각형 짧은 변."""
    r = pg.minimum_rotated_rectangle
    c = np.asarray(r.exterior.coords)[:4]
    e0, e1 = c[1] - c[0], c[2] - c[1]
    long_ = e0 if np.linalg.norm(e0) >= np.linalg.norm(e1) else e1
    nrm = long_ / np.linalg.norm(long_)                 # 건너는 방향
    ctr = pg.centroid
    near = [l for l in lanes if l.distance(ctr) < 15]
    if near:
        l = min(near, key=lambda l: l.distance(ctr))
        s = l.project(ctr)
        p0, p1 = l.interpolate(max(s - 1, 0)), l.interpolate(min(s + 1, l.length))
        d = np.array([p1.x - p0.x, p1.y - p0.y])
        if np.linalg.norm(d) > 1e-6:
            d /= np.linalg.norm(d)
            # 차선이 건너는 방향과 거의 수직이면 그 방향을 줄 방향으로
            if abs(d @ nrm) < 0.5:
                nrm = np.array([-d[1], d[0]])
    u = np.array([-nrm[1], nrm[0]])
    return zebra(pg if pg.geom_type == "Polygon" else r, u, nrm, road)


# ---------------------------------------------------------------- 점검 도구 (사용자 PC 에서 실행)
def inspect(folder, out_txt=None, max_vals=40):
    """폴더의 zip 들에서 층별 필드 이름과 Kind/Type 값별 개수만 적는다(좌표·도형은 적지 않음)."""
    import shapefile
    from collections import Counter
    lines = []
    idx = build_index(folder, log=lambda m: lines.append(m))
    for fn, ent in idx.items():
        if not ent["sections"]:
            continue
        sec = ent["sections"][0]
        lines.append(f"\n== {fn} / {sec['dir']}")
        with zipfile.ZipFile(os.path.join(folder, fn)) as z:
            files = _sections(z).get(sec["dir"], {})
            for layer in sorted(files):
                fl = files[layer]
                if ".dbf" not in fl:
                    continue
                r = shapefile.Reader(dbf=io.BytesIO(z.read(fl[".dbf"])), encoding="cp949", encodingErrors="replace")
                names = [f[0] for f in r.fields[1:]]
                lines.append(f"[{layer}] {len(r)}개  필드: {', '.join(names)}")
                for col in names:
                    if col.lower() in ("kind", "type", "subtype", "linetype", "laneno", "roadrank", "roadtype"):
                        cnt = Counter(_code(rec[col]) for rec in r.iterRecords(fields=[col]))
                        top = ", ".join(f"{v}:{n}" for v, n in cnt.most_common(max_vals))
                        lines.append(f"   {col}: {top}")
        break       # 구성 확인은 zip 하나면 충분
    txt = "\n".join(lines)
    if out_txt:
        open(out_txt, "w", encoding="utf-8").write(txt)
    return txt


if __name__ == "__main__":
    import sys
    print(inspect(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))

"""도로 디테일 자료 파일 읽기: 횡단보도·신호등·전봇대·버스정류장 표(CSV·엑셀) → 좌표.
"""
import math
import os

import numpy as np


# ---------------------------------------------------------------- 자료 파일
def _kind_of_table(path, cols):
    c = " ".join(map(str, cols)) + " " + os.path.basename(path)
    if "정류소" in c or "정류장" in c:
        return "busstop"
    if "횡단보도" in c:
        return "crosswalk"
    if "신호등" in c or "신호기" in c:
        return "signal"
    if "전주" in c or "전봇대" in c:
        return "pole"
    return None


def read_files(files, bbox, crs_wkt, log=print):
    """자료 파일들 → {"crosswalk": [(x, y, 폭, 연장)], "signal": [(x, y, 종류)], "pole": [(x, y)]}"""
    from pyproj import Transformer
    from .streetlights import _lonlat_cols, read_table
    out = {"crosswalk": [], "signal": [], "pole": [], "busstop": []}
    if not files or not crs_wkt:
        return out
    tf = Transformer.from_crs("EPSG:4326", crs_wkt, always_xy=True)
    for f in files:
        try:
            df = read_table(f)
        except Exception as e:
            log(f"   도로시설 자료를 읽지 못함 ({os.path.basename(f)}): {e}")
            continue
        kind = _kind_of_table(f, df.columns)
        lat, lon = _lonlat_cols(df.columns)
        if lat is None or lon is None:     # 서울시 정류소 자료처럼 X좌표·Y좌표 칸에 경도·위도가 든 경우
            xc = next((c for c in df.columns if str(c).strip().upper() in ("X좌표", "X", "GPS_X", "XCODE")), None)
            yc = next((c for c in df.columns if str(c).strip().upper() in ("Y좌표", "Y", "GPS_Y", "YCODE")), None)
            if xc is not None and yc is not None:
                try:
                    if 124 < float(df[xc].astype(float).median()) < 132:
                        lon, lat = xc, yc
                except (TypeError, ValueError):
                    pass
        if not kind or lat is None or lon is None:
            log(f"   {os.path.basename(f)}: 횡단보도·신호등·전주 자료인지, 위도·경도 칸이 있는지 알 수 없어 건너뜀")
            continue
        la = np.asarray(df[lat], float)
        lo = np.asarray(df[lon], float)
        ok = np.isfinite(la) & np.isfinite(lo) & (la > 33) & (la < 39) & (lo > 124) & (lo < 132)
        x, y = tf.transform(lo[ok], la[ok])
        inb = (x >= bbox[0]) & (x <= bbox[2]) & (y >= bbox[1]) & (y <= bbox[3])
        sub = df[ok][inb]
        x, y = x[inb], y[inb]

        def col(name, d):
            c = next((c for c in df.columns if name in str(c)), None)
            return [d] * len(x) if c is None else list(sub[c])
        if kind == "crosswalk":
            for xi, yi, w, L in zip(x, y, col("횡단보도폭", 4.0), col("횡단보도연장", 0.0)):
                out[kind].append((float(xi), float(yi), _f(w, 4.0), _f(L, 0.0)))
        elif kind == "signal":
            for xi, yi, k in zip(x, y, col("신호등구분", "차량")):
                out[kind].append((float(xi), float(yi), "보행자" if "보행" in str(k) else "차량"))
        elif kind == "busstop":
            nm = next((c for c in df.columns if "정류" in str(c) and "명" in str(c)), None)
            names = list(sub[nm]) if nm is not None else [""] * len(x)
            out[kind] += [(float(xi), float(yi), str(n)) for xi, yi, n in zip(x, y, names)]
        else:
            out[kind] += [(float(xi), float(yi)) for xi, yi in zip(x, y)]
        log(f"   {os.path.basename(f)}: 범위 안 {len(x)}개 ({ {'crosswalk': '횡단보도', 'signal': '신호등', 'pole': '전봇대', 'busstop': '버스 정류장'}[kind]})")
    # 신호등 표준데이터는 등 하나가 한 줄 → 1.5m 안은 기둥 하나로
    out["signal"] = _dedupe(out["signal"], 1.5)
    out["pole"] = _dedupe(out["pole"], 1.0)
    out["busstop"] = _dedupe(out["busstop"], 3.0)
    return out


def _f(v, d):
    try:
        v = float(v)
        return v if math.isfinite(v) and v > 0 else d
    except (TypeError, ValueError):
        return d


def _dedupe(pts, gap):
    if not pts:
        return pts
    from scipy.spatial import cKDTree
    P = np.array([(p[0], p[1]) for p in pts])
    tr = cKDTree(P)
    alive = np.ones(len(P), bool)
    out = []
    for i in range(len(P)):
        if not alive[i]:
            continue
        for j in tr.query_ball_point(P[i], gap):
            if j > i:
                alive[j] = False
        out.append(pts[i])
    return out

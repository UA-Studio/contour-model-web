"""가로수 대장: 지자체·산림청이 관리하는 실제 가로수 위치·수종.

내장 자료 (앞의 것이 우선, 같은 나무(2m 안)는 한 번만):
1. 서울시 가로수 위치 정보(경도, 위도) 2026 — 서울 열린데이터광장 OA-22904,
   서울특별시(25개 자치구·중부공원여가센터·서울시설공단), 공공누리 제4유형(출처표시·상업적 이용금지·변경금지).
   data/seoul_street_trees_2026.npz
2. 산림청 도시숲가로수관리 가로수 현황 2025-08-26 — 공공데이터포털, 산림청.
   서울 밖 88개 시군구, 흉고직경 포함. data/korea_street_trees_2025.npz
   원본 좌표(EPSG:5186/5179)를 경위도로 바꿔 담았다. 걸러 낸 것:
   좌표가 비었거나 우리나라 밖인 것, 같은 시군구 나무들에서 30km 넘게 떨어진 것,
   한 점에 3그루 넘게 겹쳐 찍힌 것(나무 자리가 아니라 구간 대표점), 같은 점 2건은 1그루로.
원본의 좌표·수종은 그대로 두고 형식만 압축했다. '공분'(빈 식재 자리)은 나무로 세우지 않는다.

새 자료가 나오면:  python -m contour_ai.tree_registry 새파일.xlsx   (서울)
                    python -m contour_ai.tree_registry 새파일.csv    (산림청)
"""
import os
import sys

import numpy as np

_D = os.path.join(os.path.dirname(__file__), "data")


def _pick(name):
    """작게 묶은 .npz.xz 가 있으면 그것(v1.16), 없으면 .npz"""
    xz = os.path.join(_D, name + ".xz")
    return xz if os.path.exists(xz) else os.path.join(_D, name)


DATA = _pick("seoul_street_trees_2026.npz")
FOREST = _pick("korea_street_trees_2025.npz")
SOURCE = "서울시 가로수 위치 정보(경도, 위도) 2026, 서울 열린데이터광장 OA-22904 (공공누리 제4유형)"
SOURCE_FOREST = "산림청 도시숲가로수관리 가로수 현황 2025-08-26, 공공데이터포털"
DATASETS = [(DATA, "서울시 가로수 대장"), (FOREST, "산림청 가로수 현황")]
EMPTY = ("공분",)

# 수종 → 다 자란 가로수 수관 가로 반지름(m). 표에 없으면 2.5
CROWN_R = {
    "양버즘나무": 4.0, "버즘나무": 4.0, "플라타너스": 4.0, "느티나무": 3.5, "회화나무": 3.0, "대왕참나무": 3.0,
    "루브라참나무": 3.0, "칠엽수": 3.0, "팽나무": 3.2, "벚나무": 3.0, "산벚나무": 3.0, "겹벚나무": 2.5,
    "벚나무류": 3.0, "왕벚나무": 3.0, "튜울립나무": 3.0, "튤립나무": 3.0, "백합나무": 3.0, "목백합": 3.0,
    "참나무류": 3.0, "참나무": 3.0, "상수리나무": 3.2, "느릅나무": 3.0, "수양버들": 3.5, "능수버들": 3.5,
    "버드나무": 3.2, "미루나무": 2.5, "포플러": 2.5, "이태리포플러": 2.5, "오동나무": 3.0, "벽오동": 2.5,
    "은행나무": 2.2, "은행나무 암나무": 2.2, "이팝나무": 2.5, "중국단풍": 2.5, "단풍나무": 2.3, "복자기": 2.2,
    "복자기나무": 2.2, "청단풍": 2.2, "당단풍": 2.2, "계수나무": 2.5, "은단풍": 3.0, "청단풍나무": 2.2,
    "공작단풍": 1.5, "칠자화": 2.2, "모감주나무": 2.2, "산딸나무": 2.0, "노각나무": 2.0, "쉬나무": 2.5,
    "소나무": 2.0, "곰솔(해송)": 2.2, "기타소나무": 2.0, "둥근소나무": 1.5, "반송": 1.6, "금강송": 1.8,
    "잣나무류": 1.8, "잣나무": 1.8, "스트로브잣나무": 2.0, "섬잣나무": 1.5, "메타세쿼이아": 1.8, "낙우송": 1.8,
    "히말라야시다": 2.0, "개잎갈나무": 2.0, "잎갈나무": 1.8, "전나무(젓나무)": 1.8, "삼나무": 1.6,
    "편백나무": 1.6, "주목": 1.2, "비자나무": 1.5, "침엽수": 1.8,
    "자작나무": 1.8, "향나무": 1.2, "측백나무": 1.2, "선주목": 1.0, "코니카가문비": 0.8,
    "라일락": 1.3, "무궁화": 1.2, "무궁화나무": 1.2, "배롱나무": 1.5, "배롱나무(목백일홍)": 1.5,
    "백일홍": 1.5, "산수유": 1.6, "박태기": 1.2, "박태기나무": 1.2, "감나무": 2.2, "고욤나무": 2.2,
    "꽃사과나무": 1.8, "꽃사과": 1.8, "살구나무": 2.0, "마가목": 1.6, "매화나무": 1.8,
    "매실나무(매화나무)": 1.8, "모과나무": 1.8, "꽃복숭아": 1.6, "만첩홍도(꽃복숭아)": 1.6,
    "목련": 2.0, "백목련": 2.0, "팥배나무": 2.0, "낙상홍": 1.0, "때죽나무": 1.8, "가죽나무": 2.5,
    "산사나무": 1.6, "귀룽나무": 2.2, "야광나무": 1.8, "대추나무": 1.8, "보리수": 1.5, "층층나무": 2.2,
    "후박나무": 2.5, "먼나무": 2.0, "구실잣밤나무": 3.0, "구실밤나무": 3.0, "가시나무류": 2.5,
    "종가시나무": 2.5, "녹나무": 3.0, "아왜나무": 1.8, "동백나무": 1.5, "광나무": 1.5, "홍가시나무": 1.2,
    "종려나무": 1.2, "워싱턴야자": 1.8, "자귀나무": 2.5, "아까시나무": 2.5, "아카시나무": 2.5,
    "물푸레나무": 2.5, "두충나무": 2.0, "헛개나무": 2.5, "무화과": 1.8, "뽕나무": 2.0, "밤나무": 3.0,
    "호도나무": 3.0, "쉬땅나무(개쉬땅나무)": 1.0, "화살나무": 0.8, "사철나무": 0.8, "싸리": 0.8,
}


def crown_r(species, dbh=None):
    """수관 반지름(m). 흉고직경(cm)을 알면 그 나무 크기로, 모르면 수종의 다 자란 크기로.

    도시 가로수 수관 폭 ≈ 0.4 + 0.18 × 흉고직경(cm) (m). 수종 크기의 1.3배를 넘지 않게.
    """
    r = CROWN_R.get(species, 2.5)
    if dbh and dbh > 0:
        d = min(float(dbh), 150.0)
        return float(round(max(0.8, min((0.4 + 0.18 * d) / 2, r * 1.3)), 2))
    return r


def _open(path):
    """가로수 자료 읽기. .npz.xz = 경위도를 앞 나무와의 차이로 적어 xz 로 묶은 것(값·순서는 원본과 똑같다)."""
    if not path.endswith(".xz"):
        return np.load(path, allow_pickle=False)
    import io
    import lzma
    with open(path, "rb") as fh:
        z = dict(np.load(io.BytesIO(lzma.decompress(fh.read())), allow_pickle=False))
    if "delta" in z:
        z["lat"] = np.cumsum(z["lat"], dtype=np.int64).astype(np.int32)
        z["lon"] = np.cumsum(z["lon"], dtype=np.int64).astype(np.int32)
    return _Z(z)


class _Z(dict):
    """np.load 결과처럼 .files 와 [키] 로 쓰게"""
    def __init__(self, d):
        super().__init__({k: v for k, v in d.items() if k != "files"})
        self.files = [k for k in self if k != "delta"]


def pack(npz_path, out=None):
    """.npz → .npz.xz (배포 용량 줄이기, 내용 그대로)"""
    import io
    import lzma
    d = dict(np.load(npz_path, allow_pickle=False))
    d["lat"] = np.diff(d["lat"].astype(np.int64), prepend=0).astype(np.int32)
    d["lon"] = np.diff(d["lon"].astype(np.int64), prepend=0).astype(np.int32)
    d["delta"] = np.array(1, np.uint8)
    b = io.BytesIO()
    np.savez(b, **d)
    out = out or npz_path + ".xz"
    with open(out, "wb") as fh:
        fh.write(lzma.compress(b.getvalue(), preset=9 | lzma.PRESET_EXTREME))
    return out


def _load(path, bbox, crs_wkt):
    from pyproj import Transformer
    z = _open(path)
    lon, lat = z["lon"] / 1e7, z["lat"] / 1e7
    inv = Transformer.from_crs(crs_wkt, 4326, always_xy=True)
    cx = [bbox[0], bbox[2], bbox[0], bbox[2]]
    cy = [bbox[1], bbox[1], bbox[3], bbox[3]]
    lo, la = inv.transform(cx, cy)
    m = (lon >= min(lo) - 1e-3) & (lon <= max(lo) + 1e-3) & (lat >= min(la) - 1e-3) & (lat <= max(la) + 1e-3)
    if not m.any():
        return []
    t = Transformer.from_crs(4326, crs_wkt, always_xy=True)
    x, y = t.transform(lon[m], lat[m])
    names = z["species_names"]
    sp = z["species"][m]
    dbh = z["dbh"][m] / 10.0 if "dbh" in z.files else np.zeros(int(m.sum()))
    out = []
    for xi, yi, si, di in zip(np.atleast_1d(x), np.atleast_1d(y), sp, dbh):
        name = str(names[si])
        if name in EMPTY:
            continue
        if bbox[0] <= xi <= bbox[2] and bbox[1] <= yi <= bbox[3]:
            out.append((float(xi), float(yi), name, float(di) or None))
    return out


def trees_in(bbox, crs_wkt, datasets=None):
    """모델 범위 안의 가로수. 반환: [(x, y, 수종, 흉고직경cm|None, 자료이름)] (모델 좌표계). 자료가 없으면 []"""
    out = []
    for path, label in (datasets or DATASETS):
        if not os.path.exists(path):
            continue
        new = _load(path, bbox, crs_wkt)
        if out and new:
            from scipy.spatial import cKDTree
            d, _ = cKDTree(np.array([(t[0], t[1]) for t in out])).query(np.array([(t[0], t[1]) for t in new]))
            new = [t for t, di in zip(new, d) if di > 2.0]
        out += [(x, y, s, dbh, label) for x, y, s, dbh in new]
    return out


def build_from_xlsx(xlsx, out=os.path.join(_D, "seoul_street_trees_2026.npz")):
    """서울 열린데이터광장 '서울시 가로수 위치 정보' 엑셀 → 내장 자료(npz)."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    lon, lat, sp, gu = [], [], [], []
    names, gus = {}, {}
    for ws in wb.worksheets:
        header = None
        for r in ws.iter_rows(values_only=True):
            if header is None:
                if r and "좌표(경도)" in r:
                    header = {k: i for i, k in enumerate(r)}
                continue
            try:
                lo, la = float(r[header["좌표(경도)"]]), float(r[header["좌표(위도)"]])
            except (TypeError, ValueError):
                continue
            s = str(r[header["수종"]] or "기타").strip()
            g = str(r[header["자치구"]] or ws.title).strip()
            lon.append(round(lo * 1e7)); lat.append(round(la * 1e7))
            sp.append(names.setdefault(s, len(names))); gu.append(gus.setdefault(g, len(gus)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out, lon=np.array(lon, np.int32), lat=np.array(lat, np.int32),
                        species=np.array(sp, np.uint8), gu=np.array(gu, np.uint8),
                        species_names=np.array(list(names), dtype="U16"),
                        gu_names=np.array(list(gus), dtype="U16"), source=np.array(SOURCE))
    return len(lon)


def build_from_forest_csv(path, out=os.path.join(_D, "korea_street_trees_2025.npz"), log=print):
    """산림청 「도시숲가로수관리 가로수 현황」 CSV → 내장 자료(npz). 반환: 담은 그루 수. (pandas 필요)"""
    import pandas as pd
    from pyproj import Transformer
    df = None
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            df = pd.read_csv(path, encoding=enc, dtype=str)
            break
        except UnicodeDecodeError:
            continue
    n0 = len(df)
    x = pd.to_numeric(df["지역X좌표"], errors="coerce").to_numpy()
    y = pd.to_numeric(df["지역Y좌표"], errors="coerce").to_numpy()
    lon = np.full(n0, np.nan)
    lat = np.full(n0, np.nan)
    for code in df["좌표계코드"].dropna().unique():
        m = (df["좌표계코드"] == code).to_numpy() & np.isfinite(x) & np.isfinite(y)
        try:
            t = Transformer.from_crs(str(code).strip(), 4326, always_xy=True)
        except Exception:
            continue
        lon[m], lat[m] = t.transform(x[m], y[m])
    ok = (lon > 124) & (lon < 132) & (lat > 33) & (lat < 39)
    df["lon"], df["lat"] = np.where(ok, lon, np.nan), np.where(ok, lat, np.nan)
    df = df[df["lon"].notna()].copy()
    n1 = len(df)
    # 같은 시군구 나무 무리에서 30km 넘게 떨어진 좌표 (좌표계를 잘못 적은 것 등)
    med = df.groupby("시군구명")[["lon", "lat"]].transform("median")
    far = np.hypot((df["lon"] - med["lon"]) * 88.8, (df["lat"] - med["lat"]) * 111.0) > 30
    df = df[~far.to_numpy()].copy()
    n2 = len(df)
    # 한 점(10cm 안)에 여러 건: 2건은 같은 나무를 두 번 적은 것으로 1그루, 3건 넘으면 구간 대표점이라 버림
    key = (df["lon"] * 888000).round().astype("int64").astype(str) + "," + (df["lat"] * 1110000).round().astype("int64").astype(str)
    cnt = key.map(key.value_counts())
    df = df[(cnt <= 2).to_numpy()].copy()
    df = df[~key[df.index].duplicated().to_numpy()].copy()
    n3 = len(df)
    names, gus = {}, {}
    sp = np.array([names.setdefault(str(v or "미분류").strip(), len(names)) for v in df["수종명"].fillna("미분류")], np.uint8)
    gu = np.array([gus.setdefault(str(v).strip(), len(gus)) for v in df["시군구명"].fillna("")], np.uint16)
    dbh = pd.to_numeric(df["수목흉고직경"], errors="coerce").fillna(0).clip(0, 300).to_numpy()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out, lon=np.round(df["lon"].to_numpy() * 1e7).astype(np.int32),
                        lat=np.round(df["lat"].to_numpy() * 1e7).astype(np.int32),
                        species=sp, gu=gu, dbh=np.round(dbh * 10).astype(np.uint16),
                        species_names=np.array(list(names), dtype="U16"),
                        gu_names=np.array(list(gus), dtype="U24"), source=np.array(SOURCE_FOREST))
    log(f"원본 {n0:,}건 → 좌표 정상 {n1:,} → 시군구 밖 제외 {n2:,} → 겹침 정리 {n3:,}그루, 시군구 {len(gus)}곳")
    return n3


if __name__ == "__main__":
    f = sys.argv[1]
    if f == "--pack":        # 배포용으로 작게: python -m contour_ai.tree_registry --pack 파일.npz …
        for p_ in sys.argv[2:]:
            print(pack(p_))
        sys.exit(0)
    if f.lower().endswith(".csv"):
        print(build_from_forest_csv(f, *sys.argv[2:3]), "그루")
    else:
        print(build_from_xlsx(f, *sys.argv[2:3]), "그루")

"""넣은 수치지도 살펴보기(도엽 범위·이름·개관도)와 정리한 대지 파일 저장."""
import json
import os
import re
import time


from .app_lazy import pipeline
from .app_mapview import _named_places, _overview_png
from .app_siteprep import PREP, site_info, site_prep
from .app_state import _home, _sources, _uploads


# ---------------------------------------------------------------- 입력 살펴보기
def inspect_source(path):
    """zip(하나 또는 여러 개)을 풀어 도엽 범위·이름 목록·개관도를 만든다. 여러 도엽은 한 장으로 잇는다."""
    paths = [path] if isinstance(path, str) else list(path)
    first = os.path.splitext(os.path.basename(paths[0].rstrip("/\\")))[0]
    sid = re.sub(r"[^A-Za-z0-9]+", "_", first).strip("_")[:40] + (f"_{len(paths)}" if len(paths) > 1 else "") + f"_{int(time.time())}"
    work = os.path.join(_uploads(), sid)
    os.makedirs(work, exist_ok=True)
    msgs = []
    folder = pipeline.prepare_source(paths if len(paths) > 1 else paths[0], work, log=msgs.append)
    path = paths[0] if len(paths) == 1 else f"{os.path.basename(paths[0])} 외 {len(paths) - 1}개"
    nsheet = 1
    sj = os.path.join(folder, "sheets.json")
    if os.path.exists(sj):
        nsheet = len(json.load(open(sj, encoding="utf-8"))["sheets"])
    crs = pipeline.read_crs(folder)
    sheet = pipeline.sheet_bounds(folder)
    names = _named_places(folder)
    _overview_png(folder, sheet, os.path.join(work, "overview.png"))
    lat, lon = pipeline.to_latlon(crs, (sheet[0] + sheet[2]) / 2, (sheet[1] + sheet[3]) / 2)
    _sources()[sid] = dict(zip=path, folder=folder, work=work, paths=[os.path.abspath(p) for p in paths], file=os.path.basename(path))
    files = []
    for p in paths:
        m = re.search(r"(?<!\d)(\d{8})(?!\d)", os.path.basename(p.rstrip("/\\")))
        files.append(dict(name=os.path.basename(p.rstrip("/\\")), sheet_no=m.group(1) if m else None))
    nos = [f["sheet_no"] for f in files if f["sheet_no"]]
    label = ("도엽" + "·".join(nos)) if nos else os.path.splitext(files[0]["name"])[0]
    return dict(id=sid, file=os.path.basename(path), files=files, label=label, crs=crs.name, sheet=list(sheet), sheets=nsheet,
                size=[round(sheet[2] - sheet[0]), round(sheet[3] - sheet[1])],
                center_latlon=[round(lat, 5), round(lon, 5)], names=names)


def site_save(src, path, roles):
    """고른 역할대로 표준 대지 파일을 저장하고, 그 파일을 대지 경계로 읽어 돌려준다"""
    from . import sitefile
    if (src["folder"], path) not in PREP:
        site_prep(src, path)
    an = PREP[(src["folder"], path)]
    msgs = []
    std = sitefile.build_standard(an, roles, log=msgs.append)
    outdir = os.path.join(_home(), "대지표준")
    os.makedirs(outdir, exist_ok=True)
    stem = re.sub(r'[\\/:*?"<>|]+', "_", os.path.splitext(os.path.basename(path))[0])
    out = os.path.join(outdir, (stem[:-3] if stem.endswith("_표준") else stem) + "_표준.dxf")
    info = sitefile.write_standard(std, out, an, roles, os.path.basename(path), log=msgs.append)
    v = site_info(src, path=out)
    return dict(v, saved=out, info=info, notes=[m.strip() for m in msgs])

"""화면의 '만들기' 요청 → 작업 설정 만들기와 확인 (입력 파일, 공개 서버 제한·하루 횟수)."""
import os
import re
import threading
import time


from .app_util import _inside
from . import app_state as S
from .app_state import _home, _sess, _sources, _state
from .app_sessions import _drop_old_results, _make_room
from .app_jobs import Job


OUTPUT_KINDS = ("rhino", "laser", "print", "sketchup", "plan", "photo")


def _outputs(v):
    """받을 파일 (v2.2): 화면에서 고른 것만. 없으면(옛 화면·명령줄) 전부."""
    if not isinstance(v, (list, tuple)):
        return None
    return [k for k in OUTPUT_KINDS if k in v]


def start_job(q):
    with S.LOCK:
        if _state()["job"] and not _state()["job"].done:
            raise ValueError("이미 만들고 있는 모델이 있습니다. 끝난 뒤 다시 눌러 주세요.")
        if q.get("source") not in _sources():
            raise ValueError("먼저 수치지도 zip 파일을 넣어 주세요.")
        size = [float(q.get("w") or 500), float(q.get("h") or q.get("w") or 500)]
        shape = "circle" if q.get("shape") == "circle" else "square"
        if shape == "circle":                    # 원: 가로 = 지름 (v2.2)
            size[1] = size[0]
        stem = re.sub(r"[^\w가-힣]+", "_", q.get("label") or "모델").strip("_") or "모델"
        out = q.get("out") or os.path.join(_home(), stem[:60] + time.strftime("_%m%d_%H%M"))
        _state()["view"] = None
        params = dict(source=q["source"], out=out, size=size[0] if size[0] == size[1] else tuple(size),
                      xy=tuple(q["xy"]) if q.get("xy") else None, green=bool(q.get("green", True)),
                      year=int(q["year"]) if q.get("year") else None,
                      scale=float(q.get("scale") or 1000), interval=_interval(q.get("interval")),
                      shape=shape, outputs=_outputs(q.get("outputs")), terrain=q.get("terrain") or "combo", terrain_fit="exact" if q.get("terrain_fit") == "exact" else "smooth", floor_height=float(q.get("floor_height") or 3.3),
                      no_trees=not q.get("street_trees", True), tree_hole=float(q.get("tree_hole") or 1.0),
                      road_thickness=float(q.get("road_thickness") or 1.0), sketchup=bool(q.get("sketchup", True)),
                      vworld_key=(q.get("vworld_key") or "").strip() or None,
                      vworld_domain=(q.get("vworld_domain") or "").strip(),
                      data_key=(q.get("data_key") or "").strip() or None,
                      aerial_file=(q.get("aerial_file") or "").strip().strip('"') or None,
                      site_address=(q.get("site_address") or "").strip() or None,
                      site_file=(q.get("site_file") or "").strip().strip('"') or None,
                      pads=bool(q.get("pads", True)),
                      site_ring=q.get("site_ring") or None, site_clear=bool(q.get("site_clear")), site_keep_terrain=bool(q.get("site_keep_terrain")),
                      lights=bool(q.get("lights", True)), facade=bool(q.get("facade", True)), osm=bool(q.get("osm", True)),
                      detail=int(q.get("detail") or 3), structures=bool(q.get("structures", True)),
                      facade_radius=float(q["facade_radius"]) if q.get("facade_radius") else None,
                      light_files=tuple(x for x in [(q.get("light_file") or "").strip().strip('"')] if x),
                      road_detail=bool(q.get("road_detail", True)),
                      seoul_key=(q.get("seoul_key") or "").strip() or None,
                      safemap_key=(q.get("safemap_key") or "").strip() or None,
                      seoul_service=(q.get("seoul_service") or "").strip() or None,
                      seoul_code=(q.get("seoul_code") or "").strip() or None,
                      safemap_layer=(q.get("safemap_layer") or "").strip() or None,
                      safemap_light_layer=(q.get("safemap_light_layer") or "").strip() or None,
                      hd_dir=(q.get("hd_dir") or "").strip().strip('"') or None,
                      dem_files=[x.strip().strip('"') for x in str(q.get("dem_files") or "").replace(";", "\n").splitlines()
                                 if x.strip().strip('"')] or None,
                      road_files=tuple(x.strip().strip('"') for x in str(q.get("road_files") or "").replace(";", "\n").splitlines()
                                       if x.strip().strip('"')))
        runkey = _server_check(params, q) if S.SERVER else None
        if params["hd_dir"] and not os.path.isdir(params["hd_dir"]):
            raise ValueError(f"정밀도로지도 폴더를 찾지 못했습니다: {params['hd_dir']}")
        for rf in params["road_files"]:
            if not os.path.isfile(rf):
                raise ValueError(f"도로시설 위치 파일을 찾지 못했습니다: {rf}")
        for lf in params["light_files"]:
            if not os.path.isfile(lf):
                raise ValueError(f"가로등 위치 파일을 찾지 못했습니다: {lf}")
        for df in params["dem_files"] or ():
            if not os.path.exists(df):
                raise ValueError(f"DEM 파일을 찾지 못했습니다: {df}")
        if params["site_file"] and not os.path.isfile(params["site_file"]):
            raise ValueError(f"대지 경계 파일을 찾지 못했습니다: {params['site_file']}")
        if params["aerial_file"] and not os.path.isfile(params["aerial_file"]):
            raise ValueError(f"항공사진 파일을 찾지 못했습니다: {params['aerial_file']}")
        if S.SERVER:
            _drop_old_results(params["out"])
            _make_room(_sess()["sid"])
        job = Job(params)
        if runkey:                                # 하루 횟수는 작업이 실제로 시작될 때 센다 (입력 확인 오류는 안 셈)
            S.RUNS[runkey] = S.RUNS.get(runkey, 0) + 1
            job.runkey = runkey
        _state()["job"] = job
        if S.SERVER:
            with S.QCOND:
                S.QUEUE.append(job)
                S.QCOND.notify()
        else:
            threading.Thread(target=job.run, daemon=True).start()
        return job


def _server_check(params, q):
    """공개 서버에서 받는 작업 확인: PC 경로·정밀도로지도 금지, 크기 제한, 인증키는 서버 것만."""
    root = _home()
    params["hd_dir"] = None                       # 정밀도로지도는 재배포 금지 자료라 서버에서 쓰지 않는다
    for k in ("aerial_file", "site_file"):
        if params[k] and not _inside(params[k], root):
            raise ValueError("이 사이트에서는 직접 올린 파일만 쓸 수 있습니다.")
    for k in ("road_files", "light_files"):
        params[k] = tuple(x for x in params[k] if _inside(x, root))
    params["dem_files"] = [x for x in (params["dem_files"] or []) if _inside(x, root)] or None
    params["out"] = os.path.join(root, os.path.basename(params["out"]))
    size = params["size"] if isinstance(params["size"], tuple) else (params["size"], params["size"])
    if max(size) > S.LIMITS["max_side"]:
        raise ValueError(f"이 사이트에서는 한 변 {S.LIMITS['max_side']:.0f}m 까지 만들 수 있습니다. 범위를 줄여 주세요.")
    params.update(S.SERVER_KEYS)
    with S.QCOND:
        if len(S.QUEUE) >= S.LIMITS["max_queue"]:
            raise ValueError("지금 기다리는 사람이 많습니다. 잠시 뒤 다시 눌러 주세요.")
    ip, today = q.get("_ip") or "?", time.strftime("%Y%m%d")
    if getattr(S._CTX, "dev", False):              # 개발자(관리자 토큰 쿠키): 하루 횟수 제한 없음
        return None
    used = S.RUNS.get((ip, today), 0)
    if used >= S.LIMITS["runs_per_day"]:
        raise ValueError(f"한 사람이 하루에 {S.LIMITS['runs_per_day']}번까지 만들 수 있습니다. 내일 다시 써 주세요. "
                         "더 필요하면 오른쪽 아래 '의견 보내기'로 알려 주세요.")
    return (ip, today)


def _interval(v):
    """등고 간격(판 한 장 높이): 0.5~20m, 0.1m 단위로 맞춘다. 너무 촘촘하면 판이 수백 장이 되어 느려진다."""
    try:
        x = float(v or 5)
    except (TypeError, ValueError):
        x = 5.0
    return round(min(20.0, max(0.5, x)), 1)

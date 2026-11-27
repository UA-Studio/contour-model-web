"""내 PC 의 스케치업 프로그램 찾기와 그것으로 .skp 바로 만들기."""
import json
import os
from .skp_ruby import BUILDER_RB



def find_sketchup():
    """이 PC 의 SketchUp.exe (가장 새 판). 환경 변수 CONTOUR_SKETCHUP 로 직접 지정할 수도 있다."""
    import glob
    env = os.environ.get("CONTOUR_SKETCHUP")
    if env and os.path.isfile(env):
        return env
    found = []
    for base in {os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), r"C:\Program Files"}:
        if not base:
            continue
        found += glob.glob(os.path.join(base, "SketchUp", "SketchUp 20*", "SketchUp.exe"))
        found += glob.glob(os.path.join(base, "SketchUp", "SketchUp.exe"))
    return sorted(found)[-1] if found else None


def make_skp_with_sketchup(out_dir, log=print, timeout=3600):
    """이 PC 의 스케치업을 띄워 model_sketchup_build.json → model_sketchup.skp(2021 형식, 2021~2022 이하에서 열림)."""
    import subprocess
    import time
    build = os.path.join(out_dir, "model_sketchup_build.json")
    if os.name != "nt":
        log("   스케치업 .skp: 윈도우에서 스케치업이 깔려 있으면 자동으로 만듭니다 (지금은 건너뜀)")
        return None
    exe = find_sketchup()
    if not exe:
        log("   스케치업 .skp: 이 PC 에서 스케치업을 찾지 못했습니다. 스케치업을 연 뒤 확장 › 콘타 모델 › "
            "'콘타 모델 만들기 (.json → .skp)'로 model_sketchup_build.json 을 고르면 됩니다")
        return None
    skp = os.path.join(out_dir, "model_sketchup.skp")
    status = os.path.splitext(build)[0] + ".log"
    for f in (skp, status):
        if os.path.exists(f):
            os.remove(f)
    # 한글 경로를 스케치업 시작 인자로 넘기지 않도록, 실행 스크립트는 영문 경로(공용 폴더)에 둔다
    run_dir = os.path.join(os.environ.get("PUBLIC") or os.environ.get("TEMP") or out_dir, "contour_ai")
    os.makedirs(run_dir, exist_ok=True)
    run_rb = os.path.join(run_dir, "build_skp.rb")
    with open(run_rb, "w", encoding="utf-8") as fh:
        fh.write(BUILDER_RB + f"\nUI.start_timer(2, false) {{ ContourAI.build_from({json.dumps(build)}, true) }}\n")
    log(f"   스케치업으로 .skp 만드는 중 ({os.path.basename(os.path.dirname(exe))}). "
        "스케치업 창이 잠깐 열렸다 닫힙니다. 시작 화면이 뜨면 닫아 주세요")
    t0 = time.time()
    p = subprocess.Popen([exe, "-RubyStartup", run_rb])
    while time.time() - t0 < timeout:
        if os.path.exists(status):
            break
        if p.poll() is not None:
            break
        time.sleep(2)
    if not os.path.exists(status):
        log("   스케치업 .skp: 스케치업이 끝났는데 결과가 없습니다. 스케치업에서 확장 › 콘타 모델 › "
            "'콘타 모델 만들기 (.json → .skp)'로 model_sketchup_build.json 을 직접 열어 주세요")
        return None
    res = open(status, encoding="utf-8", errors="replace").read().strip()
    if res.startswith("ok") and os.path.exists(skp):
        kv = dict(x.split("=", 1) for x in res.split("\t")[2:] if "=" in x)
        log(f"   스케치업 파일을 만들었습니다: model_sketchup.skp (2021 형식 · 그룹 {kv.get('groups')} · 밀어 올린 면 {kv.get('ext')}"
            f" · 나무·가로등 {kv.get('instances')}개 · {kv.get('seconds')}초)")
        try:
            p.wait(60)
        except Exception:
            pass
        return skp
    log(f"   스케치업 .skp 를 만들지 못했습니다: {res[:300]}")
    return None

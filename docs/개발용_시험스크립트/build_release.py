"""배포 zip 만들기: 이전 윈도우판·프로그램판 zip 에서 contour_ai 와 사용법만 새 소스로 갈아 끼운다.
사용: python build_release.py 이전_윈도우판.zip 이전_프로그램판.zip 새버전 날짜(YYYYMMDD) 출력폴더 [--slim]
--slim: 윈도우판 내장 파이썬에서 실행에 쓰지 않는 파일을 뺀다(slim_rules.py, v1.16).
zip 은 파이썬 zipfile(UTF-8 파일 이름)로 만든다."""
import os
import sys
import zipfile

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.normpath(os.path.join(here, ".."))
old_win, old_prog, ver, day, out = sys.argv[1:6]
SLIM = "--slim" in sys.argv[6:]
sys.path.insert(0, here)
from slim_rules import drop  # noqa: E402
PY = "콘타모델_윈도우/python/"


def new_files():
    base = os.path.join(src, "contour_ai")
    for d, dirs, fs in os.walk(base):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for f in fs:
            if f.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(d, f)
            yield full, os.path.relpath(full, base).replace(os.sep, "/")


def rebuild(old, new, pkg_prefix, readme_name):
    zin = zipfile.ZipFile(old)
    zout = zipfile.ZipFile(new, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
    n_keep = n_drop = 0
    for info in zin.infolist():
        name = info.filename
        if name.startswith(pkg_prefix) or name.endswith("/" + readme_name):
            continue          # 새 소스로 바꿀 것
        if SLIM and name.startswith(PY) and drop(name[len(PY):]):
            n_drop += 1
            continue
        info.compress_type = zipfile.ZIP_DEFLATED
        zout.writestr(info, zin.read(info), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        n_keep += 1
    root = pkg_prefix.split("/")[0]
    n_new = 0
    for full, rel in new_files():
        zout.write(full, pkg_prefix + rel)
        n_new += 1
    zout.write(os.path.join(src, "README.md"), f"{root}/{readme_name}")
    zout.close()
    print(os.path.basename(new), "유지", n_keep, "뺀 것", n_drop, "새 소스", n_new, f"{os.path.getsize(new) / 1e6:.1f} MB")


os.makedirs(out, exist_ok=True)
rebuild(old_win, os.path.join(out, f"contour_model_windows_v{ver}_{day}.zip"),
        "콘타모델_윈도우/python/Lib/site-packages/contour_ai/", "사용법_전체.md")
rebuild(old_prog, os.path.join(out, f"contour_ai_program_v{ver}_{day}.zip"),
        "콘타모델_프로그램/contour_ai/", "README.md")

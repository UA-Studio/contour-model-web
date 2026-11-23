"""윈도우 다운로드판(설치 없는 버전) 묶기. GitHub Actions 의 Windows 에서 돈다.

    python windows/build_windows.py 출력폴더

지금 돌고 있는 파이썬(설치본)을 통째로 복사하고, 서버판과 같은 버전의 라이브러리
(requirements-server.txt)와 contour_ai 를 그 안 Lib/site-packages 에 넣은 뒤
'콘타모델_윈도우' 폴더 하나를 zip 으로 묶는다 (파일 이름은 UTF-8).
사용자는 파이썬을 따로 설치하지 않고 '콘타모델 실행.bat' 만 더블클릭한다.
"""
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
TOP = "콘타모델_윈도우"
# 모델 만들기에 쓰지 않는 표준 라이브러리 (용량만 차지)
SKIP_STD = {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "__pycache__", "venv",
            "pydoc_data"}
SKIP_APP = ("__pycache__", "*.pyc")
# v2.2 윈도우판은 스케치업 파일만 만든다 → Rhino(3dm) 라이브러리는 넣지 않는다 (contour_model.OUTPUTS_DEFAULT)
SKIP_PKGS = {"rhino3dm"}


def _ver():
    ns = {}
    exec((ROOT / "contour_ai" / "__init__.py").read_text(encoding="utf-8"), ns)
    return ns["__version__"], ns["__date__"]


def _skip_app(d, names):
    """contour_ai 복사 때 뺄 것: 캐시와 web/site — 웹 서버 모드(CONTOUR_SERVER=1)에서만 쓰는
    공개 사이트 소개 화면(영상·그림 약 23MB). 윈도우판 화면(web/index.html)은 이것을 읽지 않는다."""
    out = set(shutil.ignore_patterns(*SKIP_APP)(d, names))
    if Path(d).name == "web" and "site" in names:
        out.add("site")
    return out


def main(out_dir):
    assert os.name == "nt", "윈도우에서만 묶을 수 있습니다 (Actions windows-latest)"
    ver, date = _ver()
    out_dir = Path(out_dir).resolve()
    stage = out_dir / TOP
    shutil.rmtree(stage, ignore_errors=True)
    py = stage / "python"
    py.mkdir(parents=True)
    base = Path(sys.base_prefix)
    print("파이썬 원본:", base, sys.version)
    # 1) 파이썬 본체
    for f in base.iterdir():
        if f.is_file() and f.suffix.lower() in (".exe", ".dll") or f.name == "LICENSE.txt":
            shutil.copy2(f, py / f.name)
    shutil.copytree(base / "DLLs", py / "DLLs", ignore=shutil.ignore_patterns("__pycache__", "tcl*", "tk*", "_tkinter*"))
    lib = py / "Lib"
    lib.mkdir()
    for f in (base / "Lib").iterdir():
        if f.name in SKIP_STD:
            continue
        if f.is_dir():
            shutil.copytree(f, lib / f.name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(f, lib / f.name)
    # rhino3dm·manifold3d 가 찾는 C++ 실행 파일 (Windows 에 없을 때를 대비해 함께 넣음)
    for name in ("msvcp140.dll", "msvcp140_1.dll", "concrt140.dll"):
        src = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name
        if src.exists() and not (py / name).exists():
            shutil.copy2(src, py / name)
    # 2) 라이브러리 (서버판과 같은 버전)
    sp = lib / "site-packages"
    req = out_dir / "requirements-windows.txt"
    req.write_text("".join(l for l in (ROOT / "requirements-server.txt").read_text(encoding="utf-8").splitlines(True)
                           if l.split("==")[0].strip().lower() not in SKIP_PKGS), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-compile", "--no-warn-script-location",
                    "--only-binary", ":all:", "--target", str(sp), "-r", str(req)],
                   check=True)
    shutil.rmtree(sp / "bin", ignore_errors=True)
    # 2-1) 실행에 쓰지 않는 파일 덜어 내기 (C 소스·시험 폴더·안 쓰는 scipy 묶음 등, windows/slim.py)
    sys.path.insert(0, str(HERE))
    from slim import slim
    slim(sp)
    # 3) 프로그램
    shutil.copytree(ROOT / "contour_ai", sp / "contour_ai", ignore=_skip_app)
    for p in sp.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    # 4) 실행 파일·안내
    shutil.copy2(HERE / "콘타모델 실행.bat", stage / "콘타모델 실행.bat")
    txt = (HERE / "먼저_읽어주세요.txt").read_text(encoding="utf-8").replace("{VERSION}", ver).replace("{DATE}", date)
    (stage / "먼저_읽어주세요.txt").write_bytes(txt.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    shutil.copy2(ROOT / "docs" / "사용법_전체.md", stage / "사용법_전체.md")
    # 5) zip (파이썬 zipfile: 한글 파일 이름은 UTF-8 표시로 저장되어 윈도우 '모두 압축 풀기'에서 안 깨짐)
    zp = out_dir / f"contour_model_windows_v{ver}_{date.replace('-', '')}.zip"
    zp.unlink(missing_ok=True)
    t0 = time.time()
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(stage.rglob("*")):
            z.write(f, f.relative_to(out_dir).as_posix())
    size = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file())
    big = {}
    for f in stage.rglob("*"):
        if f.is_file():
            rel = f.relative_to(stage).parts
            k = "/".join(rel[:4] if rel[:3] == ("python", "Lib", "site-packages") else rel[:2])
            big[k] = big.get(k, 0) + f.stat().st_size
    for k, v in sorted(big.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {v / 1e6:7.1f}MB  {k}")
    print(f"묶음: {zp.name}  풀면 {size / 1e6:.0f}MB, zip {zp.stat().st_size / 1e6:.0f}MB ({time.time() - t0:.0f}초)")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"zip={zp}\nname={zp.name}\nversion={ver}\n")
    return zp


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dist")

"""서버 작은 도구: 폴더 안인지 확인, 방문자 IP, 결과 zip 묶기, 폴더 열기.
"""
import os


def _inside(path, root):
    """path 가 root 폴더 안에 있는가 (서버 모드에서 남의 파일·서버 파일을 못 읽게)"""
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def _client_ip(handler):
    return (handler.headers.get("Fly-Client-IP") or (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            or handler.client_address[0])


def _zip_dir(d, names=None):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in sorted(names or os.listdir(d)):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                z.write(p, n)
            elif os.path.isdir(p):
                for root, _, fs in os.walk(p):
                    for f in fs:
                        fp = os.path.join(root, f)
                        z.write(fp, os.path.relpath(fp, d))
    return buf.getvalue()


def open_folder(path):
    import subprocess
    import sys
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

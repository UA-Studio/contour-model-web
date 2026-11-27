"""두 결과 폴더(고치기 전/후)가 같은 결과물인지 비교. 시각·경로·실행시간처럼 매번 바뀌는 값은 빼고 본다.

사용 (코드를 나누거나 공용 파일을 고칠 때):
    CONTOUR_TEST_OUT=전 python -m pytest tests      # 고치기 전
    CONTOUR_TEST_OUT=후 python -m pytest tests      # 고친 뒤
    python tests/compare_outputs.py 전 후

바이트가 같으면 그대로 같음. 다르면 형식별로 내용만 비교한다
(DXF 핸들, 3dm 고유번호, 만든 시각, 실행 시간, 임시 폴더 경로는 뺌).
"""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


def h(b):
    return hashlib.sha1(b).hexdigest()


def _scrub(t):
    t = re.sub(r"/tmp/[^\s\"'<>]*|[A-Za-z]:\\[^\s\"'<>]*", "<PATH>", t)
    t = re.sub(r"<created>.*?</created>|<modified>.*?</modified>", "", t)
    t = re.sub(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d(:\d\d(\.\d+)?)?", "<TIME>", t)
    return t


def norm_dxf(p):
    import ezdxf
    doc = ezdxf.readfile(p)
    rows = []
    for e in doc.modelspace():
        d = e.dxfattribs()
        for k in ("handle", "owner"):
            d.pop(k, None)
        extra = ""
        if e.dxftype() == "LWPOLYLINE":
            extra = repr([tuple(round(v, 6) for v in pt) for pt in e.get_points()])
        elif e.dxftype() == "POLYLINE":
            extra = repr([tuple(round(c, 6) for c in v.dxf.location) for v in e.vertices])
        elif e.dxftype() == "MESH":
            extra = repr((len(e.vertices), len(e.faces)))
        rows.append(e.dxftype() + repr(sorted((k, repr(v)) for k, v in d.items())) + extra)
    layers = sorted(l.dxf.name for l in doc.layers)
    return h(repr((layers, rows)).encode())


def norm_3dm(p):
    import rhino3dm
    m = rhino3dm.File3dm.Read(str(p))
    layers = [(l.Name, l.FullPath) for l in m.Layers]
    rows = []
    for o in m.Objects:
        g, a = o.Geometry, o.Attributes
        bb = g.GetBoundingBox()
        r = [type(g).__name__, a.LayerIndex, a.Name,
             tuple(round(v, 5) for v in (bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z))]
        if isinstance(g, rhino3dm.Mesh):
            r += [len(g.Vertices), len(g.Faces)]
        rows.append(repr(r))
    return h(repr((layers, rows)).encode())


def norm_json(p):
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, dict):
        d.pop("seconds", None)
    return h(_scrub(json.dumps(d, sort_keys=True, ensure_ascii=False)).encode())


def norm_text(p):
    return h(_scrub(p.read_text(encoding="utf-8", errors="replace")).encode())


def norm_zip(p):
    z = zipfile.ZipFile(p)
    return h(repr(sorted((n, h(z.read(n))) for n in z.namelist())).encode())


def norm_npz(p):
    import numpy as np
    z = np.load(p, allow_pickle=False)
    return h(repr(sorted((k, h(np.ascontiguousarray(z[k]).tobytes())) for k in z.files)).encode())


def norm_png(p):
    import numpy as np
    from PIL import Image
    return h(np.asarray(Image.open(p).convert("RGBA")).tobytes())


SKIP = {"실행기록.txt"}


def norm(p):
    s = p.suffix.lower()
    if p.name in SKIP:
        return "skip"
    if s == ".dxf":
        return norm_dxf(p)
    if s == ".3dm":
        return norm_3dm(p)
    if s == ".json":
        return norm_json(p)
    if s in (".dae", ".rb", ".txt", ".svg", ".csv", ".prj"):
        return norm_text(p)
    if s in (".rbz", ".zip"):
        return norm_zip(p)
    if s == ".npz":
        return norm_npz(p)
    if s == ".png":
        return norm_png(p)
    return h(p.read_bytes())


def main(a, b):
    a, b = Path(a), Path(b)
    fa = sorted(x.relative_to(a) for x in a.rglob("*") if x.is_file())
    fb = sorted(x.relative_to(b) for x in b.rglob("*") if x.is_file())
    bad = 0
    for r in sorted(set(fa) ^ set(fb)):
        print("한쪽에만 있음:", r)
        bad += 1
    same = 0
    for r in fa:
        if not (b / r).exists():
            continue
        if (a / r).read_bytes() == (b / r).read_bytes() or norm(a / r) == norm(b / r):
            same += 1
        else:
            print("다름:", r)
            bad += 1
    print(f"같음 {same}개" + ("" if not bad else f", 다른 곳 {bad}개"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

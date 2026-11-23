"""두 결과 폴더(고치기 전/후)가 같은 결과물인지 비교. 시각·경로·실행시간처럼 매번 바뀌는 값은 빼고 본다.

사용: CONTOUR_TEST_OUT=전 pytest tests/test_model.py → 고친 뒤 CONTOUR_TEST_OUT=후 → python tests/compare_outputs.py 전 후
"""
import hashlib, json, re, sys, zipfile
from pathlib import Path


def h(b):
    return hashlib.sha1(b).hexdigest()


def norm_dxf(p):
    import ezdxf
    doc = ezdxf.readfile(p)
    rows = []
    for e in doc.modelspace():
        d = e.dxfattribs()
        d.pop("handle", None); d.pop("owner", None)
        extra = ""
        if e.dxftype() == "LWPOLYLINE":
            extra = repr([tuple(round(v, 6) for v in pt) for pt in e.get_points()])
        rows.append(e.dxftype() + repr(sorted((k, repr(v)) for k, v in d.items())) + extra)
    layers = sorted(l.dxf.name for l in doc.layers)
    return h(repr((layers, rows)).encode())


def norm_3dm(p):
    import rhino3dm
    m = rhino3dm.File3dm.Read(str(p))
    layers = [(l.Name, l.FullPath) for l in m.Layers]
    rows = []
    for o in m.Objects:
        g = o.Geometry
        a = o.Attributes
        bb = g.GetBoundingBox()
        r = [type(g).__name__, a.LayerIndex, a.Name,
             tuple(round(v, 5) for v in (bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z))]
        if isinstance(g, rhino3dm.Mesh):
            r += [len(g.Vertices), len(g.Faces)]
        rows.append(repr(r))
    return h(repr((layers, rows)).encode())


def norm_text(p, base):
    t = Path(p).read_text(encoding="utf-8", errors="replace")
    t = t.replace(str(base), "<BASE>")
    t = re.sub(r"<created>.*?</created>|<modified>.*?</modified>", "", t)
    t = re.sub(r'build_from\(".*?"', 'build_from("<PATH>"', t)
    return h(t.encode())


def norm_zip(p):
    z = zipfile.ZipFile(p)
    return h(repr(sorted((n, h(z.read(n))) for n in z.namelist())).encode())


def norm(p, base):
    s = p.suffix.lower()
    if p.name == "실행기록.txt":
        return "skip"
    if p.name == "summary.json":
        d = json.loads(p.read_text(encoding="utf-8")); d.pop("seconds", None)
        return h(json.dumps(d, sort_keys=True).encode())
    if s == ".dxf":
        return norm_dxf(p)
    if s == ".3dm":
        return norm_3dm(p)
    if s in (".dae", ".rb"):
        return norm_text(p, base)
    if s == ".rbz":
        return norm_zip(p)
    return h(p.read_bytes())


a, b = Path(sys.argv[1]), Path(sys.argv[2])
bad = 0
for sub in ("기본모델", "대지모델"):
    fa = sorted(x.relative_to(a) for x in (a / sub).rglob("*") if x.is_file())
    fb = sorted(x.relative_to(b) for x in (b / sub).rglob("*") if x.is_file())
    if fa != fb:
        print("파일 목록 다름", set(fa) ^ set(fb)); bad += 1
    for r in fa:
        if (b / r).exists() and norm(a / r, a / sub) != norm(b / r, b / sub):
            print("다름:", r); bad += 1
print("결과 같음" if not bad else f"다른 곳 {bad}개")
sys.exit(1 if bad else 0)

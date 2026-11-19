"""스케치업 없이 '.skp 자동 만들기' 흐름 시험: 가짜 SketchUp.exe(루비 가짜 API)로 make_skp_with_sketchup 를 돌린다.
사용: python test_make_skp.py 결과폴더(model_sketchup_build.json 이 있는 곳)"""
import os, sys, stat, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from contour_ai import skp_export
out = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
fake = os.path.join(tempfile.mkdtemp(), "SketchUp.exe")
with open(fake, "w") as fh:
    fh.write(f"#!/bin/sh\nexec ruby {os.path.join(here, 'sketchup_mock.rb')} \"$@\"\n")
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
os.environ["CONTOUR_SKETCHUP"] = fake
os.environ["PUBLIC"] = tempfile.mkdtemp()
skp_export.os.name = "nt"          # 윈도우처럼
print(skp_export.make_skp_with_sketchup(out, log=print, timeout=120))

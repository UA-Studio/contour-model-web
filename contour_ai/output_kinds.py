"""받을 파일 종류 고르기: 스케치업·Rhino·3D 프린트·레이저·2D 도면·사진 지형.

3D 보기 GLB(model.glb)·요약·단면 자료는 언제나 만든다.
"""


# ---------------------------------------------------------------- 메인
# 받을 파일 종류. 3D 보기 GLB(model.glb)·요약·단면 자료는 언제나 만든다.
OUTPUT_NAMES = {"sketchup": "스케치업", "rhino": "Rhino", "print": "3D 프린트 STL", "laser": "레이저 판 DXF",
                "plan": "2D 도면", "photo": "사진 지형"}


OUTPUTS_DEFAULT = ("sketchup",)     # 스케치업으로 작업하는 사무소용 윈도우판: 기본은 스케치업만


def _outputs(a):
    """a.outputs(목록·쉼표 글자, None=기본) → 고른 종류 집합. 옛 sketchup=False 는 스케치업을 뺀다."""
    o = a.outputs
    if isinstance(o, str):
        o = [x for x in o.replace(" ", ",").split(",") if x]
    o = {str(x).lower() for x in (OUTPUTS_DEFAULT if o is None else o)}
    bad = o - set(OUTPUT_NAMES)
    if bad:
        raise SystemExit(f"모르는 받을 파일 종류: {', '.join(sorted(bad))} (고를 수 있는 것: {', '.join(OUTPUT_NAMES)})")
    if not a.sketchup:
        o.discard("sketchup")
    return o

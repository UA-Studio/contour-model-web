"""계단식 대지 시험: 비탈의 건물 터가 평평해지고, 옹벽에서 끊기고, 비탈의 큰 논이 다랑이가 되는지.

지어낸 비탈(북쪽으로 10% 오르막)과 건물·옹벽·논으로만 시험합니다.
"""
import numpy as np
from shapely.geometry import LineString, box

from contour_ai.pads import build_pads


def slope(xy):
    xy = np.atleast_2d(xy)
    return 100.0 + 0.1 * xy[:, 1]          # 북쪽으로 10% 오르막


def _z(f, x, y):
    return float(f(np.array([[x, y]]))[0])


def test_건물_터가_평평하고_옹벽에서_끊김():
    bbox = (0, 0, 200, 200)
    b1 = box(40, 60, 60, 75)               # 아래 집
    b2 = box(40, 95, 60, 110)              # 위 집 (20m 위, 높이 차 약 3.5m)
    wall = LineString([(30, 85), (70, 85)])  # 두 집 사이 옹벽
    P = build_pads([b1, b2], slope, bbox, barriers=[wall], log=lambda *a: None)
    assert P is not None and P.stats["buildings"] == 2
    f = P.wrap(slope)
    # 건물 발자국 안은 한 높이
    zs = [_z(f, x, y) for x in (42, 50, 58) for y in (62, 68, 73)]
    assert np.ptp(zs) < 1e-6
    # 마당(건물 앞 5m)도 같은 높이
    assert abs(_z(f, 50, 57) - zs[0]) < 1e-6
    # 옹벽 양쪽은 다른 단: 아래 집 터 ≈ 106.75, 위 집 터 ≈ 110.25
    lo, hi = _z(f, 50, 83), _z(f, 50, 88)
    assert hi - lo > 2.5
    # 터에서 멀리 떨어진 곳은 원래 비탈 그대로
    assert abs(_z(f, 150, 150) - slope(np.array([[150, 150]]))[0]) < 1e-9


def test_길_면은_고치지_않음():
    bbox = (0, 0, 200, 200)
    b = box(40, 60, 60, 75)
    road = box(0, 40, 200, 52)
    P = build_pads([b], slope, bbox, roads=road, log=lambda *a: None)
    f = P.wrap(slope)
    for x in (45, 50, 55):
        assert abs(_z(f, x, 46) - slope(np.array([[x, 46]]))[0]) < 1e-9


def test_비탈의_큰_논은_다랑이():
    bbox = (0, 0, 200, 200)
    paddy = box(100, 20, 160, 120)         # 남북 100m → 원래 높이 차 10m
    P = build_pads([], slope, bbox, paddies=[paddy], log=lambda *a: None)
    assert P is not None and P.stats["paddy_terraced"] == 1
    f = P.wrap(slope)
    ys = np.arange(22, 118, 0.5)
    z = f(np.column_stack([np.full_like(ys, 130.0), ys]))
    steps = np.unique(np.round(z, 3))
    assert 4 <= len(steps) <= 12              # 1.5m 마다 한 단
    assert np.all(np.diff(z) >= -1e-9)        # 오르막 방향으로 계단만 (거꾸로 내려가지 않음)


def test_제외_구역에는_터를_만들지_않음():
    bbox = (0, 0, 200, 200)
    b = box(40, 60, 60, 75)
    P = build_pads([b], slope, bbox, exclude=box(30, 50, 70, 90), log=lambda *a: None)
    assert P is None or P.stats["buildings"] == 0


def test_필지_기준():
    """개발 필지(대)는 통째로 한 단, 필지 경계가 단의 경계, 임야 필지는 그대로."""
    bbox = (0, 0, 200, 200)
    lot = box(30, 50, 70, 70)              # 대지 (남북 20m → 원래 높이 차 2m)
    lot2 = box(30, 70, 70, 90)             # 위쪽 대지
    forest = box(100, 50, 160, 150)        # 임야
    b = box(40, 55, 55, 62)
    P = build_pads([b], slope, bbox, parcels=[(lot, "대"), (lot2, "대"), (forest, "임")], log=lambda *a: None)
    assert P.stats["dev_parcels"] == 2
    f = P.wrap(slope)
    z = [_z(f, x, y) for x in (33, 50, 67) for y in (52, 60, 68)]
    assert np.ptp(z) < 1e-6                  # 필지 하나 = 한 단 (건물 밖 마당 끝까지)
    assert _z(f, 50, 85) - z[0] > 1.0        # 위 필지는 다른 단
    assert abs(_z(f, 130, 100) - slope(np.array([[130, 100]]))[0]) < 1e-9   # 임야는 원래 지형


def test_지목():
    from contour_ai.pads import jimok
    assert jimok("556-118대") == "대" and jimok("산12임") == "임" and jimok("12-3") == ""

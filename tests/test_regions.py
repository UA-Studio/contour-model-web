"""계단판 윤곽 다듬기 시험: 모델 모서리가 깎여 나가지 않는지 (2026-11-19 사용자 신고: 네 모서리가 비스듬히 잘림)."""
import shapely
from shapely.geometry import Polygon, box

from contour_ai.contour_model import smooth_poly


def test_다듬어도_모델_모서리는_그대로():
    b = (0, 0, 1000, 1000)
    # 위쪽 가운데만 파인, 모델 거의 전체를 덮는 판
    g = Polygon([(0, 0), (1000, 0), (1000, 1000), (600, 1000), (500, 900), (400, 1000), (0, 1000)])
    s = smooth_poly(g, 3, b).intersection(box(*b))
    for x, y in [(1, 1), (999, 1), (1, 999), (999, 999)]:
        assert s.covers(shapely.Point(x, y))
    assert s.area > 0.98 * g.area
    # 안쪽 파인 곳은 그대로 파여 있다
    assert not s.covers(shapely.Point(500, 990))


def test_작은_웅덩이가_있어도_판_모서리가_안_깎임():
    """지형 가장 낮은 곳이 작은 웅덩이 하나면 그 위 판은 모델 거의 전체를 덮는다.
    이때 판 윤곽 다듬기가 모델 네 모서리를 큰 삼각형으로 깎아 내던 것 (사용자 화면: 팔각형 모델)."""
    import numpy as np
    from contour_ai.contour_model import terrace_regions

    def interp(xy):
        xy = np.atleast_2d(xy)
        z = 110 + 0.01 * xy[:, 0]
        return np.where(np.hypot(xy[:, 0] - 500, xy[:, 1] - 500) < 20, 95.0, z)

    regs, _ = terrace_regions([], interp, (0, 0, 1000, 1000), 5.0, 20.0, cell=2.0)
    for L in [L for L in sorted(regs) if 95 < L <= 110]:   # 110 m 아래 판은 모델 전체(웅덩이 빼고)를 덮어야 함
        g = regs[L]
        for x, y in [(0.5, 0.5), (999.5, 0.5), (0.5, 999.5), (999.5, 999.5)]:
            assert g.covers(shapely.Point(x, y)), (L, x, y)

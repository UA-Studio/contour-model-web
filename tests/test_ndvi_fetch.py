"""위성영상 녹지(NDVI) 받기: 큰 범위에서도 빨리 끝나게 고른 장면·계산이 맞는지 (인터넷 없이)."""
import warnings

import numpy as np

from contour_ai.vegetation import _low_quartile, _pick


def test_pick_spreads_months_then_clearest():
    cands = [(0, 5, "a"), (1, 5, "b"), (2, 5, "c"), (20, 7, "d"), (25, 9, "e"), (3, 6, "f")]
    got = _pick(cands, 4)
    assert len(got) == 4
    assert {c[1] for c in got} == {5, 6, 7, 9}          # 달마다 하나씩 먼저
    assert _pick(cands, 10) == sorted(_pick(cands, 10)[:4]) + [(1, 5, "b"), (2, 5, "c")]


def test_low_quartile_matches_nanpercentile():
    rng = np.random.default_rng(1)
    S = rng.random((8, 120, 90)).astype(np.float32)
    S[rng.random(S.shape) < 0.45] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        nd = np.nanmax(S, axis=0)
        ref = np.nanpercentile(S, 25, axis=0)
    ref = np.where(np.isfinite(S).sum(axis=0) >= 3, ref, nd)
    got = _low_quartile(S, nd)
    assert np.array_equal(np.isfinite(got), np.isfinite(ref))
    m = np.isfinite(ref)
    assert np.abs(got[m] - ref[m]).max() < 1e-5

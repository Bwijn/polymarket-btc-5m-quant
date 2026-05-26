"""Regime-relative transforms — rolling z-score / rank.

Stateless per-event functions (caller supplies past_values from history store).
Mining batch builds these via pandas rolling; scanner runtime queries
feature_history per-event then calls here.

SSOT: mining scratch/research/features/transforms.py delegates here.
"""
from __future__ import annotations
import math

from .constants import TRANSFORM_SPEC


def compute_zs(current: float, past_values: list[float], min_periods: int) -> float:
    """Z-score of current relative to past_values. Matches pandas
    rolling(window=W, min_periods=mp, closed='left').mean()/std() semantics:
    - past_values excludes current (closed='left')
    - drop NaN, need at least min_periods non-NaN
    - std uses ddof=1 (pandas default sample std)
    - returns NaN if std == 0 or insufficient data
    """
    if current is None or (isinstance(current, float) and math.isnan(current)):
        return float('nan')
    vals = [v for v in past_values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < min_periods:
        return float('nan')
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    std = variance ** 0.5
    if std == 0:
        return float('nan')
    return (current - mean) / std


def compute_rank(current: float, past_values: list[float], min_periods: int) -> float:
    """Percentile rank of current within [past_values + current], pct=True,
    method='average'. Matches pandas rolling(window=W, min_periods=mp).rank(pct=True).

    Note: mining uses closed='right' (default) for rank, so current IS included in window.
    We replicate by appending current to past_values for rank calc.
    """
    if current is None or (isinstance(current, float) and math.isnan(current)):
        return float('nan')
    vals = [v for v in past_values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) + 1 < min_periods:
        return float('nan')
    all_vals = vals + [current]
    n = len(all_vals)
    less  = sum(1 for v in all_vals if v < current)
    equal = sum(1 for v in all_vals if v == current)
    # method='average': tied ranks averaged. Ranks for tied group = (less+1)..(less+equal),
    # average = less + (equal+1)/2.
    avg_rank = less + (equal + 1) / 2
    return avg_rank / n


def parse_transform_col(col: str) -> tuple[str, dict] | None:
    """If col ends with a known transform suffix (`__zs24h` etc.), return
    (base, spec) where spec is {'op', 'window', 'min_periods'}. Else None.
    """
    for sfx, spec in TRANSFORM_SPEC.items():
        if col.endswith(sfx) and len(col) > len(sfx):
            return col[:-len(sfx)], spec
    return None

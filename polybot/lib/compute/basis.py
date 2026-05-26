"""Cross-source basis features: PM internal price vs Binance-derived prob.

Derived from precomputed PM (compute_pm_features) + BN (compute_bn_features) outputs.
SSOT: mining scratch/research/features/basis.py delegates here.
"""
from __future__ import annotations
import math

from .constants import BASIS_K


def compute_basis_features(pm: dict, bn: dict) -> dict:
    """basis_pre_W_up/dn = p_pre_W - derived_prob_W.

    derived_prob_up = clip(0.5 + chg_pct / (BASIS_K * rv), 0.001, 0.999)
        where rv uses bn_rv_W if W in (300, 900) else bn_rv_300.
    Requires both pm + bn dicts computed at same cs.
    """
    out = {}
    for w in (60, 300, 900):
        chg = bn.get(f'bn_chg_pct_pre_{w}', float('nan'))
        rv_w = w if w in (300, 900) else 300
        rv = bn.get(f'bn_rv_{rv_w}', float('nan'))
        if rv == 0:
            rv = float('nan')

        if math.isnan(chg) or math.isnan(rv):
            derived = float('nan')
        else:
            derived = 0.5 + chg / (BASIS_K * rv)
            derived = max(0.001, min(0.999, derived))

        p_up = pm.get(f'p_pre_{w}_up', float('nan'))
        p_dn = pm.get(f'p_pre_{w}_dn', float('nan'))

        out[f'basis_pre_{w}_up'] = p_up - derived          # NaN propagates naturally
        out[f'basis_pre_{w}_dn'] = p_dn - (1 - derived)

    return out

"""Helpers for CORRECTED mining: carry-forward last-trade + expr_to_entry_time.

Context: 共享给 Phase 3.2 (build_event_entry_prices) + Phase 3.3 (recompute_mining_runs_entry).
         carry-forward 语义 = "在 target 时间点, last trade 的价格"; raw_history_up/down 是
         /batch-prices-history 返回的 [{t,p},...], 已按 t 升序.
Source:  events.raw_history_up / events.raw_history_down (Phase 1.1 ingest 完成)
Expected: 纯 helper module, 无 side effects, import-safe. 含 inline self-test.
"""
import json
import re
from typing import Optional, Iterable, Sequence, Mapping


INTRA_RE = re.compile(r'_intra_(\d+)')


def expr_to_entry_time(expr: str) -> int:
    """Parse entry_time_s from expr.

    Convention: entry happens at cs + max(_intra_X) for X in expr.
                If no _intra_X feature → entry_time = 0 (pre-only expr, all features
                observable at cs).
    """
    matches = INTRA_RE.findall(expr)
    return max(int(m) for m in matches) if matches else 0


def get_price_at(history, target_offset_s: int, cs: int) -> Optional[float]:
    """Carry-forward last-trade price at cs + target_offset_s.

    Args:
        history: list of {'t': int, 'p': float} OR JSON-string of same.
                 Empty / None → returns None.
        target_offset_s: seconds offset from cs (0 = at candle start, 60 = +60s).
        cs: candle_start unix timestamp.

    Returns:
        Last p value where t <= cs + target_offset_s, or None if no such tick.

    Note: history must be a list of dicts already ordered or unordered (we scan).
          For perf, callers can pre-parse JSON once outside.
    """
    if history is None:
        return None
    if isinstance(history, str):
        if not history:
            return None
        history = json.loads(history)
    if not history:
        return None

    target_t = cs + target_offset_s
    last_p = None
    last_t = -1
    for tick in history:
        t = tick['t']
        if t <= target_t and t > last_t:
            last_t = t
            last_p = tick['p']
    return last_p


# ---------------------------------------------------------------------- self-test
def _self_test():
    # expr_to_entry_time
    assert expr_to_entry_time('p_intra_60<0.445 & is_weekend==1')          == 60
    assert expr_to_entry_time('mean_intra_60<mean_intra_180')              == 180
    assert expr_to_entry_time('p_intra_60<0.445 & p_intra_120>0.6')        == 120
    assert expr_to_entry_time('asym_3600<0.819444')                        == 0
    assert expr_to_entry_time('slope_pre_900>0.001 & is_friday==1')        == 0
    assert expr_to_entry_time('p_intra_270>0.55')                          == 270
    assert expr_to_entry_time('std_pre_60>0.01')                           == 0
    print("  ✓ expr_to_entry_time: 7 cases passed")

    # get_price_at: 标准 carry-forward
    cs = 1000
    history = [
        {'t': 900,  'p': 0.50},   # cs-100s
        {'t': 950,  'p': 0.51},   # cs-50s
        {'t': 1000, 'p': 0.52},   # cs (= target for offset 0)
        {'t': 1030, 'p': 0.55},   # cs+30s
        {'t': 1080, 'p': 0.58},   # cs+80s
    ]
    assert get_price_at(history, 0, cs)   == 0.52   # exactly at cs
    assert get_price_at(history, 30, cs)  == 0.55   # exactly at cs+30
    assert get_price_at(history, 60, cs)  == 0.55   # cs+60 carry-forward from t=1030
    assert get_price_at(history, 120, cs) == 0.58   # cs+120 carry-forward from t=1080
    assert get_price_at(history, -50, cs) == 0.51   # cs-50 (pre-candle target)
    assert get_price_at(history, -200, cs) is None  # before any tick
    print("  ✓ get_price_at carry-forward: 6 cases passed")

    # get_price_at: edge cases
    assert get_price_at([], 60, cs) is None
    assert get_price_at(None, 60, cs) is None
    assert get_price_at('', 60, cs) is None
    assert get_price_at('[]', 60, cs) is None
    print("  ✓ get_price_at edge cases (empty/null): 4 cases passed")

    # get_price_at: JSON string input
    history_json = json.dumps(history)
    assert get_price_at(history_json, 60, cs) == 0.55
    print("  ✓ get_price_at JSON-string input: 1 case passed")

    # get_price_at: unsorted history (defensive — scan max not assume order)
    history_unsorted = list(reversed(history))
    assert get_price_at(history_unsorted, 60, cs) == 0.55
    print("  ✓ get_price_at unsorted input: 1 case passed")


if __name__ == '__main__':
    print("=" * 60); print("entry_price_helper self-test"); print("=" * 60)
    _self_test()
    print("\nALL PASSED")

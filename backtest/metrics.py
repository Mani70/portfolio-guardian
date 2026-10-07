"""Performance statistics for an equity curve and its trades."""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .engine import Trade


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return float("nan")
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return float("nan")
    return ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    return float((equity / equity.cummax() - 1).min() * 100)


def sharpe(equity: pd.Series, rf_annual_pct: float = 0.0) -> float:
    r = equity.pct_change().dropna()
    if len(r) < 20 or r.std() == 0:
        return float("nan")
    excess = r - rf_annual_pct / 100 / 252
    return float(excess.mean() / r.std() * math.sqrt(252))


def trade_stats(trades: List[Trade]) -> Dict[str, float]:
    if not trades:
        return dict(trades=0, win_rate=float("nan"), avg_win=float("nan"), avg_loss=float("nan"),
                    profit_factor=float("nan"), avg_bars=float("nan"), stops=0)
    rets = np.array([t.return_pct for t in trades])
    pnl = np.array([t.pnl for t in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    gross_win, gross_loss = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    return dict(
        trades=len(trades),
        win_rate=len(wins) / len(rets) * 100,
        avg_win=float(wins.mean()) if len(wins) else float("nan"),
        avg_loss=float(losses.mean()) if len(losses) else float("nan"),
        profit_factor=float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        avg_bars=float(np.mean([t.bars for t in trades])),
        stops=sum(1 for t in trades if t.reason == "stop"),
    )


def summarize(equity: pd.Series, trades: List[Trade], costs_paid: float = 0.0,
              exposure: Optional[pd.Series] = None, split: Optional[pd.Timestamp] = None) -> Dict[str, float]:
    out = dict(cagr=cagr(equity), max_dd=max_drawdown(equity), sharpe=sharpe(equity),
               final=float(equity.iloc[-1]) if len(equity) else float("nan"), costs=costs_paid,
               exposure=float(exposure.mean() * 100) if exposure is not None and len(exposure) else float("nan"))
    out.update(trade_stats(trades))
    if split is not None and len(equity):
        ins, oos = equity.loc[:split], equity.loc[split:]
        out["cagr_in_sample"] = cagr(ins)
        out["cagr_out_of_sample"] = cagr(oos)
        out["max_dd_out_of_sample"] = max_drawdown(oos)
    return out


def fd_curve(index: pd.DatetimeIndex, capital: float, rate_pct: float) -> pd.Series:
    """A fixed deposit compounding daily at rate_pct, for comparison."""
    if len(index) == 0:
        return pd.Series(dtype=float)
    days = np.array([(d - index[0]).days for d in index])
    return pd.Series(capital * (1 + rate_pct / 100) ** (days / 365.25), index=index)

"""Offline tests of research/history20.py (no network)."""
import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("history20", ROOT / "research" / "history20.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_load_index_reads_the_hand_saved_niftyindices_files(tmp_path):
    m = _mod()
    q = m.load_index(tmp_path, ["nifty100quality30"])                  # nothing downloaded: manual files only
    assert q.index.min() == pd.Timestamp("2009-10-01") and q["close"].iloc[0] == 1000.0
    assert q["open"].isna().all()                                      # "-" in the file
    n = m.load_index(tmp_path, ["nifty50", "nifty"])
    d = pd.Timestamp("2008-12-31")
    assert n.loc[d, "open"] == 2979.80 and n.loc[d, "close"] == 2959.15
    assert n.index.is_monotonic_increasing and not n.index.duplicated().any()


def test_downloaded_history_wins_over_the_manual_files(tmp_path):
    m = _mod()
    pd.DataFrame([dict(date="2008-12-31", index="NIFTY 50", open=1.0, close=2.0)]).to_csv(
        tmp_path / "index_history.csv", index=False)
    n = m.load_index(tmp_path, ["nifty50"])
    assert n.loc[pd.Timestamp("2008-12-31"), "close"] == 2.0
    assert n.loc[pd.Timestamp("2008-12-30"), "close"] == 2979.50       # gaps still filled from the manual files

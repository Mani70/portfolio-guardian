"""Offline tests of research/history20.py (no network)."""
import importlib.util
from pathlib import Path

import numpy as np
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


def test_purpose_factor_reads_nse_wording():
    m = _mod()
    assert m.purpose_factor("BONUS 1:1") == 0.5
    assert abs(m.purpose_factor("BONUS 1:2") - 2 / 3) < 1e-12
    assert m.purpose_factor("FV SPLT FRM RS 5 TO RE 1") == 0.2
    assert m.purpose_factor("FVSPLT FRM RS 10 TO RS 2") == 0.2
    assert m.purpose_factor("FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RS 2/- PER SHARE") == 0.2
    assert m.purpose_factor("CONSOLIDATION FROM RE 1 TO RS 10") == 10.0
    assert pd.isna(m.purpose_factor("DEMERGER"))
    for other in ("INTDIV - RS 2.50 PER SH", "DIV-RS 2/SPL DIV-RE 0.50", "SCHEME OF AMALGAMATION", "RIGHTS 1:5 @ PREM RS 100"):
        assert m.purpose_factor(other) is None


def _panel(closes, opens):
    idx = pd.bdate_range("2009-12-28", periods=len(closes))
    c = pd.DataFrame({"AAA": closes}, index=idx, dtype="float32")
    o = pd.DataFrame({"AAA": opens}, index=idx, dtype="float32")
    return {"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c}


def test_detection_rule_and_official_adjustment():
    m = _mod()
    # 2009: a 1:2 split (no official file then); 2010: an official bonus 1:1 and an ordinary -10% day
    closes = [100, 101, 50.4, 50.0, 25.1, 22.6]
    opens = [100, 100, 50.6, 50.2, 25.0, 25.0]
    p = _panel(closes, opens)
    det = m.detected_events(p["open"], p["close"])
    assert list(det.ex_date) == [p["close"].index[2], p["close"].index[4]] and list(det.factor) == [0.5, 0.5]
    off = pd.DataFrame(dict(symbol=["AAA"], ex_date=[p["close"].index[4]], factor=[0.5], purpose=["BONUS 1:1"]))
    m.adjust_prices(p, off, use_detection=True)
    adj = [round(float(x), 2) for x in p["close"]["AAA"]]
    assert adj == [25.0, 25.25, 25.2, 25.0, 25.1, 22.6]           # both events removed, the real fall kept


def test_detection_off_leaves_pre_2010_alone():
    m = _mod()
    p = _panel([100, 101, 50.4, 50.0, 50.1, 50.2], [100, 100, 50.6, 50.2, 50.0, 50.0])
    m.adjust_prices(p, pd.DataFrame(columns=["symbol", "ex_date", "factor", "purpose"]), use_detection=False)
    assert p["close"]["AAA"].iloc[0] == 100

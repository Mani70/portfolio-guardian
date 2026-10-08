"""Offline tests of research/download_history.py (no network)."""
import importlib.util
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("download_history", ROOT / "research" / "download_history.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_urls_old_and_new_bhavcopy():
    m = _mod()
    assert m.urls_equities(date(2005, 1, 3))[0].endswith("/content/historical/EQUITIES/2005/JAN/cm03JAN2005bhav.csv.zip")
    assert m.urls_equities(date(2024, 7, 8))[0].endswith("/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip")


def test_both_bhavcopy_formats_give_the_same_rows():
    m = _mod()
    d = date(2024, 7, 8)
    old = [dict(SYMBOL="INFY", SERIES="EQ", OPEN="1650", HIGH="1670", LOW="1640", CLOSE="1660", LAST="1661",
                PREVCLOSE="1648.5", TOTTRDQTY="1000", TOTTRDVAL="1660000", TIMESTAMP="08-JUL-2024"),
           dict(SYMBOL="INFY", SERIES="N1", OPEN="1", HIGH="1", LOW="1", CLOSE="1", PREVCLOSE="1", TOTTRDQTY="1",
                TOTTRDVAL="1")]
    new = [dict(TckrSymb="INFY", SctySrs="EQ", OpnPric="1650", HghPric="1670", LwPric="1640", ClsPric="1660",
                PrvsClsgPric="1648.5", TtlTradgVol="1000", TtlTrfVal="1660000"),
           dict(TckrSymb="NIFTYBEES", SctySrs="EQ", OpnPric="250", HghPric="251", LwPric="249", ClsPric="250.5",
                PrvsClsgPric="250", TtlTradgVol="10", TtlTrfVal="2505")]
    a, b = m.reduce_equities(old, d), m.reduce_equities(new, d)
    assert a == b[:1] and len(a) == 1 and a[0]["prevclose"] == 1648.5 and a[0]["value"] == 1660000
    assert b[1]["symbol"] == "NIFTYBEES"


def test_niftyindices_reply():
    m = _mod()
    payload = {"d": json.dumps([{"Index Name": "NIFTY100 QUALITY 30", "HistoricalDate": "02 Jan 2015",
                                 "OPEN": "1,500.5", "HIGH": "1510", "LOW": "1490", "CLOSE": "1,505.25"},
                                {"Index Name": "X", "HistoricalDate": "bad", "CLOSE": "1"}])}
    rows = m.parse_niftyindices(payload)
    assert rows == [dict(date="2015-01-02", index="NIFTY100 QUALITY 30", open=1500.5, high=1510.0, low=1490.0,
                         close=1505.25)]


def test_corporate_actions_from_the_pr_zip():
    import io
    import zipfile
    m = _mod()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Pr281024.csv", "x\n")
        z.writestr("Bc281024.csv", "SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,"
                                   "PURPOSE\nEQ,RELIANCE,Reliance Industries Ltd,28/10/2024, , ,28/10/2024, , ,"
                                   "BONUS 1:1                \nEQ,BAD,Bad Ltd, , , , , , ,DIVIDEND\n"
                                   "EQ,INFY,Infosys Limited,2026-09-04,,,2026-09-04,,,FV SPLIT FROM RS 5 TO RE 1\n")
    rows = m.parse_bc(buf.getvalue(), date(2024, 10, 28))
    assert rows == [dict(file_date="2024-10-28", series="EQ", symbol="RELIANCE", ex_date="2024-10-28",
                         purpose="BONUS 1:1"),
                    dict(file_date="2024-10-28", series="EQ", symbol="INFY", ex_date="2026-09-04",
                         purpose="FV SPLIT FROM RS 5 TO RE 1")]
    assert m.url_pr(date(2024, 10, 28)).endswith("/archives/equities/bhavcopy/pr/PR281024.zip")

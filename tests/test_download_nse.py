"""Offline tests of research/download_nse.py parsers (no network)."""
import importlib.util
import io
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("download_nse", ROOT / "research" / "download_nse.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


D = date(2024, 10, 3)


def test_urls():
    m = _mod()
    assert m.url_participant(D).endswith("/content/nsccl/fao_participant_oi_03102024.csv")
    assert m.url_indices(D).endswith("/content/indices/ind_close_all_03102024.csv")
    assert m.urls_fo(D)[0].endswith("/content/fo/BhavCopy_NSE_FO_0_0_0_20241003_F_0000.csv.zip")
    assert m.urls_fo(date(2019, 3, 5))[0].endswith("/DERIVATIVES/2019/MAR/fo05MAR2019bhav.csv.zip")


def test_participant_oi():
    m = _mod()
    text = ("Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Oct 03, 2024\n"
            "Client Type,Future Index Long,Future Index Short,Option Index Call Long,Total Long Contracts\t\n"
            "Client,100,200,300,600\nFII,50,150,40,240\nTOTAL,150,350,340,840\n")
    rows = m.parse_participant(text, D)
    fii = [r for r in rows if r["who"] == "FII"][0]
    assert fii["Future Index Long"] == 50 and fii["Future Index Short"] == 150 and fii["date"] == "2024-10-03"


def test_indices_with_valuation():
    m = _mod()
    text = ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,"
            "Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
            "Nifty 50,03-10-2024,25452.85,25639.45,25230.30,25250.10,-546.80,-2.12,1,2,22.47,4.02,1.21\n"
            "Nifty200 Quality 30,03-10-2024,1,2,0.5,1.5,0,0,0,0,-,-,-\n")
    rows = m.parse_indices(text, D)
    assert rows[0]["index"] == "Nifty 50" and rows[0]["close"] == 25250.10 and rows[0]["pe"] == 22.47
    assert rows[1]["pe"] is None and rows[1]["close"] == 1.5


def test_fo_both_formats_give_the_same_numbers():
    m = _mod()
    old = [dict(INSTRUMENT="FUTSTK", SYMBOL="INFY", EXPIRY_DT="31-Oct-2024", OPTION_TYP="XX", CLOSE="1900",
                CONTRACTS="10", OPEN_INT="4000"),
           dict(INSTRUMENT="FUTSTK", SYMBOL="INFY", EXPIRY_DT="28-Nov-2024", OPTION_TYP="XX", CLOSE="1910",
                CONTRACTS="2", OPEN_INT="1000"),
           dict(INSTRUMENT="OPTSTK", SYMBOL="INFY", EXPIRY_DT="31-Oct-2024", OPTION_TYP="CE", CLOSE="5", CONTRACTS="1",
                OPEN_INT="700"),
           dict(INSTRUMENT="OPTSTK", SYMBOL="INFY", EXPIRY_DT="31-Oct-2024", OPTION_TYP="PE", CLOSE="5", CONTRACTS="1",
                OPEN_INT="300"),
           dict(INSTRUMENT="OPTIDX", SYMBOL="NIFTY", EXPIRY_DT="10-Oct-2024", OPTION_TYP="PE", CLOSE="5",
                CONTRACTS="1", OPEN_INT="900")]
    new = [dict(FinInstrmTp="STF", TckrSymb="INFY", XpryDt="2024-10-31", OptnTp="", ClsPric="1900", TtlTradgVol="10",
                OpnIntrst="4000", UndrlygPric="1895"),
           dict(FinInstrmTp="STF", TckrSymb="INFY", XpryDt="2024-11-28", OptnTp="", ClsPric="1910", TtlTradgVol="2",
                OpnIntrst="1000", UndrlygPric="1895"),
           dict(FinInstrmTp="STO", TckrSymb="INFY", XpryDt="2024-10-31", OptnTp="CE", ClsPric="5", TtlTradgVol="1",
                OpnIntrst="700", UndrlygPric="1895"),
           dict(FinInstrmTp="STO", TckrSymb="INFY", XpryDt="2024-10-31", OptnTp="PE", ClsPric="5", TtlTradgVol="1",
                OpnIntrst="300", UndrlygPric="1895"),
           dict(FinInstrmTp="IDO", TckrSymb="NIFTY", XpryDt="2024-10-10", OptnTp="PE", ClsPric="5", TtlTradgVol="1",
                OpnIntrst="900", UndrlygPric="25000")]
    for rows in (old, new):
        out = {r["symbol"]: r for r in m.reduce_fo(rows, D)}
        i = out["INFY"]
        assert (i["fut_oi"], i["fut_close"], i["fut_vol"], i["ce_oi"], i["pe_oi"]) == (5000, 1900, 10, 700, 300)
        assert i["fut_expiry"] == "2024-10-31" and not i["is_index"]
        assert out["NIFTY"]["is_index"] and out["NIFTY"]["pe_oi"] == 900
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fo03OCT2024bhav.csv", "INSTRUMENT,SYMBOL,EXPIRY_DT,OPTION_TYP,CLOSE,CONTRACTS,OPEN_INT\n"
                                          "FUTSTK,INFY,31-Oct-2024,XX,1900,10,4000\n")
    assert m.unzip_csv(buf.getvalue())[0]["SYMBOL"] == "INFY"


def test_delivery_new_and_old_files():
    m = _mod()
    new = (" SYMBOL, SERIES, DATE1, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER\nINFY, EQ, 03-Oct-2024, 1000, 600, 60.00\n"
           "INFY, BE, 03-Oct-2024, 5, 5, 100\nZZZ, EQ, 03-Oct-2024, 1, 1, 100\n")
    rows = m.parse_delivery(new, D, {"INFY"})
    assert rows == [{"date": "2024-10-03", "symbol": "INFY", "traded": 1000, "delivered": 600, "deliv_pct": 60.0}]
    old = ("Security Wise Delivery Position - Compulsory Rolling Settlement\n10,MTO,03102016,1,0\n"
           "Record Type,Sr No,Name of Security,Type,Quantity Traded,Deliverable Quantity,% of Deliverable\n"
           "20,1,INFY,EQ,1000,550,55.00\n20,2,INFY,BE,5,5,100\n")
    assert m.parse_delivery(old, D, None) == [{"date": "2024-10-03", "symbol": "INFY", "traded": 1000,
                                               "delivered": 550, "deliv_pct": 55.0}]


def test_store_resumes_and_appends(tmp_path):
    m = _mod()
    s = m.Store(tmp_path)
    s.append("x.csv", [{"date": "2024-10-03", "a": 1}])
    s.append("x.csv", [{"date": "2024-10-04", "a": 2}])
    s.mark("fo", D)
    assert m.Store(tmp_path).is_done("fo", D) and not m.Store(tmp_path).is_done("fo", date(2024, 10, 4))
    assert (tmp_path / "x.csv").read_text().splitlines() == ["date,a", "2024-10-03,1", "2024-10-04,2"]


def test_refusing_host_is_skipped_not_retried(monkeypatch):
    m = _mod()
    f = m.Fetcher(pause=0)
    calls = []

    class R:
        status_code, content = 403, b"denied"

    monkeypatch.setattr(f.s, "get", lambda url, timeout=None: calls.append(url) or R())
    for _ in range(m.Fetcher.BLOCK_AFTER + 5):
        f.get("https://nsearchives.nseindia.com/a.csv")
    assert len(calls) == m.Fetcher.BLOCK_AFTER and "nsearchives.nseindia.com" in f.blocked
    for _ in range(m.Fetcher.BLOCK_AFTER):                       # home page + API refused: site skipped after a while
        f.api("/api/x")
    assert "www.nseindia.com" in f.blocked and f.api("/api/y") is None

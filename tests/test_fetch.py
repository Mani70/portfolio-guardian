"""Long-history download: pages backwards and stops once the instrument has no older data."""
from datetime import datetime, timedelta

from backtest.data import PriceStore, update_history
from guardian.broker import Candle


class FakeClient:
    def __init__(self, first_day):
        self.first_day = first_day
        self.calls = 0

    def candles_many(self, codes, interval, start_ms, end_ms):
        self.calls += 1
        out = {}
        for c in codes:
            bars = []
            t = datetime.fromtimestamp(start_ms / 1000).replace(hour=9, minute=15, second=0, microsecond=0)
            end = datetime.fromtimestamp(end_ms / 1000)
            while t <= end:
                if t.date() >= self.first_day and t.weekday() < 5:
                    bars.append(Candle(ts=int(t.timestamp()), o=1, h=1, l=1, c=1, v=1))
                t += timedelta(days=1)
            out[c] = bars
        return out


def test_download_stops_when_older_windows_are_empty(tmp_path):
    now = datetime(2026, 10, 1, 18, 0)
    client = FakeClient(first_day=(now - timedelta(days=400)).date())     # ~1.1 years of data exist
    frames = update_history(client, {"A": "NSE_1"}, 10, PriceStore(tmp_path), now=now)
    assert len(frames["A"]) > 250
    assert client.calls <= 3          # 2 windows with data + 1 empty, not 10

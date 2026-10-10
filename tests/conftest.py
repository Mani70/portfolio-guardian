"""Shared test setup: no test reaches the news outlets' feeds over the network."""
import pytest


@pytest.fixture(autouse=True)
def _no_outlet_feeds(monkeypatch):
    from trader.reel import market
    monkeypatch.setattr(market, "outlet_items", lambda now, hours=24, query="": [])

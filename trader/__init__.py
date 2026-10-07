"""Signal-gated trading engine (swing + intraday) for INDstocks.

Paper mode is the default. Live orders need ALL of: mode "live" in trader.yaml, the strategy's own
`live: true`, its paper record passing the promotion gates, a whitelisted static IP at INDstocks,
and no STOP file in the project folder.
"""

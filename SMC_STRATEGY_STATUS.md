# SMC strategy implementation status

The strategy interface now includes deterministic helpers for unique 3-left/3-right swing points, 1H HH/HL or LH/LL bias, 5M sweep, CHOCH, FVG, retest confirmation, and 0.05%-2.50% risk validation with a 4R target.

This is a first implementation and must be independently reviewed against the original BTC_SMC_Paper.py before use. It is not yet wired to market-data polling or the paper order lifecycle. Input timestamps must be consistent sortable ISO-8601 values, and callers must supply only completed candles. No exchange connection or order placement exists.

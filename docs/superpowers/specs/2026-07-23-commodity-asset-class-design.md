# Commodity asset class — design

**Date:** 2026-07-23
**Status:** Approved

## Goal

Add a fourth asset class, **commodity**, to de BereBank, served through the existing
Twelve Data integration. All markets are exposed as `{TICKER}-EUR` and traded in EUR,
exactly like crypto, stocks and funds.

## Universe

Twelve Data's commodity catalog (on the configured plan) contains 7 distinct usable
underlyings. Agricultural futures are not available (the documented symbols 404 or
resolve to unrelated stocks), and the copper symbol `HG1` resolves to a German stock,
so it is excluded.

| Market | TD symbol | Name | Pricing |
| --- | --- | --- | --- |
| XAU-EUR | `XAU/EUR` | Gold | native EUR |
| XAG-EUR | `XAG/EUR` | Silver | native EUR |
| XPT-EUR | `XPT/USD` | Platinum | USD → EUR |
| XPD-EUR | `XPD/USD` | Palladium | USD → EUR |
| WTI-EUR | `WTI/USD` | WTI Crude Oil | USD → EUR |
| XBR-EUR | `XBR/USD` | Brent Crude Oil | USD → EUR |
| URALS-EUR | `URALS/USD` | Urals Crude Oil | USD → EUR |

USD-quoted instruments use the same live USD/EUR conversion as US stocks.
All seven follow forex trading hours (roughly 24/5, closed weekends); Twelve Data's
`is_market_open` flag drives the existing market-hours rules (market orders rejected
while closed, limit/stop-loss orders keep resting).

## Approach

Extend the existing instrument list and Twelve Data service (approach chosen over a
separate `CommodityService`, which would duplicate the poller, FX conversion and
candle code for 7 symbols).

### Backend

- `services/instruments.py`
  - `Instrument.asset_class` gains `"commodity"`.
  - New optional `name` field: a display-name override ("Gold", "WTI Crude Oil", …)
    that the quote name never overwrites (Twelve Data names read "Gold Spot / Euro").
  - `td_symbol` returns slash style `{symbol}/{currency}` for commodities
    (`XAU/EUR`, `WTI/USD`); ticker style is unchanged for stocks/funds.
  - Seven new instrument entries as per the table above.
  - The runtime collision rule (crypto base asset wins) applies unchanged.
- `services/twelvedata.py`
  - `_load_markets` seeds the market name from the instrument override.
  - `_fetch_quotes` only stores the quote name when no override exists.
  - `fetch_recent_press_releases` skips commodity instruments (no press releases
    exist for them; the endpoint returns 404).
- `routers/markets.py`
  - `asset_class` query pattern widened to `crypto|stock|fund|commodity`.
  - `has_news` and the per-market news endpoint treat commodities like crypto:
    RSS matching only, no Twelve Data press-release fetch.
- `mcp_server.py` — `list_markets` accepts `asset_class="commodity"`; docstrings
  updated.
- `schemas.py` — comment updated.

No changes to trading, portfolio, leaderboard or snapshots: they treat any base
asset generically, and market-hours enforcement already keys off `market_open`.

### Frontend

- `lib/types.ts` — `AssetClass` gains `'commodity'`.
- `pages/TradePage.tsx` — filter tab row gains `commodity`.
- `components/AssetClassIcon.tsx` — new ingot-style icon (orange) for commodities.
- Locales — en: "Commodity"/"Commodities"; nl: "Grondstof"/"Grondstoffen".

### Docs

- `README.md` and `AGENTS.md`: asset-class tables and MCP tool descriptions list
  commodities with their forex-style trading hours.

## Error handling

Inherited from the existing feed: a symbol that fails to quote is logged once and
skipped; market orders while the market is closed are rejected with the existing
message; limit and stop-loss orders rest until the feed reports the market open.

## Verification

- `backend/test_commodities.py` (standalone script, matching the existing
  `test_*.py` pattern): asserts commodity instruments produce the correct
  `td_symbol`, market symbol and display name, and that they load into the
  Twelve Data service's market map with `asset_class="commodity"`.
- Live check: with the backend running, `/health` shows the higher market count and
  `GET /markets?asset_class=commodity` returns the 7 markets with EUR prices.

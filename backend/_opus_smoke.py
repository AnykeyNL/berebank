"""Scratch: run the Opus pipeline against a copy of the dev database."""
import os
import sys
import time

os.environ.setdefault("BEREBANK_DATABASE_URL", "sqlite:///./berebank_opus_smoke.db")

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.services import opus_calibration, opus_features, opus_store  # noqa: E402
from app.services.instruments import INSTRUMENTS_BY_MARKET  # noqa: E402

Base.metadata.create_all(bind=engine)


def asset_classes():
    db = SessionLocal()
    try:
        from sqlalchemy import distinct, select

        from app.models import MarketCandle

        markets = [m for (m,) in db.execute(select(distinct(MarketCandle.market)))]
    finally:
        db.close()
    out = {}
    for market in markets:
        instrument = INSTRUMENTS_BY_MARKET.get(market)
        out[market] = instrument.asset_class if instrument else "crypto"
    return out


CLASSES = asset_classes()
opus_store._asset_classes = lambda: CLASSES
print(f"markets in db: {len(CLASSES)}")

db = SessionLocal()
try:
    if "--macro" in sys.argv:
        import asyncio

        from app.services import opus_macro

        started = time.monotonic()
        series = asyncio.run(opus_macro.fetch_external_series([]))
        written = opus_store.upsert_many_series(db, series)
        print(f"macro: {time.monotonic() - started:.1f}s series={list(series)} points={written}")
    print("macro status:", opus_store.series_status(db))
    print("macro context:", opus_store.macro_context(db))

    started = time.monotonic()
    candles = opus_store.load_panel_candles(db, since_days=opus_store.CALIBRATION_PANEL_DAYS)
    print(f"load: {time.monotonic() - started:.1f}s rows={sum(len(v) for v in candles.values())}")

    started = time.monotonic()
    panel = opus_features.build_panel(candles, CLASSES)
    print(f"features: {time.monotonic() - started:.1f}s markets={len(panel)}")

    started = time.monotonic()
    index_returns = opus_features.group_index_returns(panel)
    opus_features.add_relative_features(
        panel, index_returns, opus_store.macro_change_series(db)
    )
    print(f"relative: {time.monotonic() - started:.1f}s")
    sample = panel.get("BTC-EUR") or next(iter(panel.values()))
    print("BTC features:", {
        k: (None if v is None else round(v, 3))
        for k, v in sample.feature_at(len(sample.days) - 1).items()
    })

    started = time.monotonic()
    calibrations = opus_calibration.calibrate(panel, index_returns)
    print(f"calibrate: {time.monotonic() - started:.1f}s rows={len(calibrations)}")

    for key in sorted(calibrations):
        payload = calibrations[key]
        wf = payload["walk_forward"]
        hit = wf["hit_rate_pct"]
        bins = [
            None if b["mean_return_pct"] is None else round(b["mean_return_pct"], 2)
            for b in payload["bins"]
        ]
        print(
            f"{key} days={payload['days']} rel={payload['reliable']} "
            f"wf_ic={wf['ic']:.4f} t={wf['ic_t']:.1f} n={wf['ic_days']} "
            f"hit={'n/a' if hit is None else round(hit, 1)} "
            f"mkt={payload['market_return']['mean_pct']:.2f}% "
            f"scale={payload['composite_scale']:.3f} alpha_bins={bins}"
        )

    opus_store.save_calibrations(db, calibrations)
    del candles
    started = time.monotonic()
    scores = opus_store.compute_scores(db)
    print(f"scores: {time.monotonic() - started:.1f}s markets={scores['markets']} regimes={scores['regimes']}")

    from app.services import opus_analysis

    for horizon in ("1d", "1w", "4w"):
        rows = opus_analysis.rank_rows([
            opus_analysis.finalize_row(row, days_since_close=row.get("days_since_close"))
            for row in scores["rows"][horizon]
        ])
        actions: dict[str, int] = {}
        for row in rows:
            actions[row["action"]] = actions.get(row["action"], 0) + 1
        print(f"\n=== {horizon} actions={actions}")
        for row in sorted(rows, key=lambda r: r["buy_rank"])[:8]:
            move = row["expected_move_pct"]
            print(
                f"#{row['buy_rank']:>3} {row['market']:<12} {row['asset_class']:<9} "
                f"score={row['score']:>4} buy={row['buy_score']:>3} sell={row['sell_score']:>3} "
                f"exp={row['expected_return_pct']} net={row['net_edge_pct']} "
                f"lim={row['net_edge_limit_pct']} "
                f"move={None if move is None else round(move, 2)} act={row['action']} "
                f"ord={row['suggested_order_type']}"
            )
        print("basket:", opus_analysis.select_basket(rows))
        worst = sorted(rows, key=lambda r: r["sell_rank"])[:5]
        print("top sells:", [(r["market"], r["sell_score"], r["sell_edge_pct"]) for r in worst])
finally:
    db.close()

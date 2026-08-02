r"""Standalone offline verification of the GTP56Sol backend integration.

Run: .venv\Scripts\python test_gtp56sol_integration.py
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AppSetting, Base, MarketCandle
from app.routers import markets
from app.services import candle_store, gtp56sol_analysis


passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def utc_day(days_ago: int = 0) -> datetime:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=days_ago)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def add_candles(db: Session, market: str, count: int, *, include_today: bool = False):
    oldest_days_ago = count - 1 if include_today else count
    for index in range(count):
        day = utc_day(oldest_days_ago - index)
        price = Decimal(100 + index)
        db.add(MarketCandle(
            market=market,
            day=day,
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=Decimal(1000),
        ))
    db.commit()


def add_candle_at(db: Session, market: str, day: datetime, price: int = 100):
    value = Decimal(price)
    db.add(MarketCandle(
        market=market,
        day=day,
        open=value,
        high=value + 1,
        low=value - 1,
        close=value,
        volume=Decimal(1000),
    ))
    db.commit()


engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
db = Session(engine)

print("completed stored candles")
has_completed_loader = hasattr(candle_store, "load_completed_daily_candles")
check("completed-day loader exists", has_completed_loader)
if has_completed_loader:
    add_candles(db, "PRIMARY-EUR", 3, include_today=True)
    completed = candle_store.load_completed_daily_candles(
        db, "PRIMARY-EUR", now=utc_day() + timedelta(hours=12)
    )
    check("currently forming UTC day is excluded", len(completed) == 2)
    check("completed candles remain oldest first", completed[0][0] < completed[-1][0])
has_summary = hasattr(candle_store, "completed_history_summary")
check("completed-history summary helper exists", has_summary)
if has_summary:
    summary = candle_store.completed_history_summary(
        db, "PRIMARY-EUR", now=utc_day() + timedelta(hours=12)
    )
    check(
        "history summary returns first last and count",
        summary.count == 2
        and summary.first_ts == completed[0][0]
        and summary.last_ts == completed[-1][0],
    )

print("same-class fallback selection")
original_market_data_service = markets.market_data_service
fake_market_catalog = {
    "PRIMARY-EUR": {"asset_class": "crypto"},
    "ALPHA-EUR": {"asset_class": "crypto"},
    "BETA-EUR": {"asset_class": "crypto"},
    "GAMMA-EUR": {"asset_class": "crypto"},
    "FULL-EUR": {"asset_class": "crypto"},
    "SHORT-EUR": {"asset_class": "crypto"},
    "PEER-EUR": {"asset_class": "crypto"},
    "REAL-EUR": {"asset_class": "crypto"},
    "KIMI-EUR": {"asset_class": "crypto"},
    "STOCK-EUR": {"asset_class": "stock"},
}


class FakeMarketDataService:
    markets = fake_market_catalog

    @staticmethod
    def get_market(market):
        return fake_market_catalog.get(market)


markets.market_data_service = FakeMarketDataService()
has_peer_selector = hasattr(markets, "_select_gtp56sol_peer_markets")
check("bounded peer selector exists", has_peer_selector)
if has_peer_selector:
    add_candles(db, "ALPHA-EUR", 4)
    add_candles(db, "BETA-EUR", 6)
    add_candles(db, "GAMMA-EUR", 6)
    add_candles(db, "STOCK-EUR", 20)
    peers = markets._select_gtp56sol_peer_markets(
        db, "PRIMARY-EUR", "crypto", cap=2
    )
    check("peer pool is deterministically capped", peers == ["BETA-EUR", "GAMMA-EUR"], repr(peers))
    check("primary market is excluded from peers", "PRIMARY-EUR" not in peers)
    check("cross-class market is excluded from peers", "STOCK-EUR" not in peers)

print("route validation and cache key")
has_handler = hasattr(markets, "get_gtp56sol_analysis")
check("dedicated route handler exists", has_handler)
if has_handler:
    async def no_backfill(*args, **kwargs):
        return False

    original_backfill = markets.ensure_gtp56sol_deep_history
    original_loader = markets.load_completed_daily_candles
    original_summary = markets.completed_history_summary
    original_summaries = markets.completed_history_summaries
    original_forecast = markets.gtp56sol_analysis_service.forecast
    original_version = gtp56sol_analysis.ENGINE_VERSION
    calls = []
    stored = [[1_700_000_000_000, "1", "2", "1", "2", "10"]]

    def fake_loader(db, market, now=None):
        return [row[:] for row in stored] if market == "PRIMARY-EUR" else []

    def fake_summary(db, market, now=None):
        rows = stored if market == "PRIMARY-EUR" else []
        return candle_store.CandleHistorySummary(
            rows[0][0] if rows else None,
            rows[-1][0] if rows else None,
            len(rows),
        )

    def fake_forecast(candles, horizon, fallback_candles_by_market=None):
        calls.append((candles[-1][0], horizon, fallback_candles_by_market))
        return {"status": "insufficient_history", "horizon": horizon}

    markets.ensure_gtp56sol_deep_history = no_backfill
    markets.load_completed_daily_candles = fake_loader
    markets.completed_history_summary = fake_summary
    markets.completed_history_summaries = lambda db, peer_markets, now=None: {}
    markets.gtp56sol_analysis_service.forecast = fake_forecast
    markets._gtp56sol_cache.clear()
    try:
        try:
            asyncio.run(markets.get_gtp56sol_analysis(
                "MISSING-EUR", user=object(), db=db, horizon="1w"
            ))
        except HTTPException as exc:
            check("unknown market returns 404", exc.status_code == 404)
        else:
            check("unknown market returns 404", False)

        try:
            asyncio.run(markets.get_gtp56sol_analysis(
                "PRIMARY-EUR", user=object(), db=db, horizon="30d"
            ))
        except HTTPException as exc:
            check("invalid horizon returns 400", exc.status_code == 400)
        else:
            check("invalid horizon returns 400", False)

        first = asyncio.run(markets.get_gtp56sol_analysis(
            "PRIMARY-EUR", user=object(), db=db, horizon="1w"
        ))
        second = asyncio.run(markets.get_gtp56sol_analysis(
            "PRIMARY-EUR", user=object(), db=db, horizon="1w"
        ))
        check("response includes route metadata", {
            "market", "asset_class", "generated_at", "status", "horizon"
        }.issubset(first))
        check("same completed candle uses cached forecast", first == second and len(calls) == 1)
        stored.append([1_700_086_400_000, "2", "3", "2", "3", "11"])
        asyncio.run(markets.get_gtp56sol_analysis(
            "PRIMARY-EUR", user=object(), db=db, horizon="1w"
        ))
        check("new completed candle invalidates cache", len(calls) == 2)
        gtp56sol_analysis.ENGINE_VERSION = "integration-test-version"
        asyncio.run(markets.get_gtp56sol_analysis(
            "PRIMARY-EUR", user=object(), db=db, horizon="1w"
        ))
        check("engine version change invalidates cache", len(calls) == 3)
    finally:
        gtp56sol_analysis.ENGINE_VERSION = original_version
        markets.ensure_gtp56sol_deep_history = original_backfill
        markets.load_completed_daily_candles = original_loader
        markets.completed_history_summary = original_summary
        markets.completed_history_summaries = original_summaries
        markets.gtp56sol_analysis_service.forecast = original_forecast
        markets._gtp56sol_cache.clear()

print("threadpool, fallback planning, and cache identity")
has_sufficiency = hasattr(gtp56sol_analysis, "has_sufficient_asset_history")
has_threadpool = hasattr(markets, "run_in_threadpool")
has_summary_first = (
    hasattr(markets, "completed_history_summary")
    and hasattr(markets, "completed_history_summaries")
)
check("engine exposes candidate sufficiency helper", has_sufficiency)
check("route imports threadpool helper", has_threadpool)
check("route imports single and grouped history summaries", has_summary_first)
if has_sufficiency and has_threadpool:
    add_candles(db, "FULL-EUR", 120)
    add_candles(db, "SHORT-EUR", 60)
    add_candles(db, "PEER-EUR", 110)
    original_backfill = markets.ensure_gtp56sol_deep_history
    original_loader = markets.load_completed_daily_candles
    original_forecast = markets.gtp56sol_analysis_service.forecast
    original_threadpool = markets.run_in_threadpool
    original_grouped_summaries = getattr(markets, "completed_history_summaries", None)
    loaded_markets = []
    forecast_calls = []
    threadpool_calls = []
    grouped_summary_calls = []

    async def no_backfill(*args, **kwargs):
        return False

    def recording_loader(session, market, now=None):
        loaded_markets.append(market)
        return original_loader(session, market, now=now)

    def recording_forecast(candles, horizon, fallback_candles_by_market=None):
        forecast_calls.append({
            "primary": candles,
            "horizon": horizon,
            "fallback": fallback_candles_by_market,
        })
        return {"status": "ok", "horizon": horizon, "probabilities": {}}

    async def threadpool_spy(func, *args, **kwargs):
        threadpool_calls.append((func, args, kwargs))
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    if original_grouped_summaries:
        def grouped_summary_spy(session, peer_markets, now=None):
            grouped_summary_calls.append(tuple(peer_markets))
            return original_grouped_summaries(session, peer_markets, now=now)

        markets.completed_history_summaries = grouped_summary_spy

    markets.ensure_gtp56sol_deep_history = no_backfill
    markets.load_completed_daily_candles = recording_loader
    markets.gtp56sol_analysis_service.forecast = recording_forecast
    markets.run_in_threadpool = threadpool_spy
    markets._gtp56sol_cache.clear()
    if hasattr(markets, "_gtp56sol_inflight_locks"):
        markets._gtp56sol_inflight_locks.clear()
    try:
        full_first = asyncio.run(markets.get_gtp56sol_analysis(
            "FULL-EUR", user=object(), db=db, horizon="1d"
        ))
        check(
            "threadpool helper is awaited and invokes forecast",
            len(threadpool_calls) == 1
            and threadpool_calls[0][0] is recording_forecast
            and full_first["status"] == "ok",
        )
        check(
            "sufficient primary skips all peer row loads",
            loaded_markets == ["FULL-EUR"],
            repr(loaded_markets),
        )
        check(
            "normal forecast receives no fallback mapping",
            forecast_calls[-1]["fallback"] is None,
        )
        loaded_markets.clear()
        threadpool_before_cache_hit = len(threadpool_calls)
        asyncio.run(markets.get_gtp56sol_analysis(
            "FULL-EUR", user=object(), db=db, horizon="1d"
        ))
        check(
            "primary cache hit avoids candle hydration",
            loaded_markets == []
            and len(threadpool_calls) == threadpool_before_cache_hit,
            repr(loaded_markets),
        )

        first_call_count = len(forecast_calls)
        add_candle_at(db, "FULL-EUR", utc_day(250), 80)
        asyncio.run(markets.get_gtp56sol_analysis(
            "FULL-EUR", user=object(), db=db, horizon="1d"
        ))
        check(
            "older primary row invalidates cache identity",
            len(forecast_calls) == first_call_count + 1,
        )

        grouped_summary_calls.clear()
        short_first = asyncio.run(markets.get_gtp56sol_analysis(
            "SHORT-EUR", user=object(), db=db, horizon="1m"
        ))
        short_call = forecast_calls[-1]
        fallback = short_call["fallback"] or {}
        check("route never passes primary in fallback", "SHORT-EUR" not in fallback)
        check("insufficient primary loads fallback rows", bool(fallback))
        check(
            "fallback signatures use one grouped summary query",
            original_grouped_summaries is not None
            and len(grouped_summary_calls) == 1,
            repr(grouped_summary_calls),
        )
        loaded_markets.clear()
        asyncio.run(markets.get_gtp56sol_analysis(
            "SHORT-EUR", user=object(), db=db, horizon="1m"
        ))
        check(
            "fallback cache hit avoids primary and peer hydration",
            loaded_markets == [],
            repr(loaded_markets),
        )
        selected_peer = sorted(fallback)[0] if fallback else None
        peer_call_count = len(forecast_calls)
        if selected_peer:
            add_candle_at(db, selected_peer, utc_day(350), 70)
            asyncio.run(markets.get_gtp56sol_analysis(
                "SHORT-EUR", user=object(), db=db, horizon="1m"
            ))
        check(
            "selected peer history change invalidates cache",
            selected_peer is not None and len(forecast_calls) == peer_call_count + 1,
        )

        short_first["status"] = "mutated"
        short_again = asyncio.run(markets.get_gtp56sol_analysis(
            "SHORT-EUR", user=object(), db=db, horizon="1m"
        ))
        check("cached payload is returned as an independent copy", short_again["status"] == "ok")

        markets._gtp56sol_cache.clear()
        if hasattr(markets, "_gtp56sol_inflight_locks"):
            markets._gtp56sol_inflight_locks.clear()
        stampede_before = len(forecast_calls)

        async def concurrent_requests():
            return await asyncio.gather(
                markets.get_gtp56sol_analysis(
                    "FULL-EUR", user=object(), db=db, horizon="1d"
                ),
                markets.get_gtp56sol_analysis(
                    "FULL-EUR", user=object(), db=db, horizon="1d"
                ),
            )

        asyncio.run(concurrent_requests())
        check(
            "per-key in-flight lock prevents forecast stampede",
            len(forecast_calls) == stampede_before + 1,
        )
    finally:
        markets.ensure_gtp56sol_deep_history = original_backfill
        markets.load_completed_daily_candles = original_loader
        markets.gtp56sol_analysis_service.forecast = original_forecast
        markets.run_in_threadpool = original_threadpool
        if original_grouped_summaries:
            markets.completed_history_summaries = original_grouped_summaries
        markets._gtp56sol_cache.clear()
        if hasattr(markets, "_gtp56sol_inflight_locks"):
            markets._gtp56sol_inflight_locks.clear()

has_cpu_runner = hasattr(markets, "_run_gtp56sol_forecast")
check("bounded CPU forecast runner exists", has_cpu_runner)
if has_cpu_runner:
    original_threadpool = markets.run_in_threadpool
    cpu_state = {"active": 0, "max_active": 0}

    async def delayed_threadpool(func, *args, **kwargs):
        cpu_state["active"] += 1
        cpu_state["max_active"] = max(cpu_state["max_active"], cpu_state["active"])
        await asyncio.sleep(0.01)
        cpu_state["active"] -= 1
        return func(*args, **kwargs)

    markets.run_in_threadpool = delayed_threadpool
    if hasattr(markets, "_gtp56sol_cpu_semaphores"):
        markets._gtp56sol_cpu_semaphores.clear()
    try:
        async def exercise_cpu_bound():
            await asyncio.gather(*(
                markets._run_gtp56sol_forecast(
                    ((1, "1", "1", "1", "1", "1"),),
                    "1d",
                    None,
                    forecast_func=lambda *args, **kwargs: {"status": "test"},
                )
                for _ in range(5)
            ))

        asyncio.run(exercise_cpu_bound())
        check(
            "CPU forecast concurrency is bounded",
            cpu_state["max_active"] <= 2,
            repr(cpu_state),
        )
    finally:
        markets.run_in_threadpool = original_threadpool
        if hasattr(markets, "_gtp56sol_cpu_semaphores"):
            markets._gtp56sol_cpu_semaphores.clear()

print("real-engine route and Kimi isolation")
has_recent_loader = hasattr(candle_store, "load_recent_daily_candles")
check("bounded recent-candle loader exists", has_recent_loader)
if has_recent_loader and has_threadpool:
    add_candles(db, "REAL-EUR", 120)
    add_candles(db, "KIMI-EUR", candle_store.HISTORY_BARS)
    kimi_before = candle_store.load_recent_daily_candles(db, "KIMI-EUR")
    add_candle_at(db, "KIMI-EUR", utc_day(700), 50)
    kimi_after = candle_store.load_recent_daily_candles(db, "KIMI-EUR")
    check(
        "older GTP rows cannot alter Kimi candle inputs",
        len(kimi_before) == candle_store.HISTORY_BARS and kimi_before == kimi_after,
    )
    has_route_recent_loader = hasattr(markets, "load_recent_daily_candles")
    check("Kimi routes import the bounded loader", has_route_recent_loader)
    if has_route_recent_loader:
        original_route_loader = markets.load_recent_daily_candles
        original_route_catalog = markets.market_data_service
        kimi_loader_calls = []

        def recording_recent_loader(session, market, limit=candle_store.HISTORY_BARS):
            rows = original_route_loader(session, market, limit=limit)
            kimi_loader_calls.append((market, len(rows)))
            return rows

        class KimiOnlyMarketData:
            markets = {"KIMI-EUR": {"asset_class": "crypto"}}

        markets.load_recent_daily_candles = recording_recent_loader
        markets.market_data_service = KimiOnlyMarketData()
        markets._kimi_outlooks_cache = None
        markets._kimi_track_record_cache.clear()
        try:
            markets.get_kimi_outlooks(user=object(), db=db)
            markets._kimi_track_record(db, "KIMI-EUR")
            check(
                "Kimi list and track record each receive exactly 400 latest rows",
                kimi_loader_calls == [
                    ("KIMI-EUR", candle_store.HISTORY_BARS),
                    ("KIMI-EUR", candle_store.HISTORY_BARS),
                ],
                repr(kimi_loader_calls),
            )
        finally:
            markets.load_recent_daily_candles = original_route_loader
            markets.market_data_service = original_route_catalog
            markets._kimi_outlooks_cache = None
            markets._kimi_track_record_cache.clear()
    original_backfill = markets.ensure_gtp56sol_deep_history

    async def no_backfill(*args, **kwargs):
        return False

    markets.ensure_gtp56sol_deep_history = no_backfill
    markets._gtp56sol_cache.clear()
    if hasattr(markets, "_gtp56sol_inflight_locks"):
        markets._gtp56sol_inflight_locks.clear()
    try:
        real_result = asyncio.run(markets.get_gtp56sol_analysis(
            "REAL-EUR", user=object(), db=db, horizon="1d"
        ))
        check(
            "real engine completes through route against stored DB rows",
            real_result["status"] == "ok"
            and real_result["market"] == "REAL-EUR"
            and real_result["source_scope"] == "asset",
        )
    except Exception as exc:
        check("real engine completes through route against stored DB rows", False, repr(exc))
    finally:
        markets.ensure_gtp56sol_deep_history = original_backfill
        markets._gtp56sol_cache.clear()

print("lazy deep-history safety")
has_backfill = hasattr(candle_store, "ensure_gtp56sol_deep_history")
check("lazy deep-history helper exists", has_backfill)
if has_backfill:
    original_fetch = candle_store._fetch_gtp56sol_deep_history
    original_upsert = candle_store.upsert_candles
    state = {"attempts": 0, "active": 0, "max_active": 0}

    async def failing_fetch(market, asset_class):
        state["attempts"] += 1
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        raise RuntimeError("offline")

    candle_store._fetch_gtp56sol_deep_history = failing_fetch
    candle_store._gtp_deep_history_locks.clear()
    failed_at = utc_day() + timedelta(hours=10)

    async def exercise_backfill():
        await asyncio.gather(
            candle_store.ensure_gtp56sol_deep_history(
                db, "LOCK-EUR", "crypto", now=failed_at
            ),
            candle_store.ensure_gtp56sol_deep_history(
                db, "LOCK-EUR", "crypto", now=failed_at
            ),
        )
        await candle_store.ensure_gtp56sol_deep_history(
            db, "LOCK-EUR", "crypto", now=failed_at + timedelta(minutes=30)
        )
        await candle_store.ensure_gtp56sol_deep_history(
            db, "LOCK-EUR", "crypto", now=failed_at + timedelta(minutes=61)
        )

    try:
        asyncio.run(exercise_backfill())
        check("upstream failure degrades without raising", True)
        check("per-market lock prevents duplicate concurrent fetch", state["max_active"] == 1)
        check("failure cooldown is approximately one hour", state["attempts"] == 2)
        persisted = db.get(AppSetting, "gtp56sol_deep:LOCK-EUR")
        check("deep-history attempt state is persisted", persisted is not None)
        check("stored history survives upstream failure", candle_store.load_completed_daily_candles(
            db, "PRIMARY-EUR", now=utc_day() + timedelta(hours=12)
        ) != [])
    except Exception as exc:
        check("upstream failure degrades without raising", False, repr(exc))
        check("per-market lock prevents duplicate concurrent fetch", False)
        check("failure cooldown is approximately one hour", False)
        check("deep-history attempt state is persisted", False)
        check("stored history survives upstream failure", False)

    success_calls = {"count": 0}
    old_timestamp = int((utc_day(30) - timedelta(days=10)).timestamp()) * 1000

    async def successful_fetch(market, asset_class):
        success_calls["count"] += 1
        return [[old_timestamp, "10", "11", "9", "10", "100"]]

    candle_store._fetch_gtp56sol_deep_history = successful_fetch
    success_at = failed_at + timedelta(days=2)
    try:
        asyncio.run(candle_store.ensure_gtp56sol_deep_history(
            db, "SUCCESS-EUR", "crypto", now=success_at
        ))
        asyncio.run(candle_store.ensure_gtp56sol_deep_history(
            db, "SUCCESS-EUR", "crypto", now=success_at + timedelta(hours=2)
        ))
        check("successful backfill uses long cooldown", success_calls["count"] == 1)
    except Exception as exc:
        check("successful backfill uses long cooldown", False, repr(exc))

    no_more_market = "NO-MORE-EUR"
    add_candles(db, no_more_market, 1)
    no_more_rows = candle_store.load_completed_daily_candles(db, no_more_market)
    no_more_calls = {"count": 0}

    async def no_older_fetch(market, asset_class):
        no_more_calls["count"] += 1
        return [no_more_rows[0]]

    candle_store._fetch_gtp56sol_deep_history = no_older_fetch
    try:
        asyncio.run(candle_store.ensure_gtp56sol_deep_history(
            db, no_more_market, "crypto", now=success_at
        ))
        asyncio.run(candle_store.ensure_gtp56sol_deep_history(
            db, no_more_market, "crypto", now=success_at + timedelta(hours=25)
        ))
        check(
            "successful fetch with no older rows uses completion cooldown",
            no_more_calls["count"] == 1,
        )
    except Exception as exc:
        check("successful fetch with no older rows uses completion cooldown", False, repr(exc))

    for payload_name, payload in (
        ("empty", []),
        ("fully-invalid", [["bad"], [0, "nan", "1", "1", "1", "1"]]),
    ):
        invalid_market = f"{payload_name.upper()}-EUR"
        invalid_calls = {"count": 0}

        async def invalid_fetch(market, asset_class, response=payload):
            invalid_calls["count"] += 1
            return response

        candle_store._fetch_gtp56sol_deep_history = invalid_fetch
        try:
            first_invalid = asyncio.run(candle_store.ensure_gtp56sol_deep_history(
                db, invalid_market, "crypto", now=success_at
            ))
            asyncio.run(candle_store.ensure_gtp56sol_deep_history(
                db, invalid_market, "crypto",
                now=success_at + timedelta(hours=2),
            ))
            persisted_invalid = db.get(
                AppSetting, f"gtp56sol_deep:{invalid_market}"
            )
            check(
                f"{payload_name} provider payload uses failure cooldown",
                first_invalid["status"] == "failure"
                and invalid_calls["count"] == 2
                and '"status": "failure"' in persisted_invalid.value,
            )
        except Exception as exc:
            check(
                f"{payload_name} provider payload uses failure cooldown",
                False,
                repr(exc),
            )

    rollback_market = "ROLLBACK-EUR"
    add_candles(db, rollback_market, 2)
    before_rollback = candle_store.load_completed_daily_candles(db, rollback_market)

    def failing_upsert(session, market, candles):
        add = candles[0]
        day = datetime.fromtimestamp(add[0] / 1000, timezone.utc)
        value = Decimal("10")
        session.add(MarketCandle(
            market=market, day=day, open=value, high=value, low=value,
            close=value, volume=value,
        ))
        session.flush()
        raise RuntimeError("write failed")

    candle_store.upsert_candles = failing_upsert
    try:
        asyncio.run(candle_store.ensure_gtp56sol_deep_history(
            db, rollback_market, "crypto", now=success_at
        ))
        after_rollback = candle_store.load_completed_daily_candles(db, rollback_market)
        check("rollback preserves same-market stored rows", after_rollback == before_rollback)
    except Exception as exc:
        check("rollback preserves same-market stored rows", False, repr(exc))
    finally:
        candle_store._fetch_gtp56sol_deep_history = original_fetch
        candle_store.upsert_candles = original_upsert
        candle_store._gtp_deep_history_locks.clear()

has_td_spacing = hasattr(candle_store, "_wait_for_gtp_twelvedata_slot")
check("Twelve Data deep-history spacing seam exists", has_td_spacing)
if has_td_spacing:
    original_monotonic = candle_store._monotonic
    original_sleep = candle_store._sleep
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    candle_store._monotonic = lambda: 100.0
    candle_store._sleep = fake_sleep
    candle_store._gtp_td_last_request = 95.0
    if hasattr(candle_store, "_gtp_td_spacing_locks"):
        candle_store._gtp_td_spacing_locks.clear()
    try:
        asyncio.run(candle_store._wait_for_gtp_twelvedata_slot())
        check(
            "Twelve Data deep calls enforce eight-per-minute spacing",
            len(sleeps) == 1 and 2.4 <= sleeps[0] <= 2.6,
            repr(sleeps),
        )
    finally:
        candle_store._monotonic = original_monotonic
        candle_store._sleep = original_sleep
        candle_store._gtp_td_last_request = 0.0
        if hasattr(candle_store, "_gtp_td_spacing_locks"):
            candle_store._gtp_td_spacing_locks.clear()

print("shared Twelve Data gate")
original_gate = candle_store._wait_for_gtp_twelvedata_slot
original_td_service = candle_store.twelvedata_service
original_harvest_catalog = candle_store.market_data_service
original_session_local = candle_store.SessionLocal
original_td_delay = candle_store.TWELVEDATA_DELAY
gate_calls = []


async def gate_spy():
    gate_calls.append("gate")


class FakeTwelveData:
    api_key = "offline-test-key"

    async def fetch_candles(self, market, range_, extra_bars=0):
        return []


class HarvestOnlyMarketData:
    markets = {"HARVEST-EUR": {"asset_class": "stock"}}


candle_store._wait_for_gtp_twelvedata_slot = gate_spy
candle_store.twelvedata_service = FakeTwelveData()
candle_store.market_data_service = HarvestOnlyMarketData()
candle_store.SessionLocal = lambda: Session(engine)
candle_store.TWELVEDATA_DELAY = 0
try:
    async def exercise_both_td_paths():
        await candle_store._fetch_gtp56sol_deep_history("LAZY-EUR", "stock")
        service = candle_store.CandleHarvestService()
        await service._harvest_once()

    asyncio.run(exercise_both_td_paths())
    check(
        "lazy and global harvest paths share the same TD spacing gate",
        gate_calls == ["gate", "gate"],
        repr(gate_calls),
    )
finally:
    candle_store._wait_for_gtp_twelvedata_slot = original_gate
    candle_store.twelvedata_service = original_td_service
    candle_store.market_data_service = original_harvest_catalog
    candle_store.SessionLocal = original_session_local
    candle_store.TWELVEDATA_DELAY = original_td_delay

print("MCP and Kimi preservation")
from app import mcp_server

check("GTP MCP wrapper is exported", hasattr(mcp_server, "get_gtp56sol_analysis"))
tool_names = set(mcp_server.mcp._tool_manager._tools)
check("GTP MCP tool is registered", "get_gtp56sol_analysis" in tool_names)
if hasattr(mcp_server, "get_gtp56sol_analysis"):
    original_session_local = mcp_server.SessionLocal
    original_current_user = mcp_server._current_user
    original_rest_handler = mcp_server._get_gtp56sol_analysis
    wrapper_calls = []

    class FakeDb:
        closed = False

        def close(self):
            self.closed = True

    fake_db = FakeDb()

    async def fake_rest_handler(market, user, db, horizon):
        wrapper_calls.append((market, user, db, horizon))
        return {"market": market, "horizon": horizon}

    mcp_server.SessionLocal = lambda: fake_db
    mcp_server._current_user = lambda db: "authenticated-user"
    mcp_server._get_gtp56sol_analysis = fake_rest_handler
    try:
        wrapper_result = asyncio.run(
            mcp_server.get_gtp56sol_analysis("PRIMARY-EUR", "1m")
        )
        check(
            "GTP MCP wrapper calls the shared REST handler",
            wrapper_result == {"market": "PRIMARY-EUR", "horizon": "1m"}
            and wrapper_calls == [
                ("PRIMARY-EUR", "authenticated-user", fake_db, "1m")
            ],
        )
        check("GTP MCP wrapper closes its DB session", fake_db.closed)
    finally:
        mcp_server.SessionLocal = original_session_local
        mcp_server._current_user = original_current_user
        mcp_server._get_gtp56sol_analysis = original_rest_handler
check("existing Kimi route remains importable", callable(markets.get_kimi_analysis))
check("existing Kimi MCP wrapper remains registered", "get_kimi_analysis" in tool_names)

markets.market_data_service = original_market_data_service
db.close()

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)

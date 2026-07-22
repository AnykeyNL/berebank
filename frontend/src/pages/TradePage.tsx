import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { usePrices } from '../lib/usePrices'
import { fmtAmount, fmtDateTime, fmtEur, fmtPct, fmtPrice } from '../lib/format'
import type { AssetClass, Market, Order, Portfolio, Trade } from '../lib/types'
import AssetClassIcon from '../components/AssetClassIcon'
import FundInfoButton from '../components/FundInfoButton'
import NewsPanel from '../components/NewsPanel'
import OrderForm from '../components/OrderForm'
import PriceChart from '../components/PriceChart'
import StopLossPanel from '../components/StopLossPanel'

type ClassFilter = 'all' | 'mine' | AssetClass
type TradeView = 'trade' | 'news'

export default function TradePage() {
  const { t } = useTranslation()
  const { market: marketParam } = useParams()
  const navigate = useNavigate()
  const { prices, connected } = usePrices()

  const [markets, setMarkets] = useState<Market[]>([])
  const [search, setSearch] = useState('')
  const [classFilter, setClassFilter] = useState<ClassFilter>('all')
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [openOrders, setOpenOrders] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [view, setView] = useState<TradeView>('trade')

  const selected = marketParam ?? 'BTC-EUR'

  const refresh = useCallback(() => {
    api<Portfolio>('/portfolio').then(setPortfolio).catch(() => {})
    api<Order[]>('/orders?status=open').then(setOpenOrders).catch(() => {})
    api<Trade[]>('/trades').then(setTrades).catch(() => {})
  }, [])

  useEffect(() => {
    api<Market[]>('/markets').then(setMarkets).catch(() => {})
    refresh()
  }, [refresh])

  // Refresh account data periodically so limit fills done by the backend show up.
  useEffect(() => {
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    setView('trade')
  }, [selected])

  // Assets the user currently owns (including amounts reserved in open orders).
  const ownedAssets = useMemo(() => {
    const owned = new Set<string>()
    for (const h of portfolio?.holdings ?? []) {
      if (parseFloat(h.amount) + parseFloat(h.reserved) > 0) owned.add(h.asset)
    }
    return owned
  }, [portfolio])

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return markets
      .map((m) => {
        const live = prices[m.market]
        const last = live?.last ?? m.last
        const open = live?.open ?? m.open
        const change =
          last !== null && open && parseFloat(open) !== 0
            ? ((parseFloat(last) - parseFloat(open)) / parseFloat(open)) * 100
            : null
        return { ...m, last, change }
      })
      .filter((m) => {
        if (classFilter === 'all') return true
        if (classFilter === 'mine') return ownedAssets.has(m.base)
        return m.asset_class === classFilter
      })
      .filter((m) => {
        if (!q) return true
        const haystack = `${m.market} ${m.base} ${m.name ?? ''} ${m.listing ?? ''}`.toLowerCase()
        return haystack.includes(q)
      })
      .sort((a, b) => parseFloat(b.volume_quote ?? '0') - parseFloat(a.volume_quote ?? '0'))
  }, [markets, prices, search, classFilter, ownedAssets])

  const selectedPrice = prices[selected]
  const selectedMarket = markets.find((m) => m.market === selected)
  const marketOpen = selectedPrice?.market_open ?? selectedMarket?.market_open ?? null
  const marketClosed = selectedMarket ? selectedMarket.asset_class !== 'crypto' && marketOpen === false : false
  const newsAvailable = selectedMarket?.has_news ?? false
  const baseAsset = selected.split('-')[0]
  const holding = portfolio?.holdings.find((h) => h.asset === baseAsset)
  const livePriceLast = selectedPrice?.last ?? selectedMarket?.last ?? null
  // holding.amount is only the available part; the rest is reserved in open
  // sell orders (limit sells and stop-losses) but still owned by the user.
  const ownedAvailable = holding ? parseFloat(holding.amount) : 0
  const ownedReserved = holding?.reserved ? parseFloat(holding.reserved) : 0
  const ownedTotal = ownedAvailable + ownedReserved
  const holdingValue = !holding
    ? 0
    : livePriceLast !== null
      ? ownedTotal * parseFloat(livePriceLast)
      : null

  const chartLimitOrders = useMemo(
    () =>
      openOrders
        .filter((o) => {
          if (o.market !== selected) return false
          const price = o.order_type === 'stop_loss' ? o.trigger_price : o.limit_price
          return (
            (o.order_type === 'limit' || o.order_type === 'stop_loss') &&
            price !== null &&
            parseFloat(price) > 0
          )
        })
        .map((o) => ({
          id: o.id,
          side: o.side,
          price: parseFloat((o.order_type === 'stop_loss' ? o.trigger_price : o.limit_price)!),
          kind: o.order_type as 'limit' | 'stop_loss',
        })),
    [openOrders, selected],
  )

  const stopLossOrders = useMemo(
    () => openOrders.filter((o) => o.market === selected && o.order_type === 'stop_loss'),
    [openOrders, selected],
  )

  // The open-orders and trade-history panels only show the selected market.
  const marketOrders = useMemo(
    () => openOrders.filter((o) => o.market === selected),
    [openOrders, selected],
  )
  const marketTrades = useMemo(
    () => trades.filter((tr) => tr.market === selected),
    [trades, selected],
  )

  const chartTrades = useMemo(
    () =>
      marketTrades
        .map((tr) => ({
          id: tr.id,
          side: tr.side,
          price: parseFloat(tr.price),
          created_at: tr.created_at,
        })),
    [marketTrades],
  )

  async function cancelOrder(id: number) {
    try {
      await api(`/orders/${id}`, { method: 'DELETE' })
      refresh()
    } catch {
      /* refresh below shows current state */
      refresh()
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
      {/* Market list */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <div className="border-b border-slate-800 p-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('trade.searchPlaceholder')}
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm outline-none focus:border-amber-500"
          />
          <div className="mt-2 flex gap-1">
            {(['all', 'mine', 'crypto', 'stock', 'fund'] as const).map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setClassFilter(c)}
                title={t(`trade.filter.${c}`)}
                aria-label={t(`trade.filter.${c}`)}
                aria-pressed={classFilter === c}
                className={`flex flex-1 items-center justify-center rounded-md py-1.5 transition-colors ${
                  classFilter === c
                    ? 'bg-amber-500/15 text-amber-400'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                {c === 'all' ? (
                  <AllFilterIcon className="h-5 w-5" />
                ) : c === 'mine' ? (
                  <MyAssetsFilterIcon className="h-5 w-5" />
                ) : (
                  <AssetClassIcon assetClass={c} className="h-5 w-5" />
                )}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">
            {connected ? (
              <span className="text-emerald-400">● {t('trade.live')}</span>
            ) : (
              <span className="text-red-400">● {t('trade.reconnecting')}</span>
            )}{' '}
            · {t('trade.marketsCount', { count: rows.length })}
            <span className="lg:hidden">
              {search.trim() || classFilter === 'mine' ? '' : ` · ${t('trade.typeToSearch')}`}
            </span>
          </p>
        </div>
        {/* On mobile the full list is hidden until the user searches or picks "My assets". */}
        <div
          className={`max-h-[40vh] overflow-y-auto lg:max-h-[70vh] ${
            search.trim() || classFilter === 'mine' ? '' : 'hidden lg:block'
          }`}
        >
          {rows.map((m) => (
            <div
              key={m.market}
              className={`flex items-center gap-1 px-3 py-2 transition-colors hover:bg-slate-800/50 ${
                m.market === selected ? 'bg-amber-500/10' : ''
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  setSearch('')
                  navigate(`/trade/${m.market}`)
                }}
                className="flex min-w-0 flex-1 items-center justify-between text-left text-sm"
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 font-medium">
                    <AssetClassIcon assetClass={m.asset_class} className="h-3.5 w-3.5 shrink-0" />
                    {m.base}
                  </span>
                  {m.listing && (
                    <span className="mt-0.5 block truncate text-xs text-slate-500">{m.listing}</span>
                  )}
                </span>
                <span className="text-right">
                  <span className="block font-mono text-xs">{fmtPrice(m.last)}</span>
                  <span
                    className={`block text-xs ${
                      m.change === null ? 'text-slate-500' : m.change >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}
                  >
                    {fmtPct(m.change)}
                  </span>
                </span>
              </button>
              {m.asset_class === 'fund' && <FundInfoButton ticker={m.base} />}
            </div>
          ))}
        </div>
      </div>

      {/* Trading panel */}
      <div className="space-y-6">
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="flex items-center gap-2 text-xl font-bold">
                {selectedMarket && <AssetClassIcon assetClass={selectedMarket.asset_class} className="h-5 w-5" />}
                {selected}
                {selectedMarket?.asset_class === 'fund' && (
                  <FundInfoButton ticker={selectedMarket.base} className="h-6 w-6 text-xs" />
                )}
                {marketClosed && (
                  <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-xs font-medium text-red-400">
                    {t('trade.marketClosed')}
                  </span>
                )}
              </h2>
              {selectedMarket?.name && (
                <p className="text-sm text-slate-300">{selectedMarket.name}</p>
              )}
              <p className="text-xs text-slate-500">
                {selectedMarket
                  ? [selectedMarket.listing, `${selectedMarket.base} / ${selectedMarket.quote}`]
                      .filter(Boolean)
                      .join(' · ')
                  : ''}
              </p>
            </div>
            <div className="flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm sm:w-auto sm:justify-end sm:gap-6">
              <PriceStat label={t('trade.last')} value={fmtPrice(selectedPrice?.last ?? selectedMarket?.last)} />
              <PriceStat label={t('trade.bid')} value={fmtPrice(selectedPrice?.bid ?? selectedMarket?.bid)} />
              <PriceStat label={t('trade.ask')} value={fmtPrice(selectedPrice?.ask ?? selectedMarket?.ask)} />
              <button
                type="button"
                onClick={() => navigate(`/analyze/${selected}`)}
                className="self-center rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800"
              >
                {t('analyze.analyzeButton')}
              </button>
              {newsAvailable && (
                <button
                  type="button"
                  onClick={() => setView((v) => (v === 'trade' ? 'news' : 'trade'))}
                  className={`self-center rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                    view === 'news'
                      ? 'bg-amber-500/15 text-amber-400'
                      : 'border border-slate-700 text-slate-300 hover:bg-slate-800'
                  }`}
                  aria-pressed={view === 'news'}
                >
                  {view === 'news' ? t('trade.showChart') : t('trade.showNews')}
                </button>
              )}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-end gap-x-10 gap-y-3 border-t border-slate-800 pt-3">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">{t('trade.youOwn')}</p>
              <p className="mt-0.5 text-xl font-bold">
                {fmtAmount(ownedTotal)} <span className="text-slate-400">{baseAsset}</span>
                <span className="ml-3 text-amber-400">{fmtEur(holdingValue)}</span>
              </p>
              {ownedReserved > 0 && (
                <p className="text-xs text-slate-500">
                  {t('portfolio.inOpenOrders', { amount: fmtAmount(ownedReserved) })}
                </p>
              )}
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">{t('trade.cash')}</p>
              <p className="mt-0.5 text-xl font-bold">{fmtEur(portfolio?.balance_eur ?? null)}</p>
            </div>
            {portfolio && (
              <p className="pb-1 text-xs text-slate-400">
                {t('trade.feesLine', {
                  maker: portfolio.fee_tier.maker_pct,
                  taker: portfolio.fee_tier.taker_pct,
                })}
              </p>
            )}
          </div>
        </div>

        {view === 'news' && newsAvailable ? (
          <NewsPanel market={selected} />
        ) : (
          <>
            <PriceChart
              market={selected}
              limitOrders={chartLimitOrders}
              trades={chartTrades}
              lastPrice={livePriceLast !== null ? parseFloat(livePriceLast) : null}
            />

            <OrderForm
              market={selected}
              lastPrice={selectedPrice?.last ?? selectedMarket?.last ?? null}
              holdingAmount={holding?.amount ?? null}
              reservedAmount={holding?.reserved ?? null}
              onPlaced={refresh}
            />

            {(holding || stopLossOrders.length > 0) && (
              <StopLossPanel
                market={selected}
                lastPrice={selectedPrice?.last ?? selectedMarket?.last ?? null}
                holdingAmount={holding?.amount ?? null}
                reservedAmount={holding?.reserved ?? null}
                stopLossOrders={stopLossOrders}
                onChanged={refresh}
              />
            )}
          </>
        )}

        {/* Open orders */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/60">
          <h3 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('trade.openOrders')}</h3>
          {marketOrders.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-500">{t('trade.noOpenOrders')}</p>
          ) : (
            <>
            {/* Card list on phones, table on md+ */}
            <div className="divide-y divide-slate-800/60 md:hidden">
              {marketOrders.map((o) => (
                <div key={o.id} className="px-4 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-baseline gap-2">
                      <span className="font-medium">{o.market}</span>
                      <span className={`text-xs font-medium ${o.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {t(`common.${o.side}`)}
                      </span>
                      <span className={`text-xs ${o.order_type === 'stop_loss' ? 'text-amber-400' : 'text-slate-400'}`}>
                        {t(`common.${o.order_type}`)}
                      </span>
                    </span>
                    <button
                      onClick={() => cancelOrder(o.id)}
                      className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800"
                    >
                      {t('common.cancel')}
                    </button>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.amount')}</p>
                      <p className="font-mono">{fmtAmount(o.amount)}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('trade.priceCol')}</p>
                      <p className="font-mono">
                        {fmtPrice(o.order_type === 'stop_loss' ? o.trigger_price : o.limit_price)}
                      </p>
                    </div>
                  </div>
                  <p className="mt-1.5 text-xs text-slate-500">{fmtDateTime(o.created_at)}</p>
                </div>
              ))}
            </div>
            <table className="hidden w-full text-sm md:table">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-4 py-2">{t('trade.marketCol')}</th>
                  <th className="px-4 py-2">{t('trade.side')}</th>
                  <th className="px-4 py-2">{t('trade.typeCol')}</th>
                  <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                  <th className="px-4 py-2 text-right">{t('trade.priceCol')}</th>
                  <th className="px-4 py-2">{t('trade.placed')}</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {marketOrders.map((o) => (
                  <tr key={o.id} className="border-t border-slate-800/60">
                    <td className="px-4 py-2">{o.market}</td>
                    <td className={`px-4 py-2 font-medium ${o.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t(`common.${o.side}`)}
                    </td>
                    <td className={`px-4 py-2 ${o.order_type === 'stop_loss' ? 'text-amber-400' : 'text-slate-400'}`}>
                      {t(`common.${o.order_type}`)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono">{fmtAmount(o.amount)}</td>
                    <td className="px-4 py-2 text-right font-mono">
                      {fmtPrice(o.order_type === 'stop_loss' ? o.trigger_price : o.limit_price)}
                    </td>
                    <td className="px-4 py-2 text-slate-400">{fmtDateTime(o.created_at)}</td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => cancelOrder(o.id)}
                        className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800"
                      >
                        {t('common.cancel')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </div>

        {/* Trade history */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/60">
          <h3 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('common.tradeHistory')}</h3>
          {marketTrades.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-500">{t('common.noTradesYet')}</p>
          ) : (
            <div className="max-h-80 overflow-y-auto">
              {/* Card list on phones, table on md+ */}
              <div className="divide-y divide-slate-800/60 md:hidden">
                {marketTrades.map((tr) => (
                  <div key={tr.id} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-baseline gap-2">
                        <span className="font-medium">{tr.market}</span>
                        <span className={`text-xs font-medium ${tr.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                          {t(`common.${tr.side}`)}
                        </span>
                      </span>
                      <span className="text-xs text-slate-500">{fmtDateTime(tr.created_at)}</span>
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-sm">
                      <div>
                        <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.amount')}</p>
                        <p className="truncate font-mono">{fmtAmount(tr.amount)}</p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.price')}</p>
                        <p className="truncate font-mono">{fmtPrice(tr.price)}</p>
                      </div>
                      <div className="text-right">
                        <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.value')}</p>
                        <p className="truncate font-mono">{fmtEur(tr.eur_value)}</p>
                      </div>
                    </div>
                    <p className="mt-1.5 text-xs text-slate-500">
                      {t('common.fee')}: <span className="font-mono text-slate-400">{fmtEur(tr.fee_eur)}</span>
                    </p>
                  </div>
                ))}
              </div>
              <table className="hidden w-full text-sm md:table">
                <thead className="sticky top-0 bg-slate-900">
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-2">{t('common.time')}</th>
                    <th className="px-4 py-2">{t('trade.marketCol')}</th>
                    <th className="px-4 py-2">{t('trade.side')}</th>
                    <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                    <th className="px-4 py-2 text-right">{t('common.price')}</th>
                    <th className="px-4 py-2 text-right">{t('common.value')}</th>
                    <th className="px-4 py-2 text-right">{t('common.fee')}</th>
                  </tr>
                </thead>
                <tbody>
                  {marketTrades.map((tr) => (
                    <tr key={tr.id} className="border-t border-slate-800/60">
                      <td className="px-4 py-2 text-slate-400">{fmtDateTime(tr.created_at)}</td>
                      <td className="px-4 py-2">{tr.market}</td>
                      <td className={`px-4 py-2 font-medium ${tr.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {t(`common.${tr.side}`)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">{fmtAmount(tr.amount)}</td>
                      <td className="px-4 py-2 text-right font-mono">{fmtPrice(tr.price)}</td>
                      <td className="px-4 py-2 text-right font-mono">{fmtEur(tr.eur_value)}</td>
                      <td className="px-4 py-2 text-right font-mono text-slate-400">{fmtEur(tr.fee_eur)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** Grid of four squares for the "all markets" filter. */
function AllFilterIcon({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      className={className} aria-hidden="true">
      <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
      <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
      <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
      <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
    </svg>
  )
}

/** Wallet for the "my assets" filter. */
function MyAssetsFilterIcon({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      className={className} aria-hidden="true">
      <path d="M12.5 5.5V4.8A1.3 1.3 0 0 0 11.2 3.5H4A1.5 1.5 0 0 0 2.5 5v6.5A1.5 1.5 0 0 0 4 13h8a1.5 1.5 0 0 0 1.5-1.5V7A1.5 1.5 0 0 0 12 5.5H2.5" />
      <path d="M13.5 8.25h-2.25a1.25 1.25 0 0 0 0 2.5h2.25" />
    </svg>
  )
}

function PriceStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-right">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="font-mono">{value}</p>
    </div>
  )
}

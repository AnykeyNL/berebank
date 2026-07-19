import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { usePrices } from '../lib/usePrices'
import { fmtAmount, fmtDateTime, fmtEur, fmtPct, fmtPrice } from '../lib/format'
import type { AssetClass, Market, Order, Portfolio, Trade } from '../lib/types'
import AssetClassIcon from '../components/AssetClassIcon'
import NewsPanel from '../components/NewsPanel'
import OrderForm from '../components/OrderForm'
import PriceChart from '../components/PriceChart'

type ClassFilter = 'all' | AssetClass
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
      .filter((m) => classFilter === 'all' || m.asset_class === classFilter)
      .filter((m) => {
        if (!q) return true
        const haystack = `${m.market} ${m.base} ${m.name ?? ''} ${m.listing ?? ''}`.toLowerCase()
        return haystack.includes(q)
      })
      .sort((a, b) => parseFloat(b.volume_quote ?? '0') - parseFloat(a.volume_quote ?? '0'))
  }, [markets, prices, search, classFilter])

  const selectedPrice = prices[selected]
  const selectedMarket = markets.find((m) => m.market === selected)
  const marketOpen = selectedPrice?.market_open ?? selectedMarket?.market_open ?? null
  const marketClosed = selectedMarket ? selectedMarket.asset_class !== 'crypto' && marketOpen === false : false
  const newsAvailable = selectedMarket ? selectedMarket.asset_class !== 'crypto' : false
  const baseAsset = selected.split('-')[0]
  const holding = portfolio?.holdings.find((h) => h.asset === baseAsset)
  const livePriceLast = selectedPrice?.last ?? selectedMarket?.last ?? null
  const holdingValue = !holding
    ? 0
    : livePriceLast !== null
      ? parseFloat(holding.amount) * parseFloat(livePriceLast)
      : null

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
            {(['all', 'crypto', 'stock', 'fund'] as const).map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setClassFilter(c)}
                className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                  classFilter === c
                    ? 'bg-amber-500/15 text-amber-400'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                {c !== 'all' && <AssetClassIcon assetClass={c} className="h-3.5 w-3.5" />}
                {t(`trade.filter.${c}`)}
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
            <span className="lg:hidden">{search.trim() ? '' : ` · ${t('trade.typeToSearch')}`}</span>
          </p>
        </div>
        {/* On mobile the full list is hidden until the user searches. */}
        <div
          className={`max-h-[40vh] overflow-y-auto lg:max-h-[70vh] ${
            search.trim() ? '' : 'hidden lg:block'
          }`}
        >
          {rows.map((m) => (
            <button
              key={m.market}
              onClick={() => {
                setSearch('')
                navigate(`/trade/${m.market}`)
              }}
              className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors hover:bg-slate-800/50 ${
                m.market === selected ? 'bg-amber-500/10' : ''
              }`}
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
            <div className="flex gap-6 text-sm">
              <PriceStat label={t('trade.last')} value={fmtPrice(selectedPrice?.last ?? selectedMarket?.last)} />
              <PriceStat label={t('trade.bid')} value={fmtPrice(selectedPrice?.bid ?? selectedMarket?.bid)} />
              <PriceStat label={t('trade.ask')} value={fmtPrice(selectedPrice?.ask ?? selectedMarket?.ask)} />
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
                {fmtAmount(holding?.amount ?? '0')} <span className="text-slate-400">{baseAsset}</span>
                <span className="ml-3 text-amber-400">{fmtEur(holdingValue)}</span>
              </p>
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
            <PriceChart market={selected} />

            <OrderForm
              market={selected}
              lastPrice={selectedPrice?.last ?? selectedMarket?.last ?? null}
              holdingAmount={holding?.amount ?? null}
              onPlaced={refresh}
            />
          </>
        )}

        {/* Open orders */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/60">
          <h3 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('trade.openOrders')}</h3>
          {openOrders.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-500">{t('trade.noOpenOrders')}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-4 py-2">{t('trade.marketCol')}</th>
                  <th className="px-4 py-2">{t('trade.side')}</th>
                  <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                  <th className="px-4 py-2 text-right">{t('trade.limitPrice')}</th>
                  <th className="px-4 py-2">{t('trade.placed')}</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {openOrders.map((o) => (
                  <tr key={o.id} className="border-t border-slate-800/60">
                    <td className="px-4 py-2">{o.market}</td>
                    <td className={`px-4 py-2 font-medium ${o.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t(`common.${o.side}`)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono">{fmtAmount(o.amount)}</td>
                    <td className="px-4 py-2 text-right font-mono">{fmtPrice(o.limit_price)}</td>
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
          )}
        </div>

        {/* Trade history */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/60">
          <h3 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('common.tradeHistory')}</h3>
          {trades.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-500">{t('common.noTradesYet')}</p>
          ) : (
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-sm">
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
                  {trades.map((tr) => (
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

function PriceStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-right">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="font-mono">{value}</p>
    </div>
  )
}

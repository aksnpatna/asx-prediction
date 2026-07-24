import React, { useState, useEffect, useMemo } from 'react'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL || '/api'
const STARTING_CAPITAL = 30000

export default function WealthTab({ token, preferredMarket }) {
  const [suggestions, setSuggestions] = useState([])
  const [paperTrades, setPaperTrades] = useState([])
  const [wfo, setWfo] = useState(null)
  const [loading, setLoading] = useState(true)

  const authH = () => ({ headers: { Authorization: `Bearer ${token}` } })

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const [sugRes, tradesRes, wfoRes] = await Promise.allSettled([
          axios.get(`${API_BASE}/suggestions/tracking`, { ...authH(), params: { days: 90 } }),
          axios.get(`${API_BASE}/paper-trades`, authH()),
          axios.get(`${API_BASE}/walk-forward/oos`, authH()),
        ])

        if (sugRes.status === 'fulfilled') {
          setSuggestions(sugRes.value.data?.suggestions || [])
        }
        if (tradesRes.status === 'fulfilled') {
          setPaperTrades(tradesRes.value.data || [])
        }
        if (wfoRes.status === 'fulfilled') {
          setWfo(wfoRes.value.data || null)
        }
      } catch (e) {
        console.error('Wealth load:', e)
      }
      setLoading(false)
    }
    load()
  }, [token, preferredMarket])

  const portfolio = useMemo(() => {
    const closed = paperTrades.filter(t => t.status === 'closed')
    const open = paperTrades.filter(t => t.status === 'open')
    const realizedPnl = closed.reduce((s, t) => s + (t.current_pnl || 0), 0)
    const invested = open.reduce((s, t) => s + ((t.entry_price || 0) * (t.qty || 0)), 0)
    const totalEquity = STARTING_CAPITAL + realizedPnl
    return {
      totalEquity: totalEquity.toFixed(0),
      availableCash: (totalEquity - invested).toFixed(0),
      invested: invested.toFixed(0),
      realizedPnl: realizedPnl.toFixed(0),
      openCount: open.length,
      closedCount: closed.length,
    }
  }, [paperTrades])

  const evalStats = useMemo(() => {
    const evaluated = suggestions.filter(s => s.status !== 'PENDING')
    const pending = suggestions.filter(s => s.status === 'PENDING')
    const hits = evaluated.filter(s => s.status === 'HIT')
    const byTier = { '10pct': { total: 0, hits: 0 }, '8pct': { total: 0, hits: 0 } }
    evaluated.forEach(s => {
      const t = s.tier
      if (byTier[t]) {
        byTier[t].total++
        if (s.status === 'HIT') byTier[t].hits++
      }
    })
    return {
      totalEvaluated: evaluated.length,
      totalPending: pending.length,
      overallHitRate: evaluated.length ? (hits.length / evaluated.length * 100).toFixed(0) : '-',
      byTier,
    }
  }, [suggestions])

  const sectorExposure = useMemo(() => {
    const sectors = {}
    paperTrades.filter(t => t.status === 'open').forEach(t => {
      const s = t.sector || 'Unknown'
      sectors[s] = (sectors[s] || 0) + ((t.entry_price || 0) * (t.qty || 0))
    })
    const total = Object.values(sectors).reduce((a, b) => a + b, STARTING_CAPITAL)
    return Object.entries(sectors).map(([k, v]) => ({ sector: k, value: v, pct: (v / total * 100).toFixed(1) }))
  }, [paperTrades])

  const wfoLatest30d = wfo?.latest_30d
  const wfoLatest63d = wfo?.latest_63d
  const wfoLatest90d = wfo?.latest_90d
  const capitalGate = wfo?.capital_gate

  if (loading) return <div style={{ padding: 40, color: '#94a3b8' }}>Loading wealth...</div>

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1100, margin: '0 auto' }}>
      {/* ── Portfolio Dashboard ────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ color: '#e2e8f0', fontSize: 22, margin: '0 0 12px 0' }}>Wealth Dashboard</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
          <MetricCard label="Total Equity" value={`$${portfolio.totalEquity}`} color="#e2e8f0" />
          <MetricCard label="Available Cash" value={`$${portfolio.availableCash}`} color="#34d399" />
          <MetricCard label="Invested" value={`$${portfolio.invested}`} color="#fbbf24" />
          <MetricCard label="Realized P&L" value={`${Number(portfolio.realizedPnl) >= 0 ? '+' : ''}$${portfolio.realizedPnl}`}
            color={Number(portfolio.realizedPnl) >= 0 ? '#34d399' : '#f87171'} />
          <MetricCard label="Open Positions" value={portfolio.openCount} color="#60a5fa" />
          <MetricCard label="Closed Trades" value={portfolio.closedCount} color="#94a3b8" />
        </div>
      </div>

      {/* ── Evaluation Tracking ────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <h3 style={{ color: '#e2e8f0', fontSize: 16, margin: '0 0 12px 0' }}>
          Evaluation Tracking (Peak-within-Window)
        </h3>
        <div style={{ background: '#1e293b', borderRadius: 12, border: '1px solid #334155', padding: 20 }}>
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 16 }}>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>Overall Hit Rate</div>
              <div style={{ color: Number(evalStats.overallHitRate) >= 70 ? '#34d399' : '#fbbf24', fontSize: 28, fontWeight: 700 }}>
                {evalStats.overallHitRate}%
              </div>
              <div style={{ color: '#64748b', fontSize: 11 }}>{evalStats.totalEvaluated} evaluated, {evalStats.totalPending} pending</div>
            </div>
            <div style={{ borderLeft: '1px solid #334155', paddingLeft: 24 }}>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>10% Tier</div>
              <div style={{ color: '#a3e635', fontSize: 20, fontWeight: 700 }}>
                {evalStats.byTier['10pct'].total ? (evalStats.byTier['10pct'].hits / evalStats.byTier['10pct'].total * 100).toFixed(0) : '-'}%
              </div>
              <div style={{ color: '#64748b', fontSize: 11 }}>
                {evalStats.byTier['10pct'].hits}/{evalStats.byTier['10pct'].total} trades
              </div>
            </div>
            <div style={{ borderLeft: '1px solid #334155', paddingLeft: 24 }}>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>8% Tier</div>
              <div style={{ color: '#60a5fa', fontSize: 20, fontWeight: 700 }}>
                {evalStats.byTier['8pct'].total ? (evalStats.byTier['8pct'].hits / evalStats.byTier['8pct'].total * 100).toFixed(0) : '-'}%
              </div>
              <div style={{ color: '#64748b', fontSize: 11 }}>
                {evalStats.byTier['8pct'].hits}/{evalStats.byTier['8pct'].total} trades
              </div>
            </div>
          </div>

          {/* Horizon grid — use actual_peak_return_90d from API */}
          <div style={{ color: '#94a3b8', fontSize: 12, marginBottom: 8 }}>Evaluation by Horizon (peak return within window)</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
            {[14, 30, 63, 90].map(d => {
              const rows = suggestions.filter(s => s.evaluated && s.actual_peak_90d != null)
              const hits = rows.filter(s => {
                const v = s.actual_peak_90d
                const target = s.tier === '10pct' ? 10 : s.tier === '8pct' ? 8 : null
                return target != null && v != null && v >= target
              })
              return (
                <div key={d} style={{ background: '#0f172a', borderRadius: 8, padding: 12, textAlign: 'center' }}>
                  <div style={{ color: '#94a3b8', fontSize: 11 }}>{d}-Day</div>
                  <div style={{ color: '#e2e8f0', fontSize: 20, fontWeight: 700 }}>
                    {rows.length ? (hits.length / rows.length * 100).toFixed(0) : '-'}%
                  </div>
                  <div style={{ color: '#64748b', fontSize: 10 }}>{rows.length} checked</div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* ── WFO Status ────────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <h3 style={{ color: '#e2e8f0', fontSize: 16, margin: '0 0 12px 0' }}>Walk-Forward OOS Validation</h3>
        <div style={{ background: '#1e293b', borderRadius: 12, border: '1px solid #334155', padding: 16 }}>
          {/* Capital Gate Status */}
          {capitalGate && (
            <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ color: '#94a3b8', fontSize: 13 }}>Capital Gate:</span>
              <span style={{
                background: capitalGate.state === 'GREEN' ? '#14532d' : capitalGate.state === 'AMBER' ? '#78350f' : capitalGate.state === 'RED' ? '#7f1d1d' : '#1e293b',
                color: capitalGate.state === 'GREEN' ? '#4ade80' : capitalGate.state === 'AMBER' ? '#fbbf24' : capitalGate.state === 'RED' ? '#f87171' : '#64748b',
                padding: '3px 12px', borderRadius: 12, fontSize: 13, fontWeight: 600,
              }}>{capitalGate.state}</span>
              <span style={{ color: '#64748b', fontSize: 11 }}>Max {capitalGate.rules?.max_positions || '?'} positions, {capitalGate.rules?.max_allocation_pct || '?'}% allocation</span>
            </div>
          )}

          {/* Per-horizon WFO metrics */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
            {[
              { label: '30-Day', data: wfoLatest30d },
              { label: '63-Day', data: wfoLatest63d },
              { label: '90-Day', data: wfoLatest90d },
            ].map(({ label, data }) => (
              <div key={label} style={{ background: '#0f172a', borderRadius: 8, padding: 12 }}>
                <div style={{ color: '#94a3b8', fontSize: 12, marginBottom: 8 }}>{label} Horizon</div>
                {data ? (
                  <>
                    <div style={{ display: 'flex', gap: 16, fontSize: 12 }}>
                      <div>
                        <div style={{ color: '#64748b' }}>Sharpe</div>
                        <div style={{ color: (data.oos_sharpe || 0) >= 0 ? '#34d399' : '#f87171', fontWeight: 600 }}>
                          {(data.oos_sharpe || 0).toFixed(2)}
                        </div>
                      </div>
                      <div>
                        <div style={{ color: '#64748b' }}>Hit Rate</div>
                        <div style={{ color: '#e2e8f0', fontWeight: 600 }}>{(data.hit_rate_pct || 0).toFixed(0)}%</div>
                      </div>
                      <div>
                        <div style={{ color: '#64748b' }}>Signals</div>
                        <div style={{ color: '#e2e8f0', fontWeight: 600 }}>{data.total_signals || 0}</div>
                      </div>
                    </div>
                    {data.oos_sharpe_ci_lower != null && (
                      <div style={{ color: '#64748b', fontSize: 10, marginTop: 4 }}>
                        95% CI: [{data.oos_sharpe_ci_lower.toFixed(2)}, {data.oos_sharpe_ci_upper?.toFixed(2) || '?'}]
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ color: '#64748b', fontSize: 12 }}>No data yet</div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Suggestions Tracking Table ─────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <h3 style={{ color: '#e2e8f0', fontSize: 16, margin: '0 0 12px 0' }}>
          Suggestions Tracking ({suggestions.length})
        </h3>
        <div style={{ background: '#1e293b', borderRadius: 12, border: '1px solid #334155', overflow: 'hidden' }}>
          <div style={{ maxHeight: 500, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#0f172a', position: 'sticky', top: 0 }}>
                  <th style={{ padding: '10px 12px', textAlign: 'left', color: '#94a3b8', fontWeight: 500 }}>Symbol</th>
                  <th style={{ padding: '10px 12px', textAlign: 'left', color: '#94a3b8', fontWeight: 500 }}>Tier</th>
                  <th style={{ padding: '10px 12px', textAlign: 'right', color: '#94a3b8', fontWeight: 500 }}>Entry</th>
                  <th style={{ padding: '10px 12px', textAlign: 'right', color: '#94a3b8', fontWeight: 500 }}>Target</th>
                  <th style={{ padding: '10px 12px', textAlign: 'right', color: '#94a3b8', fontWeight: 500 }}>Days Ago</th>
                  <th style={{ padding: '10px 12px', textAlign: 'right', color: '#94a3b8', fontWeight: 500 }}>Peak Ret</th>
                  <th style={{ padding: '10px 12px', textAlign: 'center', color: '#94a3b8', fontWeight: 500 }}>Status</th>
                  <th style={{ padding: '10px 12px', textAlign: 'center', color: '#94a3b8', fontWeight: 500 }}>Days Left</th>
                </tr>
              </thead>
              <tbody>
                {suggestions.length === 0 ? (
                  <tr><td colSpan={8} style={{ padding: 30, textAlign: 'center', color: '#64748b' }}>
                    No suggestions yet. First scan data appears after 5 AM Mon-Fri.
                  </td></tr>
                ) : (
                  suggestions.slice(0, 100).map((s, i) => {
                    const daysLeft = s.screened_at && s.status === 'PENDING'
                      ? Math.max(0, 90 - (s.days_ago || 0))
                      : null
                    return (
                    <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '8px 12px', color: '#e2e8f0', fontWeight: 600 }}>{s.symbol}</td>
                      <td style={{ padding: '8px 12px' }}>
                        <span style={{
                          background: s.tier === '10pct' ? '#365314' : s.tier === '8pct' ? '#1e3a5f' : '#1e293b',
                          color: s.tier === '10pct' ? '#a3e635' : s.tier === '8pct' ? '#60a5fa' : '#64748b',
                          padding: '1px 8px', borderRadius: 8, fontSize: 11, fontWeight: 600,
                        }}>{s.tier === '10pct' ? '10%' : s.tier === '8pct' ? '8%' : s.tier || '-'}</span>
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: '#cbd5e1' }}>
                        ${typeof s.entry === 'number' ? s.entry.toFixed(2) : '-'}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: '#cbd5e1' }}>
                        {s.target_price ? `$${s.target_price.toFixed(2)}` : '-'}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: '#94a3b8' }}>
                        {s.days_ago != null ? s.days_ago : '-'}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (s.actual_peak_90d || 0) >= 0 ? '#34d399' : '#f87171' }}>
                        {s.actual_peak_90d != null ? `${s.actual_peak_90d >= 0 ? '+' : ''}${s.actual_peak_90d}%` : '-'}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                        <span style={{
                          background: s.status === 'HIT' ? '#14532d' : s.status === 'MISS' ? '#7f1d1d' : '#1e293b',
                          color: s.status === 'HIT' ? '#4ade80' : s.status === 'MISS' ? '#f87171' : '#64748b',
                          padding: '2px 10px', borderRadius: 8, fontSize: 11, fontWeight: 600,
                        }}>{s.status}</span>
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'center', color: '#64748b', fontSize: 11 }}>
                        {daysLeft !== null ? `${daysLeft}d` : '-'}
                      </td>
                    </tr>
                  )})
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ── Sector Exposure ─────────────────────────────────────────────────── */}
      {sectorExposure.length > 0 && (
        <div>
          <h3 style={{ color: '#e2e8f0', fontSize: 16, margin: '0 0 12px 0' }}>Sector Exposure</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {sectorExposure.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ color: '#cbd5e1', fontSize: 13, minWidth: 180 }}>{s.sector}</span>
                <div style={{ flex: 1, background: '#0f172a', borderRadius: 4, height: 8, overflow: 'hidden' }}>
                  <div style={{
                    width: `${Math.min(parseFloat(s.pct), 100)}%`,
                    height: '100%', borderRadius: 4,
                    background: parseFloat(s.pct) > 25 ? '#f87171' : '#34d399',
                    transition: 'width 0.3s',
                  }} />
                </div>
                <span style={{ color: '#94a3b8', fontSize: 12, minWidth: 60, textAlign: 'right' }}>
                  ${(s.value / 1000).toFixed(1)}k ({s.pct}%)
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MetricCard({ label, value, color = '#e2e8f0' }) {
  return (
    <div style={{ background: '#1e293b', borderRadius: 10, border: '1px solid #334155', padding: '14px 16px' }}>
      <div style={{ color: '#94a3b8', fontSize: 11, marginBottom: 4 }}>{label}</div>
      <div style={{ color, fontSize: 18, fontWeight: 700 }}>{value}</div>
    </div>
  )
}

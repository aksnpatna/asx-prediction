import React, { useState, useEffect, useMemo } from 'react'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL || '/api'
// NOTE: STARTING_CAPITAL is now loaded from the API — see budgetInfo below.

// ── Projection math ─────────────────────────────────────────────────────────
function projectCapital(startingCap, annualRate, years) {
  return Array.from({ length: years + 1 }, (_, i) => Math.round(startingCap * Math.pow(1 + annualRate, i)))
}

const SCENARIOS = [
  { key: 'conservative', label: 'Conservative', rate: 0.144, color: '#9CA3AF' },
  { key: 'base',         label: 'Base',          rate: 0.183, color: '#0F0F0F' },
  { key: 'bull',         label: 'Bull',          rate: 0.223, color: '#1A6B3C' },
]

const CALENDAR_DATA = [
  { month: 'Jan', stars: 2, avg: 3.8, winRate: 54, note: '' },
  { month: 'Feb', stars: 0, avg: -0.4, winRate: 38, note: 'Avoid' },
  { month: 'Mar', stars: 0, avg: 0.1, winRate: 40, note: 'Avoid' },
  { month: 'Apr', stars: 3, avg: 4.9, winRate: 62, note: 'Strong entry' },
  { month: 'May', stars: 1, avg: 1.8, winRate: 46, note: '' },
  { month: 'Jun', stars: 0, avg: -0.8, winRate: 35, note: 'Avoid' },
  { month: 'Jul', stars: 3, avg: 5.2, winRate: 64, note: 'Strong entry' },
  { month: 'Aug', stars: 2, avg: 3.2, winRate: 54, note: '' },
  { month: 'Sep', stars: 0, avg: 0.4, winRate: 42, note: 'Caution' },
  { month: 'Oct', stars: 0, avg: 0.9, winRate: 43, note: 'Caution' },
  { month: 'Nov', stars: 3, avg: 5.97, winRate: 59, note: 'Best entry month' },
  { month: 'Dec', stars: 1, avg: 1.2, winRate: 47, note: '' },
]

export default function WealthTab({ token, preferredMarket }) {
  const [suggestions, setSuggestions] = useState([])
  const [paperTrades, setPaperTrades] = useState([])
  const [wfo, setWfo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [smsfData, setSmsfData] = useState(null)
  const [startingCap, setStartingCap] = useState(200000)
  const [scenario, setScenario] = useState('base')
  const [capInput, setCapInput] = useState('')
  const [editingCap, setEditingCap] = useState(false)

  const authH = () => ({ headers: { Authorization: `Bearer ${token}` } })

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const [sugRes, tradesRes, wfoRes, dashRes, budgetRes] = await Promise.allSettled([
          axios.get(`${API_BASE}/suggestions/tracking`, { ...authH(), params: { days: 90 } }),
          axios.get(`${API_BASE}/paper-trades`, authH()),
          axios.get(`${API_BASE}/walk-forward/oos`, authH()),
          axios.get(`${API_BASE}/smsf/dashboard`, authH()),
          axios.get(`${API_BASE}/user/investment-budget`, authH()),
        ])

        if (sugRes.status === 'fulfilled') {
          setSuggestions(sugRes.value.data?.suggestions || [])
        }
        if (tradesRes.status === 'fulfilled') {
          setPaperTrades(Array.isArray(tradesRes.value.data) ? tradesRes.value.data : (tradesRes.value.data?.items || []))
        }
        if (wfoRes.status === 'fulfilled') {
          setWfo(wfoRes.value.data || null)
        }
        if (dashRes.status === 'fulfilled') {
          setSmsfData(dashRes.value.data)
        }
        // Real starting capital from budget API
        if (budgetRes.status === 'fulfilled') {
          const b = budgetRes.value.data
          const cap = b?.total_budget || b?.starting_capital || 200000
          setStartingCap(cap)
        } else if (dashRes.status === 'fulfilled') {
          // Fallback: portfolio starting capital from dashboard
          const sc = dashRes.value.data?.portfolio?.starting_capital
          if (sc && sc > 0) setStartingCap(sc)
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
    const realizedPnl = closed.reduce((s, t) => s + (t.unrealized_pnl_value || 0), 0)
    const invested = open.reduce((s, t) => s + ((t.entry_price || 0) * (t.quantity || 0)), 0)
    const totalEquity = startingCap + realizedPnl
    return {
      totalEquity: totalEquity.toFixed(0),
      availableCash: (totalEquity - invested).toFixed(0),
      invested: invested.toFixed(0),
      realizedPnl: realizedPnl.toFixed(0),
      openCount: open.length,
      closedCount: closed.length,
    }
  }, [paperTrades, startingCap])

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
      sectors[s] = (sectors[s] || 0) + ((t.entry_price || 0) * (t.quantity || 0))
    })
    const total = Object.values(sectors).reduce((a, b) => a + b, startingCap)
    return Object.entries(sectors).map(([k, v]) => ({ sector: k, value: v, pct: (v / total * 100).toFixed(1) }))
  }, [paperTrades])

  const wfoLatest30d = wfo?.latest_30d
  const wfoLatest63d = wfo?.latest_63d
  const wfoLatest90d = wfo?.latest_90d
  const capitalGate = wfo?.capital_gate

  if (loading) return (
    <div style={{ padding: 40, textAlign: 'center', color: '#6B7280', fontFamily: "'Outfit', sans-serif" }}>
      <div style={{ fontSize: 32, marginBottom: 12 }}>📈</div>
      Loading your wealth projector…
    </div>
  )

  // ── G-Gate live data ────────────────────────────────────────────────────────
  const closedTradeCount = paperTrades.filter(t => t.status === 'closed').length
  const auc = smsfData?.model?.auc
  const topDecileRaw = (() => {
    try { const m = (smsfData?.model?.notes || '').match(/topDecile=(\d+\.?\d*)/); return m ? parseFloat(m[1]) : null } catch { return null }
  })()
  const g1Done = true // Survivorship de-bias complete from MODEL_IMPROVEMENT_LOG Fix 6
  const g2Done = topDecileRaw != null && topDecileRaw >= 60
  const g3Done = closedTradeCount >= 60
  const g3Count = Math.min(closedTradeCount, 60)

  const gGates = [
    { id: 'G1', done: g1Done, label: 'Survivorship bias removed — 1,858 delisted stocks added', plain: 'Training on realistic market data (includes stocks that went bust)' },
    { id: 'G2', done: g2Done, label: `WFO top-decile ≥60% OOS — ${topDecileRaw ? topDecileRaw.toFixed(1) + '% current' : 'in progress'}`, plain: `AI wins ${topDecileRaw ? topDecileRaw.toFixed(1) : '—'}% of top picks vs 60% target` },
    { id: 'G3', done: g3Done, label: `60 paper trades — ${g3Count}/60 logged`, plain: `Paper trading proof: ${g3Count} of 60 test trades complete` },
    { id: 'G4', done: false, label: 'Micro-live $500–$1K positions', plain: 'First real money: small test trades with real dollars' },
    { id: 'G5', done: false, label: 'Risk limits consistent + CI ≥ 0.5', plain: '12 months of consistent results before full deployment' },
  ]

  // ── Projection chart data ───────────────────────────────────────────────────
  const startYear = new Date().getFullYear()
  const years = 9
  const yearLabels = Array.from({ length: years + 1 }, (_, i) => startYear + i)
  const projections = {}
  SCENARIOS.forEach(s => { projections[s.key] = projectCapital(startingCap, s.rate, years) })
  const activeScenario = SCENARIOS.find(s => s.key === scenario)
  const milestoneYears = [2, 4, 7, 9]

  // ── Calendar ────────────────────────────────────────────────────────────────
  const currentMonth = new Date().getMonth() // 0 = Jan
  const nextStrongMonth = CALENDAR_DATA.findIndex((m, i) => i > currentMonth && m.stars >= 2)
  const currentMonthData = CALENDAR_DATA[currentMonth]

  const fmtK = n => n >= 1000000 ? `$${(n / 1000000).toFixed(2)}M` : `$${Math.round(n / 1000)}K`

  return (
    <div style={{ fontFamily: "'Outfit', -apple-system, BlinkMacSystemFont, sans-serif", background: '#F9FAFB', minHeight: '100vh', paddingBottom: 80 }}>
      <div style={{ maxWidth: 860, margin: '0 auto', padding: '20px 16px' }}>

        {/* ── Wealth Projector ──────────────────────────────────────────────── */}
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '20px 22px', marginBottom: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 800, color: '#0F0F0F', marginBottom: 4 }}>Wealth Projector</div>
          <div style={{ fontSize: 13, color: '#6B7280', marginBottom: 16 }}>$200K → $1M by 2035 — scenario planner</div>

          {/* Capital input */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            {editingCap ? (
              <>
                <input
                  type="number"
                  value={capInput}
                  onChange={e => setCapInput(e.target.value)}
                  style={{ border: '1.5px solid #0F0F0F', borderRadius: 8, padding: '6px 12px', fontSize: 16, fontWeight: 700, width: 160 }}
                  autoFocus
                />
                <button onClick={() => { const v = parseFloat(capInput); if (v > 0) setStartingCap(v); setEditingCap(false); }} style={{ background: '#0F0F0F', color: '#fff', border: 'none', borderRadius: 8, padding: '6px 14px', cursor: 'pointer', fontWeight: 700 }}>Save</button>
                <button onClick={() => setEditingCap(false)} style={{ background: '#F3F4F6', border: 'none', borderRadius: 8, padding: '6px 14px', cursor: 'pointer' }}>Cancel</button>
              </>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, border: '1px solid #E5E7EB', borderRadius: 8, padding: '8px 14px', cursor: 'pointer' }} onClick={() => { setCapInput(startingCap); setEditingCap(true); }}>
                <span style={{ fontWeight: 800, fontSize: 18, color: '#0F0F0F' }}>${startingCap.toLocaleString()} SMSF</span>
                <span style={{ color: '#9CA3AF' }}>✎</span>
              </div>
            )}
          </div>

          {/* Scenario selector */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
            {SCENARIOS.map(s => (
              <button key={s.key} onClick={() => setScenario(s.key)} style={{
                padding: '7px 14px', borderRadius: 20, border: '1.5px solid',
                borderColor: scenario === s.key ? '#0F0F0F' : '#E5E7EB',
                background: scenario === s.key ? '#0F0F0F' : '#fff',
                color: scenario === s.key ? '#fff' : '#6B7280',
                fontWeight: 700, fontSize: 12, cursor: 'pointer',
              }}>
                {s.label}<br />
                <span style={{ fontSize: 10, fontWeight: 400 }}>{(s.rate * 100).toFixed(1)}%/yr net</span>
              </button>
            ))}
          </div>

          {/* Simple ASCII-style projection bar chart */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginBottom: 8 }}>Projected SMSF value — {activeScenario?.label} scenario</div>
            {yearLabels.filter((_, i) => i > 0 && i % 2 === 0 || i === years).map((yr, i) => {
              const idx = yearLabels.indexOf(yr)
              const val = projections[scenario][idx]
              const maxVal = projections['bull'][years]
              const barWidth = Math.round((val / maxVal) * 100)
              return (
                <div key={yr} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <div style={{ fontSize: 11, color: '#6B7280', width: 36, textAlign: 'right', flexShrink: 0 }}>{yr}</div>
                  <div style={{ flex: 1, background: '#F3F4F6', borderRadius: 4, height: 20, overflow: 'hidden' }}>
                    <div style={{ width: `${barWidth}%`, height: '100%', background: '#1A6B3C', borderRadius: 4, display: 'flex', alignItems: 'center', paddingLeft: 8 }}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: '#fff', whiteSpace: 'nowrap' }}>{fmtK(val)}</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Milestone cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
            {milestoneYears.map(y => (
              <div key={y} style={{ background: '#F9FAFB', border: '1px solid #E5E7EB', borderRadius: 8, padding: '10px 12px', textAlign: 'center' }}>
                <div style={{ fontSize: 11, color: '#9CA3AF' }}>{startYear + y}</div>
                <div style={{ fontWeight: 800, fontSize: 16, color: '#0F0F0F' }}>{fmtK(projections[scenario][y])}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ── SMSF Tax Advantage ────────────────────────────────────────────── */}
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 20px', marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#0F0F0F', marginBottom: 10 }}>💰 SMSF Tax Advantage</div>
          {[
            '15% SMSF tax rate — vs your personal marginal rate (up to 47%). Every dollar saved in tax compounds.',
            'Fully franked dividends from CBA/BHP add an effective ~0.6%/yr on top of the stated yield.',
            'Hold satellite trades >12 months → CGT drops to ~10% effective rate (1/3 SMSF discount).',
          ].map((t, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8, fontSize: 13, color: '#374151' }}>
              <span style={{ color: '#1A6B3C', fontWeight: 700, flexShrink: 0 }}>●</span>
              <span>{t}</span>
            </div>
          ))}
        </div>

        {/* ── G-Gate Validation Progress ────────────────────────────────────── */}
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 20px', marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#0F0F0F', marginBottom: 4 }}>G-GATE VALIDATION PROGRESS</div>
          <div style={{ fontSize: 12, color: '#6B7280', marginBottom: 14 }}>5 checkpoints before investing real SMSF money</div>
          {gGates.map((g, i) => (
            <div key={g.id} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', marginBottom: 12 }}>
              <span style={{ fontSize: 18, flexShrink: 0 }}>{g.done ? '✅' : i === 1 && !g2Done ? '🟡' : i === 2 && !g3Done && g3Count > 0 ? '🟡' : '🔲'}</span>
              <div>
                <div style={{ fontWeight: 700, fontSize: 13, color: '#0F0F0F' }}>{g.id}: {g.plain}</div>
                <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 2 }}>{g.label}</div>
              </div>
            </div>
          ))}
          <div style={{ marginTop: 14, padding: '10px 14px', background: '#E8F5EE', borderRadius: 8, fontSize: 13, color: '#1A6B3C', fontWeight: 600 }}>
            Estimated live capital date: NOVEMBER 2026
          </div>
        </div>

        {/* ── Deployment Calendar ────────────────────────────────────────────── */}
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 12, padding: '16px 20px', marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#0F0F0F', marginBottom: 4 }}>OPTIMAL DEPLOYMENT MONTHS</div>
          <div style={{ fontSize: 12, color: '#6B7280', marginBottom: 14 }}>Based on 10+ years of ASX monthly seasonality data</div>

          {/* Current month highlight */}
          <div style={{ background: currentMonthData.stars >= 2 ? '#E8F5EE' : currentMonthData.stars === 0 ? '#FEF2F2' : '#FFFBEB', border: `1px solid ${currentMonthData.stars >= 2 ? '#1A6B3C' : currentMonthData.stars === 0 ? '#DC2626' : '#D97706'}22`, borderRadius: 8, padding: '10px 14px', marginBottom: 14, fontSize: 13 }}>
            <strong style={{ color: currentMonthData.stars >= 2 ? '#1A6B3C' : currentMonthData.stars === 0 ? '#DC2626' : '#D97706' }}>
              {CALENDAR_DATA[currentMonth].month} — NOW {currentMonthData.stars >= 3 ? '★★★' : currentMonthData.stars >= 2 ? '★★' : currentMonthData.stars === 1 ? '★' : '✗'}
            </strong>
            <span style={{ color: '#6B7280', marginLeft: 8 }}>
              {currentMonthData.note || (currentMonthData.stars >= 2 ? 'Good month to enter new positions' : currentMonthData.stars === 0 ? 'Avoid new positions this month' : 'Neutral — be selective')}
            </span>
            <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 4 }}>
              Historical avg: {currentMonthData.avg >= 0 ? '+' : ''}{currentMonthData.avg}% · Win rate: {currentMonthData.winRate}%
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 4 }}>
            {CALENDAR_DATA.map((m, i) => {
              const isNow = i === currentMonth
              const starsStr = m.stars >= 3 ? '★★★' : m.stars >= 2 ? '★★' : m.stars === 1 ? '★' : m.note === 'Avoid' ? '✗' : '·'
              const bgColor = isNow ? '#0F0F0F' : m.stars >= 3 ? '#E8F5EE' : m.stars === 0 ? '#FEF2F2' : '#fff'
              const textColor = isNow ? '#fff' : m.stars >= 3 ? '#1A6B3C' : m.stars === 0 ? '#DC2626' : '#6B7280'
              return (
                <div key={m.month} title={`${m.note || ''} · ${m.avg >= 0 ? '+' : ''}${m.avg}% avg · ${m.winRate}% win rate`} style={{ background: bgColor, border: '1px solid #E5E7EB', borderRadius: 6, padding: '6px 4px', textAlign: 'center', cursor: 'help' }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: isNow ? '#9CA3AF' : '#9CA3AF' }}>{m.month}</div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: textColor }}>{starsStr}</div>
                </div>
              )
            })}
          </div>
          <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 8 }}>
            November: best entry month of the year — +5.97% avg, 59% win rate.
          </div>
        </div>

      {/* ── Existing sections below (WFO, tracking etc) ──────────────────── */}
      <div style={{ padding: '0' }}>

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
      </div>
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

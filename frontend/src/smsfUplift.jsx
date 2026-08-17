/*
 * SMSF UI Uplift — Emergent-grade screens (Fix 33).
 *
 * New design system (light, confident), 5-screen structure:
 *   Dashboard (morning brief) · Screener · Portfolio · Wealth · Legacy (old tabs)
 *
 * All data comes from the new read-only endpoints:
 *   /api/dashboard/morning-brief, /api/screener/scan,
 *   /api/portfolio/nav-breakdown, /api/model/health-summary,
 *   /api/wealth/projection, /api/positions/:id/detail
 *
 * Legacy screens remain untouched and reachable from the nav bar.
 */
import React, { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { Line } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler } from 'chart.js'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler)

const API_BASE = import.meta.env.VITE_API_URL || '/api'

const authH = (token) => ({ headers: { Authorization: `Bearer ${token}` } })

function useApi(path, token, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    if (!token) return
    let alive = true
    setLoading(true)
    axios.get(`${API_BASE}${path}`, authH(token))
      .then(r => { if (alive) { setData(r.data); setError(null) } })
      .catch(e => { if (alive) setError(e.message) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [token, path, ...deps])
  return { data, error, loading }
}

const fmtMoney = (v) => v == null ? '—' : `$${Number(v).toLocaleString('en-AU', { maximumFractionDigits: 0 })}`

// ══════════════════════════════ Components ══════════════════════════════

export function CircuitBreakerBanner({ level, drawdown_pct, vix, xjo }) {
  const map = {
    NORMAL: { cls: 'sx-ok', title: 'RISK STATUS: ALL CLEAR ●',
      text: 'Safe to open new trades today.' },
    YELLOW: { cls: 'sx-amber', title: 'RISK STATUS: CAUTION ●',
      text: 'Your portfolio is down from its peak. No new trades until it recovers above the watermark.' },
    ORANGE: { cls: 'sx-orange', title: 'RISK STATUS: HIGH CAUTION ●',
      text: 'Drawdown is significant — cut to at most 4 satellite positions. No new buys.' },
    RED: { cls: 'sx-red', title: 'RISK STATUS: STOP 🛑',
      text: 'Satellite trading is FROZEN — 8-week cooling period. Your money is safe in cash.' },
  }
  const m = map[level] || map.NORMAL
  const calm = vix != null ? (vix < 15 ? 'very calm' : vix < 20 ? 'calm' : vix < 25 ? 'elevated' : 'high') : null
  return (
    <div className={`sx-breaker ${m.cls}`}>
      <span className="sx-breaker-dot" /> {m.title}
      <div className="sx-breaker-meta">
        {m.text}{' '}
        {calm != null && `Market volatility (VIX ${vix}) is ${calm}.`}
      </div>
    </div>
  )
}

export function StockCard({ c, onAI, aiOpen }) {
  const pct = c.institutional_pct
  return (
    <div className="sx-card">
      <div className="sx-card-head">
        <div>
          <span className="sx-sym">{c.symbol}</span>
          <span className="sx-market"> [ASX]</span>
          <div className="sx-name">{c.name || c.symbol}</div>
        </div>
        <div className="sx-score">
          <span className="sx-score-num">{c.score}</span>
          <span className={`sx-pill ${c.tier === '10pct' ? 'sx-pill-gold' : c.tier === '8pct' ? 'sx-pill-green' : 'sx-pill-grey'}`}>
            {c.tier === '10pct' ? 'HIGH ↑' : c.tier === '8pct' ? 'BUY' : 'WATCH'}
          </span>
        </div>
      </div>
      <div className="sx-price-row">
        <div><div className="sx-meta">PRICE</div><div className="sx-price">{c.price ? `$${c.price.toFixed(2)}` : '—'}</div></div>
        <div className="sx-target"><div className="sx-meta">⊙ TARGET +8%</div>
          <div className="sx-price">{c.target_price ? `$${c.target_price.toFixed(2)}` : '—'}</div>
          {c.reachable_63d && <div className="sx-reach">≤63D ✓</div>}
        </div>
      </div>
      <div className="sx-stats">
        <div className="sx-stat"><div className="sx-meta">EXPECTED VALUE<br />PER TRADE</div><div className="sx-stat-val">{c.ev_est_pct != null ? `${c.ev_est_pct > 0 ? '+' : ''}${c.ev_est_pct.toFixed(2)}%` : '—'}</div></div>
        <div className="sx-stat"><div className="sx-meta">HITS +8%<br />WITHIN 63 DAYS</div><div className="sx-stat-val">{c.reachable_63d ? 'LIKELY ✓' : 'NOT SURE'}</div></div>
        <div className="sx-stat"><div className="sx-meta">SUGGESTED<br />SMSF SIZE</div><div className="sx-stat-val">{fmtMoney(c.smsf_size)}</div></div>
      </div>
      <div className="sx-badges">
        <span className="sx-badge">{c.momentum_20d != null ? `↗ 20d ${c.momentum_20d > 0 ? '+' : ''}${c.momentum_20d}%` : ''}</span>
        {c.rsi != null && <span className="sx-badge">RSI {Number(c.rsi).toFixed(1)}</span>}
        {c.trend && <span className="sx-badge">{c.trend}</span>}
        {pct != null && <span className="sx-badge">🏦 Inst: {pct}%</span>}
        {c.pe != null && <span className="sx-badge">📊 PE: {c.pe}</span>}
        {c.div_yield != null && <span className="sx-badge">💰 Div: {c.div_yield}%</span>}
        {c.franked && <span className="sx-badge sx-badge-frank">⊕ FRANKED</span>}
      </div>
      <button className="sx-ai-btn" onClick={() => onAI && onAI(c.symbol)}>
        ✦ AI ANALYST NOTE
      </button>
      {aiOpen && (
        <div className="sx-ai-note">
          <div className="sx-ai-sec"><b>✅ BULL CASE:</b> {pct != null && pct >= 24 ? `Institutional owners hold ${pct}% (above the ~24% market average) — smart money is present. ` : ''}{c.momentum_20d != null && c.momentum_20d > 0 ? `Price momentum is positive over the last 20 days (+${c.momentum_20d}%). ` : ''}{c.rsi != null && c.rsi >= 40 && c.rsi <= 65 ? `RSI ${Number(c.rsi).toFixed(0)} shows strength without being overbought.` : ''}</div>
          <div className="sx-ai-sec"><b>⚠️ BEAR CASE:</b> If market volatility spikes (VIX above 25) or the ASX200 falls below its 200-day average, this trade carries drawdown risk beyond the −20% catastrophe stop. Check for earnings announcements in the next 14 days before entering.</div>
          <div className="sx-ai-verdict">🏦 SMSF VERDICT: {c.tier === '10pct' ? 'BUY (Satellite, 63-day hold)' : c.tier === '8pct' ? 'CONDITIONAL BUY' : 'WATCH ONLY'} · Suggested size: {fmtMoney(c.smsf_size)} · Catastrophe stop: −20% · CGT: hold &gt;12 months for the 10% rate</div>
        </div>
      )}
    </div>
  )
}

export function SatelliteClock({ pos }) {
  const d = Math.min(pos.day_of_63 || 0, 63)
  const pct = Math.min(100, Math.round(d / 63 * 100))
  const cls = pos.target_reached ? 'sx-clock-green' : d >= 56 ? 'sx-clock-red' : d >= 41 ? 'sx-clock-amber' : 'sx-clock-grey'
  let status = { text: 'On track — plenty of time in the 63-day window.', cls: 'sx-meta' }
  if (pos.target_reached) status = { text: 'Target +8% reached — consider selling to lock in the gain.', cls: 'sx-gain' }
  else if (d > 63) status = { text: 'Exit window EXPIRED — review urgently: exit or consciously hold.', cls: 'sx-loss' }
  else if (d >= 56) status = { text: 'Exit within the next few days or consciously hold past the window.', cls: 'sx-loss' }
  else if (d >= 41) status = { text: 'Window tightening — monitor closely.', cls: 'sx-amber-text' }
  return (
    <div className={`sx-clock-card ${pos.target_reached ? 'sx-ring-green' : ''}`}>
      <div className="sx-clock-head">
        <span className="sx-sym">{pos.symbol}</span>
        <span className="sx-meta">📅 Day {d} of 63</span>
      </div>
      <div className="sx-bar"><div className={`sx-bar-fill ${cls}`} style={{ width: `${pct}%` }} /></div>
      <div className="sx-clock-body">
        Entered {fmtMoney(pos.entry_price)} · Now {fmtMoney(pos.current_price)} ·{' '}
        <b className={pos.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{pos.pnl_pct > 0 ? '+' : ''}{pos.pnl_pct}%</b>
      </div>
      {pos.target_price && <div className="sx-meta">Target: {fmtMoney(pos.target_price)} (+8%) · Stop: {pos.catastrophe_stop_price != null ? fmtMoney(pos.catastrophe_stop_price) : '—'} (−20%)</div>}
      {pos.time_stop_date && <div className="sx-meta">Exit window closes: {pos.time_stop_date}</div>}
      <div className={status.cls}>{status.text}</div>
    </div>
  )
}

export function CoreSleeveRow({ pos }) {
  return (
    <div className="sx-core-row">
      <div className="sx-sym">{pos.symbol}</div>
      <div className="sx-core-mid">
        <div className="sx-meta">Fully franked candidate · Div yield via broker</div>
        <div className="sx-meta">HOLD — thesis intact · CGT: {pos.cgt_note || 'countdown active'}</div>
      </div>
      <div className="sx-core-val">
        <div>{fmtMoney(pos.value)}</div>
        <div className={pos.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{pos.pnl_pct > 0 ? '+' : ''}{pos.pnl_pct}% ↑</div>
      </div>
    </div>
  )
}

export function GGateProgress({ gates, paperClosed }) {
  const icons = { done: '✅', amber: '🟡', open: '🔲' }
  const MEANING = {
    G1: 'No survivor bias — the model trained on 1,858 stocks that later disappeared. Realistic.',
    G2: 'The model must pick winners in 60% of its top-ranked stocks (today: ~59% in-sample, still proving itself out-of-sample).',
    G3: 'We only trade with pretend money until 60 test trades are logged — then real, small positions.',
    G4: 'First real-money positions of $500–$1,000 — the proving ground.',
    G5: 'Full SMSF deployment only after consistent results for 12 months.',
  }
  const list = gates && Object.keys(gates).length ? gates : {
    G1: { status: 'done', label: 'Survivorship de-biased (1,858 delisted added)' },
    G2: { status: 'amber', label: 'WFO top-decile ≥60% OOS — in progress' },
    G3: { status: 'open', label: `60 paper trades — ${paperClosed != null ? paperClosed + '/60' : 'logging'}` },
    G4: { status: 'open', label: 'Micro-live $500–$1K positions' },
    G5: { status: 'open', label: 'Risk limits consistent + CI ≥ 0.5' },
  }
  return (
    <div className="sx-ggate">
      <div className="sx-ggate-icons">{Object.keys(list).map(k => <span key={k}>{icons[list[k].status] || '🔲'} {k}</span>)}</div>
      {Object.entries(list).map(([k, v]) => (
        <div key={k} className="sx-ggate-row"><b>{k}:</b> {v.label}
          <div className="sx-meta">— {MEANING[k] || ''}</div>
        </div>
      ))}
      <div className="sx-meta">Estimated date to start investing real money: NOVEMBER 2026 — and only if the paper results hold up.</div>
    </div>
  )
}

export function CalendarBar() {
  const CAL = [
    { m: 'Jan', stars: 2, note: '' },
    { m: 'Feb', stars: 0, note: 'Historically weak — reduce exposure' },
    { m: 'Mar', stars: 0, note: 'Historically weak — reduce exposure' },
    { m: 'Apr', stars: 3, note: 'Strong entry month' },
    { m: 'May', stars: 1, note: '' },
    { m: 'Jun', stars: 0, note: 'Historically weak — reduce exposure' },
    { m: 'Jul', stars: 3, note: 'Strong entry month' },
    { m: 'Aug', stars: 2, note: 'Moderate entry — safe to open up to 3 new trades' },
    { m: 'Sep', stars: 0, note: 'Statistically weak — consider waiting' },
    { m: 'Oct', stars: 0, note: 'Statistically weak — consider waiting' },
    { m: 'Nov', stars: 3, note: 'Best entry month of the year (+5.97% avg, 59% hit rate)' },
    { m: 'Dec', stars: 1, note: '' },
  ]
  const now = new Date()
  const thisMonth = now.getMonth()
  const thisYear = now.getFullYear()
  const cur = CAL[thisMonth]
  const stars = '★'.repeat(cur.stars) + '☆'.repeat(3 - cur.stars)
  const nextBest = CAL.map((c, i) => ({ ...c, i })).filter(c => c.stars === 3 && c.i !== thisMonth)
  const nextAvoid = CAL.map((c, i) => ({ ...c, i })).filter(c => c.stars === 0 && c.i !== thisMonth)
  const later = (i) => i > thisMonth ? i : i + 12
  const soonBest = nextBest.sort((a, b) => later(a.i) - later(b.i))[0]
  const soonAvoid = nextAvoid.sort((a, b) => later(a.i) - later(b.i))[0]
  return (
    <div className="sx-cal">
      <div className="sx-meta">BEST MONTHS TO OPEN NEW POSITIONS</div>
      <div className="sx-cal-now">
        <b>{cur.m} {thisYear} — NOW</b> <span className="sx-cal-stars">{stars} {cur.stars >= 3 ? 'MAX CONVICTION' : cur.stars === 2 ? 'MODERATE ENTRY' : cur.stars === 1 ? 'NEUTRAL' : 'REDUCE EXPOSURE'}</span>
      </div>
      <div className="sx-meta">“{cur.note}.”</div>
      {soonBest && soonBest.i !== thisMonth && (
        <div className="sx-cal-next">NEXT BEST: <b>{soonBest.m} ★★★</b> — “{soonBest.note}”</div>
      )}
      {soonAvoid && soonAvoid.i !== thisMonth && (
        <div className="sx-cal-next">AVOID: <b>{soonAvoid.m} ⚠️</b> — “{soonAvoid.note}”</div>
      )}
      <div className="sx-cal-bar">{CAL.map((c, i) => (
        <span key={c.m} className={`sx-cal-m ${i === thisMonth ? 'sx-cal-now' : ''}`}>{c.m} {'★'.repeat(c.stars) || '✗'}</span>
      ))}</div>
    </div>
  )
}

export function WealthProjector({ data, capital, setCapital }) {
  if (!data) return null
  const colors = { Conservative: '#6B7280', Base: '#1A6B3C', Bull: '#0F0F0F' }
  const ds = Object.entries(data.scenarios).map(([name, s]) => ({
    label: `${name} (${s.nav[s.nav.length - 1] >= 1e6 ? 'M' : 'K'} target)`,
    data: s.nav, borderColor: colors[name], backgroundColor: colors[name] + '18',
    fill: true, tension: 0.35, pointRadius: 0,
  }))
  const chartData = { labels: data.scenarios.Base.years, datasets: ds }
  return (
    <div className="sx-projector">
      <div className="sx-capital">
        <span className="sx-meta">SMSF CAPITAL</span>
        <input className="sx-capital-input" type="number" value={capital}
          onChange={e => setCapital(e.target.value)}
          onBlur={() => setCapital(Math.max(10000, Math.min(5000000, Number(capital) || 200000)))} />
      </div>
      <Line data={chartData} options={{ responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'top' } },
        scales: { y: { ticks: { callback: v => '$' + (v / 1000) + 'K' } } } }} />
      <div className="sx-milestones">
        {data.milestones.filter(m => m.scenario === 'Base').map(m => (
          <div key={m.year} className="sx-milestone"><div className="sx-milestone-year">{m.year}</div><div className="sx-milestone-nav">{fmtMoney(m.nav)}</div></div>
        ))}
      </div>
      <div className="sx-tax">
        <div>• 15% SMSF rate vs your marginal rate — saves thousands/yr</div>
        <div>• Fully franked dividends (CBA/BHP): effective yield ~{data.tax.franked_effective_yield}% after credits</div>
        <div>• {data.tax.cgt_note}</div>
      </div>
    </div>
  )
}

// ══════════════════════════════ Screens ══════════════════════════════

function Screen({ children, title, subtitle, right }) {
  return (
    <div className="sx-page">
      <div className="sx-page-head">
        <div>
          <div className="sx-page-title">{title}</div>
          {subtitle && <div className="sx-page-sub">{subtitle}</div>}
        </div>
        {right}
      </div>
      {children}
    </div>
  )
}

export function DashboardScreen({ token, onRunScan }) {
  const { data } = useApi('/dashboard/morning-brief', token)
  const [runState, setRunState] = useState('idle')
  const d = data || {}
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning ☀️' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const openCount = d.satellite_summary?.length || 0
  const now = new Date()
  const marketOpen = now.getDay() >= 1 && now.getDay() <= 5 && hour >= 10 && hour < 16
  const edge = d.model?.top_decile ? (d.model.top_decile / 21.3).toFixed(1) : null
  const posStatus = (p) => {
    if (p.flag === 'target_reached') return { text: 'Target +8% reached — consider selling to lock in the gain.', cls: 'sx-gain' }
    const days = p.days || 0
    if (days > 63) return { text: 'Exit window has EXPIRED — review: exit or consciously hold.', cls: 'sx-loss' }
    if (days >= 56) return { text: 'Exit window closes soon — plan your exit within days.', cls: 'sx-loss' }
    if (days >= 41) return { text: 'Window tightening — monitor this one closely.', cls: 'sx-amber-text' }
    return { text: 'Still within your 63-day exit window — plenty of time.', cls: 'sx-meta' }
  }
  const runScan = () => {
    setRunState('running')
    axios.post(`${API_BASE}/broad-scan/run`, {}, authH(token)).catch(() => {})
    if (onRunScan) onRunScan()
    setTimeout(() => setRunState('done'), 1500)
  }
  return (
    <Screen title={greeting} subtitle={new Date().toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}>
      <div className="sx-greeting">
        Your SMSF is tracking <b>{openCount} active {openCount === 1 ? 'trade' : 'trades'}</b>.
        <br />{marketOpen
          ? 'The market is open — today\'s opportunities are being scanned.'
          : d.regime?.allow_new === false
            ? 'The model\'s safety rules are pausing new buys right now.'
            : 'The market is closed right now — the next scan runs before open.'}
      </div>
      <CircuitBreakerBanner level={d.circuit_breaker?.level} drawdown_pct={d.circuit_breaker?.drawdown_pct} vix={d.regime?.vix} xjo={d.regime?.xjo_vs_sma200_pct} />
      <div className="sx-regime">
        ● MARKET OUTLOOK: <b>{d.regime?.label || 'UNKNOWN'}</b>
        {d.regime?.vix != null && <> — volatility (VIX {d.regime.vix}) is {d.regime.vix < 15 ? 'very calm' : d.regime.vix < 20 ? 'calm' : d.regime.vix < 25 ? 'elevated' : 'high'}</>}
        {d.regime?.xjo_vs_sma200_pct != null && <> · ASX200 is {d.regime.xjo_vs_sma200_pct >= 0 ? 'above' : 'below'} its 200-day average</>}
        {d.regime?.allow_new === false ? ' · ⛔ safety rules pause new buys' : ' · safe to open new trades'}
      </div>

      {d.actions && d.actions.length > 0 && (
        <div className="sx-actions">
          <div className="sx-section-title">TODAY'S ACTIONS</div>
          {d.actions.map((a, i) => (
            <div key={i} className={`sx-action ${a.kind === 'exit' ? 'sx-action-red' : 'sx-action-green'}`}>
              {a.kind === 'exit' ? '🔴' : '✅'} <b>{a.symbol}:</b> {a.text}
            </div>
          ))}
        </div>
      )}

      <div className="sx-section-title">MY ACTIVE TRADES ({openCount} open)</div>
      <div className="sx-summary-list">
        {(d.satellite_summary || []).map((s, i) => {
          const st = posStatus(s)
          return (
            <div key={i} className="sx-summary-card">
              <div className="sx-summary-row">
                <span className="sx-sym">{s.symbol}</span>
                <div className="sx-bar sx-bar-sm"><div className={`sx-bar-fill ${s.days >= 56 ? 'sx-clock-red' : s.days >= 41 ? 'sx-clock-amber' : 'sx-clock-grey'}`} style={{ width: `${Math.min(100, (s.days || 0) / 63 * 100)}%` }} /></div>
                <span className="sx-meta">Day {s.days}/63</span>
                <b className={s.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{s.pnl_pct > 0 ? '+' : ''}{s.pnl_pct}%</b>
                {s.flag === 'exit_window' && ' 🔴'}{s.flag === 'target_reached' && ' ✅'}
              </div>
              <div className={st.cls}>{st.text}</div>
            </div>
          )
        })}
      </div>

      {d.announcements && d.announcements.length > 0 && (
        <div className="sx-section-title">LATEST COMPANY NEWS</div>
      )}
      {d.announcements && d.announcements.map((a, i) => (
        <div key={i} className="sx-action">
          📣 <b>{a.symbol}:</b> {a.title} <span className={a.sentiment > 0 ? 'sx-gain' : a.sentiment < 0 ? 'sx-loss' : ''}>[{a.sentiment > 0 ? '+' : ''}{a.sentiment}]</span>
        </div>
      ))}

      <div className="sx-section-title">MODEL HEALTH {d.model?.top_decile != null && d.model.top_decile >= 45 ? '✅ STRONG' : '🟡 WATCH'}</div>
      <div className="sx-model-plain">
        {d.model?.top_decile != null ? (
          <>The AI correctly picked winners in <b>{d.model.top_decile}%</b> of its top-ranked stocks
            — vs about <b>21%</b> if you picked at random.{edge != null && <> That's <b>{edge}× better than chance</b>.</>}{' '}
            It learns from the last 10 months of ASX data and re-trains every morning.</>
        ) : 'Model health data is being prepared — check back after the morning training run.'}
      </div>
      <button className="sx-run-btn" onClick={runScan} disabled={runState === 'running'}>
        {runState === 'running' ? '⏳ SCANNING…' : '🔍 RUN TODAY\'S SCAN'}
      </button>
    </Screen>
  )
}

export function ScreenerScreen({ token, onRunScan }) {
  const { data, loading } = useApi('/screener/scan', token)
  const [filter, setFilter] = useState('all')
  const [aiOpen, setAiOpen] = useState(null)
  const [runState, setRunState] = useState('idle')
  const d = data || {}
  const cands = (d.candidates || []).filter(c =>
    filter === 'all' ? true :
    filter === 'reachable' ? c.reachable_63d :
    filter === 'high' ? c.tier === '10pct' :
    filter === 'satellite' ? c.tier !== 'watch' : true)
  const runScan = () => {
    setRunState('running')
    axios.post(`${API_BASE}/broad-scan/run`, {}, authH(token)).catch(() => {})
    if (onRunScan) onRunScan()
    setTimeout(() => setRunState('done'), 1500)
  }
  return (
    <Screen title="SMSF Screener" subtitle="RULE-BASED · AI-ASSISTED · PATH-AWARE MODEL"
      right={<button className="sx-run-btn" onClick={runScan} disabled={runState === 'running'}>{runState === 'running' ? '⏳ SCANNING…' : '↻ RUN NEW SCAN'}</button>}>
      <div className="sx-hero">
        <div className="sx-hero-cell"><div className="sx-hero-num">{d.stocks_screened || '—'}</div><div className="sx-meta">STOCKS SCREENED<br />(today)</div></div>
        <div className="sx-hero-cell"><div className="sx-hero-num">{d.top_decile_hit != null ? d.top_decile_hit + '%' : '—'}</div><div className="sx-meta">WINNERS IN TOP<br />RANKED STOCKS</div></div>
        <div className="sx-hero-cell"><div className="sx-hero-num">{d.high_conviction ?? '—'}</div><div className="sx-meta">HIGH CONFIDENCE<br />PICKS TODAY</div></div>
        <div className="sx-hero-cell"><div className="sx-hero-num">{d.model_edge != null ? d.model_edge.toFixed(2) + '×' : '—'}</div><div className="sx-meta">BETTER THAN<br />RANDOM PICKS</div></div>
      </div>
      <div className="sx-meta sx-last-scan">
        {d.top_decile_hit != null ? (
          <>Out of every 10 stocks the AI ranks highest, about <b>{(d.top_decile_hit / 10).toFixed(1)}</b> hit +8%
            within 63 days — vs <b>2.1</b> if you picked at random.{' '}</>
        ) : null}
        Last scan: {d.last_scan || 'never'} · Honest numbers from the live model.
      </div>
      <div className="sx-chips">
        {[['all', '● ALL PICKS'], ['reachable', '≤63 DAYS — hits target soon'], ['high', 'HIGH CONFIDENCE — model very sure'], ['satellite', 'SATELLITE — AI picks']].map(([k, label]) => (
          <button key={k} className={`sx-chip ${filter === k ? 'sx-chip-on' : ''}`} onClick={() => setFilter(k)}>{label}</button>
        ))}
      </div>
      {loading && <div className="sx-meta">Loading latest scan…</div>}
      {cands.map(c => (
        <StockCard key={c.symbol} c={c} aiOpen={aiOpen === c.symbol} onAI={(s) => setAiOpen(aiOpen === s ? null : s)} />
      ))}
      {!loading && cands.length === 0 && <div className="sx-meta">No scan results yet — run a scan.</div>}
    </Screen>
  )
}

export function PortfolioScreen({ token }) {
  const { data } = useApi('/portfolio/nav-breakdown', token)
  const breaker = useApi('/smsf/dashboard', token)
  const d = data || {}
  const cb = breaker.data?.circuit_breaker || {}
  return (
    <Screen title="My SMSF Portfolio" subtitle="What you own, how it's doing, what needs attention">
      <CircuitBreakerBanner level={cb.level} drawdown_pct={cb.drawdown_pct} />
      <div className="sx-nav-card">
        <div className="sx-hero-num">{fmtMoney(d.nav)}</div>
        <div className={d.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{d.pnl_pct > 0 ? '+' : ''}{d.pnl_pct}% since inception</div>
        <div className="sx-bar">
          <div className="sx-bar-fill sx-split-core" style={{ width: `${d.core_pct || 0}%` }} />
          <div className="sx-bar-fill sx-split-sat" style={{ width: `${d.satellite_pct || 0}%` }} />
        </div>
        <div className="sx-meta">CORE HOLDINGS {d.core_pct || 0}% · AI PICKS {d.satellite_pct || 0}%</div>
        <div className="sx-model-plain">
          Your core holdings (buy-and-hold blue chips) make up {d.core_pct || 0}% of your SMSF;
          the AI picks {d.satellite_pct || 0}% — {d.satellite_pct != null && d.satellite_pct <= 40 ? 'within your 30–40% safety target. ✅' : 'above the 40% safety target — new AI entries are paused until it comes back down. ⚠️'}
        </div>
      </div>

      <div className="sx-section-title">CORE HOLDINGS — BUY &amp; HOLD</div>
      {(d.core_positions || []).map(p => <CoreSleeveRow key={p.id} pos={p} />)}
      {(!d.core_positions || d.core_positions.length === 0) && <div className="sx-meta">No core positions yet (CBA/BHP/WOW/ETFs per strategy).</div>}

      <div className="sx-section-title">AI PICKS — THE 63-DAY CLOCK</div>
      {(d.satellite_positions || []).map(p => <SatelliteClock key={p.id} pos={p} />)}

      {(d.cgt_alerts || []).map((a, i) => (
        <div key={i} className="sx-action sx-action-amber">
          💡 TAX TIP: {a.symbol} has a ${Math.round(a.gain).toLocaleString()} gain.
          Holding {a.days_to_discount} more days (past 12 months) saves ~${Math.round(a.gain * 0.05).toLocaleString()} in tax.
        </div>
      ))}
      {d.gate_alerts && d.gate_alerts.map((g, i) => (
        <div key={i} className="sx-action sx-action-red">⚠️ {g}</div>
      ))}
      <div className="sx-section-title">SECTOR EXPOSURE</div>
      {(d.sector_exposure || []).map(s => (
        <div key={s.sector} className="sx-sector-row">
          <span>{s.sector}</span>
          <div className="sx-bar sx-bar-sm"><div className={`sx-bar-fill ${s.near_cap ? 'sx-clock-amber' : 'sx-clock-grey'}`} style={{ width: `${Math.min(100, s.pct / s.cap * 100)}%` }} /></div>
          <span className="sx-meta">{s.pct}% / {s.cap}% cap</span>
        </div>
      ))}
    </Screen>
  )
}

export function WealthScreen({ token }) {
  const [capital, setCapital] = useState(200000)
  const { data, loading } = useApi(`/wealth/projection?capital=${capital}`, token, [capital])
  const health = useApi('/model/health-summary', token)
  return (
    <Screen title="Wealth Projector" subtitle="$200K → $1M by 2035 — scenario planner">
      {!loading && data && <WealthProjector data={data} capital={capital} setCapital={setCapital} />}
      <div className="sx-section-title">G-GATE VALIDATION PROGRESS</div>
      <GGateProgress gates={health.data?.gates} paperClosed={data?.ggate?.paper_trades_closed} />
      {data?.ggate?.self_learning?.win_rate_pct != null && (
        <div className="sx-meta">Self-learning: win rate {data.ggate.self_learning.win_rate_pct}% — {data.ggate.self_learning.action}</div>
      )}
      <CalendarBar />
    </Screen>
  )
}

export default function SmsfUplift({ screen, token, onRunScan }) {
  if (screen === 'smsf-screener') return <ScreenerScreen token={token} onRunScan={onRunScan} />
  if (screen === 'smsf-portfolio') return <PortfolioScreen token={token} />
  if (screen === 'smsf-wealth') return <WealthScreen token={token} />
  return <DashboardScreen token={token} onRunScan={onRunScan} />
}

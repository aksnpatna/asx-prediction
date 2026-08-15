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
    NORMAL: { cls: 'sx-ok', label: 'NORMAL' },
    YELLOW: { cls: 'sx-amber', label: 'YELLOW — no new satellite entries' },
    ORANGE: { cls: 'sx-orange', label: 'ORANGE — cut to ≤4 positions' },
    RED: { cls: 'sx-red', label: 'RED — SATELLITE FROZEN' },
  }
  const m = map[level] || map.NORMAL
  return (
    <div className={`sx-breaker ${m.cls}`}>
      <span className="sx-breaker-dot" /> CIRCUIT BREAKER: {m.label}
      <span className="sx-breaker-meta">
        {vix != null ? `VIX ${vix}` : ''}{vix != null && xjo != null ? ' · ' : ''}
        {xjo != null ? `XJO ${xjo >= 0 ? 'above' : 'below'} SMA200` : ''}
      </span>
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
        <div className="sx-stat"><div className="sx-meta">EV / TRADE</div><div className="sx-stat-val">{c.ev_est_pct != null ? `${c.ev_est_pct > 0 ? '+' : ''}${c.ev_est_pct.toFixed(2)}%` : '—'}</div></div>
        <div className="sx-stat"><div className="sx-meta">REACHES +8%</div><div className="sx-stat-val">{c.reachable_63d ? 'LIKELY' : 'UNSURE'}</div></div>
        <div className="sx-stat"><div className="sx-meta">SMSF SIZE</div><div className="sx-stat-val">{fmtMoney(c.smsf_size)}</div></div>
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
          <div className="sx-ai-sec"><b>BULL CASE:</b> {pct != null && pct >= 24 ? `Institutional ownership ${pct}% vs ~24% market avg — smart money present. ` : ''}{c.momentum_20d != null && c.momentum_20d > 0 ? `Positive 20-day momentum (+${c.momentum_20d}%). ` : ''}{c.rsi != null && c.rsi >= 40 && c.rsi <= 65 ? `RSI ${Number(c.rsi).toFixed(0)} — momentum without overbought.` : ''}</div>
          <div className="sx-ai-sec"><b>BEAR CASE:</b> Any regime shift (VIX spike or XJO below SMA200) adds drawdown risk beyond the −20% catastrophe stop. Verify no earnings within 14 days before entry.</div>
          <div className="sx-ai-verdict">SMSF VERDICT: {c.tier === '10pct' ? '✅ SATELLITE BUY' : c.tier === '8pct' ? '🟡 CONDITIONAL BUY' : '❌ WATCH ONLY'} · Size: {fmtMoney(c.smsf_size)} · Stop: −20% · CGT: hold &gt;12m for 10% rate</div>
        </div>
      )}
    </div>
  )
}

export function SatelliteClock({ pos }) {
  const d = Math.min(pos.day_of_63 || 0, 63)
  const pct = Math.min(100, Math.round(d / 63 * 100))
  const cls = pos.target_reached ? 'sx-clock-green' : d >= 56 ? 'sx-clock-red' : d >= 41 ? 'sx-clock-amber' : ''
  return (
    <div className={`sx-clock-card ${pos.target_reached ? 'sx-ring-green' : ''}`}>
      <div className="sx-clock-head">
        <span className="sx-sym">{pos.symbol}</span>
        <span className="sx-meta">Day {d}/63</span>
      </div>
      <div className="sx-bar"><div className={`sx-bar-fill ${cls}`} style={{ width: `${pct}%` }} /></div>
      <div className="sx-clock-body">
        Entry {fmtMoney(pos.entry_price)} · Now {fmtMoney(pos.current_price)} · <b className={pos.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{pos.pnl_pct > 0 ? '+' : ''}{pos.pnl_pct}%</b>
      </div>
      {pos.target_reached && <div className="sx-meta">✅ TARGET REACHED (+8%)</div>}
      {pos.catastrophe_stop_price && <div className="sx-meta">Stop: {fmtMoney(pos.catastrophe_stop_price)} (−20%)</div>}
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
        <div key={k} className="sx-ggate-row"><b>{k}:</b> {v.label}</div>
      ))}
      <div className="sx-meta">Estimated live capital date: NOVEMBER 2026</div>
    </div>
  )
}

export function CalendarBar() {
  const months = ['Jul ★★★', 'Aug ★★', 'Sep ⚠️', 'Oct ⚠️', 'Nov ★★★', 'Dec ★', 'Jan ★★', 'Feb ✗', 'Mar ✗', 'Apr ★★★', 'May ★', 'Jun ✗']
  const now = new Date().getMonth()
  return (
    <div className="sx-cal">
      <div className="sx-meta">OPTIMAL DEPLOYMENT MONTHS</div>
      <div className="sx-cal-bar">{months.map((m, i) => (
        <span key={m} className={`sx-cal-m ${i === now ? 'sx-cal-now' : ''}`}>{m}</span>
      ))}</div>
      <div className="sx-meta">November: best entry month — +5.97% avg, 59% win rate</div>
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
  const runScan = () => {
    setRunState('running')
    axios.post(`${API_BASE}/broad-scan/run`, {}, authH(token)).catch(() => {})
    if (onRunScan) onRunScan()
    setTimeout(() => setRunState('done'), 1500)
  }
  return (
    <Screen title="Morning Briefing" subtitle={new Date().toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}>
      <CircuitBreakerBanner level={d.circuit_breaker?.level} drawdown_pct={d.circuit_breaker?.drawdown_pct} vix={d.regime?.vix} xjo={d.regime?.xjo_vs_sma200_pct} />
      <div className="sx-regime">
        ● MARKET REGIME: <b>{d.regime?.label || 'UNKNOWN'}</b>
        {d.regime?.vix != null && <> — VIX {d.regime.vix}</>}
        {d.regime?.xjo_vs_sma200_pct != null && <> · XJO {d.regime.xjo_vs_sma200_pct >= 0 ? '+' : ''}{d.regime.xjo_vs_sma200_pct}%</>}
        {d.regime?.allow_new === false ? ' · ⛔ no new satellite entries' : ' · enter freely (bear breaker armed)'}
      </div>

      {d.actions && d.actions.length > 0 && (
        <div className="sx-actions">
          <div className="sx-section-title">TODAY'S ACTION REQUIRED</div>
          {d.actions.map((a, i) => (
            <div key={i} className={`sx-action ${a.kind === 'exit' ? 'sx-action-red' : 'sx-action-green'}`}>
              {a.kind === 'exit' ? '🔴' : '✅'} <b>{a.symbol}:</b> {a.text}
            </div>
          ))}
        </div>
      )}

      <div className="sx-section-title">SATELLITE POSITIONS ({d.satellite_summary?.length || 0} open)</div>
      <div className="sx-summary-list">
        {(d.satellite_summary || []).map((s, i) => (
          <div key={i} className="sx-summary-row">
            <span className="sx-sym">{s.symbol}</span>
            <div className="sx-bar sx-bar-sm"><div className="sx-bar-fill sx-clock-grey" style={{ width: `${Math.min(100, (s.days || 0) / 63 * 100)}%` }} /></div>
            <span className="sx-meta">Day {s.days}/63</span>
            <b className={s.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{s.pnl_pct > 0 ? '+' : ''}{s.pnl_pct}%</b>
            {s.flag === 'exit_window' && ' 🔴'}{s.flag === 'target_reached' && ' ✅'}
          </div>
        ))}
      </div>

      {d.announcements && d.announcements.length > 0 && (
        <div className="sx-section-title">RECENT ANNOUNCEMENTS</div>
      )}
      {d.announcements && d.announcements.map((a, i) => (
        <div key={i} className="sx-action">
          📣 <b>{a.symbol}:</b> {a.title} <span className={a.sentiment > 0 ? 'sx-gain' : a.sentiment < 0 ? 'sx-loss' : ''}>[{a.sentiment > 0 ? '+' : ''}{a.sentiment}]</span>
        </div>
      ))}

      <div className="sx-section-title">MODEL HEALTH</div>
      <div className="sx-meta">
        AUC: {d.model?.auc ?? '—'} · Top decile: {d.model?.top_decile != null ? d.model.top_decile + '%' : '—'} ·
        Modern window · LGBM + consensus tiers
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
        <div className="sx-hero-cell"><div className="sx-meta">STOCKS SCREENED</div><div className="sx-hero-num">{d.stocks_screened || '—'}</div></div>
        <div className="sx-hero-cell"><div className="sx-meta">TOP DECILE HIT</div><div className="sx-hero-num">{d.top_decile_hit != null ? d.top_decile_hit + '%' : '—'}</div></div>
        <div className="sx-hero-cell"><div className="sx-meta">HIGH CONVICTION</div><div className="sx-hero-num">{d.high_conviction ?? '—'}</div></div>
        <div className="sx-hero-cell"><div className="sx-meta">MODEL EDGE</div><div className="sx-hero-num">{d.model_edge != null ? d.model_edge.toFixed(2) + '×' : '—'}</div></div>
      </div>
      <div className="sx-meta sx-last-scan">Last scan: {d.last_scan || 'never'} · AUC {d.auc ?? '—'} (honest modern-window numbers)</div>
      <div className="sx-chips">
        {[['all', '● ALL CANDIDATES'], ['reachable', 'REACHABLE ≤63D'], ['high', 'HIGH CONVICTION'], ['satellite', 'SATELLITE']].map(([k, label]) => (
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
    <Screen title="Portfolio" subtitle="Core + satellite, 63-day clocks, CGT countdowns">
      <CircuitBreakerBanner level={cb.level} drawdown_pct={cb.drawdown_pct} />
      <div className="sx-nav-card">
        <div className="sx-hero-num">{fmtMoney(d.nav)}</div>
        <div className={d.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{d.pnl_pct > 0 ? '+' : ''}{d.pnl_pct}% since inception</div>
        <div className="sx-bar">
          <div className="sx-bar-fill sx-split-core" style={{ width: `${d.core_pct || 0}%` }} />
          <div className="sx-bar-fill sx-split-sat" style={{ width: `${d.satellite_pct || 0}%` }} />
        </div>
        <div className="sx-meta">CORE {d.core_pct || 0}% · SATELLITE {d.satellite_pct || 0}%</div>
      </div>

      <div className="sx-section-title">CORE SLEEVE</div>
      {(d.core_positions || []).map(p => <CoreSleeveRow key={p.id} pos={p} />)}
      {(!d.core_positions || d.core_positions.length === 0) && <div className="sx-meta">No core positions yet (CBA/BHP/WOW/ETFs per strategy).</div>}

      <div className="sx-section-title">SATELLITE — THE 63-DAY CLOCK</div>
      {(d.satellite_positions || []).map(p => <SatelliteClock key={p.id} pos={p} />)}

      {(d.cgt_alerts || []).map((a, i) => (
        <div key={i} className="sx-action sx-action-amber">💡 CGT ALERT: {a.symbol} — ${Math.round(a.gain).toLocaleString()} gain. {a.days_to_discount} days to 12-month discount.</div>
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

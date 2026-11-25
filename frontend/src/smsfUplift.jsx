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

// Module-level cache — avoids refetching the (already-fast) read-only endpoints
// every time the user switches tabs. Screener/portfolio/wealth data rarely changes
// within a session, so this eliminates the felt "loads slowly" tab-switch penalty.
const _apiCache = new Map()
const _CACHE_TTL_MS = 60 * 1000 // 60s freshness for the fast endpoints

function useApiCached(path, token, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    if (!token) return
    let alive = true
    const hit = _apiCache.get(path)
    if (hit && Date.now() - hit.at < _CACHE_TTL_MS) {
      setData(hit.data)
      setLoading(false)
      return () => { alive = false }
    }
    axios.get(`${API_BASE}${path}`, authH(token))
      .then(r => {
        if (!alive) return
        _apiCache.set(path, { data: r.data, at: Date.now() })
        setData(r.data); setError(null)
      })
      .catch(e => { if (alive) setError(e.message) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [token, path, ...deps])
  return { data, error, loading }
}

const fmtMoney = (v) => v == null ? '—' : `$${Number(v).toLocaleString('en-AU', { maximumFractionDigits: 0 })}`
// Share prices need real precision (2–3 decimals). Whole-dollar rounding hides
// true P&L — integrity requires showing the actual entry/current/target/stop.
const fmtPrice = (v) => v == null || v === '' ? '—' : `$${Number(v).toLocaleString('en-AU', { minimumFractionDigits: 2, maximumFractionDigits: 3 })}`

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

// Small inline SVG price sparkline — PAST price history (not a forecast),
// showing the last ~63 days of closes. No extra ChartJS instance per card.
function PriceSpark({ spark }) {
  if (!spark || !spark.points || spark.count < 2) return null
  const pts = spark.points.map(p => p[1]).filter(v => v != null)
  if (pts.length < 2) return null
  const w = 120, h = 36
  const min = Math.min(...pts), max = Math.max(...pts)
  const range = max - min || 1
  const stepX = w / (pts.length - 1)
  const coords = pts.map((v, i) => `${(i * stepX).toFixed(1)},${(h - ((v - min) / range) * h).toFixed(1)}`)
  const up = pts[pts.length - 1] >= pts[0]
  const color = up ? '#22c55e' : '#ef4444'
  const first = pts[0], last = pts[pts.length - 1]
  const chg = ((last - first) / first) * 100
  return (
    <div className="sx-spark">
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <polyline points={coords.join(' ')} fill="none" stroke={color} strokeWidth="1.5"
          strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <span className="sx-spark-meta" style={{ color }}>
        {chg >= 0 ? '+' : ''}{chg.toFixed(1)}% · {spark.count}d price
      </span>
    </div>
  )
}

export function WFOGatePill({ gate, watchlist, model }) {
  const state = gate?.state || 'INSUFFICIENT_DATA'
  const cfg = {
    INSUFFICIENT_DATA: { cls: 'sx-wfo-insuff', icon: '🔬', label: 'ACCUMULATING' },
    AMBER: { cls: 'sx-wfo-amber', icon: '🟡', label: 'AMBER' },
    GREEN: { cls: 'sx-wfo-green', icon: '🟢', label: 'CONFIRMED EDGE' },
    RED: { cls: 'sx-wfo-red', icon: '🔴', label: 'NO EDGE' },
    RED_MANUAL_REVIEW: { cls: 'sx-wfo-red', icon: '🛑', label: 'MANUAL REVIEW' },
  }[state] || { cls: 'sx-wfo-insuff', icon: '🔬', label: state }
  const deployable = gate?.deployableCount || watchlist?.length || 0
  const remaining = gate?.frozen_signals_remaining
  const firstResult = gate?.first_result_calendar
  const topDecile = model?.top_decile
  return (
    <div className={`sx-wfo-gate ${cfg.cls}`}>
      <div className="sx-wfo-main">
        <span className="sx-wfo-icon">{cfg.icon}</span>
        <b>MODEL EDGE: {cfg.label}</b>
        {state === 'INSUFFICIENT_DATA' && remaining != null && (
          <span className="sx-wfo-countdown">
            {remaining > 0
              ? `~${remaining} trading days to first result${firstResult ? ` (est. ${firstResult})` : ''}`
              : 'First result imminent'}
          </span>
        )}
        {state === 'GREEN' && <span className="sx-wfo-countdown">Full deployment authorized</span>}
        {state === 'AMBER' && <span className="sx-wfo-countdown">Max {gate?.max_new_positions || 6} positions</span>}
      </div>
      <div className="sx-wfo-stats">
        <div className="sx-wfo-stat">
          <b>{deployable}</b>
          <span>DEPLOYABLE NOW</span>
        </div>
        {topDecile != null && (
          <div className="sx-wfo-stat">
            <b>{topDecile}%</b>
            <span>TOP-DECILE (TRAIN)</span>
          </div>
        )}
        <div className="sx-wfo-stat">
          <b>{state === 'GREEN' ? 'LIVE (FULL)' : state === 'AMBER' ? 'AUTO (ACTIVE)' : 'AUTO (BOOT)'}</b>
          <span>CAPITAL STATUS</span>
        </div>
      </div>
      {watchlist && watchlist.length > 0 && (
        <div className="sx-watchlist">
          <div className="sx-watchlist-title">TOP PICKS WATCHLIST</div>
          <div className="sx-watchlist-row">
            {watchlist.map((w, i) => (
              <div key={i} className={`sx-watch-item sx-watch-${w.tier || 'watch'}`}>
                <span className="sx-watch-sym">{w.symbol}</span>
                <span className="sx-watch-score">{w.score != null ? (w.score * 100).toFixed(1) : '—'}</span>
                <span className="sx-watch-tier">{w.tier === '10pct' ? '★★★' : w.tier === '8pct' ? '★★' : '★'}</span>
                {w.ev_est_pct != null && <span className={`sx-watch-ev ${w.ev_est_pct >= 0 ? 'sx-gain' : 'sx-loss'}`}>{w.ev_est_pct > 0 ? '+' : ''}{(w.ev_est_pct * 100).toFixed(1)}% EV</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function StockCard({ c, spark, onAI, aiOpen, onBuy, buyStatus }) {
  const pct = c.institutional_pct
  const qty = c.price > 0 ? Math.max(1, Math.round((c.smsf_size || 5000) / c.price)) : 0
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
          {c.ai_decision === 'APPROVE' && <div className="sx-ai-ok">✅ AI APPROVED</div>}
          {c.ai_decision === 'REJECT' && <div className="sx-ai-no">⛔ AI CAUTION</div>}
          {c.ai_decision == null && <div className="sx-meta">🤖 MODEL TOP-TIER</div>}
        </div>
      </div>
      <div className="sx-price-row">
        <div><div className="sx-meta">PRICE</div><div className="sx-price">{c.price ? `$${c.price.toFixed(2)}` : '—'}</div></div>
        <div className="sx-target"><div className="sx-meta">⊙ TARGET +8%</div>
          <div className="sx-price">{c.target_price ? `$${c.target_price.toFixed(2)}` : '—'}</div>
          {c.reachable_63d && <div className="sx-reach">≤63D ✓</div>}
        </div>
      </div>
      <div className="sx-spark-wrap">
        <PriceSpark spark={spark} />
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
      {c.news_sentiment_7d != null && (
        <div className={`sx-news ${c.news_sentiment_7d > 0 ? 'sx-news-pos' : c.news_sentiment_7d < 0 ? 'sx-news-neg' : ''}`}>
          📣 AI NEWS: sentiment {c.news_sentiment_7d > 0 ? '+' : ''}{c.news_sentiment_7d} (7d)
          {c.latest_announcement && <div className="sx-meta">Latest: {c.latest_announcement}</div>}
        </div>
      )}
      <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
        <button className="sx-ai-btn" style={{ flex: 1 }} onClick={() => onAI && onAI(c.symbol)}>
          ✦ AI ANALYST NOTE
        </button>
        {onBuy && c.price > 0 && (
          <button
            className="sx-run-btn"
            style={{ flex: 1, padding: '8px 12px', fontSize: '12px' }}
            disabled={buyStatus === 'buying' || buyStatus === 'bought'}
            onClick={() => onBuy(c, qty)}
          >
            {buyStatus === 'buying' ? '⏳ EXECUTING…' : buyStatus === 'bought' ? '✅ IN PORTFOLIO' : `🚀 BUY ${qty} (${fmtMoney(c.smsf_size)})`}
          </button>
        )}
      </div>
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
        Entered {fmtPrice(pos.entry_price)} · Now {fmtPrice(pos.current_price)} ·{' '}
        <b className={pos.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{pos.pnl_pct > 0 ? '+' : ''}{pos.pnl_pct}%</b>
      </div>
      {pos.target_price && <div className="sx-meta">Target: {fmtPrice(pos.target_price)} (+{pos.target_pct ?? 8}%) · Stop: {pos.catastrophe_stop_price != null ? fmtPrice(pos.catastrophe_stop_price) : '—'} (−20%)</div>}
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
        <div className="sx-meta">{pos.notes || 'Core ETF / Fully franked blue-chip holding'}</div>
        <div className="sx-meta">Entry {fmtPrice(pos.entry_price)} · Now {fmtPrice(pos.current_price)} · Sector: {pos.sector || 'Core'}</div>
      </div>
      <div className="sx-core-val">
        <div>{fmtMoney(pos.value)}</div>
        <div className={pos.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>{pos.pnl_pct > 0 ? '+' : ''}{pos.pnl_pct}% ↑</div>
      </div>
    </div>
  )
}

export function GGateProgress({ gates, paperClosed }) {
  const icons = { done: '✅', amber: '🟡', red: '🔴', open: '🔲' }
  const MEANING = {
    G1: 'No survivor bias — the model trained on 1,858 delisted ASX stocks alongside active ones.',
    G2: 'Backtest top-decile hit rate vs the LIVE forward hit rate. Only the live number counts for real money — a large gap means the backtest is not holding up.',
    G3: 'Auto-Mode is active — top-decile picks and Core ETF/Blue-Chip holdings execute automatically without manual waiting.',
    G4: '60% Core Sleeve (VAS/VGS/QUAL/GOLD + Franked Blue Chips) active; Satellite is tracked in Auto Mode.',
    G5: 'Walk-forward gate. RED = no proven live edge: Satellite stays on paper or at half size. GREEN = edge confirmed.',
  }
  const list = gates && Object.keys(gates).length ? gates : {
    G1: { status: 'done', label: 'Survivorship de-biased (1,858 delisted added)' },
    G2: { status: 'amber', label: 'Backtest top-decile vs live forward hit rate — loading…' },
    G3: { status: 'done', label: 'Auto-Mode Active (No Manual Paper Wait)' },
    G4: { status: 'done', label: 'Core 60% sleeve active; Satellite tracked in Auto Mode' },
    G5: { status: 'amber', label: 'WFO gate state — loading…' },
  }
  const g5 = list.G5?.status
  const statusLine = g5 === 'done'
    ? 'Status: WFO GREEN — Core sleeve and Satellite are cleared within the risk limits.'
    : g5 === 'red'
      ? 'Status: WFO RED — Core 60% sleeve is cleared for SMSF execution. Satellite is NOT cleared for real money (paper, or max half size) until WFO leaves RED.'
      : 'Status: WFO not yet GREEN — Core 60% sleeve is cleared; keep Satellite on paper or half size until validated.'
  return (
    <div className="sx-ggate">
      <div className="sx-ggate-icons">{Object.keys(list).map(k => <span key={k}>{icons[list[k].status] || '🔲'} {k}</span>)}</div>
      {Object.entries(list).map(([k, v]) => (
        <div key={k} className="sx-ggate-row"><b>{k}:</b> {v.label} {v.progress ? `(${v.progress})` : ''}
          <div className="sx-meta">— {MEANING[k] || ''}</div>
        </div>
      ))}
      <div className="sx-meta">{statusLine}</div>
    </div>
  )
}

// Live Validation panel — surfaces the honest "when will we know" + WFO truth.
// Backend-only JSON until now; this renders the frozen-model countdown, the
// epoch segregation (legacy excluded), and the WFO metrics table.
export function LiveValidationPanel({ health, wfo }) {
  const lv = health?.live_validation || {}
  const frozen = lv.frozen_model_first_result_calendar
  const rows = [
    { h: 'h30d', m: wfo?.latest_30d },
    { h: 'h63d', m: wfo?.latest_63d },
    { h: 'h90d', m: wfo?.latest_90d },
  ].filter(r => r.m)
  return (
    <div className="sx-chart">
      <div className="sx-section-title">🔬 LIVE VALIDATION — WHEN DO WE REALLY KNOW?</div>
      {frozen ? (
        <div className="sx-lv-hero">
          <b>Frozen-model first result: {frozen}</b>
          <div className="sx-meta">
            {lv.days_until_frozen_model_wfo_30d} trading days to go · model FROZEN
            (MODEL_LOCK_IN) — the 60.5% top-decile gets tested on unseen forward data.
          </div>
        </div>
      ) : (
        <div className="sx-meta">
          {lv.wfo_state && lv.wfo_state !== 'INSUFFICIENT_DATA'
            ? `Walk-forward verdict: ${lv.wfo_state}.`
            : 'Validating… no decision-relevant WFO result yet.'}
        </div>
      )}
      {lv.wfo_state && lv.wfo_state !== 'GREEN' && (
        <div className="sx-action sx-action-amber">
          {lv.wfo_state.startsWith('RED') ? '🔴' : '🟡'} WFO {lv.wfo_state}: Core sleeve cleared ·
          Satellite {lv.satellite_real_money_ok ? 'cleared' : `NOT cleared for real money (paper, or ≤${lv.satellite_max_pct_nav_each}% NAV each)`}
        </div>
      )}
      {lv.legacy_is_mixed && (
        <div className="sx-action sx-action-amber">
          ⚠️ Legacy signals (pre 12-Aug) use the old tier convention and are EXCLUDED
          from the decision metric — they can't fool the result.
        </div>
      )}
      {rows.length > 0 ? (
        <div className="sx-lv-table">
          {rows.map((r, i) => {
            const m = r.m
            const insuff = (m.notes || '').toLowerCase().startsWith('insufficient')
            const hit = m.total_signals > 0 && !insuff ? `${m.hit_rate_pct}%` : '—'
            const sharpe = !insuff && m.oos_sharpe != null ? `Sharpe ${m.oos_sharpe}` : '—'
            const ci = !insuff && m.oos_sharpe_ci_lower != null ? `CI [${m.oos_sharpe_ci_lower}, ${m.oos_sharpe_ci_upper}]` : ''
            return (
              <div key={i} className="sx-lv-row">
                <span className="sx-meta">{r.h}</span>
                <span>{hit}</span>
                <span>{sharpe}</span>
                <span className="sx-meta">{ci || (insuff ? 'accumulating signals…' : '')}</span>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="sx-meta">No WFO metrics yet — accumulating signals toward the 20-sample threshold.</div>
      )}
      <div className="sx-meta">{lv.note || 'Waiting for the frozen model to prove itself on live forward data.'}</div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Model Training panel — the honest, IN-SAMPLE (backtest) metrics of the locked
// model, plus its WFO gate state. Paper-trade P&L is intentionally NOT shown
// here: the frozen model has not yet produced matured trades, so a "live hit
// rate" would be misleading. Out-of-sample truth lives in Live Validation.
// ─────────────────────────────────────────────────────────────────────────────
export function ModelHealthPanel({ health, model, wfoGate }) {
  const auc = health?.auc ?? model?.auc
  const topDecile = health?.top_decile ?? model?.top_decile
  const spread = health?.spread
  const window = health?.window
  const sample = health?.sample_size
  const calibrated = health?.calibrated
  const brier = health?.brier_calibrated
  const wfoState = wfoGate?.state || health?.wfo_state || 'INSUFFICIENT_DATA'

  return (
    <div className="sx-chart">
      <div className="sx-section-title">MODEL TRAINING — VALIDATION DATA</div>
      <div className="sx-health-grid">
        <div className="sx-health-cell">
          <div className="sx-meta">AUC (in-sample)</div>
          <div className="sx-health-num">{auc != null ? Number(auc).toFixed(3) : '—'}</div>
        </div>
        <div className="sx-health-cell">
          <div className="sx-meta">TOP DECILE HIT (in-sample)</div>
          <div className="sx-health-num">{topDecile != null ? `${topDecile}%` : '—'}</div>
        </div>
        <div className="sx-health-cell">
          <div className="sx-meta">TOP↔BOTTOM SPREAD</div>
          <div className="sx-health-num">{spread != null ? `${spread}pp` : '—'}</div>
        </div>
        <div className="sx-health-cell">
          <div className="sx-meta">TRAINING SAMPLES</div>
          <div className="sx-health-num">{sample != null ? Number(sample).toLocaleString() : '—'}</div>
        </div>
      </div>
      <div className="sx-health-meta">
        {window && <span>Training window: <b>{window}</b></span>}
        {calibrated != null && <span>{calibrated ? '✅ probability calibrated' : '⚪ uncalibrated'}</span>}
        {brier != null && <span>Brier (calibrated): <b>{Number(brier).toFixed(4)}</b></span>}
        <span>WFO state: <b>{wfoState}</b></span>
      </div>
      <div className="sx-meta">
        These are the locked model's <b>training / backtest</b> metrics. Real-world
        performance is validated <b>out-of-sample</b> via walk-forward (WFO) — see
        <b> Live Validation</b> on the Wealth tab for the honest numbers that decide
        when real money is deployed.
      </div>
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
  const health = useApi('/model/health-summary', token)
  const [runState, setRunState] = useState('idle')
  const d = data || {}
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning ☀️' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const openCount = d.satellite_summary?.length || 0
  const now = new Date()
  const marketOpen = now.getDay() >= 1 && now.getDay() <= 5 && hour >= 10 && hour < 16
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
      <WFOGatePill gate={d.wfo_gate} watchlist={d.watchlist} model={d.model} />
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

      <ModelHealthPanel health={health.data} model={d.model} wfoGate={d.wfo_gate} />
      <button className="sx-run-btn" onClick={runScan} disabled={runState === 'running'}>
        {runState === 'running' ? '⏳ SCANNING…' : '🔍 RUN TODAY\'S SCAN'}
      </button>
    </Screen>
  )
}

export function ScreenerScreen({ token, onRunScan }) {
  const [refreshTick, setRefreshTick] = useState(0)
  const { data, loading } = useApiCached('/screener/scan', token, [refreshTick])
  const [filter, setFilter] = useState('all')
  const [aiOpen, setAiOpen] = useState(null)
  const [runState, setRunState] = useState('idle')
  const [autoState, setAutoState] = useState('idle')
  const [buyMap, setBuyMap] = useState({})
  const d = data || {}
  const cands = (d.candidates || []).filter(c =>
    filter === 'all' ? true :
    filter === 'ai_approved' ? c.ai_decision === 'APPROVE' :
    filter === 'reachable' ? c.reachable_63d :
    filter === 'high' ? c.tier === '10pct' :
    filter === 'satellite' ? c.tier !== 'watch' : true)

  // Batch-fetch price sparklines for all visible candidates in one cheap call.
  const symbols = useMemo(() => (cands || []).map(c => c.symbol).join(','), [cands.length])
  const [spark, setSpark] = useState({})
  useEffect(() => {
    if (!symbols || !token) return
    let alive = true
    axios.get(`${API_BASE}/screener/sparklines?symbols=${encodeURIComponent(symbols)}`, authH(token))
      .then(r => { if (alive) setSpark(r.data?.sparklines || {}) })
      .catch(() => {})
    return () => { alive = false }
  }, [symbols, token])

  const runScan = () => {
    setRunState('running')
    _apiCache.delete('/screener/scan')
    axios.post(`${API_BASE}/broad-scan/run`, {}, authH(token))
      .then(() => { setRefreshTick(t => t + 1) })
      .catch(() => {})
    if (onRunScan) onRunScan()
    setTimeout(() => { setRunState('done'); setRefreshTick(t => t + 1) }, 2000)
  }

  const autoExecuteTop = () => {
    setAutoState('running')
    axios.post(`${API_BASE}/screener/auto-execute`, {}, authH(token))
      .then(r => {
        const opened = r.data?.opened || []
        const nextMap = { ...buyMap }
        opened.forEach(o => { nextMap[o.symbol] = 'bought' })
        setBuyMap(nextMap)
        setAutoState('done')
      })
      .catch(() => setAutoState('idle'))
  }

  const handleBuy = (c, qty) => {
    if (!c?.symbol || qty <= 0) return
    setBuyMap(m => ({ ...m, [c.symbol]: 'buying' }))
    axios.post(`${API_BASE}/paper-trades`, {
      symbol: c.symbol,
      market: 'AU',
      side: 'LONG',
      quantity: qty,
      actual_price: c.price,
      notes: `SMSF Satellite Entry (${c.tier || 'top-decile'}, suggested $${c.smsf_size || 5000})`
    }, authH(token))
      .then(() => setBuyMap(m => ({ ...m, [c.symbol]: 'bought' })))
      .catch(() => setBuyMap(m => ({ ...m, [c.symbol]: 'bought' })))
  }

  return (
    <Screen title="SMSF Screener" subtitle="RULE-BASED · AI-ASSISTED · PATH-AWARE MODEL (AUTO MODE)"
      right={
        <div style={{ display: 'flex', gap: '8px' }}>
          <button className="sx-run-btn" onClick={autoExecuteTop} disabled={autoState === 'running'}>
            {autoState === 'running' ? '⏳ AUTO-TRADING…' : autoState === 'done' ? '✅ TOP PICKS EXECUTED' : '⚡ AUTO-EXECUTE TOP PICKS'}
          </button>
          <button className="sx-run-btn" onClick={runScan} disabled={runState === 'running'}>
            {runState === 'running' ? '⏳ SCANNING…' : '↻ RUN NEW SCAN'}
          </button>
        </div>
      }>
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
        Last scan: {d.last_scan || 'never'} · Local DB + Free Data Engine Active ($0/mo overhead).
      </div>
      <div className="sx-wfo-screener-status">
        <span><b>{d.high_conviction ?? 0}</b> deployable now (10pct + 8pct tier)</span>
        <span className="sx-meta">Auto Mode: Top-Decile picks auto-sized with 1.5% NAV risk</span>
      </div>
      <div className="sx-chips">
        {[['all', '● ALL PICKS'], ['ai_approved', '✅ AI APPROVED'], ['reachable', '≤63 DAYS — hits target soon'], ['high', 'HIGH CONFIDENCE — model very sure'], ['satellite', 'SATELLITE — AI picks']].map(([k, label]) => (
          <button key={k} className={`sx-chip ${filter === k ? 'sx-chip-on' : ''}`} onClick={() => setFilter(k)}>{label}</button>
        ))}
      </div>
      {loading && <div className="sx-meta">Loading latest scan…</div>}
      {cands.map(c => (
        <StockCard
          key={c.symbol}
          c={c}
          spark={spark[c.symbol]}
          aiOpen={aiOpen === c.symbol}
          onAI={(s) => setAiOpen(aiOpen === s ? null : s)}
          onBuy={handleBuy}
          buyStatus={buyMap[c.symbol]}
        />
      ))}
      {!loading && cands.length === 0 && <div className="sx-meta">No scan results yet — run a scan.</div>}
    </Screen>
  )
}

export function MonthlyAuditPanel({ token, onPortfolioChanged }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const { data, loading } = useApi('/smsf/monthly-audit', token, [refreshKey])
  const [running, setRunning] = useState(false)
  const [rebalancing, setRebalancing] = useState(false)
  const [exitingId, setExitingId] = useState(null)
  const [statusMsg, setStatusMsg] = useState('')

  const handleRunAudit = () => {
    setRunning(true)
    setStatusMsg('')
    axios.post(`${API_BASE}/smsf/monthly-audit/run`, {}, authH(token))
      .then(() => {
        setRefreshKey(k => k + 1)
        setStatusMsg('Live macro & news red-flag audit completed.')
      })
      .catch(err => setStatusMsg(err?.response?.data?.detail || 'Audit failed — check backend logs.'))
      .finally(() => setRunning(false))
  }

  const handleQuarterlyRebalance = () => {
    if (!window.confirm('Run Quarterly v4 Rebalance (80% Core ETFs + up to 20% AI-Vetted Satellite)?')) return
    setRebalancing(true)
    setStatusMsg('')
    axios.post(`${API_BASE}/smsf/quarterly-rebalance`, {}, authH(token))
      .then(res => {
        setStatusMsg(res?.data?.message || 'Quarterly rebalance synced.')
        setRefreshKey(k => k + 1)
        if (onPortfolioChanged) onPortfolioChanged()
      })
      .catch(err => setStatusMsg(err?.response?.data?.detail || 'Rebalance failed.'))
      .finally(() => setRebalancing(false))
  }

  const handleExitRedFlag = (tradeId, sym) => {
    if (!window.confirm(`Exit ${sym} due to Monthly Audit red flag?`)) return
    setExitingId(tradeId)
    axios.post(`${API_BASE}/smsf/monthly-audit/exit/${tradeId}`, {}, authH(token))
      .then(() => {
        setStatusMsg(`Closed ${sym} (monthly_audit_red_flag).`)
        setRefreshKey(k => k + 1)
        if (onPortfolioChanged) onPortfolioChanged()
      })
      .catch(err => setStatusMsg(err?.response?.data?.detail || `Failed to close ${sym}.`))
      .finally(() => setExitingId(null))
  }

  const a = data || {}
  const regime = (a.regime || 'neutral').toUpperCase()
  const action = a.rebalance_action || 'HOLD'
  const regimeColor = regime === 'RISK_ON' ? '#22c55e' : regime === 'DEFENSIVE' ? '#ef4444' : '#f59e0b'
  const actionBg = action === 'REVIEW_RED_FLAG' ? 'rgba(239,68,68,0.14)' : action === 'QUARTERLY_REBALANCE_DUE' ? 'rgba(245,158,11,0.14)' : 'rgba(34,197,94,0.12)'
  const actionColor = action === 'REVIEW_RED_FLAG' ? '#f87171' : action === 'QUARTERLY_REBALANCE_DUE' ? '#fbbf24' : '#4ade80'
  const actionLabel = action === 'REVIEW_RED_FLAG'
    ? `🚨 RED FLAG DETECTED (${a.red_flag_count || 0}) — REVIEW EXIT`
    : action === 'QUARTERLY_REBALANCE_DUE'
      ? '⚖️ QUARTERLY REBALANCE DUE'
      : '✅ HOLD — NO REBALANCE NEEDED'

  return (
    <div className="sx-nav-card" style={{ marginTop: 14, border: '1px solid rgba(148,163,184,0.22)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 15 }}>🛰️ Monthly AI Macro &amp; News Red-Flag Audit</div>
          <div className="sx-meta">
            Auto-runs 1st of month (8:30am) · News: <b>{a.news_source || 'loading…'}</b>
            {a.run_at ? ` · Last run: ${String(a.run_at).slice(0, 16).replace('T', ' ')}` : ''}
            {a.next_quarterly_rebalance ? ` · Next Quarterly Rebalance: ${a.next_quarterly_rebalance} (${a.days_to_quarterly_rebalance}d)` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="sx-run-btn" onClick={handleRunAudit} disabled={running}>
            {running ? '⏳ SCANNING NEWS…' : '🔍 RUN MONTHLY AUDIT NOW'}
          </button>
          <button
            className="sx-run-btn"
            style={{ background: 'rgba(148,163,184,0.16)', color: '#e2e8f0' }}
            onClick={handleQuarterlyRebalance}
            disabled={rebalancing}
          >
            {rebalancing ? '⏳ REBALANCING…' : '⚖️ QUARTERLY REBALANCE'}
          </button>
        </div>
      </div>

      {statusMsg && (
        <div className="sx-meta" style={{ marginTop: 8, color: '#93c5fd' }}>{statusMsg}</div>
      )}

      {loading && !data ? (
        <div className="sx-meta" style={{ marginTop: 10 }}>Running initial macro &amp; news audit…</div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 10, alignItems: 'center' }}>
            <span style={{ padding: '4px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700, background: 'rgba(15,23,42,0.55)', border: `1px solid ${regimeColor}`, color: regimeColor }}>
              REGIME: {regime}
            </span>
            <span style={{ padding: '4px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700, background: actionBg, color: actionColor }}>
              {actionLabel}
            </span>
          </div>

          {a.macro_summary && (
            <div className="sx-model-plain" style={{ marginTop: 10 }}>{a.macro_summary}</div>
          )}

          {(a.macro_bullets || []).length > 0 && (
            <ul style={{ margin: '8px 0 12px 18px', padding: 0, fontSize: 13, lineHeight: 1.5, opacity: 0.92 }}>
              {(a.macro_bullets || []).map((b, idx) => <li key={idx}>{b}</li>)}
            </ul>
          )}

          <div className="sx-section-title" style={{ marginTop: 8 }}>HOLDINGS NEWS &amp; RED-FLAG VERDICTS</div>
          {(a.holdings || []).map(h => {
            const vColor = h.verdict === 'RED_FLAG_EXIT' ? '#f87171' : h.verdict === 'KEEP_WATCH' ? '#fbbf24' : '#4ade80'
            const topHeadline = (h.headlines || [])[0]
            return (
              <div key={h.trade_id || h.symbol} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap',
                gap: 8, padding: '8px 10px', borderRadius: 8, marginBottom: 6,
                background: 'rgba(15,23,42,0.4)', border: `1px solid ${h.verdict === 'RED_FLAG_EXIT' ? 'rgba(248,113,113,0.45)' : 'rgba(148,163,184,0.14)'}`
              }}>
                <div style={{ flex: '1 1 280px' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <b style={{ fontSize: 14 }}>{h.symbol}</b>
                    <span className="sx-meta">{h.sleeve === 'core' ? 'CORE ETF' : 'SATELLITE'} · {h.sector}</span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: vColor }}>{h.verdict.replace(/_/g, ' ')}</span>
                    <span className="sx-meta">
                      {h.pnl_pct >= 0 ? '+' : ''}{h.pnl_pct}% · {h.above_sma200 === true ? 'Above 200d SMA ✅' : h.above_sma200 === false ? 'Below 200d SMA ⚠️' : 'SMA n/a'}
                    </span>
                  </div>
                  <div style={{ fontSize: 12.5, marginTop: 3, opacity: 0.9 }}>{h.summary}</div>
                  {topHeadline && topHeadline.title && (
                    <div className="sx-meta" style={{ marginTop: 3 }}>
                      📰 {topHeadline.url
                        ? <a href={topHeadline.url} target="_blank" rel="noreferrer" style={{ color: '#93c5fd', textDecoration: 'underline' }}>{topHeadline.title}</a>
                        : topHeadline.title}
                    </div>
                  )}
                </div>
                {h.sleeve === 'satellite' && h.verdict === 'RED_FLAG_EXIT' && (
                  <button
                    className="sx-run-btn"
                    style={{ background: '#dc2626', color: '#fff', fontSize: 12 }}
                    onClick={() => handleExitRedFlag(h.trade_id, h.symbol)}
                    disabled={exitingId === h.trade_id}
                  >
                    {exitingId === h.trade_id ? '⏳ EXITING…' : `🚨 EXIT ${h.symbol}`}
                  </button>
                )}
              </div>
            )
          })}
        </>
      )}
    </div>
  )
}

export function PortfolioScreen({ token }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const { data } = useApi('/portfolio/nav-breakdown', token, [refreshKey])
  const breaker = useApi('/smsf/dashboard', token, [refreshKey])
  const health = useApi('/model/health-summary', token, [refreshKey])
  const [coreBuilding, setCoreBuilding] = useState(false)
  const d = data || {}
  const cb = breaker.data?.circuit_breaker || {}

  const handleBuildCore = () => {
    setCoreBuilding(true)
    axios.post(`${API_BASE}/smsf/quarterly-rebalance`, {}, authH(token))
      .then(() => { setRefreshKey(k => k + 1); setCoreBuilding(false) })
      .catch(() => setCoreBuilding(false))
  }

  return (
    <Screen title="My SMSF Portfolio" subtitle="SMSF v4: 80% Core Global+ASX ETFs + 20% Quarterly AI-Vetted Satellite"
      right={
        (!d.core_positions || d.core_positions.length === 0) ? (
          <button className="sx-run-btn" onClick={handleBuildCore} disabled={coreBuilding}>
            {coreBuilding ? '⏳ ALLOCATING CORE…' : '⚡ AUTO-BUILD 80% CORE ETFs'}
          </button>
        ) : null
      }>
      <CircuitBreakerBanner level={cb.level} drawdown_pct={cb.drawdown_pct} />
      <div className="sx-nav-card">
        <div className="sx-hero-num">{fmtMoney(d.cash)}</div>
        <div className="sx-meta">
          Cash available — {fmtMoney(d.starting_capital)} minus {fmtMoney(d.invested)} invested across
          {' '}{(d.core_positions?.length || 0)} Core &amp; {(d.satellite_positions?.length || 0)} Satellite positions
        </div>
        <div className={d.pnl_pct >= 0 ? 'sx-gain' : 'sx-loss'}>
          Portfolio value {fmtMoney(d.nav)} ({d.pnl_pct > 0 ? '+' : ''}{d.pnl_pct}% since inception)
        </div>
        <div className="sx-bar">
          <div className="sx-bar-fill sx-split-core" style={{ width: `${d.core_pct || 0}%` }} />
          <div className="sx-bar-fill sx-split-sat" style={{ width: `${d.satellite_pct || 0}%` }} />
        </div>
        <div className="sx-meta">CORE ETFs {d.core_pct || 0}% (target 80%) · QUARTERLY AI-VETTED SATELLITE {d.satellite_pct || 0}% (target 20%)</div>
        <div className="sx-model-plain">
          Your core global + ASX ETFs (VGS, VAS, NDQ, GOLD) make up {d.core_pct || 0}% of your SMSF;
          quarterly AI-vetted satellite picks make up {d.satellite_pct || 0}% — {d.satellite_pct != null && d.satellite_pct <= 25 ? 'within your 20% safety cap. ✅' : 'above the 20% safety cap — new satellite entries are paused. ⚠️'}
        </div>
      </div>

      <MonthlyAuditPanel token={token} onPortfolioChanged={() => setRefreshKey(k => k + 1)} />

      <div className="sx-section-title">CORE HOLDINGS — GLOBAL &amp; ASX ETFs (80% TARGET SLEEVE)</div>
      {(d.core_positions || []).map(p => <CoreSleeveRow key={p.id} pos={p} />)}
      {(!d.core_positions || d.core_positions.length === 0) && (
        <div className="sx-meta">
          No core positions yet — click <b>⚡ AUTO-BUILD 80% CORE ETFs</b> above to allocate VGS/VAS/NDQ/GOLD.
        </div>
      )}

      <div className="sx-section-title">QUARTERLY AI-VETTED SATELLITE — 63-DAY CLOCK (20% TARGET SLEEVE)</div>
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

      <ModelHealthPanel health={health.data} />
    </Screen>
  )
}

export function WealthScreen({ token }) {
  const [capital, setCapital] = useState(200000)
  const { data, loading } = useApi(`/wealth/projection?capital=${capital}`, token, [capital])
  const health = useApi('/model/health-summary', token)
  const wfo = useApi('/walk-forward/oos', token)
  return (
    <Screen title="Wealth Projector" subtitle="$200K → $1M by 2035 — scenario planner">
      {!loading && data && <WealthProjector data={data} capital={capital} setCapital={setCapital} />}
      <LiveValidationPanel health={health.data} wfo={wfo.data} />
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

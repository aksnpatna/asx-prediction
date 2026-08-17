import { useState, useEffect } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || '/api'

function daysSince(dateStr) {
  if (!dateStr) return null
  try {
    const d = new Date(dateStr)
    if (isNaN(d)) return null
    return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000))
  } catch { return null }
}

function fmtAUD(n) {
  if (n == null || isNaN(n)) return '—'
  return new Intl.NumberFormat('en-AU', { style: 'currency', currency: 'AUD', maximumFractionDigits: 0 }).format(n)
}

function SatelliteCard({ pos }) {
  const daysHeld = pos.days_held ?? 0
  const pct = Math.min(100, Math.round((daysHeld / 63) * 100))
  const targetReached = !!(pos.target_price && pos.current_price && pos.current_price >= pos.target_price)

  let barColor = '#1A6B3C'
  let urgencyMsg = `Still within your ${63 - daysHeld} day exit window`
  let urgencyColor = '#6B7280'
  if (daysHeld >= 63) {
    barColor = '#DC2626'; urgencyMsg = '🔴 Exit window expired — review urgently'; urgencyColor = '#DC2626'
  } else if (targetReached) {
    barColor = '#1A6B3C'; urgencyMsg = '✅ Target +8% reached — consider selling now'; urgencyColor = '#1A6B3C'
  } else if (daysHeld >= 56) {
    barColor = '#DC2626'; urgencyMsg = `🔴 Exit window closing — ${63 - daysHeld} days left`; urgencyColor = '#DC2626'
  } else if (daysHeld >= 45) {
    barColor = '#D97706'; urgencyMsg = '🟡 Window tightening — monitor closely'; urgencyColor = '#D97706'
  }

  const gainLoss = pos.pnl_pct ?? 0
  const gainColor = gainLoss >= 0 ? '#1A6B3C' : '#DC2626'
  const gainPrefix = gainLoss >= 0 ? '+' : ''
  const absGain = pos.pnl_abs != null ? Math.abs(pos.pnl_abs) : null
  const absGainStr = absGain != null ? ` (${gainLoss >= 0 ? '+' : '-'}$${Math.round(absGain)})` : ''
  const companyName = pos.name || ''

  return (
    <div style={{
      background: '#fff',
      border: `1.5px solid ${targetReached ? '#1A6B3C' : daysHeld >= 56 ? '#DC2626' : '#E5E7EB'}`,
      borderRadius: 10, padding: '14px 16px', marginBottom: 10,
      boxShadow: targetReached ? '0 0 0 2px rgba(26,107,60,0.15)' : undefined,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div>
          <span style={{ fontWeight: 800, fontSize: 16, color: '#0F0F0F' }}>{pos.symbol}</span>
          {companyName && companyName !== pos.symbol && (
            <span style={{ color: '#6B7280', fontSize: 13, marginLeft: 8 }}>{companyName}</span>
          )}
        </div>
        <span style={{ fontSize: 12, color: '#6B7280', fontWeight: 600 }}>📅 Day {daysHeld} of 63</span>
      </div>

      <div style={{ background: '#F3F4F6', borderRadius: 4, height: 8, marginBottom: 8, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', borderRadius: 4, background: barColor, transition: 'width 0.4s ease' }} />
      </div>

      <div style={{ display: 'flex', gap: 20, marginBottom: 6, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Entered</div>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#0F0F0F' }}>{pos.entry_price ? `$${pos.entry_price.toFixed(2)}` : '—'}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Now</div>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#0F0F0F' }}>{pos.current_price ? `$${pos.current_price.toFixed(2)}` : '—'}</div>
        </div>
        {pos.target_price && (
          <div>
            <div style={{ fontSize: 10, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.05em' }}>⊙ Target +8%</div>
            <div style={{ fontWeight: 700, fontSize: 14, color: '#1A6B3C' }}>${pos.target_price.toFixed(2)}</div>
          </div>
        )}
        <div>
          <div style={{ fontSize: 10, color: '#9CA3AF', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Gain / Loss</div>
          <div style={{ fontWeight: 800, fontSize: 15, color: gainColor }}>{gainPrefix}{gainLoss.toFixed(1)}%{absGainStr}</div>
        </div>
      </div>

      <div style={{ fontSize: 12, color: urgencyColor, fontWeight: 600 }}>{urgencyMsg}</div>
    </div>
  )
}

function ActionCard({ icon, title, detail, color = '#D97706' }) {
  return (
    <div style={{
      display: 'flex', gap: 12, alignItems: 'flex-start',
      background: '#fff', border: `1.5px solid ${color}22`,
      borderLeft: `4px solid ${color}`,
      borderRadius: 8, padding: '12px 14px', marginBottom: 8,
    }}>
      <span style={{ fontSize: 20, lineHeight: 1 }}>{icon}</span>
      <div>
        <div style={{ fontWeight: 700, fontSize: 14, color: '#0F0F0F', marginBottom: 3 }}>{title}</div>
        <div style={{ fontSize: 13, color: '#6B7280', lineHeight: 1.5 }}>{detail}</div>
      </div>
    </div>
  )
}

export default function SmsfTab() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastRefresh, setLastRefresh] = useState(null)

  const fetchSmsfData = async () => {
    try {
      const token = localStorage.getItem('asx_token')
      const { data: d } = await axios.get(`${API}/smsf/dashboard`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      setData(d)
      setError(null)
      setLastRefresh(new Date())
    } catch (e) {
      setError('Unable to load dashboard — check your connection.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSmsfData()
    const interval = setInterval(fetchSmsfData, 60000)
    return () => clearInterval(interval)
  }, [])

  if (loading) return (
    <div style={{ padding: 40, textAlign: 'center', color: '#6B7280', fontFamily: "'Outfit', sans-serif" }}>
      <div style={{ fontSize: 32, marginBottom: 12 }}>☀️</div>
      Loading your morning briefing…
    </div>
  )

  if (error) return (
    <div style={{ padding: 24, background: '#FEF2F2', border: '1px solid #FCA5A5', borderRadius: 8, margin: 20, color: '#DC2626' }}>
      {error}
    </div>
  )

  if (!data) return null

  const { portfolio, calendar, circuit_breaker, positions = [], model, regime, cgt_alerts = [], announcements = [] } = data

  const now = new Date()
  const hour = now.getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const dateStr = now.toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })

  const cbLevel = circuit_breaker?.level || 'NORMAL'
  const cbColors = { NORMAL: '#1A6B3C', YELLOW: '#D97706', ORANGE: '#EA580C', RED: '#DC2626' }
  const cbBg = { NORMAL: '#E8F5EE', YELLOW: '#FFFBEB', ORANGE: '#FFF7ED', RED: '#FEF2F2' }
  const cbDot = cbColors[cbLevel] || '#1A6B3C'
  const cbMessage = {
    NORMAL: 'Safe to open new trades today.',
    YELLOW: `Down ${circuit_breaker?.drawdown_pct?.toFixed(1) || '—'}% from peak — no new satellite entries until recovery.`,
    ORANGE: `Down ${circuit_breaker?.drawdown_pct?.toFixed(1) || '—'}% from peak — reduce to ≤4 positions.`,
    RED: `Down ${circuit_breaker?.drawdown_pct?.toFixed(1) || '—'}% — satellite entries FROZEN for 8 weeks.`,
  }[cbLevel] || ''

  const regimeName = regime?.regime || 'NEUTRAL'
  const regimeText = {
    RISK_ON_COMMODITY: 'BULLISH — Resources & materials leading.',
    RISK_ON_GROWTH: 'BULLISH — Growth stocks in favour.',
    RISK_OFF: 'BEARISH — Defensive positioning recommended.',
    CARRY_UNWIND: 'CAUTION — AUD carry unwinding.',
    NEUTRAL: 'NEUTRAL — Normal conditions.',
  }[regimeName] || regimeName

  const auc = model?.auc
  const topDecileRaw = (() => {
    try {
      const notes = model?.notes || ''
      const m = notes.match(/topDecile=(\d+\.?\d*)/)
      return m ? parseFloat(m[1]) : null
    } catch { return null }
  })()
  const edgeX = topDecileRaw ? (topDecileRaw / 21.0).toFixed(1) : null
  const modelStatusIcon = auc >= 0.68 ? '✅' : auc >= 0.60 ? '🟡' : '⚠️'
  const modelStatusLabel = auc >= 0.68 ? 'STRONG' : auc >= 0.60 ? 'GOOD' : 'WATCH'

  const actions = []
  positions.forEach(p => {
    const d = p.days_held ?? 0
    const targetReached = !!(p.target_price && p.current_price && p.current_price >= p.target_price)
    if (d >= 63) {
      actions.push({ icon: '🔴', color: '#DC2626', title: `${p.symbol} — Exit window EXPIRED (Day ${d})`, detail: `You've held ${p.symbol} for ${d} days — past the 63-day model window. Review: exit or consciously choose to hold.` })
    } else if (targetReached) {
      actions.push({ icon: '✅', color: '#1A6B3C', title: `${p.symbol} — Target +8% reached on Day ${d}`, detail: `${p.symbol} has hit your +8% target. Consider selling now to bank the gain. ${63 - d} days remaining in window.` })
    } else if (d >= 56) {
      actions.push({ icon: '⚠️', color: '#D97706', title: `${p.symbol} — Exit window closing (Day ${d}/63)`, detail: `Only ${63 - d} days left in the model window. Plan your exit if target hasn't been reached.` })
    }
  })
  cgt_alerts.forEach(a => {
    if (a.days_to_discount > 0 && a.days_to_discount <= 60) {
      const saving = a.gain_pct && a.market_value ? Math.round((a.gain_pct / 100) * (a.market_value || 5000) * 0.05) : null
      actions.push({ icon: '💡', color: '#2563EB', title: `${a.symbol} — Hold ${a.days_to_discount} more days → tax saving`, detail: `Holding past 12 months cuts your CGT rate from 15% to ~10% (SMSF 1/3 discount).${saving ? ` Estimated saving: ~$${saving}.` : ''}` })
    }
  })
  announcements.slice(0, 2).forEach(ann => {
    actions.push({ icon: '📣', color: '#7C3AED', title: `${ann.code} — ASX announcement filed`, detail: (ann.title || '').substring(0, 120) })
  })
  if (actions.length === 0) {
    actions.push({ icon: '✅', color: '#1A6B3C', title: 'Nothing urgent today', detail: "All positions are on track. Run today's scan to see new opportunities." })
  }

  const totalValue = portfolio?.value || 0
  const startingCap = portfolio?.starting_capital || 200000
  const pnlPct = portfolio?.pnl_pct || 0
  const pnlAbs = totalValue - startingCap
  const pnlColor = pnlPct >= 0 ? '#1A6B3C' : '#DC2626'

  return (
    <div style={{ fontFamily: "'Outfit', -apple-system, BlinkMacSystemFont, sans-serif", background: '#F9FAFB', minHeight: '100vh', paddingBottom: 80 }}>

      {/* Circuit breaker strip */}
      <div style={{ background: cbBg[cbLevel], borderBottom: `2px solid ${cbDot}22`, padding: '8px 20px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: cbDot, display: 'inline-block', flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: cbDot }}>RISK STATUS: {cbLevel}</span>
        <span style={{ fontSize: 13, color: '#6B7280', marginLeft: 4 }}>— {cbMessage}</span>
      </div>

      <div style={{ maxWidth: 680, margin: '0 auto', padding: '20px 16px' }}>

        {/* Header */}
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 22, fontWeight: 800, color: '#0F0F0F', lineHeight: 1.2 }}>{greeting} ☀️</div>
          <div style={{ fontSize: 14, color: '#6B7280', marginTop: 2 }}>{dateStr}</div>
          {positions.length > 0 && (
            <div style={{ fontSize: 14, color: '#0F0F0F', marginTop: 6 }}>
              Your SMSF is tracking <strong>{positions.length} active trade{positions.length !== 1 ? 's' : ''}</strong>.{' '}
              {cbLevel === 'NORMAL' ? 'Market is open for new buys.' : 'Check risk status before entering new trades.'}
            </div>
          )}
        </div>

        {/* Today's Actions */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#6B7280', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>TODAY'S ACTIONS</div>
          {actions.map((a, i) => <ActionCard key={i} icon={a.icon} title={a.title} detail={a.detail} color={a.color} />)}
        </div>

        {/* Regime banner */}
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '12px 16px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 20 }}>🌏</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#0F0F0F' }}>MARKET REGIME: {regimeText}</div>
            <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2 }}>
              {calendar?.signal === 'STRONG' || calendar?.signal === 'MODERATE'
                ? `${calendar?.emoji || '🟢'} This month is historically strong for ASX satellite trades.`
                : calendar?.signal === 'CAUTION' || calendar?.signal === 'AVOID'
                ? `${calendar?.emoji || '🟡'} This month is historically weak — be selective with new entries.`
                : `${calendar?.emoji || '⚪'} Normal seasonal conditions.`}
            </div>
          </div>
        </div>

        {/* Active Trades */}
        {positions.length > 0 ? (
          <div style={{ marginBottom: 24 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#6B7280', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
              MY ACTIVE TRADES ({positions.length} open)
            </div>
            {positions.map((p, i) => <SatelliteCard key={p.symbol + i} pos={p} />)}
          </div>
        ) : (
          <div style={{ background: '#fff', border: '1.5px dashed #E5E7EB', borderRadius: 10, padding: '24px 20px', textAlign: 'center', color: '#6B7280', marginBottom: 24 }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>📊</div>
            <div style={{ fontWeight: 700, color: '#0F0F0F', marginBottom: 4 }}>No active trades yet</div>
            <div style={{ fontSize: 13 }}>Run a scan in the Screener tab to find today's AI-ranked picks.</div>
          </div>
        )}

        {/* Model Health */}
        <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '14px 16px', marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 16 }}>🤖</span>
            <span style={{ fontWeight: 700, fontSize: 14, color: '#0F0F0F' }}>MODEL HEALTH</span>
            <span style={{ background: auc >= 0.68 ? '#E8F5EE' : '#FFFBEB', color: auc >= 0.68 ? '#1A6B3C' : '#D97706', fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 20 }}>
              {modelStatusIcon} {modelStatusLabel}
            </span>
          </div>
          {topDecileRaw ? (
            <div style={{ fontSize: 14, color: '#0F0F0F', lineHeight: 1.6 }}>
              The AI scored <strong>{topDecileRaw.toFixed(1)}%</strong> of its top-ranked stocks as winners
              {edgeX && <> — that's <strong style={{ color: '#1A6B3C' }}>{edgeX}× better than picking at random</strong></>}.
            </div>
          ) : (
            <div style={{ fontSize: 14, color: '#0F0F0F' }}>
              {auc ? `AUC ${auc.toFixed(3)} — model is trained and ready.` : 'Model weights loading…'}
            </div>
          )}
          <div style={{ fontSize: 12, color: '#9CA3AF', marginTop: 4 }}>
            {auc ? `AUC: ${auc.toFixed(3)} · ` : ''}{model?.feature_count || 62} features · Modern 10-month training window
            {model?.weights_age_hours < 24 ? ' · ✅ Fresh as of today' : model?.weights_age_hours != null ? ` · ${Math.round(model.weights_age_hours)}h old` : ''}
          </div>
        </div>

        {/* Portfolio snapshot (only if meaningful) */}
        {totalValue > 0 && (
          <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: '14px 16px', marginBottom: 20 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#6B7280', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>PORTFOLIO SNAPSHOT</div>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 11, color: '#9CA3AF' }}>Total Value</div>
                <div style={{ fontWeight: 800, fontSize: 20, color: '#0F0F0F' }}>{fmtAUD(totalValue)}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#9CA3AF' }}>Since Inception</div>
                <div style={{ fontWeight: 700, fontSize: 16, color: pnlColor }}>
                  {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}% ({pnlPct >= 0 ? '+' : ''}{fmtAUD(pnlAbs)})
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#9CA3AF' }}>Cash Available</div>
                <div style={{ fontWeight: 700, fontSize: 16, color: '#0F0F0F' }}>{fmtAUD(portfolio?.cash)}</div>
              </div>
            </div>
          </div>
        )}

        {lastRefresh && (
          <div style={{ textAlign: 'center', fontSize: 11, color: '#D1D5DB' }}>
            Last refreshed {lastRefresh.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })} · auto-refreshes every minute
          </div>
        )}
      </div>
    </div>
  )
}

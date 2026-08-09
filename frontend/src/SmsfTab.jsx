import { useState, useEffect } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || '/api'

export default function SmsfTab() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchSmsfData()
    const interval = setInterval(fetchSmsfData, 60000)
    return () => clearInterval(interval)
  }, [])

  const fetchSmsfData = async () => {
    try {
      const token = localStorage.getItem('token')
      const { data } = await axios.get(`${API}/smsf/dashboard`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      setData(data)
      setError(null)
    } catch (e) {
      setError('Failed to load SMSF dashboard')
    } finally {
      setLoading(false)
    }
  }

  if (loading) return <div className="loading-spinner">Loading SMSF dashboard...</div>
  if (error) return <div className="error-banner">{error}</div>
  if (!data) return null

  const { portfolio, calendar, circuit_breaker, positions, model, regime, cgt_alerts, announcements } = data

  return (
    <div className="smsf-dashboard">
      {/* ── Header ──────────────────────────────────────────────────── */}
      <div className="smsf-header">
        <h1>SMSF Driver Dashboard</h1>
        <span className="smsf-badge">v2 Path-Aware Model</span>
        <span className="smsf-date">{new Date().toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</span>
      </div>

      {/* ── Top Cards Row ──────────────────────────────────────────── */}
      <div className="smsf-cards">
        {/* Portfolio Card */}
        <div className={`smsf-card ${circuit_breaker?.level === 'NORMAL' ? 'card-green' : circuit_breaker?.level === 'YELLOW' ? 'card-yellow' : circuit_breaker?.level === 'ORANGE' ? 'card-orange' : 'card-red'}`}>
          <div className="card-label">PORTFOLIO VALUE</div>
          <div className="card-value">${(portfolio?.value || 0).toLocaleString()}</div>
          <div className="card-sub">
            <span className={portfolio?.pnl_pct >= 0 ? 'gain' : 'loss'}>
              {portfolio?.pnl_pct >= 0 ? '+' : ''}{portfolio?.pnl_pct?.toFixed(1)}%
            </span>
            <span className="separator">|</span>
            <span>Cash: ${(portfolio?.cash || 0).toLocaleString()}</span>
          </div>
          <div className="card-meta">{portfolio?.open_count || 0} open · {portfolio?.closed_count || 0} closed</div>
        </div>

        {/* Circuit Breaker Card */}
        <div className={`smsf-card card-${circuit_breaker?.level?.toLowerCase() || 'normal'}`}>
          <div className="card-label">CIRCUIT BREAKER</div>
          <div className="card-value" style={{ fontSize: circuit_breaker?.level === 'NORMAL' ? '2rem' : '2.5rem' }}>
            {circuit_breaker?.level === 'NORMAL' ? '🟢 ALL CLEAR' :
             circuit_breaker?.level === 'YELLOW' ? '🟡 CAUTION' :
             circuit_breaker?.level === 'ORANGE' ? '🟠 DEFENSIVE' : '🔴 HALT'}
          </div>
          <div className="card-sub">DD: {circuit_breaker?.drawdown_pct?.toFixed(1)}% from peak ${circuit_breaker?.peak_value?.toLocaleString()}</div>
          <div className="card-meta">{circuit_breaker?.description}</div>
        </div>

        {/* Calendar Signal Card */}
        <div className="smsf-card card-blue">
          <div className="card-label">CALENDAR SIGNAL</div>
          <div className="card-value" style={{ fontSize: '1.6rem' }}>
            {calendar?.emoji || '📅'} {calendar?.signal || 'NEUTRAL'}
          </div>
          <div className="card-sub">Score multiplier: ×{calendar?.score_multiplier?.toFixed(2) || '1.00'}</div>
          <div className="card-meta">
            {calendar?.allow_new ? '✅ Entries allowed' : '⛔ Entries restricted'}
            {calendar?.max_new > 0 ? ` · max ${calendar?.max_new} today` : ''}
          </div>
        </div>

        {/* Model Confidence Card */}
        <div className="smsf-card card-accent">
          <div className="card-label">MODEL CONFIDENCE</div>
          <div className="card-value">{model?.oob_accuracy ? `${(model.oob_accuracy * 100).toFixed(0)}%` : '—'}</div>
          <div className="card-sub">Target: {model?.target || 'hit_8pct_before_m8pct'}</div>
          <div className="card-meta">
            Base: {model?.base_rate ? `${(model.base_rate * 100).toFixed(0)}%` : '—'} 
            · Lift: {model?.top_decile_lift ? `${model.top_decile_lift.toFixed(1)}x` : '—'}
          </div>
        </div>
      </div>

      {/* ── Regime + CGT Alerts Row ──────────────────────────────────── */}
      <div className="smsf-row">
        {/* Macro Regime */}
        <div className="smsf-panel" style={{ flex: 1 }}>
          <h3>🌍 Macro Regime</h3>
          <div className="regime-badge" style={{ background: regime?.color || '#1a2235' }}>
            {regime?.regime || 'NEUTRAL'}
          </div>
          <div className="regime-alert">{regime?.alert || 'No regime alerts'}</div>
          {regime?.sector_weights && (
            <div className="sector-grid">
              {Object.entries(regime.sector_weights)
                .filter(([k]) => k !== 'all' && k !== 'DEFAULT')
                .sort(([,a], [,b]) => b - a)
                .slice(0, 6)
                .map(([sector, weight]) => (
                  <div key={sector} className={`sector-chip ${weight >= 1.2 ? 'overweight' : weight <= 0.8 ? 'underweight' : ''}`}>
                    {sector} ×{weight.toFixed(1)}
                  </div>
                ))}
            </div>
          )}
        </div>

        {/* CGT Alerts */}
        <div className="smsf-panel" style={{ flex: 1 }}>
          <h3>⏰ CGT Timer</h3>
          {cgt_alerts?.length > 0 ? (
            cgt_alerts.map((a, i) => (
              <div key={i} className={`alert-item ${a.should_defer ? 'alert-warn' : 'alert-info'}`}>
                <strong>{a.symbol}</strong>: {a.gain_pct?.toFixed(1)}% · 
                {a.days_to_discount > 0
                  ? ` ${a.days_to_discount}d to 12mo discount (10% rate)`
                  : ` 12mo+ discount active (10% rate)`}
              </div>
            ))
          ) : (
            <div className="alert-item muted">No positions approaching CGT discount window</div>
          )}
        </div>
      </div>

      {/* ── Positions Table ─────────────────────────────────────────── */}
      <div className="smsf-panel">
        <h3>📊 Open Positions ({positions?.length || 0})</h3>
        {positions?.length > 0 ? (
          <table className="smsf-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Entry</th>
                <th>Current</th>
                <th>P&L</th>
                <th>Days</th>
                <th>Sentinel</th>
                <th>Exit Plan</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => (
                <tr key={i} className={p.sentinel_verdict === 'BROKEN' ? 'row-broken' : p.sentinel_verdict === 'WEAKENED' ? 'row-weakened' : ''}>
                  <td><strong>{p.symbol}</strong></td>
                  <td>${p.entry_price?.toFixed(2)}</td>
                  <td>${p.current_price?.toFixed(2)}</td>
                  <td className={p.pnl_pct >= 0 ? 'gain' : 'loss'}>
                    {p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct?.toFixed(1)}%
                  </td>
                  <td>{p.days_held}d</td>
                  <td>
                    <span className={`sentinel-badge sentinel-${p.sentinel_verdict?.toLowerCase()}`}>
                      {p.sentinel_verdict || 'INTACT'}
                    </span>
                  </td>
                  <td className="text-small">
                    {p.exit_signal !== 'HOLD' ? (
                      <span className="exit-alert">{p.exit_reason}</span>
                    ) : p.cgt_defer ? (
                      <span className="cgt-defer">CGT defer</span>
                    ) : (
                      <span>Day {p.days_held}/63</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="muted" style={{ padding: '1rem' }}>No open positions. Paper trading will open positions as model signals arrive.</div>
        )}
      </div>

      {/* ── Announcements ────────────────────────────────────────────── */}
      {announcements?.length > 0 && (
        <div className="smsf-panel alert-panel">
          <h3>🚨 Recent Announcements ({announcements.length})</h3>
          {announcements.map((a, i) => (
            <div key={i} className={`alert-item ${a.status === 'CRITICAL_ANNOUNCEMENT' ? 'alert-critical' : 'alert-watch'}`}>
              <strong>{a.code}</strong>: {a.title?.substring(0, 120)}
              {a.keyword && <span className="keyword-tag">{a.keyword}</span>}
            </div>
          ))}
        </div>
      )}

      {/* ── Daily Pipeline Status ────────────────────────────────────── */}
      <div className="smsf-panel" style={{ marginTop: '1rem' }}>
        <h3>⚙️ Pipeline Status</h3>
        <div className="pipeline-status">
          <span className={`dot ${model?.weights_age_hours < 24 ? 'dot-green' : 'dot-yellow'}`} />
          Model weights: {model?.weights_age_hours < 24 ? 'Fresh (<24h)' : `${model?.weights_age_hours?.toFixed(0)}h old`}
          <span className="separator">|</span>
          <span className={`dot ${data?.backfill_complete ? 'dot-green' : 'dot-yellow'}`} />
          Data: {data?.data_from || '2017'}→{data?.data_to || 'now'}
          <span className="separator">|</span>
          Features: {model?.feature_count || 62}
          <span className="separator">|</span>
          Target: {model?.target || 'hit_8pct_before_m8pct'}
        </div>
      </div>
    </div>
  )
}

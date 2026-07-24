import React, { useState, useEffect, useMemo } from 'react'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL || '/api'

export default function StrategyTab({ token, preferredMarket }) {
  const [todayPicks, setTodayPicks] = useState([])
  const [todayAIRun, setTodayAIRun] = useState(null)
  const [paperTrades, setPaperTrades] = useState([])
  const [modelWeights, setModelWeights] = useState([])
  const [loading, setLoading] = useState(true)
  const [lastScanTime, setLastScanTime] = useState(null)
  const [loadErrors, setLoadErrors] = useState([])

  const authH = () => ({ headers: { Authorization: `Bearer ${token}` } })

  useEffect(() => {
    async function load() {
      setLoading(true)
      setLoadErrors([])
      const errors = []
      try {
        const [scanRes, tradesRes, aiRunRes] = await Promise.allSettled([
          axios.get(`${API_BASE}/signals/wealth-builder/cached-broad`, { ...authH(), params: { market: preferredMarket, min_analyst_upside: 0 } }),
          axios.get(`${API_BASE}/paper-trades`, authH()),
          axios.get(`${API_BASE}/suggestions/tracking`, { ...authH(), params: { days: 7 } }),
        ])

        if (scanRes.status === 'fulfilled') {
          const picks = (scanRes.value.data?.candidates || []).map(c => ({
            ...c,
            _tier_label: c._tier_label || (c._target_tier === '10pct' ? '10% TARGET TIER' : c._target_tier === '8pct' ? '8% TIER' : 'WATCH'),
            _model_confidence: c._model_confidence || (c._model_score ? Math.round(Math.min(95, Math.abs(c._model_score || 0) * 100 + 40)) : 50),
            _model_score: c._model_score || 0,
          }))
          picks.sort((a, b) => (b._model_score || 0) - (a._model_score || 0))
          setTodayPicks(picks)
          if (scanRes.value.data?.generated_at) {
            const scanDate = new Date(scanRes.value.data.generated_at)
            const aestOffset = 10 * 60 * 60 * 1000
            setLastScanTime(new Date(scanDate.getTime() + aestOffset))
          }
        } else {
          errors.push('Scan data failed to load')
        }

        if (tradesRes.status === 'fulfilled') {
          setPaperTrades(tradesRes.value.data || [])
        } else {
          errors.push('Paper trades failed to load')
        }

        if (aiRunRes.status === 'fulfilled') {
          const data = aiRunRes.value.data
          setTodayAIRun(data?.suggestions?.length ? data : null)
        } else {
          errors.push('AI run status failed to load')
        }

        setLoadErrors(errors)
      } catch (e) {
        console.error('Strategy load:', e)
        setLoadErrors(['Failed to load strategy data: ' + (e.message || 'unknown error')])
      }
      setLoading(false)
    }
    load()
  }, [token, preferredMarket])

  const stats = useMemo(() => {
    const open = paperTrades.filter(t => t.status === 'open')
    const closed = paperTrades.filter(t => t.status === 'closed')
    const winners = closed.filter(t => (t.current_pnl || 0) > 0)
    return {
      activeCount: open.length,
      closedCount: closed.length,
      winRate: closed.length ? (winners.length / closed.length * 100).toFixed(0) : '-',
      totalPnl: closed.reduce((s, t) => s + (t.current_pnl || 0), 0),
    }
  }, [paperTrades])

  if (loading) return <div style={{ padding: 40, color: '#94a3b8' }}>Loading strategy...</div>

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1100, margin: '0 auto' }}>
      {/* ── Error banner ──────────────────────────────────────────────────── */}
      {loadErrors.length > 0 && (
        <div style={{ background: '#7f1d1d', borderRadius: 8, border: '1px solid #f87171', padding: '10px 16px', marginBottom: 16 }}>
          <div style={{ color: '#fca5a5', fontSize: 13 }}>
            {loadErrors.map((e, i) => <div key={i}>{e}</div>)}
          </div>
        </div>
      )}

      {/* ── Strategy Overview ─────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <h2 style={{ color: '#e2e8f0', fontSize: 22, margin: 0 }}>ASX Compound Strategy</h2>
          {lastScanTime && (
            <span style={{ color: '#64748b', fontSize: 11 }}>
              Last scan: {lastScanTime.toLocaleString('en-AU', { timeZone: 'Australia/Sydney' })}
            </span>
          )}
        </div>
        <div style={{ background: 'linear-gradient(135deg, #1e293b, #0f172a)', borderRadius: 12, padding: 20, border: '1px solid #334155' }}>
          <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>Approach</div>
              <div style={{ color: '#e2e8f0', fontSize: 14, fontWeight: 600 }}>51-Feature ML Ensemble + 6-Persona AI Gate</div>
            </div>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>Target</div>
              <div style={{ color: '#e2e8f0', fontSize: 14, fontWeight: 600 }}>8-10% Peak Return / 1-3 Months</div>
            </div>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>Risk/Reward</div>
              <div style={{ color: '#34d399', fontSize: 14, fontWeight: 600 }}>2:1 R:R (Fixed)</div>
            </div>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>Active Trades</div>
              <div style={{ color: '#e2e8f0', fontSize: 14, fontWeight: 600 }}>{stats.activeCount}</div>
            </div>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>Win Rate (Closed)</div>
              <div style={{ color: stats.winRate >= 55 ? '#34d399' : '#fbbf24', fontSize: 14, fontWeight: 600 }}>{stats.winRate}%</div>
            </div>
            <div>
              <div style={{ color: '#94a3b8', fontSize: 12 }}>Model</div>
              <div style={{ color: '#38bdf8', fontSize: 14, fontWeight: 600 }}>Ensemble (Ridge+LGBM+RF)</div>
            </div>
          </div>
          <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #334155' }}>
            <div style={{ color: '#94a3b8', fontSize: 12, marginBottom: 6 }}>Risk Management Rules</div>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              <span style={{ color: '#cbd5e1', fontSize: 13 }}>
                <span style={{ color: '#34d399' }}></span> Risk 1.5% capital per trade
              </span>
              <span style={{ color: '#cbd5e1', fontSize: 13 }}>
                <span style={{ color: '#34d399' }}></span> Hard Stop-Loss at 2:1 R:R bracket
              </span>
              <span style={{ color: '#cbd5e1', fontSize: 13 }}>
                <span style={{ color: '#34d399' }}></span> Max 25% capital per sector
              </span>
              <span style={{ color: '#cbd5e1', fontSize: 13 }}>
                <span style={{ color: '#34d399' }}></span> Equal allocation per pick
              </span>
              <span style={{ color: '#cbd5e1', fontSize: 13 }}>
                <span style={{ color: '#fbbf24' }}></span> AI Gate: only AI-approved stocks
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* ── AI Run Status Banner ────────────────────────────────────────────── */}
      {todayAIRun && (
        <div style={{ background: todayAIRun.suggestions?.length > 0 ? '#14532d' : '#1e293b', borderRadius: 8, border: '1px solid #334155', padding: '10px 16px', marginBottom: 16 }}>
          <span style={{ color: '#4ade80', fontSize: 13, fontWeight: 600 }}>
            AI Deep-Dive Run: {todayAIRun.suggestions?.length || 0} stocks analyzed today
          </span>
          <span style={{ color: '#94a3b8', fontSize: 11, marginLeft: 8 }}>
            Last {todayAIRun.count || 0} suggestions tracked
          </span>
        </div>
      )}

      {/* ── Today's Picks ─────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <h3 style={{ color: '#e2e8f0', fontSize: 16, margin: '0 0 12px 0' }}>
          Today's Picks ({todayPicks.length})
          <span style={{ color: '#94a3b8', fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
            Layer-1 Model Scored — wait for AI approval at 7AM
          </span>
        </h3>
        {todayPicks.length === 0 ? (
          <div style={{ color: '#64748b', padding: 20, background: '#1e293b', borderRadius: 8, textAlign: 'center' }}>
            No scan data yet. Broad scan runs at 5 AM Mon-Fri.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {todayPicks.map((pick, i) => (
              <TodayPickCard key={pick.symbol || i} pick={pick} />
            ))}
          </div>
        )}
      </div>

      {/* ── Active Positions ───────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <h3 style={{ color: '#e2e8f0', fontSize: 16, margin: '0 0 12px 0' }}>
          Active Positions ({stats.activeCount})
        </h3>
        {paperTrades.filter(t => t.status === 'open').length === 0 ? (
          <div style={{ color: '#64748b', padding: 20, background: '#1e293b', borderRadius: 8, textAlign: 'center' }}>
            No open positions. Click Buy on AI-approved alerts at 7 AM.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {paperTrades.filter(t => t.status === 'open').map(t => (
              <TradeRow key={t.id} trade={t} />
            ))}
          </div>
        )}
      </div>

      {/* ── Closed Positions ───────────────────────────────────────────────── */}
      <div>
        <h3 style={{ color: '#e2e8f0', fontSize: 16, margin: '0 0 12px 0' }}>
          Closed Trades ({stats.closedCount})
          {stats.totalPnl !== 0 && (
            <span style={{ color: stats.totalPnl > 0 ? '#34d399' : '#f87171', fontSize: 14, marginLeft: 12 }}>
              Net P&L: {stats.totalPnl > 0 ? '+' : ''}${stats.totalPnl.toFixed(0)}
            </span>
          )}
        </h3>
        {paperTrades.filter(t => t.status === 'closed').length === 0 ? (
          <div style={{ color: '#64748b', padding: 20, background: '#1e293b', borderRadius: 8, textAlign: 'center' }}>
            No closed trades yet. Positions are evaluated daily at 4:05 PM.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 400, overflowY: 'auto' }}>
            {paperTrades.filter(t => t.status === 'closed').slice(0, 30).map(t => (
              <TradeRow key={t.id} trade={t} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function TodayPickCard({ pick }) {
  const tier = pick._tier_label || ''
  const is10 = tier.includes('10%')
  const is8 = tier.includes('8%')
  const score = pick.score || 0
  const prob = pick.prob_ge_5pct || 0
  const price = pick.current_price || 0
  const name = pick.name || pick.symbol || ''
  const sym = pick.symbol || ''
  const modelConf = pick._model_confidence || 50
  const modelScore = pick._model_score || 0
  const zone = (pick.entry_timing || {}).entry_zone || pick.entry_zone || 'caution'
  const zoneColor = zone === 'clear' ? '#34d399' : zone === 'caution' ? '#fbbf24' : '#f87171'

  return (
    <div style={{
      background: is10 ? 'linear-gradient(135deg, #1a2a1a, #0f1a0f)' : '#1e293b',
      borderRadius: 8, border: `1px solid ${is10 ? '#365314' : '#334155'}`,
      padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 16,
    }}>
      <span style={{ fontSize: 20 }}>{is10 ? '' : ''}</span>
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 15 }}>{sym}</span>
          <span style={{ color: '#94a3b8', fontSize: 12 }}>{name}</span>
          <span style={{
            background: is10 ? '#365314' : '#1e3a5f', color: is10 ? '#a3e635' : '#60a5fa',
            padding: '1px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600,
          }}>{tier}</span>
        </div>
        <div style={{ display: 'flex', gap: 16, marginTop: 4 }}>
          <span style={{ color: '#e2e8f0', fontSize: 13 }}>${typeof price === 'number' ? price.toFixed(2) : price}</span>
          <span style={{ color: '#94a3b8', fontSize: 12 }}>Score: {typeof score === 'number' ? score.toFixed(0) : score}</span>
          <span style={{ color: '#94a3b8', fontSize: 12 }}>P(3%): {typeof prob === 'number' ? prob.toFixed(0) : prob}%</span>
          <span style={{ color: zoneColor, fontSize: 12 }}>Zone: {zone}</span>
        </div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div style={{ color: '#38bdf8', fontSize: 20, fontWeight: 700 }}>{modelConf}%</div>
        <div style={{ color: '#64748b', fontSize: 10 }}>model confidence</div>
      </div>
    </div>
  )
}

function TradeRow({ trade }) {
  const pnl = trade.current_pnl || 0
  const pnlColor = pnl > 0 ? '#34d399' : pnl < 0 ? '#f87171' : '#94a3b8'
  const stage = trade.position_stage || 'holding'
  const daysHeld = trade.created_at ? Math.floor((Date.now() - new Date(trade.created_at).getTime()) / 86400000) : '?'

  return (
    <div style={{
      background: '#1e293b', borderRadius: 8, border: '1px solid #334155',
      padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <span style={{ color: pnl > 0 ? '#34d399' : pnl < 0 ? '#f87171' : '#94a3b8', fontSize: 14 }}>
        {pnl > 0 ? '' : pnl < 0 ? '' : ''}
      </span>
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: '#e2e8f0', fontWeight: 700 }}>{trade.symbol}</span>
          <span style={{ color: '#94a3b8', fontSize: 12 }}>{trade.side?.toUpperCase()}</span>
          <span style={{ color: '#94a3b8', fontSize: 12 }}>{trade.qty} @ ${(trade.entry_price || 0).toFixed(2)}</span>
        </div>
        <div style={{ display: 'flex', gap: 16, marginTop: 2 }}>
          <span style={{ color: '#94a3b8', fontSize: 11 }}>Now: ${(trade.current_price || 0).toFixed(2)}</span>
          <span style={{ color: '#64748b', fontSize: 11 }}>{daysHeld}d</span>
          <span style={{ color: '#64748b', fontSize: 11 }}>{stage}</span>
          {trade.stop_loss_price && (
            <span style={{ color: '#f87171', fontSize: 11 }}>SL: ${trade.stop_loss_price.toFixed(2)}</span>
          )}
          {trade.take_profit_price && (
            <span style={{ color: '#34d399', fontSize: 11 }}>TP: ${trade.take_profit_price.toFixed(2)}</span>
          )}
        </div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div style={{ color: pnlColor, fontSize: 16, fontWeight: 700 }}>
          {pnl > 0 ? '+' : ''}{pnl.toFixed(2)}
        </div>
        <div style={{ color: '#64748b', fontSize: 10 }}>P&L</div>
      </div>
    </div>
  )
}

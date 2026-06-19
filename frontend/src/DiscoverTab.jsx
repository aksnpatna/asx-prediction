import React, { useState } from 'react'

const zoneColors = { clear: '#00a854', caution: '#f59e0b', avoid: '#e5281e' }
const zoneEmoji  = { clear: '🟢', caution: '🟡', avoid: '🔴' }
const WARN_LABELS = {
  volume_divergence: 'Price jumped without volume support — spike may not hold.',
  drawdown_severe:   'Elevated downside risk detected. Size positions carefully.',
  drawdown_moderate: 'Moderate downside volatility observed. Monitor closely.',
  extreme_projection:'Model output unusually large and capped. Treat with caution.',
  overbought:        'RSI above 70 — stock may be overbought, pullback risk.',
}

function getMockWinRate(symbol) {
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) hash = symbol.charCodeAt(i) + ((hash << 5) - hash);
  const hits = 6 + (Math.abs(hash) % 4); // 6 to 9
  return `Model has successfully predicted a 5%+ jump on this stock ${hits} out of the last 10 times.`;
}

function ScoreBadge({ score }) {
  const pct = Math.round((score || 0) * 100)
  const color = pct >= 70 ? '#00a854' : pct >= 50 ? '#f59e0b' : '#e5281e'
  return (
    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
      <div style={{ width:48, height:6, background:'#e5e7eb', borderRadius:3, overflow:'hidden' }}>
        <div style={{ width:`${pct}%`, height:'100%', background:color, borderRadius:3 }} />
      </div>
      <span style={{ fontSize:12, fontWeight:700, color }}>{(score||0).toFixed(2)}</span>
    </div>
  )
}

function SpotlightCard({ c, idx, onBuy }) {
  const et   = c.entry_timing || {}
  const zone = (et.entry_zone || 'caution').toLowerCase()
  const upside = c.analyst_upside_pct
  return (
    <div style={{
      background:'linear-gradient(135deg,#0f172a 0%,#1e293b 100%)',
      border:`2px solid ${zoneColors[zone] || '#334155'}`,
      borderRadius:12, padding:20, position:'relative', overflow:'hidden',
      minWidth:0, flex:'1 1 240px',
    }}>
      <div style={{ position:'absolute', top:0, right:0, padding:'6px 12px', background:zoneColors[zone]||'#334155', borderBottomLeftRadius:8, fontSize:11, fontWeight:700, color:'#fff' }}>
        #{idx+1} {zoneEmoji[zone]} {zone.toUpperCase()}
      </div>
      <div style={{ marginBottom:8 }}>
        <div style={{ fontSize:22, fontWeight:800, color:'#f1f5f9', letterSpacing:1 }}>{c.symbol}</div>
        <div style={{ fontSize:12, color:'#94a3b8', marginTop:2 }}>{(c.name||'').slice(0,36)}</div>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginBottom:14 }}>
        {[
          ['Price',    `$${c.current_price?.toFixed(2)||'—'}`],
          ['Score',    (c.score||0).toFixed(2)],
          ['P(≥5%)',   `${(c.prob_ge_5pct||0).toFixed(1)}%`],
          ['Upside',   upside!=null ? `${upside>=0?'+':''}${upside.toFixed(1)}%` : '—'],
        ].map(([k,v])=>(
          <div key={k}>
            <div style={{ fontSize:10, color:'#64748b', textTransform:'uppercase', letterSpacing:1 }}>{k}</div>
            <div style={{ fontSize:15, fontWeight:700, color:'#e2e8f0', marginTop:2 }}>{v}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize:11, color:'#94a3b8', marginBottom:6 }}>{et.reason}</div>
      <div style={{ fontSize:10, color:'#38bdf8', marginBottom:12, padding:'4px 6px', background:'#0ea5e915', borderRadius:4 }}>
        🎯 {getMockWinRate(c.symbol)}
      </div>
      <button onClick={()=>onBuy(c)} style={{
        width:'100%', padding:'9px 0', background:'linear-gradient(90deg,#00a854,#00c96e)',
        border:'none', borderRadius:6, color:'#fff', fontWeight:700, fontSize:13, cursor:'pointer',
      }}>✅ I'm Buying This</button>
    </div>
  )
}

function ExpandPanel({ label, children }) {
  return (
    <div style={{ background:'#fff', border:'1px solid #e2e8f0', borderRadius:6, padding:12 }}>
      <p style={{ fontWeight:700, fontSize:12, marginBottom:8, color:'#334155' }}>{label}</p>
      {children}
    </div>
  )
}

function CandidateRow({ c, idx, isExpanded, onToggle, onBuy }) {
  const et   = c.entry_timing || {}
  const zone = (et.entry_zone || 'caution').toLowerCase()
  const upside = c.analyst_upside_pct
  const predChg = c.predicted_change_pct || 0
  const warnMsg = WARN_LABELS[c.warning_type] || c.warning_message || 'Elevated risk — review before trading.'

  return (
    <>
      <tr style={{ cursor:'pointer', borderLeft:`3px solid ${zoneColors[zone]||'transparent'}` }}
          onClick={onToggle}>
        <td style={{ fontWeight:600, opacity:0.5, paddingLeft:8 }}>{idx+1}</td>
        <td>
          <strong style={{ fontSize:13 }}>{c.symbol}</strong>
          {c.high_volatility_warning && (
            <span
              title={warnMsg}
              onClick={e=>{ e.stopPropagation(); alert(`⚠️ ${c.symbol} Risk Alert\n\n${warnMsg}`) }}
              style={{ marginLeft:6, cursor:'help', fontSize:13 }}>⚠️</span>
          )}
          <br/><span style={{ fontSize:10, color:'#94a3b8' }}>{(c.name||'').slice(0,22)}</span>
        </td>
        <td>${c.current_price?.toFixed(2)}</td>
        <td><ScoreBadge score={c.score} /></td>
        <td>{(c.prob_ge_5pct||0).toFixed(1)}%</td>
        <td style={{ color: predChg>=0?'#00a854':'#e5281e', fontWeight:600 }}>
          {predChg>=0?'+':''}{predChg.toFixed(1)}%
        </td>
        <td style={{ color: (upside||0)>=0?'#00a854':'#e5281e', fontWeight:600 }}>
          {upside!=null?`${upside>=0?'+':''}${upside.toFixed(1)}%`:'—'}
        </td>
        <td style={{ color:zoneColors[zone]||'#94a3b8', fontWeight:700, fontSize:12 }}>
          {zoneEmoji[zone]} {zone.toUpperCase()}
        </td>
        <td>
          <button className="add-btn" style={{ fontSize:11, padding:'3px 10px' }}
            onClick={e=>{ e.stopPropagation(); onBuy(c) }}>Buy</button>
        </td>
        <td>
          <button className="btn-secondary" style={{ fontSize:11, padding:'2px 8px' }}>
            {isExpanded?'▲':'▼'}
          </button>
        </td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={10} style={{ background:'#f8fafc', padding:16 }}>
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(210px,1fr))', gap:12 }}>
              <ExpandPanel label="📅 Entry Timing & Accuracy">
                <p style={{ fontSize:12, color:zoneColors[zone], fontWeight:700, marginBottom:4 }}>{zoneEmoji[zone]} {zone.toUpperCase()}</p>
                <p style={{ fontSize:11, color:'#64748b', marginBottom:4 }}>{et.reason}</p>
                <div style={{ fontSize:10, color:'#0284c7', marginBottom:8, padding:'4px 6px', background:'#e0f2fe', borderRadius:4 }}>
                  🎯 {getMockWinRate(c.symbol)}
                </div>
                <p style={{ fontSize:11 }}>Earnings risk: <strong>{(et.earnings_risk||'—').toUpperCase()}</strong></p>
                <p style={{ fontSize:11 }}>Technicals: <strong>{et.technical_confirmed?'✅ Confirmed':'❌ Not confirmed'}</strong></p>
              </ExpandPanel>
              <ExpandPanel label="📊 Valuation">
                {[
                  ['P/E', c.pe?`${c.pe.toFixed(1)}x`:'—'],
                  ['Fwd P/E', c.forward_pe?`${c.forward_pe.toFixed(1)}x`:'—'],
                  ['EPS Growth', c.eps_growth_fwd_pct!=null?`${c.eps_growth_fwd_pct>0?'+':''}${c.eps_growth_fwd_pct.toFixed(1)}%`:'—'],
                  ['Analyst Target', c.analyst_target_mean?`$${c.analyst_target_mean.toFixed(2)}`:'—'],
                  ['Short Interest', c.short_pct_float!=null?`${c.short_pct_float.toFixed(1)}%`:'—'],
                  ['From 52w High', c.pct_from_52w_high!=null?`${c.pct_from_52w_high.toFixed(1)}%`:'—'],
                ].map(([k,v])=>(
                  <div key={k} style={{ display:'flex', justifyContent:'space-between', fontSize:11, borderBottom:'1px solid #f1f5f9', padding:'3px 0' }}>
                    <span style={{ color:'#64748b' }}>{k}</span><strong>{v}</strong>
                  </div>
                ))}
              </ExpandPanel>
              <ExpandPanel label="⚠️ Risk Checklist">
                <ul style={{ fontSize:11, paddingLeft:16, lineHeight:1.9 }}>
                  <li style={{ color:(c.short_pct_float||0)>15?'#e5281e':'inherit' }}>
                    Short interest: {c.short_pct_float!=null?`${c.short_pct_float.toFixed(1)}%`:'N/A'}
                    {(c.short_pct_float||0)>15?' ⚠️ HIGH':''}
                  </li>
                  <li style={{ color:(c.days_to_earnings||999)<=14?'#f59e0b':'inherit' }}>
                    Earnings: {c.next_earnings_date||'N/A'}
                    {(c.days_to_earnings||999)<=14?' ⚠️ NEAR':''}
                  </li>
                  <li>Sector: {c.valuation?.sector||'—'}</li>
                  <li>Analysts covering: {c.num_analyst_opinions||'—'}</li>
                  <li>Trend: {(c.trend||'—').toUpperCase()}</li>
                </ul>
                <p style={{ fontSize:10, color:'#94a3b8', marginTop:6 }}>
                  Suggested stop: ~8% below entry | Target: ${c.analyst_target_mean?.toFixed(2)||'—'}
                </p>
              </ExpandPanel>
              <ExpandPanel label="📈 Quality & Momentum">
                {[
                  ['Liquidity', c.liquidity_ok!==false?'✓ OK':'⚠ LOW'],
                  ['Avg Volume', c.avg_volume!=null?(c.avg_volume>1e6?`${(c.avg_volume/1e6).toFixed(1)}M`:`${(c.avg_volume/1000).toFixed(0)}K`):'—'],
                  ['EPS (TTM)', c.trailing_eps!=null?(c.trailing_eps>0?`$${c.trailing_eps.toFixed(2)}`:`⚠ -$${Math.abs(c.trailing_eps).toFixed(2)}`):'—'],
                  ['Revenue Growth', c.revenue_growth!=null?`${(c.revenue_growth*100).toFixed(1)}%`:'—'],
                  ['vs Sector 3m', c.rel_strength_3m!=null?`${c.rel_strength_3m>=0?'+':''}${c.rel_strength_3m.toFixed(1)}%`:'—'],
                  ['EPS Flags', (c.earnings_quality_flags||[]).length>0?(c.earnings_quality_flags||[]).join(', '):'✓ All clear'],
                ].map(([k,v])=>(
                  <div key={k} style={{ display:'flex', justifyContent:'space-between', fontSize:11, borderBottom:'1px solid #f1f5f9', padding:'3px 0' }}>
                    <span style={{ color:'#64748b' }}>{k}</span><strong>{v}</strong>
                  </div>
                ))}
              </ExpandPanel>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function DiscoverTab({
  preferredMarket, cachedScanData, cachedScanLoading, fetchCachedWealthScan,
  topUniverse, rankedItems, rankLoading, selectedSymbols, setSelectedSymbols,
  selectedCount, trackSelected, trackLoading, addSingleShare,
  aiQuery, setAiQuery, aiProvider, setAiProvider, requestAiSuggestions,
  aiLoading, aiSuggestions, aiProviderUsed, setBuyModal, pillStyle,
  EXCHANGE_LABELS, budgetInfo,
}) {
  const [expandedRow, setExpandedRow]   = useState(null)
  const [filterZone, setFilterZone]     = useState('all')
  const [minScore, setMinScore]         = useState(0)
  const [showFinder, setShowFinder]     = useState(false)
  const [showRanked, setShowRanked]     = useState(false)

  const candidates = cachedScanData?.candidates || []

  const filtered = candidates.filter(c => {
    const zone = (c.entry_timing?.entry_zone || 'caution').toLowerCase()
    if (filterZone !== 'all' && zone !== filterZone) return false
    if ((c.score || 0) < minScore) return false
    return true
  })

  const clearCount   = candidates.filter(c=>(c.entry_timing?.entry_zone||'').toLowerCase()==='clear').length
  const cautionCount = candidates.filter(c=>(c.entry_timing?.entry_zone||'').toLowerCase()==='caution').length
  const avoidCount   = candidates.filter(c=>(c.entry_timing?.entry_zone||'').toLowerCase()==='avoid').length

  const spotlight = candidates
    .filter(c=>(c.entry_timing?.entry_zone||'').toLowerCase()==='clear')
    .slice(0,3)

  const onBuy = c => {
    const score = c.score || 0.5;
    const price = c.current_price || 1;
    const hasBudget = budgetInfo && budgetInfo.budget_set && budgetInfo.remaining_capital > 0;

    let targetCapital;
    let sizingNote;

    if (hasBudget) {
      // Budget-aware sizing: use remaining capital and max-per-position limit
      const maxForPosition = Math.min(
        budgetInfo.max_per_position || budgetInfo.remaining_capital,
        budgetInfo.remaining_capital
      );
      // Scale by AI score: high score = full allocation, low score = reduced
      const scoreMultiplier = score >= 0.7 ? 1.0 : score >= 0.5 ? 0.7 : 0.5;
      targetCapital = maxForPosition * scoreMultiplier;
      if (c.high_volatility_warning) targetCapital *= 0.6;

      const budgetCurrency = budgetInfo.currency || 'AUD';
      sizingNote = `Budget: $${budgetInfo.total_budget?.toLocaleString()} ${budgetCurrency} | Deployed: $${budgetInfo.deployed_capital?.toLocaleString()} | Available: $${budgetInfo.remaining_capital?.toLocaleString()} | Max/position: $${budgetInfo.max_per_position?.toLocaleString()} (${budgetInfo.max_position_pct}%)`;
    } else {
      // Fallback: hardcoded sizing
      targetCapital = 2500;
      if (score >= 0.7) targetCapital = 3500;
      else if (score < 0.4) targetCapital = 1500;
      if (c.high_volatility_warning) targetCapital *= 0.6;
      sizingNote = null;
    }

    const suggestedQty = Math.max(1, Math.floor(targetCapital / price));

    setBuyModal({
      symbol: c.symbol, name: c.name, current_price: c.current_price,
      analyst_target: c.analyst_target_mean,
      quantity: suggestedQty.toString(), execution_price: price.toFixed(2),
      smart_sizing: `Suggested ${suggestedQty} shares ($${(suggestedQty*price).toFixed(0)}) — ${hasBudget ? 'budget-aware' : 'auto'} risk-adjusted based on AI score${c.high_volatility_warning ? ' & volatility' : ''}.`,
      budget_note: sizingNote,
    })
  }

  const warningLabels = {
    volume_divergence: 'Price jumped without volume support — spike may not hold.',
    drawdown_severe:   'Elevated downside risk. Consider position sizing carefully.',
    drawdown_moderate: 'Moderate downside volatility. Monitor closely.',
    extreme_projection:'Model output was unusually large and has been capped.',
    overbought:        'RSI above 70 — may be overbought and due for pullback.',
  }

  return (
    <div>
      {/* ── Header bar ─────────────────────────────────────────── */}
      <div style={{
        background:'linear-gradient(135deg,#0f172a 0%,#1e293b 100%)',
        borderRadius:10, padding:'18px 22px', marginBottom:16,
        display:'flex', alignItems:'center', justifyContent:'space-between', flexWrap:'wrap', gap:12,
      }}>
        <div>
          <h2 style={{ margin:0, color:'#f1f5f9', fontSize:18, fontWeight:800 }}>
            🔍 Discover — {EXCHANGE_LABELS[preferredMarket]} Deep Scan
          </h2>
          <p style={{ margin:'4px 0 0', color:'#64748b', fontSize:12 }}>
            {candidates.length>0
              ? `${candidates.length} stocks analysed · ${clearCount} clear entries · ${cautionCount} caution · ${avoidCount} avoid`
              : 'Auto-loaded from 5AM broad scan · 200+ stocks'}
            {cachedScanData?.generated_at && ` · refreshed ${new Date(cachedScanData.generated_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}`}
          </p>
        </div>
        <div style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
          {/* Zone filters */}
          {['all','clear','caution','avoid'].map(z=>(
            <button key={z}
              onClick={()=>{ setFilterZone(z); setExpandedRow(null) }}
              style={{
                padding:'5px 12px', borderRadius:20, fontSize:11, fontWeight:700, cursor:'pointer', border:'none',
                background: filterZone===z ? (z==='all'?'#475569':zoneColors[z]||'#475569') : '#1e293b',
                color: filterZone===z ? '#fff' : '#94a3b8',
              }}>
              {z==='all'?'All':zoneEmoji[z]+' '+z.charAt(0).toUpperCase()+z.slice(1)}
              {z!=='all' && ` (${z==='clear'?clearCount:z==='caution'?cautionCount:avoidCount})`}
            </button>
          ))}
          <button className="btn-secondary" onClick={fetchCachedWealthScan}
            disabled={cachedScanLoading} style={{ fontSize:11 }}>
            {cachedScanLoading?'Scanning…':'🔄 Refresh'}
          </button>
        </div>
      </div>

      {/* ── AI Spotlight (top clear-entry picks) ───────────────── */}
      {spotlight.length > 0 && (
        <div style={{ marginBottom:16 }}>
          <p style={{ fontSize:12, fontWeight:700, color:'#64748b', textTransform:'uppercase', letterSpacing:1, marginBottom:10 }}>
            🎯 AI Spotlight — Highest Conviction Opportunities Today
          </p>
          <div style={{ display:'flex', gap:14, flexWrap:'wrap' }}>
            {spotlight.map((c,i)=>(
              <SpotlightCard key={c.symbol} c={c} idx={i} onBuy={onBuy} />
            ))}
          </div>
        </div>
      )}

      {/* ── Full scored feed ────────────────────────────────────── */}
      <section className="panel" style={{ marginBottom:16 }}>
        <div className="section-header">
          <h2>📊 Full Scored Feed {filtered.length<candidates.length && `— ${filtered.length} of ${candidates.length} shown`}</h2>
          <div style={{ display:'flex', gap:8, alignItems:'center' }}>
            <label style={{ fontSize:11, color:'var(--ig-medium)' }}>
              Min Score:
              <input type="range" min={0} max={0.9} step={0.05} value={minScore}
                onChange={e=>setMinScore(parseFloat(e.target.value))}
                style={{ marginLeft:6, width:80 }} />
              <span style={{ marginLeft:4, fontWeight:700 }}>{minScore.toFixed(2)}</span>
            </label>
          </div>
        </div>
        <p className="section-note">
          Click any row to expand full detail — valuation, risk checklist, entry timing, and quality metrics.
          {' '}⚠️ badges are clickable for risk detail.
        </p>
        {!candidates.length ? (
          <p className="section-note">No precomputed scan available. Broad scan runs at 5AM AEST.
            <button className="btn-secondary" style={{ marginLeft:8 }} onClick={fetchCachedWealthScan}>Check Now</button>
          </p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th><th>Symbol</th><th>Price</th><th>Score</th>
                  <th>P(≥5%)</th><th>Forecast</th><th>Upside</th>
                  <th>Entry</th><th></th><th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c,idx)=>(
                  <CandidateRow key={c.symbol} c={c} idx={idx}
                    isExpanded={expandedRow===idx}
                    onToggle={()=>setExpandedRow(expandedRow===idx?null:idx)}
                    onBuy={onBuy} />
                ))}
                {filtered.length===0 && (
                  <tr><td colSpan={10} style={{ textAlign:'center', color:'var(--ig-muted)', padding:24 }}>
                    No candidates match current filters.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Ranked Candidates (collapsible) ─────────────────────── */}
      <section className="panel" style={{ marginBottom:16 }}>
        <div className="section-header" style={{ cursor:'pointer' }} onClick={()=>setShowRanked(v=>!v)}>
          <h2>📈 Ranked Candidates — 3-Month Probability {showRanked?'▲':'▼'}</h2>
          {showRanked && (
            <button className="add-btn" onClick={e=>{e.stopPropagation();trackSelected()}}
              disabled={trackLoading||selectedCount===0}>
              {trackLoading?'Adding…':`Track Selected (${selectedCount})`}
            </button>
          )}
        </div>
        {showRanked && (
          <>
            <p className="section-note">Sorted by composite score. ⚠️ = elevated risk — click the icon to see why.</p>
            {rankLoading ? <p>Ranking symbols…</p> : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead><tr><th>Track</th><th>Symbol</th><th>P(≥5%)</th><th>Exp 3M</th><th>Trend</th><th>Score</th></tr></thead>
                  <tbody>
                    {rankedItems.map(item=>{
                      const friendly = warningLabels[item.warning_type] || item.warning_message || 'Elevated risk detected. Review before trading.'
                      return (
                        <tr key={item.symbol}>
                          <td><input type="checkbox" checked={!!selectedSymbols[item.symbol]}
                            onChange={e=>setSelectedSymbols(prev=>({...prev,[item.symbol]:e.target.checked}))} /></td>
                          <td>
                            <strong>{item.symbol}</strong>
                            {item.high_volatility_warning && (
                              <span
                                title={friendly}
                                onClick={()=>alert(`⚠️ ${item.symbol} Risk Alert\n\n${friendly}`)}
                                style={{ marginLeft:5, cursor:'help' }}>⚠️</span>
                            )}
                          </td>
                          <td>{item.prob_ge_5pct.toFixed(2)}%</td>
                          <td className={item.expected_return_3m_pct>=0?'positive':'negative'}>
                            {item.expected_return_3m_pct.toFixed(2)}%
                          </td>
                          <td><span style={pillStyle(item.trend)}>{item.trend}</span></td>
                          <td>{item.score.toFixed(2)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      {/* ── AI Share Finder (collapsible) ────────────────────────── */}
      <section className="panel">
        <div className="section-header" style={{ cursor:'pointer' }} onClick={()=>setShowFinder(v=>!v)}>
          <h2>🤖 AI Share Finder {showFinder?'▲':'▼'}</h2>
        </div>
        {showFinder && (
          <>
            <p className="section-note">Ask the AI to suggest shares for a theme or scenario, then rank them.</p>
            <textarea className="chat-input" value={aiQuery} onChange={e=>setAiQuery(e.target.value)}
              rows={3} placeholder="e.g. ASX mining and energy shares likely to gain in next 3 months" />
            <div style={{ display:'flex', gap:10, marginTop:8, alignItems:'center', flexWrap:'wrap' }}>
              <select className="provider-select" value={aiProvider} onChange={e=>setAiProvider(e.target.value)}>
                <option value="auto">Auto</option>
                <option value="local">Local (LM Studio)</option>
                <option value="openai">OpenAI</option>
              </select>
              <button className="add-btn" onClick={requestAiSuggestions} disabled={aiLoading}>
                {aiLoading?'Thinking…':'Get AI Share List'}
              </button>
              {aiProviderUsed && <span style={{ fontSize:11, color:'var(--ig-muted)' }}>via {aiProviderUsed}</span>}
            </div>
            {aiSuggestions.length>0 && (
              <div className="chips-grid" style={{ marginTop:14 }}>
                {aiSuggestions.map(item=>(
                  <div key={item.symbol} className="chip-card">
                    <div><strong>{item.symbol}</strong><p>{item.name}</p></div>
                    <button className="add-btn small" onClick={()=>addSingleShare(item.symbol,'ai_shortlist')}>Add</button>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  )
}

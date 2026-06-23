import React, { useState, useEffect } from 'react'
import { Line } from 'react-chartjs-2'

const zoneColors = { clear: '#00a854', caution: '#f59e0b', avoid: '#e5281e' }
const zoneEmoji  = { clear: '🟢', caution: '🟡', avoid: '🔴' }
const WARN_LABELS = {
  volume_divergence: 'Price jumped without volume support — spike may not hold.',
  drawdown_severe:   'Elevated downside risk detected. Size positions carefully.',
  drawdown_moderate: 'Moderate downside volatility observed. Monitor closely.',
  extreme_projection:'Model output unusually large and capped. Treat with caution.',
  overbought:        'RSI above 70 — stock may be overbought, pullback risk.',
}



function ScoreBadge({ score }) {
  const pct = Math.round((score || 0) * 100)
  const color = pct >= 70 ? '#00a854' : pct >= 50 ? '#f59e0b' : '#e5281e'
  return (
    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
      <div style={{ width:48, height:6, background:'var(--border-color)', borderRadius:3, overflow:'hidden' }}>
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
      background:'linear-gradient(135deg,var(--text-primary) 0%,var(--text-primary) 100%)',
      border:`2px solid ${zoneColors[zone] || 'var(--border-strong)'}`,
      borderRadius:12, padding:20, position:'relative', overflow:'hidden',
      minWidth:0, flex:'1 1 240px',
    }}>
      <div style={{ position:'absolute', top:0, right:0, padding:'6px 12px', background:zoneColors[zone]||'var(--border-strong)', borderBottomLeftRadius:8, fontSize:11, fontWeight:700, color:'var(--bg-secondary)' }}>
        #{idx+1} {zoneEmoji[zone]} {zone.toUpperCase()}
      </div>
      <div style={{ marginBottom:8 }}>
        <div style={{ fontSize:22, fontWeight:800, color:'var(--bg-tertiary)', letterSpacing:1 }}>{c.symbol}</div>
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
            <div style={{ fontSize:15, fontWeight:700, color:'var(--border-color)', marginTop:2 }}>{v}</div>
          </div>
        ))}
      </div>
      
      <div style={{ padding:'8px 10px', background:'#5f1515', border:'1px solid #991b1b', borderRadius:6, marginBottom:12 }}>
        <p style={{ margin:0, color:'rgba(255, 51, 51, 0.3)', fontSize:10, fontWeight:700, lineHeight:1.4 }}>
          ⚠️ PREMORTEM GUARDRAIL: <span style={{fontWeight:400}}>Verify no upcoming earnings or recent stock splits. AI strictly evaluates technical price action and is blind to binary corporate events.</span>
        </p>
      </div>

      <div style={{ fontSize:11, color:'#94a3b8', marginBottom:12 }}>{et.reason}</div>
      <button onClick={()=>onBuy(c)} style={{
        width:'100%', padding:'9px 0', background:'linear-gradient(90deg,#00a854,#00c96e)',
        border:'none', borderRadius:6, color:'var(--bg-secondary)', fontWeight:700, fontSize:13, cursor:'pointer',
      }}>✅ I'm Buying This</button>
    </div>
  )
}

function ExpandPanel({ label, children }) {
  return (
    <div style={{ background:'var(--bg-secondary)', border:'1px solid var(--border-color)', borderRadius:6, padding:12 }}>
      <p style={{ fontWeight:700, fontSize:12, marginBottom:8, color:'var(--border-strong)' }}>{label}</p>
      {children}
    </div>
  )
}

function VolatilityMeter({ score, capRiskNote }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 4 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--border-strong)' }}>Volatility Rating</span>
        <span style={{ fontSize: 13, fontWeight: 800, color: score <= 3 ? '#00a854' : score <= 6 ? '#f59e0b' : '#e5281e' }}>{score}/10</span>
      </div>
      <div style={{ display: 'flex', gap: 2 }}>
        {[1,2,3,4,5,6,7,8,9,10].map(n => {
          let bgColor = 'var(--border-color)';
          if (n <= score) {
            if (score <= 3) bgColor = '#00a854';
            else if (score <= 6) bgColor = '#f59e0b';
            else bgColor = '#e5281e';
          }
          return (
            <div key={n} style={{ flex: 1, height: 8, borderRadius: 1, background: bgColor }} />
          )
        })}
      </div>
      <div style={{ fontSize: 10, color: '#64748b', marginTop: 6, lineHeight: 1.4 }}>
        {capRiskNote}
      </div>
    </div>
  )
}

function CandidateRow({ c, idx, isExpanded, onToggle, onBuy, budgetInfo }) {
  const et   = c.entry_timing || {}
  const zone = (et.entry_zone || 'caution').toLowerCase()
  const upside = c.analyst_upside_pct
  const predChg = c.predicted_change_pct || c.expected_return_3m_pct || 0
  const predPrice = c.predicted_price_3m || c.predicted_price
  const warnMsg = WARN_LABELS[c.warning_type] || c.warning_message || 'Elevated risk — review before trading.'

  const [newsData, setNewsData] = useState(null)
  const [newsLoading, setNewsLoading] = useState(false)
  const [graphData, setGraphData] = useState(null)
  const [graphLoading, setGraphLoading] = useState(false)

  useEffect(() => {
    if (isExpanded && !graphData && !graphLoading) {
      setGraphLoading(true);
      fetch(`${import.meta.env.VITE_API_URL || '/api'}/ai/analyze/${c.symbol}?market=${c.market||'AU'}`)
        .then(r => r.json())
        .then(d => setGraphData(d))
        .catch(e => console.error(e))
        .finally(() => setGraphLoading(false));
    }
  }, [isExpanded, c.symbol, c.market]);

  const loadNews = async () => {
    if (newsData) return;
    setNewsLoading(true);
    try {
      const res = await fetch(`/api/analyze/sentiment?symbol=${c.symbol}`);
      if (res.ok) {
        const data = await res.json();
        setNewsData(data);
      }
    } catch (e) {
      console.error(e);
    }
    setNewsLoading(false);
  };

  let sizingSuggestion = 'Standard Size (e.g. $2,500)';
  if (budgetInfo && budgetInfo.budget_set && budgetInfo.remaining_capital > 0) {
    const maxForPosition = Math.min(budgetInfo.max_per_position || budgetInfo.remaining_capital, budgetInfo.remaining_capital);
    const scoreMultiplier = (c.score || 0) >= 0.7 ? 1.0 : (c.score || 0) >= 0.5 ? 0.7 : 0.5;
    let target = maxForPosition * scoreMultiplier;
    if (c.high_volatility_warning) target *= 0.6;
    sizingSuggestion = `$${target.toFixed(0)} (Max allowed: $${maxForPosition.toFixed(0)})`;
  }

  let capRiskNote = 'Standard Volatility';
  let volatilityScore = 5;
  if (c.market_cap) {
    if (c.market_cap < 300000000) { capRiskNote = 'High Volatility (Micro Cap) - Historically prone to extreme swings and low liquidity. Reduce position size.'; volatilityScore = 9; }
    else if (c.market_cap < 2000000000) { capRiskNote = 'Moderate Volatility (Small Cap) - Higher growth potential but wider spreads. Stick to limits.'; volatilityScore = 7; }
    else if (c.market_cap < 10000000000) { capRiskNote = 'Balanced (Mid Cap) - Established business but can move aggressively on news.'; volatilityScore = 4; }
    else { capRiskNote = 'Lower Volatility (Large Cap) - Established blue chip, safer historical tracking.'; volatilityScore = 2; }
  }
  if (c.high_volatility_warning) {
    volatilityScore = Math.min(10, volatilityScore + 2);
    capRiskNote += ' ⚠️ Active volatility warning detected.';
  }

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
          {predPrice ? `$${predPrice.toFixed(2)} ` : ''}({predChg>=0?'+':''}{predChg.toFixed(1)}%)
        </td>
        <td style={{ color: (upside||0)>=0?'#00a854':'#e5281e', fontWeight:600 }}>
          {c.analyst_target_mean ? `$${c.analyst_target_mean.toFixed(2)} ` : ''}{upside!=null?`(${upside>=0?'+':''}${upside.toFixed(1)}%)`:'—'}
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
          <td colSpan={10} style={{ background:'var(--bg-tertiary)', padding:16, whiteSpace: 'normal', wordWrap: 'break-word' }}>
            <div style={{ padding:'10px 12px', background:'#fee2e2', border:'1px solid rgba(255, 51, 51, 0.3)', borderRadius:6, marginBottom:14 }}>
              <p style={{ margin:0, color:'var(--ig-red)', fontSize:11, fontWeight:700, lineHeight: 1.5 }}>
                🚨 CRITICAL SYSTEM GUARDRAIL CHECK: 
                <span style={{fontWeight:500}}> The algorithmic engine is statistically robust but strictly blind to sudden binary events. Before buying, you MUST manually verify: 1) No Earnings Reports in the next 3 days, 2) No unadjusted stock splits/special dividends artificially warping the price chart, and 3) Prepare for "Fat Tail" unpredictable news drops in Micro-Caps.</span>
              </p>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(210px,1fr))', gap:12 }}>
              <ExpandPanel label="📅 Entry Timing & Accuracy">
                <p style={{ fontSize:12, color:zoneColors[zone], fontWeight:700, marginBottom:4 }}>{zoneEmoji[zone]} {zone.toUpperCase()}</p>
                <p style={{ fontSize:11, color:'#64748b', marginBottom:8, lineHeight:1.5 }}>{et.reason}</p>
                <p style={{ fontSize:11 }}>Earnings risk: <strong>{(et.earnings_risk||'—').toUpperCase()}</strong></p>
                <p style={{ fontSize:11 }}>Technicals: <strong>{et.technical_confirmed?'✅ Confirmed':'❌ Not confirmed'}</strong></p>
              </ExpandPanel>
              <ExpandPanel label="📊 Valuation">
                {[
                  ['P/E', c.pe?`${c.pe.toFixed(1)}x`:'—'],
                  ['Fwd P/E', c.forward_pe?`${c.forward_pe.toFixed(1)}x`:'—'],
                  ['EPS Growth', c.eps_growth_fwd_pct!=null?`${c.eps_growth_fwd_pct>0?'+':''}${c.eps_growth_fwd_pct.toFixed(1)}%`:'—'],
                  ['Analyst Target', c.analyst_target_mean?`$${c.analyst_target_mean.toFixed(2)}`:'—'],
                  ['Dividend Yield', c.dividend_yield?`${c.dividend_yield.toFixed(1)}%`:'—'],
                  ['52w Range', (c['52w_low'] && c['52w_high']) ? `$${c['52w_low'].toFixed(2)} - $${c['52w_high'].toFixed(2)}` : '—'],
                  ['Short Interest', c.short_pct_float!=null?`${c.short_pct_float.toFixed(1)}%`:'—'],
                ].map(([k,v])=>(
                  <div key={k} style={{ display:'flex', justifyContent:'space-between', fontSize:11, borderBottom:'1px solid var(--bg-tertiary)', padding:'3px 0' }}>
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
                  <li>Sector: {c.sector || c.valuation?.sector || '—'}</li>
                  <li>Industry: {c.industry || '—'}</li>
                  <li>Analysts covering: {c.num_analyst_opinions||'—'}</li>
                  <li>Trend: {(c.trend||'—').toUpperCase()}</li>
                </ul>
                <p style={{ fontSize:10, color:'#94a3b8', marginTop:6 }}>
                  Suggested stop: 5-6% below entry (cap tier dependent) | Target: ${c.analyst_target_mean?.toFixed(2)||'—'}
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
                  <div key={k} style={{ display:'flex', justifyContent:'space-between', fontSize:11, borderBottom:'1px solid var(--bg-tertiary)', padding:'3px 0' }}>
                    <span style={{ color:'#64748b' }}>{k}</span><strong>{v}</strong>
                  </div>
                ))}
              </ExpandPanel>
              <ExpandPanel label="📊 14-Day Traceability">
                {graphLoading ? (
                  <p style={{fontSize:11, color:'#64748b'}}>Loading historical accuracy...</p>
                ) : graphData?.weekly_data?.length > 0 ? (
                  <div style={{ position: 'relative', height: 180 }}>
                    <Line
                      data={{
                        labels: graphData.weekly_data.map(d => d.date),
                        datasets: [
                          { label: 'Actual', data: graphData.weekly_data.map(d => d.actual_price), borderColor: '#00a854', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 2, tension: 0.2 },
                          { label: 'Predicted', data: graphData.weekly_data.map(d => d.predicted_price), borderColor: '#d4ac0d', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 2, tension: 0.2, borderDash: [5,5] }
                        ]
                      }}
                      options={{
                        responsive: true, maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: { x: { display: false }, y: { ticks: { fontSize: 9 } } }
                      }}
                    />
                  </div>
                ) : (
                  <p style={{fontSize:11, color:'#64748b'}}>No recent tracking data available.</p>
                )}
              </ExpandPanel>

              <ExpandPanel label="💼 Strategy Mix & Position Sizing">
                <p style={{ fontSize:11, color:'#64748b', marginBottom:8, lineHeight:1.6 }}>
                  Based on your configured total budget, our algorithm suggests scaling into this trade proportionally.
                </p>
                <div style={{ background:'var(--bg-tertiary)', padding:10, borderRadius:6, marginBottom:10 }}>
                  <div style={{ fontSize:11, color:'#64748b', textTransform:'uppercase', letterSpacing:1 }}>Suggested Size</div>
                  <div style={{ fontSize:16, fontWeight:700, color:'var(--text-primary)' }}>{sizingSuggestion}</div>
                </div>
                <VolatilityMeter score={volatilityScore} capRiskNote={capRiskNote} />
              </ExpandPanel>

              <ExpandPanel label="📰 Sentiment & News">
                {!newsData ? (
                  <div style={{ textAlign: 'center', padding: '10px 0' }}>
                    <button onClick={loadNews} disabled={newsLoading} style={{
                      padding:'6px 12px', background:'var(--border-color)', color:'var(--text-secondary)', borderRadius:6, border:'none', fontSize:11, fontWeight:600, cursor:newsLoading?'not-allowed':'pointer'
                    }}>
                      {newsLoading ? 'Fetching latest news...' : 'Load Latest EODHD News & Sentiment'}
                    </button>
                  </div>
                ) : (
                  <div>
                    {(!newsData.news || newsData.news.length === 0 || (newsData.news.length === 1 && newsData.news[0].title.startsWith("No recent news"))) ? (
                      <p style={{ fontSize:11, color:'#64748b', textAlign: 'center', padding: '10px 0' }}>No recent news available for this ticker.</p>
                    ) : (
                      <>
                        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
                          <span style={{ fontSize:12, fontWeight:700 }}>Overall: <span style={{ color: newsData.score >= 0.2 ? '#00a854' : newsData.score <= -0.2 ? '#e5281e' : '#f59e0b' }}>{newsData.sentiment.toUpperCase()}</span></span>
                          <span style={{ fontSize:10, color:'#94a3b8' }}>Score: {newsData.score?.toFixed(2)}</span>
                        </div>
                        <p style={{ fontSize:11, color:'var(--text-secondary)', marginBottom:10, lineHeight:1.5 }}>{newsData.summary}</p>
                        <div style={{ fontSize:11, fontWeight:700, marginBottom:4 }}>Headlines:</div>
                        <ul style={{ paddingLeft:16, margin:0, fontSize:10, color:'#64748b', lineHeight:1.5 }}>
                          {(newsData.news || []).slice(0,4).map((n, i) => (
                            <li key={i}>{n.title}</li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                )}
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

  // Cap grouping
  const largeCap = filtered.filter(c => c.market_cap >= 10000000000).sort((a,b)=>(b.score||0)-(a.score||0));
  const midCap = filtered.filter(c => c.market_cap >= 2000000000 && c.market_cap < 10000000000).sort((a,b)=>(b.score||0)-(a.score||0));
  const smallCap = filtered.filter(c => c.market_cap == null || c.market_cap < 2000000000).sort((a,b)=>(b.score||0)-(a.score||0));

  // If a cap is empty after filtering, grab top 3 from unfiltered candidates of that cap size
  if (largeCap.length === 0) {
    largeCap.push(...candidates.filter(c => c.market_cap >= 10000000000).sort((a,b)=>(b.score||0)-(a.score||0)).slice(0,3));
  }
  if (midCap.length === 0) {
    midCap.push(...candidates.filter(c => c.market_cap >= 2000000000 && c.market_cap < 10000000000).sort((a,b)=>(b.score||0)-(a.score||0)).slice(0,3));
  }
  if (smallCap.length === 0) {
    smallCap.push(...candidates.filter(c => c.market_cap == null || c.market_cap < 2000000000).sort((a,b)=>(b.score||0)-(a.score||0)).slice(0,3));
  }

  const renderCapTable = (title, list, capType) => (
    <div style={{ marginBottom: 24 }}>
      <h3 style={{ margin:'0 0 10px 0', fontSize:14, color:'var(--text-secondary)' }}>{title}</h3>
      {list.length === 0 ? (
        <div style={{ padding: 12, background: 'var(--bg-tertiary)', borderRadius: 8, color: '#94a3b8', fontSize: 13 }}>
          No candidates found in this category.
        </div>
      ) : (
        <div className="table-wrap" style={{ margin:0, border:'1px solid var(--border-color)', borderRadius:8 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th><th>Symbol</th><th>Price</th><th>Score</th>
                <th>P(≥5%)</th><th>AI Target (3M)</th><th>Analyst Target</th>
                <th>Entry</th><th></th><th></th>
              </tr>
            </thead>
            <tbody>
              {list.map((c,idx)=>{
                // Ensure unique key if grabbed from unfiltered list
                const isFilteredOut = !filtered.find(fc => fc.symbol === c.symbol);
                return (
                  <React.Fragment key={`${capType}-${c.symbol}`}>
                    {isFilteredOut && idx === 0 && (
                       <tr><td colSpan={10} style={{ padding:'8px 12px', background:'rgba(255, 184, 0, 0.1)', color:'var(--ig-warning)', fontSize:11, fontWeight:600 }}>
                         Note: Top {list.length} shown below did not meet the "{filterZone}" or score filters, but are listed for reference.
                       </td></tr>
                    )}
                    <CandidateRow c={c} idx={idx}
                      isExpanded={expandedRow===`${capType}-${idx}`}
                      onToggle={()=>setExpandedRow(expandedRow===`${capType}-${idx}`?null:`${capType}-${idx}`)}
                      onBuy={onBuy}
                      budgetInfo={budgetInfo} />
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )

  return (
    <div>
      {/* ── Header bar ─────────────────────────────────────────── */}
      <div style={{
        background:'linear-gradient(135deg,var(--text-primary) 0%,var(--text-primary) 100%)',
        borderRadius:10, padding:'18px 22px', marginBottom:16,
        display:'flex', alignItems:'center', justifyContent:'space-between', flexWrap:'wrap', gap:12,
      }}>
        <div>
          <h2 style={{ margin:0, color:'var(--bg-tertiary)', fontSize:18, fontWeight:800 }}>
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
                background: filterZone===z ? (z==='all'?'var(--text-secondary)':zoneColors[z]||'var(--text-secondary)') : 'var(--text-primary)',
                color: filterZone===z ? 'var(--bg-secondary)' : '#94a3b8',
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
          <div>
            {renderCapTable('Large Cap (>$10B)', largeCap, 'large')}
            {renderCapTable('Mid Cap ($2B - $10B)', midCap, 'mid')}
            {renderCapTable('Small & Micro Cap (<$2B)', smallCap, 'small')}
          </div>
        )}
      </section>

      {/* ── ETFs & Commodities Spotlight ────────────────────────── */}
      <section className="panel" style={{ marginBottom:16 }}>
        <div className="section-header">
          <h2>🪙 ETFs & Commodities Spotlight</h2>
        </div>
        <p className="section-note">
          A dedicated view for broad indices and precious metals. (Suggested selections — automated scanning coming soon!)
        </p>
        <div className="table-wrap" style={{ margin:0, border:'1px solid var(--border-color)', borderRadius:8 }}>
          <table className="data-table">
            <thead>
              <tr><th>Asset</th><th>Type</th><th>Rationale</th></tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ fontWeight:700 }}>GOLD.AX</td>
                <td style={{ color:'#64748b' }}>Physical Gold ETF</td>
                <td style={{ fontSize:12, color:'var(--text-secondary)' }}>Excellent hedge against high market volatility and inflation.</td>
              </tr>
              <tr>
                <td style={{ fontWeight:700 }}>NDQ.AX</td>
                <td style={{ color:'#64748b' }}>NASDAQ 100 ETF</td>
                <td style={{ fontSize:12, color:'var(--text-secondary)' }}>Broad tech exposure. High momentum in low-rate environments.</td>
              </tr>
              <tr>
                <td style={{ fontWeight:700 }}>IOZ.AX</td>
                <td style={{ color:'#64748b' }}>ASX 200 ETF</td>
                <td style={{ fontSize:12, color:'var(--text-secondary)' }}>Core portfolio foundation tracking the broad Australian market.</td>
              </tr>
            </tbody>
          </table>
        </div>
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

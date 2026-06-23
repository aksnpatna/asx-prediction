import React from 'react';

export default function NewsSentimentMonitor({ sentimentScan, sentimentLoading, fetchSentimentScan }) {
  return (
    <section className="panel" style={{ marginBottom: 14 }}>
      <div className="section-header">
        <h2>📰 News Sentiment Monitor</h2>
        <button className="btn-secondary" onClick={fetchSentimentScan} disabled={sentimentLoading} style={{ fontSize: 12 }}>
          {sentimentLoading ? 'Scanning...' : '🔍 Scan News Sentiment'}
        </button>
      </div>
      {!sentimentScan && !sentimentLoading && (
        <p className="section-note">Click "Scan News Sentiment" to analyse recent news for all your held positions. Negative news will generate sell alerts with profit-at-risk calculations.</p>
      )}
      {sentimentScan && (
        <div>
          {/* Summary bar */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginBottom: 12 }}>
            <div className="stat-card blue">
              <div className="stat-content"><span className="stat-title">Scanned</span><span className="stat-value">{sentimentScan.scanned}</span></div>
            </div>
            <div className={`stat-card ${sentimentScan.high_urgency_count > 0 ? 'red' : 'green'}`}>
              <div className="stat-content"><span className="stat-title">⚠️ High Alerts</span><span className="stat-value">{sentimentScan.high_urgency_count}</span></div>
            </div>
            <div className={`stat-card ${sentimentScan.medium_urgency_count > 0 ? '' : 'green'}`} style={{ borderLeftColor: sentimentScan.medium_urgency_count > 0 ? 'var(--ig-warning)' : undefined }}>
              <div className="stat-content"><span className="stat-title">🟡 Watch</span><span className="stat-value">{sentimentScan.medium_urgency_count}</span></div>
            </div>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 16 }}>
            {sentimentScan.summary} • Scanned at {new Date(sentimentScan.scanned_at).toLocaleTimeString()}
          </p>

          {/* Sell Alerts */}
          {sentimentScan.alerts?.filter(a => a.urgency === 'high').map((alert, i) => (
            <div key={i} style={{
              padding: 16, marginBottom: 12, borderRadius: 8,
              background: 'rgba(255, 51, 51, 0.05)',
              border: '1px solid rgba(255, 51, 51, 0.3)',
              boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <strong style={{ fontSize: 15, color: '#FF6B6B' }}>⚠️ NEGATIVE NEWS — {alert.symbol}</strong>
                <span style={{ padding: '3px 10px', borderRadius: 4, fontSize: 10, fontWeight: 700, background: 'var(--ig-red)', color: '#fff', letterSpacing: '0.5px' }}>REVIEW SELL</span>
              </div>
              {alert.headline && <p style={{ margin: '0 0 10px', fontSize: 14, color: 'var(--text-primary)', fontStyle: 'italic' }}>"{alert.headline}"</p>}
              <p style={{ margin: '0 0 10px', fontSize: 13, color: 'var(--text-secondary)' }}>{alert.reason}</p>
              {alert.themes?.length > 0 && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                  {alert.themes.map((t, j) => (
                    <span key={j} style={{ padding: '3px 10px', borderRadius: 12, fontSize: 11, background: 'rgba(255, 51, 51, 0.1)', border: '1px solid rgba(255, 51, 51, 0.2)', color: '#FF6B6B' }}>{t}</span>
                  ))}
                </div>
              )}
              {alert.profit_at_risk && Object.keys(alert.profit_at_risk).length > 0 && (
                <div style={{ padding: 12, background: 'var(--bg-secondary)', borderRadius: 6, border: '1px solid var(--border-color)', marginBottom: 10 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10, fontSize: 13 }}>
                    <div><span style={{ color: 'var(--text-secondary)' }}>Avg Cost: </span><strong style={{ color: 'var(--text-primary)' }}>${alert.profit_at_risk.avg_cost?.toFixed(2)}</strong></div>
                    <div><span style={{ color: 'var(--text-secondary)' }}>Live: </span><strong style={{ color: 'var(--text-primary)' }}>${alert.profit_at_risk.live_price?.toFixed(2)}</strong></div>
                    <div><span style={{ color: 'var(--text-secondary)' }}>P&L: </span>
                      <strong style={{ color: alert.profit_at_risk.unrealized_profit >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>
                        {alert.profit_at_risk.unrealized_profit >= 0 ? '+' : ''}${alert.profit_at_risk.unrealized_profit?.toFixed(2)} ({alert.profit_at_risk.unrealized_pct?.toFixed(1)}%)
                      </strong>
                    </div>
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>Risk if 5% drop: </span>
                      <strong style={{ color: 'var(--ig-red)' }}>-${alert.profit_at_risk.potential_loss_at_risk_pct?.toFixed(2)}</strong>
                    </div>
                  </div>
                </div>
              )}
              <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: 0, fontStyle: 'italic' }}>
                ⚖️ Use this as decision support, not an automatic trade signal. Always verify with your own research.
              </p>
            </div>
          ))}

          {/* Medium alerts */}
          {sentimentScan.alerts?.filter(a => a.urgency === 'medium').map((alert, i) => (
            <div key={i} style={{
              padding: 12, marginBottom: 8, borderRadius: 6,
              background: 'rgba(255, 184, 0, 0.05)', border: '1px solid rgba(255, 184, 0, 0.3)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontSize: 13, color: 'var(--ig-warning)' }}>🟡 {alert.symbol} — Monitor</strong>
                <span style={{ fontSize: 11, color: 'var(--ig-warning)' }}>Score: {alert.sentiment_score?.toFixed(2)}</span>
              </div>
              {alert.headline && <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--text-primary)' }}>{alert.headline}</p>}
            </div>
          ))}

          {/* Sentiment grid for all positions */}
          {sentimentScan.sentiment_results?.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Sentiment by Position:</p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {sentimentScan.sentiment_results.map(sr => {
                  const isPos = sr.sentiment === 'positive';
                  const isNeg = sr.sentiment === 'negative';
                  const color = isPos ? 'var(--ig-gain)' : isNeg ? 'var(--ig-red)' : 'var(--text-primary)';
                  const bg = isPos ? 'rgba(0, 210, 135, 0.1)' : isNeg ? 'rgba(255, 51, 51, 0.1)' : 'rgba(255, 255, 255, 0.05)';
                  const border = isPos ? 'rgba(0, 210, 135, 0.3)' : isNeg ? 'rgba(255, 51, 51, 0.3)' : 'var(--ig-border)';
                  
                  return (
                    <div key={sr.symbol} style={{
                      padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                      background: bg, border: `1px solid ${border}`, color: color,
                    }}>
                      {isPos ? '🟢' : isNeg ? '🔴' : '⚪'} {sr.symbol}
                      <span style={{ marginLeft: 6, fontWeight: 400, opacity: 0.8 }}>({sr.score?.toFixed(2)})</span>
                      {sr.cached && <span style={{ marginLeft: 6, fontSize: 10, opacity: 0.5, fontStyle: 'italic' }}>cached</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

import React, { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { Line } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend } from 'chart.js'
import './App.css'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend)

const API_BASE = import.meta.env.VITE_API_URL || '/api'

const EXCHANGE_LABELS = { AU: 'ASX 200', US: 'NASDAQ', IN: 'BSE/NSE' }
const MARKET_FLAGS = { AU: '🇦🇺', US: '🇺🇸', IN: '🇮🇳' }

// ─── Portfolio Tab Component ───────────────────────────────────────────────────
function PortfolioTab({ token, preferredMarket }) {
  const authH = () => ({ headers: { Authorization: `Bearer ${token}` } })

  const [portfolios, setPortfolios] = useState([])
  const [activePortfolio, setActivePortfolio] = useState(null)
  const [holdings, setHoldings] = useState([])
  const [performance, setPerformance] = useState(null)
  const [optimizeResult, setOptimizeResult] = useState(null)
  const [llmReview, setLlmReview] = useState(null)
  // Manual creation
  const [newPortfolioName, setNewPortfolioName] = useState('')
  const [addForm, setAddForm] = useState({ symbol: '', market: 'AU', qty: '', avg_buy_price: '' })
  const [loading, setLoading] = useState(false)
  const [optLoading, setOptLoading] = useState(false)
  const [llmLoading, setLlmLoading] = useState(false)
  // AI build (creates its own portfolio)
  const [aiBuildForm, setAiBuildForm] = useState({
    name: '',
    risk_profile: 'balanced',
    markets: [preferredMarket || 'AU'],
    num_stocks: 8,
    total_investment: 10000,
  })
  const [aiBuildResult, setAiBuildResult] = useState(null)
  const [aiBuildLoading, setAiBuildLoading] = useState(false)
  // AI suggest only (preview without creating)
  const [suggestForm, setSuggestForm] = useState({ risk_profile: 'balanced', markets: [preferredMarket || 'AU'], num_stocks: 8 })
  const [suggestResult, setSuggestResult] = useState(null)
  const [suggestLoading, setSuggestLoading] = useState(false)
  const [err, setErr] = useState(null)

  useEffect(() => { fetchPortfolios() }, [])

  const fetchPortfolios = async () => {
    try {
      const r = await axios.get(`${API_BASE}/portfolios`, authH())
      setPortfolios(r.data || [])
      if (r.data?.length && !activePortfolio) {
        selectPortfolio(r.data[0].id)
      }
    } catch (e) { setErr('Failed to load portfolios') }
  }

  const selectPortfolio = async (id) => {
    setActivePortfolio(id)
    setOptimizeResult(null)
    setLlmReview(null)
    try {
      const [ph, pf] = await Promise.all([
        axios.get(`${API_BASE}/portfolios/${id}/holdings`, authH()),
        axios.get(`${API_BASE}/portfolios/${id}/performance`, authH()),
      ])
      setHoldings(ph.data || [])
      setPerformance(pf.data || null)
    } catch (e) { setErr('Failed to load portfolio data') }
  }

  const createPortfolio = async () => {
    if (!newPortfolioName.trim()) return
    try {
      const resp = await axios.post(`${API_BASE}/portfolios`, { name: newPortfolioName.trim(), type: 'manual' }, authH())
      setNewPortfolioName('')
      await fetchPortfolios()
      selectPortfolio(resp.data.id)
    } catch (e) { setErr('Failed to create portfolio') }
  }

  const runAiBuild = async () => {
    if (!aiBuildForm.name.trim()) { setErr('Please enter a portfolio name first'); return }
    if (!aiBuildForm.markets.length) { setErr('Select at least one market'); return }
    setAiBuildLoading(true)
    setAiBuildResult(null)
    try {
      const r = await axios.post(`${API_BASE}/portfolios/ai-build`, aiBuildForm, authH())
      setAiBuildResult(r.data)
      await fetchPortfolios()
      selectPortfolio(r.data.portfolio_id)
    } catch (e) { setErr('AI build failed: ' + (e.response?.data?.detail || e.message)) }
    setAiBuildLoading(false)
  }

  const runAiSuggest = async () => {
    if (!activePortfolio) return
    setSuggestLoading(true)
    setSuggestResult(null)
    try {
      const r = await axios.post(`${API_BASE}/portfolios/${activePortfolio}/suggest`, suggestForm, authH())
      setSuggestResult(r.data)
    } catch (e) { setErr('AI suggest failed: ' + (e.response?.data?.detail || e.message)) }
    setSuggestLoading(false)
  }

  const addHolding = async () => {
    if (!activePortfolio || !addForm.symbol.trim() || !addForm.qty || !addForm.avg_buy_price) return
    setLoading(true)
    try {
      await axios.post(`${API_BASE}/portfolios/${activePortfolio}/holdings`, {
        symbol: addForm.symbol.trim().toUpperCase(),
        market: addForm.market,
        quantity: parseFloat(addForm.qty),
        avg_buy_price: parseFloat(addForm.avg_buy_price),
      }, authH())
      setAddForm({ symbol: '', market: 'AU', qty: '', avg_buy_price: '' })
      await selectPortfolio(activePortfolio)
    } catch (e) { setErr('Failed to add holding') }
    setLoading(false)
  }

  const removeHolding = async (hid) => {
    if (!activePortfolio) return
    try {
      await axios.delete(`${API_BASE}/portfolios/${activePortfolio}/holdings/${hid}`, authH())
      await selectPortfolio(activePortfolio)
    } catch (e) { setErr('Failed to remove holding') }
  }

  const runOptimize = async () => {
    if (!activePortfolio) return
    setOptLoading(true)
    try {
      const r = await axios.post(`${API_BASE}/portfolios/${activePortfolio}/optimize`, {}, authH())
      setOptimizeResult(r.data)
    } catch (e) { setErr('Optimization failed: ' + (e.response?.data?.detail || e.message)) }
    setOptLoading(false)
  }

  const runLlmReview = async () => {
    if (!activePortfolio) return
    setLlmLoading(true)
    try {
      const r = await axios.post(`${API_BASE}/portfolios/${activePortfolio}/llm-review`, {}, authH())
      setLlmReview(r.data)
    } catch (e) { setErr('LLM review failed: ' + (e.response?.data?.detail || e.message)) }
    setLlmLoading(false)
  }

  const pnlColor = (v) => v >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)'

  return (
    <div>
      {err && <div className="error-toast"><p>{err}</p><button onClick={() => setErr(null)}>x</button></div>}

      {/* Portfolio Selector */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="section-header"><h2>My Portfolios</h2></div>

        {/* Existing portfolios */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {portfolios.length === 0 && <p className="section-note">No portfolios yet — create one below.</p>}
          {portfolios.map(p => (
            <button key={p.id}
              className={activePortfolio === p.id ? 'add-btn' : 'btn-secondary'}
              onClick={() => selectPortfolio(p.id)}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span>{p.type === 'ai' ? '🤖' : '📋'}</span>
              <span>{p.name}</span>
              <span style={{ fontSize: 10, opacity: 0.7, background: 'rgba(255,255,255,0.2)', borderRadius: 3, padding: '1px 5px' }}>
                {p.type === 'ai' ? 'AI' : 'Manual'}
              </span>
            </button>
          ))}
        </div>

        {/* Two creation methods side by side */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          {/* Manual creation */}
          <div style={{ background: 'var(--ig-white)', border: '1px solid var(--ig-border)', borderRadius: 4, padding: 14 }}>
            <p style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, color: 'var(--ig-dark)' }}>📋 New Manual Portfolio</p>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="search-input" style={{ flex: 1 }} placeholder="Portfolio name…"
                value={newPortfolioName} onChange={e => setNewPortfolioName(e.target.value)}
                onKeyPress={e => e.key === 'Enter' && createPortfolio()} />
              <button className="add-btn" onClick={createPortfolio} disabled={!newPortfolioName.trim()}>+ Create</button>
            </div>
            <p style={{ fontSize: 11, color: 'var(--ig-muted)', marginTop: 8 }}>You add holdings manually at your own prices &amp; quantities.</p>
          </div>

          {/* AI Build creation */}
          <div style={{ background: 'var(--ig-light-grey)', border: '2px solid var(--ig-border-strong)', borderRadius: 4, padding: 14 }}>
            <p style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, color: 'var(--ig-dark)' }}>🤖 New AI Portfolio</p>
            <input className="search-input" style={{ width: '100%', marginBottom: 8, boxSizing: 'border-box' }}
              placeholder="Portfolio name (e.g. My Growth Portfolio)"
              value={aiBuildForm.name}
              onChange={e => setAiBuildForm(p => ({ ...p, name: e.target.value }))} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 4, alignItems: 'center' }}>
              <select className="provider-select" value={aiBuildForm.risk_profile}
                onChange={e => setAiBuildForm(p => ({ ...p, risk_profile: e.target.value }))}>
                <option value="conservative">😌 Conservative</option>
                <option value="balanced">⚖️ Balanced</option>
                <option value="aggressive">🚀 Aggressive</option>
              </select>
              <span style={{ fontSize: 11, color: 'var(--ig-medium)', fontStyle: 'italic' }}>
                {aiBuildForm.risk_profile === 'conservative' && 'Low risk · stable dividend stocks · min volatility'}
                {aiBuildForm.risk_profile === 'balanced' && 'Mix of growth & value · Sharpe-optimised'}
                {aiBuildForm.risk_profile === 'aggressive' && 'High growth · tech/momentum · max returns'}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
              <input className="search-input" placeholder="# Stocks" type="number" min="3" max="15"
                value={aiBuildForm.num_stocks}
                onChange={e => setAiBuildForm(p => ({ ...p, num_stocks: parseInt(e.target.value) || 8 }))}
                style={{ width: 90 }} />
              <input className="search-input" placeholder="Total invest $" type="number" min="100"
                value={aiBuildForm.total_investment}
                onChange={e => setAiBuildForm(p => ({ ...p, total_investment: parseFloat(e.target.value) || 10000 }))}
                style={{ width: 130 }} />
            </div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
              <span style={{ fontSize: 12, color: 'var(--ig-medium)' }}>Markets:</span>
              {['AU', 'US', 'IN'].map(m => (
                <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontSize: 12 }}>
                  <input type="checkbox" checked={aiBuildForm.markets.includes(m)}
                    onChange={e => setAiBuildForm(p => ({
                      ...p,
                      markets: e.target.checked ? [...p.markets, m] : p.markets.filter(x => x !== m)
                    }))} />
                  {MARKET_FLAGS[m]} {m}
                </label>
              ))}
            </div>
            <button className="add-btn" style={{ width: '100%' }} onClick={runAiBuild}
              disabled={aiBuildLoading || !aiBuildForm.name.trim() || !aiBuildForm.markets.length}>
              {aiBuildLoading ? '⏳ Building AI Portfolio…' : '🤖 Build &amp; Create Portfolio'}
            </button>
            <p style={{ fontSize: 11, color: 'var(--ig-muted)', marginTop: 8 }}>AI picks stocks, MPT optimises weights, holdings created automatically.</p>
          </div>
        </div>
      </section>

      {/* AI Build Result Banner */}
      {aiBuildResult && (
        <section className="panel" style={{ marginBottom: 16, border: '2px solid var(--ig-gain)' }}>
          <div className="section-header">
            <h2>🤖 AI Portfolio Created: <em>{aiBuildResult.portfolio_name}</em></h2>
            <button className="btn-secondary" onClick={() => setAiBuildResult(null)}>Dismiss</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 12 }}>
            <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Expected Return</span><span className="stat-value" style={{ color: 'var(--ig-gain)' }}>{(aiBuildResult.expected_annual_return * 100).toFixed(2)}%</span></div></div>
            <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Annual Volatility</span><span className="stat-value">{(aiBuildResult.annual_volatility * 100).toFixed(2)}%</span></div></div>
            <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Sharpe Ratio</span><span className="stat-value">{aiBuildResult.sharpe_ratio?.toFixed(2)}</span></div></div>
            <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Holdings</span><span className="stat-value">{aiBuildResult.holdings?.length}</span></div></div>
          </div>
          {aiBuildResult.rationale && <p className="section-note" style={{ marginBottom: 12 }}>{aiBuildResult.rationale}</p>}
          <div className="table-wrap">
            <table className="data-table">
              <thead><tr><th>Symbol</th><th>Name</th><th>Market</th><th>Weight</th><th>Allocated $</th><th>Qty</th><th>Price</th></tr></thead>
              <tbody>
                {aiBuildResult.holdings?.map(h => (
                  <tr key={h.symbol}>
                    <td><strong>{h.symbol}</strong></td>
                    <td>{h.name}</td>
                    <td>{MARKET_FLAGS[h.market]} {h.market}</td>
                    <td>
                      <div className="weight-bar-wrap">
                        <div className="weight-bar-fill" style={{ width: `${h.weight_pct}%` }} />
                        <span className="weight-pct">{h.weight_pct}%</span>
                      </div>
                    </td>
                    <td>${h.allocated?.toFixed(2)}</td>
                    <td>{h.quantity}</td>
                    <td>${h.avg_buy_price?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {activePortfolio && (
        <>
          {/* Summary Cards */}
          {performance && (
            <section className="stats-grid" style={{ marginBottom: 16 }}>
              <div className="stat-card blue">
                <div className="stat-content">
                  <span className="stat-title">Total Value</span>
                  <span className="stat-value">${performance.total_value?.toFixed(2) ?? '—'}</span>
                </div>
              </div>
              <div className="stat-card" style={{ background: pnlColor(performance.total_pnl ?? 0), border: '2px solid var(--ig-border-strong)' }}>
                <div className="stat-content">
                  <span className="stat-title">Total P&amp;L</span>
                  <span className="stat-value" style={{ color: 'white' }}>
                    {(performance.total_pnl ?? 0) >= 0 ? '+' : ''}{performance.total_pnl?.toFixed(2) ?? '—'}
                  </span>
                </div>
              </div>
              <div className="stat-card blue">
                <div className="stat-content">
                  <span className="stat-title">Return %</span>
                  <span className="stat-value" style={{ color: pnlColor(performance.total_pnl_pct ?? 0) }}>
                    {(performance.total_pnl_pct ?? 0) >= 0 ? '+' : ''}{performance.total_pnl_pct?.toFixed(2) ?? '—'}%
                  </span>
                </div>
              </div>
              <div className="stat-card blue">
                <div className="stat-content">
                  <span className="stat-title">Holdings</span>
                  <span className="stat-value">{holdings.length}</span>
                </div>
              </div>
            </section>
          )}

          {/* Holdings Table */}
          <section className="panel" style={{ marginBottom: 16 }}>
            <div className="section-header"><h2>Holdings</h2></div>
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr>
                  <th>Symbol</th><th>Market</th><th>Qty</th>
                  <th>Avg Buy</th><th>Current</th><th>Value</th>
                  <th>P&amp;L $</th><th>P&amp;L %</th><th></th>
                </tr></thead>
                <tbody>
                  {holdings.map(h => (
                    <tr key={h.id}>
                      <td><strong>{h.symbol}</strong></td>
                      <td>{h.market}</td>
                      <td>{h.quantity}</td>
                      <td>${h.avg_buy_price?.toFixed(2)}</td>
                      <td>${h.current_price?.toFixed(2) ?? '—'}</td>
                      <td>${h.current_value?.toFixed(2) ?? '—'}</td>
                      <td style={{ color: pnlColor(h.pnl ?? 0) }}>{(h.pnl ?? 0) >= 0 ? '+' : ''}{h.pnl?.toFixed(2) ?? '—'}</td>
                      <td style={{ color: pnlColor(h.pnl_pct ?? 0) }}>{(h.pnl_pct ?? 0) >= 0 ? '+' : ''}{h.pnl_pct?.toFixed(2) ?? '—'}%</td>
                      <td><button className="btn-danger" onClick={() => removeHolding(h.id)}>✕</button></td>
                    </tr>
                  ))}
                  {holdings.length === 0 && <tr><td colSpan={9} style={{ textAlign: 'center', color: 'var(--ig-muted)' }}>No holdings yet</td></tr>}
                </tbody>
              </table>
            </div>

            {/* Add Holding Form */}
            <div className="portfolio-add-form">
              <input className="search-input" placeholder="Symbol (e.g. BHP, AAPL, RELIANCE)"
                value={addForm.symbol} onChange={e => setAddForm(p => ({ ...p, symbol: e.target.value }))} />
              <select className="provider-select" value={addForm.market}
                onChange={e => setAddForm(p => ({ ...p, market: e.target.value }))}>
                <option value="AU">AU (ASX 200)</option>
                <option value="US">US (NASDAQ)</option>
                <option value="IN">IN (BSE/NSE)</option>
              </select>
              <input className="search-input" placeholder="Quantity" type="number" min="0"
                value={addForm.qty} onChange={e => setAddForm(p => ({ ...p, qty: e.target.value }))} />
              <input className="search-input" placeholder="Avg Buy Price" type="number" min="0" step="0.01"
                value={addForm.avg_buy_price} onChange={e => setAddForm(p => ({ ...p, avg_buy_price: e.target.value }))} />
              <button className="add-btn" onClick={addHolding} disabled={loading}>
                {loading ? 'Adding...' : '+ Add Holding'}
              </button>
            </div>
          </section>

          {/* Optimise */}
          <section className="panel" style={{ marginBottom: 16 }}>
            <div className="section-header">
              <h2>Portfolio Optimisation (MPT)</h2>
              <button className="add-btn" onClick={runOptimize} disabled={optLoading || holdings.length < 2}>
                {optLoading ? 'Optimising...' : '⚡ Optimise Weights'}
              </button>
            </div>
            {holdings.length < 2 && <p className="section-note">Add at least 2 holdings to run optimisation.</p>}
            {optimizeResult && (
              <div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Expected Return</span><span className="stat-value" style={{ color: 'var(--ig-gain)' }}>{(optimizeResult.expected_annual_return * 100).toFixed(2)}%</span></div></div>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Annual Volatility</span><span className="stat-value">{(optimizeResult.annual_volatility * 100).toFixed(2)}%</span></div></div>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Sharpe Ratio</span><span className="stat-value">{optimizeResult.sharpe_ratio?.toFixed(2)}</span></div></div>
                </div>
                <p className="section-note">Optimal weights (Max Sharpe):</p>
                {Object.entries(optimizeResult.weights || {}).map(([sym, w]) => (
                  <div key={sym} className="weight-bar-wrap">
                    <span style={{ minWidth: 80 }}>{sym}</span>
                    <div className="weight-bar-fill" style={{ width: `${(w * 100).toFixed(1)}%` }} />
                    <span className="weight-pct">{(w * 100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* AI Build Portfolio */}
          <section className="panel" style={{ marginBottom: 16 }}>
            <div className="section-header">
              <h2>✨ AI Suggest for This Portfolio</h2>
              <button className="add-btn" onClick={runAiSuggest} disabled={suggestLoading || !activePortfolio}>
                {suggestLoading ? 'Analysing...' : '🔍 Preview Suggestions'}
              </button>
            </div>
            <p className="section-note">Preview AI-suggested weights for this portfolio without modifying it. Use the AI Portfolio creator above to auto-populate a new portfolio.</p>
            <div className="portfolio-add-form" style={{ flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <select className="provider-select" value={suggestForm.risk_profile}
                  onChange={e => setSuggestForm(p => ({ ...p, risk_profile: e.target.value }))}>
                  <option value="conservative">😌 Conservative</option>
                  <option value="balanced">⚖️ Balanced</option>
                  <option value="aggressive">🚀 Aggressive</option>
                </select>
                <span style={{ fontSize: 11, color: 'var(--ig-medium)', fontStyle: 'italic' }}>
                  {suggestForm.risk_profile === 'conservative' && 'Low risk · stable dividend stocks · min volatility'}
                  {suggestForm.risk_profile === 'balanced' && 'Mix of growth & value · Sharpe-optimised'}
                  {suggestForm.risk_profile === 'aggressive' && 'High growth · tech/momentum · max returns'}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: 'var(--ig-medium)' }}>Markets:</span>
                {['AU', 'US', 'IN'].map(m => (
                  <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontSize: 13 }}>
                    <input type="checkbox" checked={suggestForm.markets.includes(m)}
                      onChange={e => setSuggestForm(p => ({
                        ...p,
                        markets: e.target.checked ? [...p.markets, m] : p.markets.filter(x => x !== m)
                      }))} />
                    {MARKET_FLAGS[m]} {EXCHANGE_LABELS[m]}
                  </label>
                ))}
              </div>
              <input className="search-input" placeholder="# Stocks" type="number" min="3" max="15"
                value={suggestForm.num_stocks}
                onChange={e => setSuggestForm(p => ({ ...p, num_stocks: parseInt(e.target.value) || 8 }))}
                style={{ width: 100 }} />
            </div>
            {suggestResult && (
              <div style={{ marginTop: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 12 }}>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Expected Return</span><span className="stat-value" style={{ color: 'var(--ig-gain)' }}>{(suggestResult.expected_annual_return * 100).toFixed(2)}%</span></div></div>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Annual Volatility</span><span className="stat-value">{(suggestResult.annual_volatility * 100).toFixed(2)}%</span></div></div>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Sharpe Ratio</span><span className="stat-value">{suggestResult.sharpe_ratio?.toFixed(2)}</span></div></div>
                </div>
                {suggestResult.rationale && <p className="section-note" style={{ marginBottom: 12 }}>{suggestResult.rationale}</p>}
                <div className="table-wrap" style={{ marginBottom: 8 }}>
                  <table className="data-table">
                    <thead><tr><th>Symbol</th><th>Name</th><th>Market</th><th>Optimal Weight</th></tr></thead>
                    <tbody>
                      {suggestResult.suggested_holdings.map(h => (
                        <tr key={h.symbol}>
                          <td><strong>{h.symbol}</strong></td>
                          <td>{h.name}</td>
                          <td>{MARKET_FLAGS[h.market]} {h.market}</td>
                          <td>
                            <div className="weight-bar-wrap">
                              <div className="weight-bar-fill" style={{ width: `${h.suggested_quantity_pct}%` }} />
                              <span className="weight-pct">{h.suggested_quantity_pct}%</span>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>

          {/* LLM Advisor */}
          <section className="panel" style={{ marginBottom: 16 }}>
            <div className="section-header">
              <h2>AI Portfolio Advisor</h2>
              <button className="add-btn" onClick={runLlmReview} disabled={llmLoading || holdings.length === 0}>
                {llmLoading ? 'Reviewing...' : '🤖 Get AI Review'}
              </button>
            </div>
            {llmReview && (
              <div className="advisor-card">
                <div className="advisor-header">
                  <span>Risk Grade: <strong>{llmReview.risk_grade}</strong></span>
                  <span>Diversification: <strong>{llmReview.diversification_score}/10</strong></span>
                </div>
                <div className="advisor-body">
                  {llmReview.summary && <p style={{ marginBottom: 12 }}>{llmReview.summary}</p>}
                  {llmReview.rebalancing_actions?.length > 0 && (
                    <div className="advisor-section">
                      <h4>Rebalancing Actions</h4>
                      <ul>
                        {llmReview.rebalancing_actions.map((a, i) => <li key={i}>{a}</li>)}
                      </ul>
                    </div>
                  )}
                  {llmReview.scenarios && (
                    <div className="advisor-section">
                      <h4>Scenario Analysis</h4>
                      <div className="scenario-grid">
                        {['bull', 'bear', 'crash'].map(s => llmReview.scenarios[s] && (
                          <div key={s} className={`scenario-card scenario-${s}`}>
                            <strong>{s.toUpperCase()}</strong>
                            <p>{llmReview.scenarios[s]}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
            {!llmReview && <p className="section-note">Click "Get AI Review" for risk assessment, diversification score, rebalancing suggestions, and 3-scenario stress test (bull / bear / crash).</p>}
          </section>
        </>
      )}
    </div>
  )
}

// ─── Main App ─────────────────────────────────────────────────────────────────
function App() {
  const [token, setToken] = useState(() => localStorage.getItem('asx_token') || '')
  const [user, setUser] = useState(null)
  const [authMode, setAuthMode] = useState('login')
  const [authLoading, setAuthLoading] = useState(false)
  const [authForm, setAuthForm] = useState({ full_name: '', email: '', password: '' })

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('tracker')

  const [topUniverse, setTopUniverse] = useState([])
  const [aiQuery, setAiQuery] = useState('Find ASX shares with strong momentum and low volatility')
  const [aiProvider, setAiProvider] = useState('auto')
  const [aiProviderUsed, setAiProviderUsed] = useState('')
  const [aiSuggestions, setAiSuggestions] = useState([])
  const [rankedItems, setRankedItems] = useState([])
  const [selectedSymbols, setSelectedSymbols] = useState({})
  const [trackingOverview, setTrackingOverview] = useState([])
  const [bucketCounts, setBucketCounts] = useState({ meeting_expectation: 0, non_meeting_expectation: 0 })
  const [metrics, setMetrics] = useState({ validation_hit_rate_pct: 0, average_forecast_error_pct: 0 })
  const [marketPulse, setMarketPulse] = useState(null)
  const [explainability, setExplainability] = useState(null)
  const [marketAnalysis, setMarketAnalysis] = useState(null)
  const [marketLoading, setMarketLoading] = useState(false)
  const [sentimentData, setSentimentData] = useState(null)
  const [sentimentSymbol, setSentimentSymbol] = useState('')
  const [sentimentLoading, setSentimentLoading] = useState(false)

  const [analyzeSymbol, setAnalyzeSymbol] = useState('')
  const [analyzeData, setAnalyzeData] = useState(null)
  const [analyzeLoading, setAnalyzeLoading] = useState(false)
  const [userNotes, setUserNotes] = useState('')
  const [notesLoading, setNotesLoading] = useState(false)
  const [preferredMarket, setPreferredMarket] = useState('AU')

  const [aiLoading, setAiLoading] = useState(false)
  const [rankLoading, setRankLoading] = useState(false)
  const [trackLoading, setTrackLoading] = useState(false)

  const authHeaders = () => ({ headers: { Authorization: `Bearer ${token}` } })
  const selectedCount = useMemo(() => Object.values(selectedSymbols).filter(Boolean).length, [selectedSymbols])

  useEffect(() => {
    if (!token) { setLoading(false); return }
    bootstrap()
  }, [token])

  const bootstrap = async () => {
    try {
      setLoading(true)
      try {
        const me = await axios.get(`${API_BASE}/auth/me`, authHeaders())
        setUser(me.data)
        setPreferredMarket(me.data.preferred_market || 'AU')
      } catch (authErr) {
        throw new Error(`Authentication failed (${authErr.response?.status}): ${authErr.response?.data?.detail || authErr.message}`)
      }
      try {
        await Promise.all([fetchTopUniverse(), fetchTrackingOverview(), fetchMarketPulse(), fetchExplainability()])
      } catch (dataErr) {
        setError(`Data load incomplete: ${dataErr.message}`)
      }
    } catch (err) {
      localStorage.removeItem('asx_token')
      setToken('')
      setUser(null)
      setError(err.message || 'Session expired. Please login again.')
    } finally {
      setLoading(false)
    }
  }

  const fetchTopUniverse = async () => {
    const r = await axios.get(`${API_BASE}/universe/top`, authHeaders())
    const items = r.data?.items || []
    setTopUniverse(items)
    if (items.length > 0) await rankSymbols(items.map(i => i.symbol))
  }
  const fetchTrackingOverview = async () => {
    const r = await axios.get(`${API_BASE}/tracking/overview`, authHeaders())
    setTrackingOverview(r.data?.rows || [])
    setBucketCounts(r.data?.buckets || { meeting_expectation: 0, non_meeting_expectation: 0 })
    setMetrics(r.data?.metrics || { validation_hit_rate_pct: 0, average_forecast_error_pct: 0 })
  }
  const fetchMarketPulse = async () => {
    const r = await axios.get(`${API_BASE}/v1/market/pulse`, authHeaders())
    setMarketPulse(r.data)
  }
  const fetchExplainability = async () => {
    const r = await axios.get(`${API_BASE}/v1/backtest/summary`, authHeaders())
    setExplainability(r.data)
  }

  const refreshMarketAnalysis = async () => {
    if (!marketPulse) return
    setMarketLoading(true)
    try {
      const r = await axios.post(`${API_BASE}/ai/market-analysis`, { regime: marketPulse.regime, metrics: marketPulse.metrics }, authHeaders())
      setMarketAnalysis(r.data.analysis)
    } catch { setError('Failed to get market analysis') }
    setMarketLoading(false)
  }

  const fetchSentiment = async () => {
    if (!sentimentSymbol.trim()) return
    setSentimentLoading(true)
    try {
      const r = await axios.post(`${API_BASE}/ai/sentiment`, { symbol: sentimentSymbol.trim().toUpperCase() }, authHeaders())
      setSentimentData(r.data)
    } catch { setError('Failed to get sentiment analysis') }
    setSentimentLoading(false)
  }

  const analyzeShare = async () => {
    if (!analyzeSymbol.trim()) return
    setAnalyzeLoading(true)
    setAnalyzeData(null)
    setUserNotes('')
    try {
      const r = await axios.get(`${API_BASE}/ai/analyze/${analyzeSymbol.trim().toUpperCase()}`, authHeaders())
      setAnalyzeData(r.data)
      loadNotes(analyzeSymbol.trim().toUpperCase())
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to analyze share')
    }
    setAnalyzeLoading(false)
  }

  const loadNotes = async (symbol) => {
    setNotesLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/notes/${symbol}`, authHeaders())
      setUserNotes(String(r.data?.notes ?? ''))
    } catch { setUserNotes('') }
    setNotesLoading(false)
  }

  const saveNotes = async () => {
    if (!analyzeData?.symbol || !String(userNotes).trim()) return
    try {
      await axios.post(`${API_BASE}/notes/${analyzeData.symbol}`, { note: String(userNotes).trim() }, authHeaders())
      setError(null)
      alert('Note saved!')
    } catch { setError('Failed to save note') }
  }

  const rankSymbols = async (symbols) => {
    if (!symbols?.length) { setRankedItems([]); return }
    try {
      setRankLoading(true)
      const r = await axios.post(`${API_BASE}/screener/rank`, { symbols }, authHeaders())
      const items = r.data?.items || []
      setRankedItems(items)
      const defaults = {}
      items.slice(0, 3).forEach(i => { defaults[i.symbol] = true })
      setSelectedSymbols(prev => ({ ...defaults, ...prev }))
    } catch { setError('Failed to rank symbols.') } finally { setRankLoading(false) }
  }

  const requestAiSuggestions = async () => {
    try {
      setAiLoading(true)
      const r = await axios.post(`${API_BASE}/ai/suggest-shares`, { query: aiQuery, max_symbols: 10, provider: aiProvider }, authHeaders())
      const items = r.data?.items || []
      setAiProviderUsed(r.data?.provider_used || '')
      setAiSuggestions(items)
      await rankSymbols(items.map(i => i.symbol))
    } catch (err) { setError(err.response?.data?.detail || 'AI could not return suggestions.') } finally { setAiLoading(false) }
  }

  const addSingleShare = async (symbol, source = 'manual_add') => {
    try {
      await axios.post(`${API_BASE}/shares`, null, { ...authHeaders(), params: { symbol, source } })
      await fetchTrackingOverview()
    } catch (err) {
      const message = err.response?.data?.detail || ''
      if (!message.toLowerCase().includes('already tracked')) throw err
    }
  }

  const trackSelected = async () => {
    const picks = Object.entries(selectedSymbols).filter(e => e[1]).map(e => e[0])
    if (!picks.length) { setError('Select at least one ranked share to track.'); return }
    try {
      setTrackLoading(true)
      for (const symbol of picks) await addSingleShare(symbol, 'promoted_from_screener')
      await fetchTrackingOverview()
      await fetchExplainability()
      setError(null)
    } catch (err) { setError(err.response?.data?.detail || 'Failed to track selected shares.') } finally { setTrackLoading(false) }
  }

  const handleAuthSubmit = async (e) => {
    e.preventDefault()
    setAuthLoading(true)
    setError(null)
    try {
      const endpoint = authMode === 'register' ? 'register' : 'login'
      const payload = authMode === 'register' ? authForm : { email: authForm.email, password: authForm.password }
      const r = await axios.post(`${API_BASE}/auth/${endpoint}`, payload)
      const nextToken = r.data.access_token
      localStorage.setItem('asx_token', nextToken)
      setToken(nextToken)
      setUser(r.data.user)
      setAuthForm({ full_name: '', email: '', password: '' })
    } catch (err) { setError(err.response?.data?.detail || 'Authentication failed') } finally { setAuthLoading(false) }
  }

  const updateMarket = async (market) => {
    setPreferredMarket(market)
    try {
      await axios.put(`${API_BASE}/auth/me/market`, { market }, authHeaders())
    } catch { /* non-critical */ }
  }

  const logout = () => {
    localStorage.removeItem('asx_token')
    setToken(''); setUser(null)
    setTopUniverse([]); setAiSuggestions([]); setRankedItems([]); setTrackingOverview([])
  }

  // IG-compatible inline style helpers
  const cardStyle = { background: 'var(--ig-light-grey)', border: '2px solid var(--ig-border-strong)', borderRadius: 4, padding: 16, marginBottom: 12 }
  const subPanelStyle = { background: 'var(--ig-white)', border: '1px solid var(--ig-border)', borderRadius: 4, padding: 14, marginTop: 12 }
  const gainStyle = (v) => ({ color: (v ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' })
  const pillStyle = (sentiment) => ({
    padding: '4px 14px', borderRadius: 20, fontWeight: 'bold', color: 'white',
    background: sentiment === 'bullish' ? 'var(--ig-gain)' : sentiment === 'bearish' ? 'var(--ig-red)' : '#d4ac0d'
  })
  const btnGreen = { background: 'var(--ig-gain)', color: 'white', border: 'none', padding: '8px 16px', borderRadius: 4, cursor: 'pointer', fontWeight: 'bold' }
  const inputStyle = { flex: 1, padding: '10px 12px', borderRadius: 4, border: '2px solid var(--ig-border-strong)', background: 'var(--ig-white)', color: 'var(--ig-dark)', fontSize: 14 }

  if (!token) {
    return (
      <div className="auth-shell">
        <div className="auth-panel">
          <div className="auth-brand">
            <p className="auth-eyebrow">ASX Intelligence Terminal</p>
            <h1>Sign in to your workspace</h1>
            <p>Each account has isolated watchlists, predictions, and analytics.</p>
          </div>
          <form className="auth-form" onSubmit={handleAuthSubmit}>
            {authMode === 'register' && (
              <input type="text" placeholder="Full name" value={authForm.full_name}
                onChange={(e) => setAuthForm(p => ({ ...p, full_name: e.target.value }))} className="search-input" />
            )}
            <input type="email" placeholder="Email" value={authForm.email}
              onChange={(e) => setAuthForm(p => ({ ...p, email: e.target.value }))} className="search-input" required />
            <input type="password" placeholder="Password" value={authForm.password}
              onChange={(e) => setAuthForm(p => ({ ...p, password: e.target.value }))} className="search-input" required />
            <button type="submit" className="add-btn" disabled={authLoading}>
              {authLoading ? 'Please wait...' : authMode === 'register' ? 'Create account' : 'Sign in'}
            </button>
          </form>
          <button type="button" className="auth-switch" onClick={() => setAuthMode(authMode === 'register' ? 'login' : 'register')}>
            {authMode === 'register' ? 'Already have an account? Sign in' : 'Need an account? Register'}
          </button>
          {error && <p className="auth-error">{error}</p>}
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-content">
          <div className="logo">
            <span className="logo-icon">📈</span>
            <h1>{EXCHANGE_LABELS[preferredMarket]} Intelligence Dashboard</h1>
          </div>
          <nav className="nav">
            <button className={`nav-btn ${activeTab === 'tracker' ? 'active' : ''}`} onClick={() => setActiveTab('tracker')}>Tracker</button>
            <button className={`nav-btn ${activeTab === 'market' ? 'active' : ''}`} onClick={() => setActiveTab('market')}>Market</button>
            <button className={`nav-btn ${activeTab === 'explain' ? 'active' : ''}`} onClick={() => setActiveTab('explain')}>Analytics</button>
            <button className={`nav-btn ${activeTab === 'analyze' ? 'active' : ''}`} onClick={() => setActiveTab('analyze')}>Analyze</button>
            <button className={`nav-btn ${activeTab === 'portfolio' ? 'active' : ''}`} onClick={() => setActiveTab('portfolio')}>Portfolio</button>
            <div style={{ display: 'flex', gap: 4 }}>
              {['AU', 'US', 'IN'].map(m => (
                <button key={m}
                  className={`nav-btn ${preferredMarket === m ? 'active' : ''}`}
                  onClick={() => updateMarket(m)}
                  title={EXCHANGE_LABELS[m]}>
                  {MARKET_FLAGS[m]} {EXCHANGE_LABELS[m]}
                </button>
              ))}
            </div>
            <span className="nav-user">{user?.email}</span>
            <button className="nav-btn" onClick={logout}>Logout</button>
          </nav>
        </div>
      </header>

      <main className="main">
        {activeTab === 'tracker' && (
          <>
            <section className="stats-grid">
              <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Validation Hit Rate</span><span className="stat-value">{metrics.validation_hit_rate_pct.toFixed(2)}%</span></div></div>
              <div className="stat-card red"><div className="stat-content"><span className="stat-title">Average Forecast Error</span><span className="stat-value">{metrics.average_forecast_error_pct.toFixed(2)}%</span></div></div>
              <div className="stat-card green"><div className="stat-content"><span className="stat-title">Meeting Expectation</span><span className="stat-value">{bucketCounts.meeting_expectation}</span></div></div>
              <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Non-Meeting Expectation</span><span className="stat-value">{bucketCounts.non_meeting_expectation}</span></div></div>
            </section>

            <section className="tab-grid">
              <div className="panel">
                <div className="section-header"><h2>Top ASX Set (Default)</h2></div>
                <p className="section-note">Preloaded top ASX names from ASX200 context. Click Add to track directly.</p>
                <div className="chips-grid">
                  {topUniverse.map(item => (
                    <div key={item.symbol} className="chip-card">
                      <div>
                        <strong>{item.symbol}</strong>
                        <p>{item.name}</p>
                        <small className={item.change_percent >= 0 ? 'positive' : 'negative'}>{item.change_percent >= 0 ? '+' : ''}{item.change_percent.toFixed(2)}%</small>
                      </div>
                      <button className="add-btn small" onClick={() => addSingleShare(item.symbol, 'top_asx_default')}>Add</button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="panel">
                <div className="section-header"><h2>AI Share Finder</h2></div>
                <p className="section-note">Ask for share context only, then rank predicted 3-month opportunities.</p>
                <textarea className="chat-input" value={aiQuery} onChange={(e) => setAiQuery(e.target.value)} rows={4} placeholder="Example: give me ASX mining and bank shares likely to gain in next 3 months" />
                <div className="provider-row">
                  <label htmlFor="providerSelect">Provider</label>
                  <select id="providerSelect" className="provider-select" value={aiProvider} onChange={(e) => setAiProvider(e.target.value)}>
                    <option value="auto">Auto (Local then OpenAI)</option>
                    <option value="local">Local (LM Studio)</option>
                    <option value="openai">OpenAI</option>
                  </select>
                </div>
                <div className="chat-actions">
                  <button className="add-btn" onClick={requestAiSuggestions} disabled={aiLoading}>{aiLoading ? 'Thinking...' : 'Get AI Share List'}</button>
                </div>
                {aiProviderUsed && <p className="section-note">Provider used: {aiProviderUsed}</p>}
                {aiSuggestions.length > 0 && (
                  <>
                    <p className="section-note">AI shortlist (same layout as default):</p>
                    <div className="chips-grid">
                      {aiSuggestions.map(item => (
                        <div key={item.symbol} className="chip-card">
                          <div><strong>{item.symbol}</strong><p>{item.name}</p></div>
                          <button className="add-btn small" onClick={() => addSingleShare(item.symbol, 'ai_shortlist')}>Add</button>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </section>

            <section className="panel">
              <div className="section-header">
                <h2>Ranked Candidates (3-Month Probability)</h2>
                <button className="add-btn" onClick={trackSelected} disabled={trackLoading || selectedCount === 0}>
                  {trackLoading ? 'Adding...' : `Keep Selected (${selectedCount})`}
                </button>
              </div>
              <p className="section-note">Sorted by composite score using probability of return &ge;5%, trend, quality, regime fit, and liquidity.</p>
              {rankLoading ? <p>Ranking symbols...</p> : (
                <div className="table-wrap"><table className="data-table"><thead><tr><th>Keep</th><th>Symbol</th><th>P(&ge;5%)</th><th>Expected 3M</th><th>Trend</th><th>Score</th></tr></thead><tbody>
                  {rankedItems.map(item => (
                    <tr key={item.symbol}>
                      <td><input type="checkbox" checked={!!selectedSymbols[item.symbol]} onChange={e => setSelectedSymbols(prev => ({ ...prev, [item.symbol]: e.target.checked }))} /></td>
                      <td><strong>{item.symbol}</strong></td>
                      <td>{item.prob_ge_5pct.toFixed(2)}%</td>
                      <td className={item.expected_return_3m_pct >= 0 ? 'positive' : 'negative'}>{item.expected_return_3m_pct.toFixed(2)}%</td>
                      <td>{item.trend}</td>
                      <td>{item.score.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody></table></div>
              )}
            </section>

            <section className="panel">
              <div className="section-header"><h2>14-Day Tracking Bucket Table</h2></div>
              <p className="section-note">Auto-evaluated into meeting vs non-meeting expectation at D+14 with 1.5% tolerance.</p>
              {loading ? <p>Loading...</p> : (
                <div className="table-wrap"><table className="data-table"><thead><tr><th>Symbol</th><th>Start</th><th>Day</th><th>Entry</th><th>Target 14D</th><th>Current</th><th>Progress</th><th>Expected</th><th>Actual</th><th>Status</th><th>Bucket</th></tr></thead><tbody>
                  {trackingOverview.map(row => (
                    <tr key={row.id}><td><strong>{row.symbol}</strong></td><td>{new Date(row.start_date).toLocaleDateString()}</td><td>D+{row.current_day}</td><td>${row.entry_price.toFixed(2)}</td><td>${row.target_price_14d.toFixed(2)}</td><td>${row.current_price.toFixed(2)}</td><td>{row.progress_to_target.toFixed(1)}%</td><td>{row.expected_direction}</td><td>{row.actual_direction}</td><td>{row.status}</td><td>{row.bucket || '-'}</td></tr>
                  ))}
                </tbody></table></div>
              )}
            </section>
          </>
        )}

        {activeTab === 'market' && marketPulse && (
          <>
            <section className="stats-grid">
              <div className="stat-card blue"><div className="stat-content"><span className="stat-title">ASX200 Daily</span><span className="stat-value">{marketPulse.metrics.asx200_ret.toFixed(2)}%</span></div></div>
              <div className="stat-card blue"><div className="stat-content"><span className="stat-title">S&amp;P 500 Daily</span><span className="stat-value">{marketPulse.metrics.sp500_ret.toFixed(2)}%</span></div></div>
              <div className="stat-card green"><div className="stat-content"><span className="stat-title">Gold Daily</span><span className="stat-value">{marketPulse.metrics.gold_ret.toFixed(2)}%</span></div></div>
              <div className="stat-card red"><div className="stat-content"><span className="stat-title">DXY Daily</span><span className="stat-value">{marketPulse.metrics.dxy_ret.toFixed(2)}%</span></div></div>
            </section>
            <section className="panel">
              <div className="section-header"><h2>Regime Engine</h2></div>
              <p className="section-note">Regime: <strong>{marketPulse.regime.name}</strong> ({marketPulse.regime.confidence.toFixed(1)}% confidence)</p>
              <p className="regime-summary">{marketPulse.summary}</p>
              <div className="ai-suggestions">{marketPulse.regime.tags.map(tag => <span key={tag} className="suggest-pill">{tag}</span>)}</div>
            </section>
            <section className="panel">
              <div className="section-header">
                <h2>Market Analysis</h2>
                <button style={btnGreen} onClick={refreshMarketAnalysis} disabled={marketLoading}>
                  {marketLoading ? 'Analyzing...' : '🔄 Refresh Analysis'}
                </button>
              </div>
              {marketAnalysis
                ? <div className="ai-analysis"><p>{marketAnalysis}</p></div>
                : <p className="section-note">Click refresh to get AI-powered market analysis</p>}
            </section>
          </>
        )}

        {activeTab === 'explain' && (
          <>
            <section className="panel">
              <div className="section-header"><h2>Sentiment Analysis</h2></div>
              <div style={{ display: 'flex', gap: 10, marginBottom: 15 }}>
                <input style={inputStyle} type="text" placeholder="Enter symbol (e.g., BHP)"
                  value={sentimentSymbol} onChange={e => setSentimentSymbol(e.target.value)}
                  onKeyPress={e => e.key === 'Enter' && fetchSentiment()} />
                <button onClick={fetchSentiment} disabled={sentimentLoading || !sentimentSymbol.trim()} style={{ ...btnGreen, background: sentimentLoading ? 'var(--ig-muted)' : 'var(--ig-gain)', cursor: sentimentLoading ? 'not-allowed' : 'pointer' }}>
                  {sentimentLoading ? 'Analyzing...' : 'Analyze'}
                </button>
              </div>
              {sentimentData && (
                <div style={cardStyle}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 15, marginBottom: 15 }}>
                    <span style={{ fontSize: 24, fontWeight: 'bold' }}>{sentimentData.symbol}</span>
                    <span style={pillStyle(sentimentData.sentiment)}>{sentimentData.sentiment.toUpperCase()}</span>
                    <span style={{ color: 'var(--ig-muted)' }}>Score: {sentimentData.score?.toFixed(2)}</span>
                  </div>
                  <p style={{ marginBottom: 10, lineHeight: 1.6 }}>{sentimentData.summary}</p>
                  {sentimentData.themes?.length > 0 && (
                    <div style={{ marginTop: 10 }}>
                      <strong style={{ color: 'var(--ig-medium)' }}>Key Themes:</strong>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 5 }}>
                        {sentimentData.themes.map((theme, i) => (
                          <span key={i} style={{ background: 'var(--ig-light-grey)', border: '1px solid var(--ig-border)', padding: '3px 10px', borderRadius: 15, fontSize: 12 }}>{theme}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {sentimentData.news?.length > 0 && (
                    <div style={{ marginTop: 15, borderTop: '1px solid var(--ig-border)', paddingTop: 10 }}>
                      <strong style={{ color: 'var(--ig-medium)' }}>Latest News:</strong>
                      <ul style={{ marginTop: 5, paddingLeft: 20, color: 'var(--ig-medium)', fontSize: 14 }}>
                        {sentimentData.news.map((item, i) => <li key={i} style={{ marginBottom: 5 }}>{item.title}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </section>

            <section className="stats-grid">
              <div className="stat-card blue"><div className="stat-content"><span className="stat-title">MAE</span><span className="stat-value">{explainability?.metrics?.mae_pct?.toFixed(2) || 0}%</span></div></div>
              <div className="stat-card blue"><div className="stat-content"><span className="stat-title">RMSE</span><span className="stat-value">{explainability?.metrics?.rmse_pct?.toFixed(2) || 0}%</span></div></div>
              <div className="stat-card red"><div className="stat-content"><span className="stat-title">MAPE</span><span className="stat-value">{explainability?.metrics?.mape_pct?.toFixed(2) || 0}%</span></div></div>
              <div className="stat-card green"><div className="stat-content"><span className="stat-title">Directional Accuracy</span><span className="stat-value">{explainability?.metrics?.directional_accuracy_pct?.toFixed(2) || 0}%</span></div></div>
            </section>

            <section className="panel">
              <div className="section-header"><h2>Calibration</h2></div>
              <div className="table-wrap"><table className="data-table"><thead><tr><th>Bin</th><th>Predicted Avg</th><th>Realized Positive</th><th>Count</th></tr></thead><tbody>
                {explainability.calibration.map(row => (
                  <tr key={row.bin}><td>{row.bin}</td><td>{row.predicted_avg.toFixed(1)}%</td><td>{row.realized_positive_pct.toFixed(1)}%</td><td>{row.count}</td></tr>
                ))}
              </tbody></table></div>
            </section>

            <section className="panel">
              <div className="section-header"><h2>Recent Evaluations</h2></div>
              <div className="table-wrap"><table className="data-table"><thead><tr><th>Symbol</th><th>Start</th><th>Predicted 14D</th><th>Actual 14D</th><th>Bucket</th></tr></thead><tbody>
                {explainability.recent.map(row => (
                  <tr key={`${row.symbol}-${row.start_date}`}><td>{row.symbol}</td><td>{new Date(row.start_date).toLocaleDateString()}</td><td>{row.predicted_return_14d_pct.toFixed(2)}%</td><td>{row.actual_return_14d_pct.toFixed(2)}%</td><td>{row.bucket}</td></tr>
                ))}
              </tbody></table></div>
            </section>
          </>
        )}

        {activeTab === 'analyze' && (
          <>
            <section className="panel">
              <div className="section-header"><h2>Individual Share Analysis</h2></div>
              <div style={{ display: 'flex', gap: 10, marginBottom: 20 }}>
                <input style={{ ...inputStyle, fontSize: 16 }} type="text" placeholder="Enter symbol (e.g., BHP, CBA, CSL, AAPL, RELIANCE)"
                  value={analyzeSymbol} onChange={e => setAnalyzeSymbol(e.target.value.toUpperCase())}
                  onKeyPress={e => e.key === 'Enter' && analyzeShare()} />
                <button onClick={analyzeShare} disabled={analyzeLoading || !String(analyzeSymbol).trim()}
                  style={{ ...btnGreen, background: analyzeLoading ? 'var(--ig-muted)' : 'var(--ig-gain)', cursor: analyzeLoading ? 'not-allowed' : 'pointer', padding: '12px 24px', fontSize: 16 }}>
                  {analyzeLoading ? 'Analyzing...' : '🔍 Analyze'}
                </button>
              </div>
              {error && !analyzeLoading && <div style={{ background: 'var(--ig-red)', color: 'white', padding: 10, borderRadius: 4, marginBottom: 20 }}>{error}</div>}

              {analyzeData && (
                <>
                  <div style={cardStyle}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 15 }}>
                      <div>
                        <span style={{ fontSize: 32, fontWeight: 'bold' }}>{analyzeData.symbol}</span>
                        <span style={{ fontSize: 16, color: 'var(--ig-medium)', marginLeft: 15 }}>{analyzeData.name}</span>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <span style={{ fontSize: 28, fontWeight: 'bold' }}>${analyzeData.current_price?.toFixed(2)}</span>
                        <span style={{ marginLeft: 10, padding: '5px 10px', borderRadius: 4, background: analyzeData.change_percent >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)', color: 'white', fontWeight: 'bold' }}>
                          {analyzeData.change_percent >= 0 ? '+' : ''}{analyzeData.change_percent}%
                        </span>
                      </div>
                    </div>

                    {analyzeData.prediction && (
                      <div style={subPanelStyle}>
                        <h4 style={{ margin: '0 0 10px 0', color: 'var(--ig-red)' }}>📊 Prediction (3-Month Forecast)</h4>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Predicted Price:</span><br/><strong>${analyzeData.prediction.predicted_price?.toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Expected Return:</span><br/><strong style={gainStyle(analyzeData.prediction.change_from_current)}>{analyzeData.prediction.change_from_current?.toFixed(2)}%</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Range:</span><br/><strong>${analyzeData.prediction.confidence_low?.toFixed(2)} – ${analyzeData.prediction.confidence_high?.toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Trend:</span><br/><strong style={{ color: analyzeData.prediction.trend === 'bullish' ? 'var(--ig-gain)' : analyzeData.prediction.trend === 'bearish' ? 'var(--ig-red)' : '#d4ac0d' }}>{analyzeData.prediction.trend?.toUpperCase()}</strong></div>
                        </div>
                      </div>
                    )}

                    {analyzeData.weekly_data?.length > 0 && (
                      <div style={subPanelStyle}>
                        <h4 style={{ margin: '0 0 10px 0', color: 'var(--ig-red)' }}>📊 Price Prediction Tracking (Last 2 Weeks)</h4>
                        <div style={{ position: 'relative', height: 300 }}>
                          <Line
                            data={{
                              labels: analyzeData.weekly_data.map(d => d.date),
                              datasets: [
                                { label: 'Actual Price', data: analyzeData.weekly_data.map(d => d.actual_price), borderColor: 'var(--ig-gain)', backgroundColor: 'rgba(0,122,76,0.1)', borderWidth: 2, fill: false, pointRadius: 4, tension: 0.4 },
                                { label: 'Predicted Price', data: analyzeData.weekly_data.map(d => d.predicted_price), borderColor: '#d4ac0d', backgroundColor: 'rgba(212,172,13,0.1)', borderWidth: 2, fill: false, pointRadius: 4, tension: 0.4 }
                              ]
                            }}
                            options={{
                              responsive: true, maintainAspectRatio: false,
                              plugins: { legend: { display: true, labels: { color: 'var(--ig-medium)' } }, tooltip: { backgroundColor: 'rgba(0,0,0,0.85)', titleColor: '#fff', bodyColor: '#ccc', borderColor: '#333', borderWidth: 1, callbacks: { label: ctx => `${ctx.dataset.label}: $${ctx.parsed.y.toFixed(2)}` } } },
                              scales: { x: { grid: { color: '#eee' }, ticks: { color: 'var(--ig-medium)' } }, y: { grid: { color: '#eee' }, ticks: { color: 'var(--ig-medium)' } } }
                            }}
                          />
                        </div>
                        <div style={{ marginTop: 15, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Avg Actual:</span><br/><strong>${(analyzeData.weekly_data.reduce((s, d) => s + d.actual_price, 0) / analyzeData.weekly_data.length).toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Avg Predicted:</span><br/><strong>${(analyzeData.weekly_data.reduce((s, d) => s + d.predicted_price, 0) / analyzeData.weekly_data.length).toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Avg Error:</span><br/><strong style={{ color: '#d4ac0d' }}>${(Math.abs(analyzeData.weekly_data.reduce((s, d) => s + (d.actual_price - d.predicted_price), 0) / analyzeData.weekly_data.length)).toFixed(2)}</strong></div>
                        </div>
                      </div>
                    )}

                    {analyzeData.technical_indicators && (
                      <div style={subPanelStyle}>
                        <h4 style={{ margin: '0 0 10px 0', color: 'var(--ig-red)' }}>📈 Technical Indicators</h4>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10 }}>
                          <div><span style={{ color: 'var(--ig-medium)' }}>RSI:</span><br/><strong>{analyzeData.technical_indicators.rsi?.toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>MACD:</span><br/><strong>{analyzeData.technical_indicators.macd?.toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>SMA 20:</span><br/><strong>${analyzeData.technical_indicators.sma_20?.toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>SMA 50:</span><br/><strong>${analyzeData.technical_indicators.sma_50?.toFixed(2)}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Trend:</span><br/><strong style={{ color: analyzeData.technical_indicators.trend === 'bullish' ? 'var(--ig-gain)' : analyzeData.technical_indicators.trend === 'bearish' ? 'var(--ig-red)' : '#d4ac0d' }}>{analyzeData.technical_indicators.trend?.toUpperCase()}</strong></div>
                        </div>
                      </div>
                    )}

                    {analyzeData.valuation_metrics && (
                      <div style={subPanelStyle}>
                        <h4 style={{ margin: '0 0 10px 0', color: 'var(--ig-red)' }}>💰 Valuation Metrics</h4>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10 }}>
                          <div><span style={{ color: 'var(--ig-medium)' }}>P/E Ratio:</span><br/><strong>{analyzeData.valuation_metrics.pe != null ? Number(analyzeData.valuation_metrics.pe).toFixed(2) : 'N/A'}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>P/B Ratio:</span><br/><strong>{analyzeData.valuation_metrics.pb != null ? Number(analyzeData.valuation_metrics.pb).toFixed(2) : 'N/A'}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Dividend Yield:</span><br/><strong>{analyzeData.valuation_metrics.dividend_yield != null ? Number(analyzeData.valuation_metrics.dividend_yield).toFixed(2) : 'N/A'}%</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Sector:</span><br/><strong>{analyzeData.valuation_metrics.sector || 'N/A'}</strong></div>
                        </div>
                      </div>
                    )}

                    {analyzeData.risk_metrics && (
                      <div style={subPanelStyle}>
                        <h4 style={{ margin: '0 0 10px 0', color: 'var(--ig-red)' }}>⚠️ Risk Metrics</h4>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10 }}>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Beta:</span><br/><strong>{analyzeData.risk_metrics.beta != null ? Number(analyzeData.risk_metrics.beta).toFixed(2) : 'N/A'}</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Max Drawdown (90d):</span><br/><strong style={{ color: 'var(--ig-red)' }}>{analyzeData.risk_metrics.max_drawdown_90d != null ? Number(analyzeData.risk_metrics.max_drawdown_90d).toFixed(2) : 'N/A'}%</strong></div>
                          <div><span style={{ color: 'var(--ig-medium)' }}>Sharpe (90d):</span><br/><strong>{analyzeData.risk_metrics.sharpe_90d != null ? Number(analyzeData.risk_metrics.sharpe_90d).toFixed(2) : 'N/A'}</strong></div>
                        </div>
                      </div>
                    )}

                    {analyzeData.regime && (
                      <div style={subPanelStyle}>
                        <h4 style={{ margin: '0 0 10px 0', color: 'var(--ig-red)' }}>🎯 Market Regime</h4>
                        <p style={{ margin: 0 }}><strong>Regime:</strong> {analyzeData.regime.name} <span style={{ color: 'var(--ig-medium)' }}>({analyzeData.regime.confidence?.toFixed(1)}% confidence)</span></p>
                      </div>
                    )}
                  </div>

                  {analyzeData.llm_analysis && (
                    <section className="panel" style={{ marginBottom: 20 }}>
                      <div className="section-header"><h2>🤖 AI Analysis</h2></div>
                      <div className="ai-analysis"><p style={{ lineHeight: 1.8, fontSize: 15 }}>{analyzeData.llm_analysis}</p></div>
                    </section>
                  )}

                  <section className="panel">
                    <div className="section-header">
                      <h2>📝 Your Notes</h2>
                      <button onClick={saveNotes} disabled={!String(userNotes).trim()} style={{ ...btnGreen, background: !String(userNotes).trim() ? 'var(--ig-muted)' : 'var(--ig-gain)', cursor: !String(userNotes).trim() ? 'not-allowed' : 'pointer' }}>
                        💾 Save Note
                      </button>
                    </div>
                    <textarea value={userNotes} onChange={e => setUserNotes(e.target.value)}
                      placeholder="Add your notes about this stock..."
                      style={{ width: '100%', minHeight: 150, padding: 15, borderRadius: 4, border: '2px solid var(--ig-border-strong)', background: 'var(--ig-white)', color: 'var(--ig-dark)', fontSize: 14, fontFamily: 'inherit', resize: 'vertical', boxSizing: 'border-box' }} />
                    {userNotes && <p style={{ color: 'var(--ig-muted)', fontSize: 12, marginTop: 5 }}>Click "Save Note" to append your note to the file.</p>}
                  </section>
                </>
              )}
            </section>
          </>
        )}

        {activeTab === 'portfolio' && (
          <PortfolioTab token={token} preferredMarket={preferredMarket} />
        )}

        {error && (
          <div className="error-toast">
            <p>{error}</p>
            <button onClick={() => setError(null)}>x</button>
          </div>
        )}
      </main>
    </div>
  )
}

export default App

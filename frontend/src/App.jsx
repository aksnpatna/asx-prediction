import React, { Component, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { Line } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend } from 'chart.js'
import './App.css'
import SmsfTab from './SmsfTab'
import SmsfUplift from './smsfUplift'
import DiscoverTab from './DiscoverTab'
import NewsSentimentMonitor from './NewsSentimentMonitor'
import GlobalMarketsTab from './GlobalMarketsTab'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend)

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null } }
  static getDerivedStateFromError(error) { return { hasError: true, error } }
  componentDidCatch(error, info) { console.error('React ErrorBoundary:', error, info) }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, textAlign: 'center' }}>
          <h2 style={{ color: 'var(--ig-red)' }}>Something went wrong</h2>
          <p style={{ color: 'var(--ig-muted)', margin: '12px 0' }}>{this.state.error?.message || 'Unknown error'}</p>
          <button className="add-btn" onClick={() => this.setState({ hasError: false, error: null })}>Retry</button>
        </div>
      )
    }
    return this.props.children
  }
}

const API_BASE = import.meta.env.VITE_API_URL || '/api'

const EXCHANGE_LABELS = { AU: 'ASX Broad Market', US: 'NASDAQ', IN: 'BSE/NSE' }
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
                <option value="AU">AU (ASX Broad Market)</option>
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

            {/* Backtest panel */}
            <section className="panel" style={{ marginTop: 16 }}>
              <div className="section-header">
                <h2>📊 Weekly Pick Performance vs Benchmark</h2>
                <button className="btn-secondary" onClick={fetchBacktest} disabled={backtestLoading}>
                  {backtestLoading ? 'Loading...' : '📈 Load Backtest'}
                </button>
              </div>
              {!backtestData && !backtestLoading && (
                <p className="section-note">Click "Load Backtest" to see how past weekly picks performed vs the ASX200 benchmark.</p>
              )}
              {backtestData?.summary && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginBottom: 12 }}>
                    <div className="stat-card blue">
                      <div className="stat-content">
                        <span className="stat-title">Direction Accuracy</span>
                        <span className="stat-value">{backtestData.summary.direction_accuracy_pct?.toFixed(1)}%</span>
                      </div>
                    </div>
                    <div className="stat-card blue">
                      <div className="stat-content">
                        <span className="stat-title">Avg Predicted</span>
                        <span className="stat-value" style={{ color: 'var(--ig-blue)' }}>{backtestData.summary.avg_predicted_pct >= 0 ? '+' : ''}{backtestData.summary.avg_predicted_pct?.toFixed(2)}%</span>
                      </div>
                    </div>
                    <div className="stat-card blue">
                      <div className="stat-content">
                        <span className="stat-title">Avg Actual</span>
                        <span className="stat-value" style={{ color: backtestData.summary.avg_actual_pct >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>{backtestData.summary.avg_actual_pct >= 0 ? '+' : ''}{backtestData.summary.avg_actual_pct?.toFixed(2)}%</span>
                      </div>
                    </div>
                    {backtestData.summary.benchmark_6mo_pct != null && (
                      <div className={`stat-card ${backtestData.summary.outperform ? 'green' : 'red'}`}>
                        <div className="stat-content">
                          <span className="stat-title">XJO 6mo Benchmark</span>
                          <span className="stat-value">{backtestData.summary.benchmark_6mo_pct >= 0 ? '+' : ''}{backtestData.summary.benchmark_6mo_pct?.toFixed(2)}%</span>
                          <span style={{ fontSize: 10, color: backtestData.summary.outperform ? 'var(--ig-gain)' : 'var(--ig-red)' }}>
                            {backtestData.summary.outperform ? 'Picks OUTPERFORM' : 'Picks UNDERPERFORM'}
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                  {backtestData.weeks?.length > 0 && (
                    <div className="table-wrap">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Week</th>
                            <th>Picks</th>
                            <th>Avg Predicted</th>
                            <th>Avg Actual</th>
                            <th>Direction Acc</th>
                          </tr>
                        </thead>
                        <tbody>
                          {backtestData.weeks.map(w => (
                            <tr key={w.week}>
                              <td><strong>{w.week}</strong></td>
                              <td>{w.picks_count}</td>
                              <td style={{ color: w.avg_predicted_pct >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>{w.avg_predicted_pct >= 0 ? '+' : ''}{w.avg_predicted_pct}%</td>
                              <td style={{ color: w.avg_actual_pct >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>{w.avg_actual_pct >= 0 ? '+' : ''}{w.avg_actual_pct}%</td>
                              <td style={{ color: w.direction_accuracy >= 50 ? 'var(--ig-gain)' : 'var(--ig-red)', fontWeight: 600 }}>{w.direction_accuracy}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <p style={{ fontSize: 11, color: 'var(--ig-muted)', marginTop: 8 }}>
                    Evaluated {backtestData.summary.total_picks_evaluated} picks. Direction accuracy measures whether the predicted direction (up/down) matched actual outcome 7 days later.
                  </p>
                </div>
              )}
              {backtestData && !backtestData.summary && (
                <p className="section-note">No evaluated picks yet. Weekly picks need to age 7 days before backtest data is available.</p>
              )}
            </section>
          </>
      )}
    </div>
  )
}

// ─── Wealth Builder Tab ───────────────────────────────────────────────────────
const zoneColors = { clear: 'var(--ig-gain)', caution: '#f59e0b', avoid: 'var(--ig-red)' }
const zoneEmoji = { clear: '🟢', caution: '🟡', avoid: '🔴' }

function WealthBuilderTab({ 
  token, preferredMarket, setActiveTab,
  topUniverse, rankedItems, rankLoading, selectedSymbols, setSelectedSymbols,
  selectedCount, trackSelected, trackLoading, addSingleShare,
  aiQuery, setAiQuery, aiProvider, setAiProvider, requestAiSuggestions,
  aiLoading, aiSuggestions, aiProviderUsed, pillStyle, EXCHANGE_LABELS,
  budgetInfo, fetchBudget
}) {
  const authH = () => ({ headers: { Authorization: `Bearer ${token}` } })

  const [form, setForm] = React.useState({
    market: preferredMarket || 'AU',
    min_analyst_upside: -100,
    min_score: 45.0,
    min_prob_5pct: 40.0,
    max_symbols: 20,
    send_telegram: false,
    scan_mode: 'top',
  })
  const [result, setResult] = React.useState(null)
  const [cachedResult, setCachedResult] = React.useState(null)
  const [loading, setLoading] = React.useState(false)
  const [cacheLoading, setCacheLoading] = React.useState(false)
  const [err, setErr] = React.useState(null)
  const [buyModal, setBuyModal] = React.useState(null)

  const loadCached = async () => {
    setCacheLoading(true)
    setErr(null)
    setCachedResult(null)
    try {
      const r = await axios.get(`${import.meta.env.VITE_API_URL || '/api'}/signals/wealth-builder/cached-broad`, {
        ...authH(),
        params: { market: form.market, min_score: form.min_score, min_prob_5pct: form.min_prob_5pct, min_analyst_upside: form.min_analyst_upside }
      })
      setCachedResult(r.data)
    } catch (e) {
      setErr('No cached scan yet. Broad scan runs at 5AM daily.')
    }
    setCacheLoading(false)
  }

  React.useEffect(() => { loadCached() }, [])

  return (
    <>
      <DiscoverTab 
        preferredMarket={preferredMarket}
        cachedScanData={cachedResult}
        cachedScanLoading={cacheLoading}
        fetchCachedWealthScan={loadCached}
        setBuyModal={setBuyModal}
        EXCHANGE_LABELS={{ AU: 'ASX Broad Market', US: 'NASDAQ', IN: 'BSE/NSE' }}
        topUniverse={topUniverse}
        rankedItems={rankedItems}
        rankLoading={rankLoading}
        selectedSymbols={selectedSymbols}
        setSelectedSymbols={setSelectedSymbols}
        selectedCount={selectedCount}
        trackSelected={trackSelected}
        trackLoading={trackLoading}
        addSingleShare={addSingleShare}
        aiQuery={aiQuery}
        setAiQuery={setAiQuery}
        aiProvider={aiProvider}
        setAiProvider={setAiProvider}
        requestAiSuggestions={requestAiSuggestions}
        aiLoading={aiLoading}
        aiSuggestions={aiSuggestions}
        aiProviderUsed={aiProviderUsed}
        pillStyle={pillStyle}
        budgetInfo={budgetInfo}
      />
      
      {/* Buy Modal */}
      {buyModal && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setBuyModal(null) }}>
          <div className="modal" style={{ maxWidth: 520 }}>
            <div className="modal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 20px', background: 'var(--text-primary)', borderBottom: '2px solid var(--border-strong)' }}>
              <div>
                <h2 style={{ margin: 0, color: 'var(--bg-tertiary)', fontSize: 17, fontWeight: 800 }}>📈 Buy {buyModal.symbol}</h2>
                <p style={{ margin: '2px 0 0', color: '#64748b', fontSize: 11 }}>{buyModal.name || 'Paper Trade'}</p>
              </div>
              <button className="close-btn" onClick={() => setBuyModal(null)} style={{ background: 'var(--text-primary)', borderColor: 'var(--text-secondary)', color: '#94a3b8' }}>×</button>
            </div>
            <div className="modal-body" style={{ padding: 20, background: 'var(--bg-secondary)' }}>
              {/* Current price badge */}
              {buyModal.current_price && (
                <div style={{ display: 'flex', gap: 10, marginBottom: 16, padding: '10px 14px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', borderRadius: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1 }}>Market Price</div>
                    <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--text-primary)', fontFamily: 'monospace' }}>${Number(buyModal.current_price).toFixed(3)}</div>
                  </div>
                  {buyModal.analyst_target && (
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1 }}>Analyst Target</div>
                      <div style={{ fontSize: 22, fontWeight: 800, color: '#00a854', fontFamily: 'monospace' }}>${Number(buyModal.analyst_target).toFixed(2)}</div>
                    </div>
                  )}
                </div>
              )}

              {/* Smart sizing tip */}
              {buyModal.smart_sizing && (
                <div style={{ marginBottom: 14, padding: 10, background: 'rgba(0, 210, 135, 0.1)', border: '1px solid rgba(0, 210, 135, 0.3)', borderRadius: 6, fontSize: 12, color: 'var(--ig-gain)', fontWeight: 600 }}>
                  💡 {buyModal.smart_sizing}
                </div>
              )}
              {buyModal.budget_note && (
                <div style={{ marginBottom: 14, padding: 10, background: 'rgba(0, 163, 255, 0.1)', border: '1px solid rgba(0, 163, 255, 0.3)', borderRadius: 6, fontSize: 11, color: 'var(--ig-blue-accent)' }}>
                  💰 {buyModal.budget_note}
                </div>
              )}

              {/* Form */}
              <div style={{ display: 'grid', gap: 12 }}>
                <div>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>Quantity *</label>
                  <input type="number" step="1" min="1"
                    value={buyModal.quantity}
                    onChange={e => setBuyModal(m => ({ ...m, quantity: e.target.value }))}
                    placeholder="e.g. 100"
                    style={{ width: '100%', padding: '10px 12px', border: '2px solid var(--border-color)', borderRadius: 6, fontSize: 14, fontFamily: 'inherit', outline: 'none' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    Execution Price <span style={{ fontWeight: 400, color: '#94a3b8' }}>(leave blank to use live price)</span>
                  </label>
                  <input type="number" step="0.001" min="0"
                    value={buyModal.execution_price || ''}
                    onChange={e => setBuyModal(m => ({ ...m, execution_price: e.target.value }))}
                    placeholder={buyModal.current_price ? `Market: $${Number(buyModal.current_price).toFixed(3)}` : 'e.g. 4.50'}
                    style={{ width: '100%', padding: '10px 12px', border: '2px solid var(--border-color)', borderRadius: 6, fontSize: 14, fontFamily: 'inherit', outline: 'none' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>Commission (optional)</label>
                  <input type="number" step="0.01" min="0"
                    value={buyModal.commission || ''}
                    onChange={e => setBuyModal(m => ({ ...m, commission: e.target.value }))}
                    placeholder="e.g. 9.50 (CommSec default)"
                    style={{ width: '100%', padding: '10px 12px', border: '2px solid var(--border-color)', borderRadius: 6, fontSize: 14, fontFamily: 'inherit', outline: 'none' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>Notes (optional)</label>
                  <input type="text"
                    value={buyModal.notes || ''}
                    onChange={e => setBuyModal(m => ({ ...m, notes: e.target.value }))}
                    placeholder="e.g. Following AI signal on discovery tab"
                    style={{ width: '100%', padding: '10px 12px', border: '2px solid var(--border-color)', borderRadius: 6, fontSize: 14, fontFamily: 'inherit', outline: 'none' }}
                  />
                </div>
              </div>

              {/* Total estimate */}
              {buyModal.quantity && buyModal.current_price && (
                <div style={{ marginTop: 12, padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
                  📊 Estimated total: <strong style={{ color: 'var(--text-primary)' }}>
                    ${(Number(buyModal.quantity) * Number(buyModal.execution_price || buyModal.current_price)).toFixed(2)}
                  </strong>
                  {buyModal.commission ? ` + $${Number(buyModal.commission).toFixed(2)} commission` : ''}
                </div>
              )}

              {/* Action buttons */}
              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--border-color)' }}>
                <button
                  onClick={() => setBuyModal(null)}
                  style={{ padding: '10px 20px', background: 'var(--bg-secondary)', border: '2px solid var(--border-color)', borderRadius: 6, fontWeight: 700, fontSize: 13, cursor: 'pointer', color: 'var(--text-secondary)' }}
                >
                  Cancel
                </button>
                <button
                  onClick={async () => {
                    if (!buyModal.quantity || Number(buyModal.quantity) <= 0) {
                      alert('Please enter a valid quantity'); return;
                    }
                    try {
                      await axios.post(`${import.meta.env.VITE_API_URL || '/api'}/paper-trades`, {
                        symbol: buyModal.symbol,
                        market: preferredMarket || 'AU',
                        side: 'LONG',
                        quantity: Number(buyModal.quantity),
                        notes: buyModal.notes || `Opened from Discover tab`
                      }, authH())
                      setBuyModal(null)
                      alert(`✅ Paper trade opened: ${buyModal.quantity}x ${buyModal.symbol}! Check the Strategy tab → Paper Trades section.`)
                    } catch (e) {
                      alert(`❌ ${e.response?.data?.detail || 'Failed to log paper trade. Please try again.'}`)
                    }
                  }}
                  style={{ padding: '10px 24px', background: 'linear-gradient(135deg, #00a854, #00c96e)', border: 'none', borderRadius: 6, fontWeight: 800, fontSize: 13, cursor: 'pointer', color: 'var(--bg-secondary)', letterSpacing: 0.3 }}
                >
                  ✅ Execute Paper Trade
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

// ─── Telegram Bot Command Guide ───────────────────────────────────────────────
function TelegramBotGuide() {
  const [open, setOpen] = React.useState(false)
  return (
    <div style={{
      background: 'rgba(0, 163, 255, 0.05)',
      border: '1px solid rgba(0, 163, 255, 0.2)',
      borderRadius: 10,
      marginBottom: 14,
      overflow: 'hidden',
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', background: 'none', border: 'none', cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 16px', fontWeight: 600, fontSize: 14, color: 'var(--ig-blue-accent)',
        }}
      >
        <span>📱 Telegram Bot Commands — text your bot to update your portfolio</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{open ? '▲ Hide' : '▼ Show'}</span>
      </button>
      {open && (
        <div style={{ padding: '0 16px 16px', display: 'grid', gap: 10 }}>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)' }}>
            Text your Telegram bot to record trades, check your portfolio, or manage watchlist items.
            Every trade command automatically starts position monitoring with alerts.
          </p>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: 'rgba(0, 163, 255, 0.15)' }}>
                <th style={{ padding: '6px 10px', textAlign: 'left', color: 'var(--text-primary)' }}>What to send</th>
                <th style={{ padding: '6px 10px', textAlign: 'left', color: 'var(--text-primary)' }}>What happens</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['BUY BHP 100 45.50', 'Records buy · adds to watchlist · starts monitoring (8% stop, model target)'],
                ['BUY BHP 100 @ 45.50', 'Same — @ is optional'],
                ['ADD FMG 200 18.50', 'Records additional buy for existing position'],
                ['SELL CBA 50 123.00', 'Records sell · closes position monitor'],
                ['REDUCE WOW 100 30.00', 'Records partial sell'],
                ['PORTFOLIO  or  P', 'Returns your current holdings summary'],
                ['STATUS  or  S', 'Shows all open monitored positions with P&L'],
                ['TRACK BHP', 'Adds BHP to your watchlist without recording a trade'],
                ['HELP', 'Shows the full command reference'],
              ].map(([cmd, desc], i) => (
                <tr key={i} style={{ background: i % 2 === 0 ? 'var(--bg-secondary)' : 'var(--bg-tertiary)' }}>
                  <td style={{ padding: '5px 10px', fontFamily: 'monospace', color: 'var(--ig-blue-accent)', whiteSpace: 'nowrap' }}>{cmd}</td>
                  <td style={{ padding: '5px 10px', color: 'var(--text-secondary)' }}>{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ margin: 0, fontSize: 12, color: '#8a9ab0' }}>
            ⚙️ To activate: link your Telegram chat ID in the <strong>Telegram Setup</strong> section below, then ask your admin to run <em>POST /api/telegram/register-webhook</em> once (or send <strong>any message</strong> to the bot after the backend URL is live).
          </p>
        </div>
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
  
  // Forgot password states
  const [forgotPasswordEmail, setForgotPasswordEmail] = useState('')
  const [resetToken, setResetToken] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [resetInstructions, setResetInstructions] = useState('')

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('smsf-brief')

  const [topUniverse, setTopUniverse] = useState([])
  const [aiQuery, setAiQuery] = useState('Find ASX shares with strong momentum and low volatility')
  const [aiProvider, setAiProvider] = useState('auto')
  const [aiProviderUsed, setAiProviderUsed] = useState('')
  const [aiSuggestions, setAiSuggestions] = useState([])
  const [rankedItems, setRankedItems] = useState([])
  const [selectedSymbols, setSelectedSymbols] = useState({})
  const [telegramRecipients, setTelegramRecipients] = useState([])
  const [telegramAdvice, setTelegramAdvice] = useState(null)
  const [telegramDigest, setTelegramDigest] = useState(null)
  const [telegramAdviceLoading, setTelegramAdviceLoading] = useState(false)
  const [telegramDigestLoading, setTelegramDigestLoading] = useState(false)
  const [telegramHistory, setTelegramHistory] = useState([])
  const [telegramHistoryLoading, setTelegramHistoryLoading] = useState(false)
  const [newTelegramChatId, setNewTelegramChatId] = useState('')
  const [newTelegramLabel, setNewTelegramLabel] = useState('')
  const [telegramRecipientSaving, setTelegramRecipientSaving] = useState(false)
  const [adviceActions, setAdviceActions] = useState([])
  const [adviceHoldings, setAdviceHoldings] = useState([])
  const [adviceActionsLoading, setAdviceActionsLoading] = useState(false)
  const [adviceActionSaving, setAdviceActionSaving] = useState(false)
  const [adviceActionDrafts, setAdviceActionDrafts] = useState({})
  const [positionHistory, setPositionHistory] = useState([])
  const [positionHistoryLoading, setPositionHistoryLoading] = useState(false)
  const [tradeDrafts, setTradeDrafts] = useState({})
  const [paperTrades, setPaperTrades] = useState([])
  const [paperTradeLoading, setPaperTradeLoading] = useState(false)
  const [trackingOverview, setTrackingOverview] = useState([])
  const [bucketCounts, setBucketCounts] = useState({ meeting_expectation: 0, non_meeting_expectation: 0 })
  const [metrics, setMetrics] = useState({ validation_hit_rate_pct: 0, average_forecast_error_pct: 0 })
  const [marketPulse, setMarketPulse] = useState(null)
  const [explainability, setExplainability] = useState(null)
  const [marketAnalysis, setMarketAnalysis] = useState(null)
  const [marketLoading, setMarketLoading] = useState(false)
  const [sentimentData, setSentimentData] = useState(null)
  const [sentimentSymbol, setSentimentSymbol] = useState('')

  const [analyzeSymbol, setAnalyzeSymbol] = useState('')
  const [analyzeMarket, setAnalyzeMarket] = useState('AU')
  const [analyzeData, setAnalyzeData] = useState(null)
  const [analyzeLoading, setAnalyzeLoading] = useState(false)
  const [userNotes, setUserNotes] = useState('')
  const [notesLoading, setNotesLoading] = useState(false)
  const [preferredMarket, setPreferredMarket] = useState('AU')

  const [aiLoading, setAiLoading] = useState(false)
  const [rankLoading, setRankLoading] = useState(false)
  const [trackLoading, setTrackLoading] = useState(false)

  // Phase 6 – Weekly Predictions
  const [weeklyData, setWeeklyData] = useState(null)
  const [weeklyLoading, setWeeklyLoading] = useState(false)
  const [weeklyCap, setWeeklyCap] = useState('all')
  const [weeklySectors, setWeeklySectors] = useState(null)
  const [weeklySectorsLoading, setWeeklySectorsLoading] = useState(false)
  const [backtestData, setBacktestData] = useState(null)
  const [backtestLoading, setBacktestLoading] = useState(false)

  // Phase 6 – Crypto Dashboard
  const [cryptoMarket, setCryptoMarket] = useState(null)
  const [cryptoLoading, setCryptoLoading] = useState(false)
  const [cryptoDetail, setCryptoDetail] = useState(null)
  const [cryptoDetailLoading, setCryptoDetailLoading] = useState(false)
  const [cryptoSearch, setCryptoSearch] = useState('')
  const [cryptoSearchResults, setCryptoSearchResults] = useState([])
  const [cryptoWatchlist, setCryptoWatchlist] = useState([])
  const [cryptoAnalysis, setCryptoAnalysis] = useState(null)
  const [cryptoAnalysisLoading, setCryptoAnalysisLoading] = useState(false)

  // Phase 6 – ETF Explorer
  const [etfList, setEtfList] = useState(null)
  const [etfLoading, setEtfLoading] = useState(false)
  const [etfDetail, setEtfDetail] = useState(null)
  const [etfDetailLoading, setEtfDetailLoading] = useState(false)
  const [etfSearch, setEtfSearch] = useState('')
  const [etfCompare, setEtfCompare] = useState(null)
  const [etfCompareLoading, setEtfCompareLoading] = useState(false)
  const [etfCompareTickers, setEtfCompareTickers] = useState('')
  const [etfSpotlight, setEtfSpotlight] = useState(null)
  const [etfSpotlightLoading, setEtfSpotlightLoading] = useState(false)

  // Phase 6 – AI banner
  const [aiBanner, setAiBanner] = useState(null)

  // Phase 7 – Investment Budget, Hedge Suggestions, Sentiment Monitor
  const [budgetInfo, setBudgetInfo] = useState(null)
  const [budgetLoading, setBudgetLoading] = useState(false)
  const [budgetForm, setBudgetForm] = useState({ total_budget: '', max_position_pct: '10', currency: 'AUD' })
  const [hedgeSuggestions, setHedgeSuggestions] = useState(null)
  const [hedgeLoading, setHedgeLoading] = useState(false)

  const authHeaders = () => ({ headers: { Authorization: `Bearer ${token}` } })
  const selectedCount = useMemo(() => Object.values(selectedSymbols).filter(Boolean).length, [selectedSymbols])
  const activeStrategyDashboard = useMemo(
    () => telegramAdvice?.strategy_dashboard || telegramDigest?.strategy_dashboard || null,
    [telegramAdvice, telegramDigest],
  )
  const executionSnapshot = useMemo(() => {
    const recent = (adviceActions || []).slice(0, 30)
    let buyCount = 0
    let reduceCount = 0
    let netDeployed = 0
    let commissions = 0

    recent.forEach((item) => {
      const action = String(item.action_type || '').toUpperCase()
      const net = Number(item.net_amount || 0)
      const fee = Number(item.commission || 0)
      commissions += fee
      if (action === 'BUY' || action === 'ADD' || action === 'HOLD') {
        buyCount += 1
        netDeployed += net
      } else if (action === 'SELL' || action === 'REDUCE') {
        reduceCount += 1
        netDeployed -= net
      }
    })

    const holdingsCost = (adviceHoldings || []).reduce((sum, item) => {
      const invested = Number(item.invested_amount || 0)
      if (invested > 0) return sum + invested
      return sum + (Number(item.quantity || 0) * Number(item.avg_cost || 0))
    }, 0)

    return {
      decisions: recent.length,
      buyCount,
      reduceCount,
      netDeployed,
      commissions,
      holdingsCost,
      holdingsTracked: (adviceHoldings || []).length,
    }
  }, [adviceActions, adviceHoldings])

  useEffect(() => {
    if (!token) { setLoading(false); return }
    bootstrap()
  }, [token])

  const bootstrap = async () => {
    try {
      setLoading(true)
      let marketValue = 'AU'
      try {
        const me = await axios.get(`${API_BASE}/auth/me`, authHeaders())
        setUser(me.data)
        marketValue = me.data.preferred_market || 'AU'
        setPreferredMarket(marketValue)
        setAnalyzeMarket(marketValue)
      } catch (authErr) {
        throw new Error(`Authentication failed (${authErr.response?.status}): ${authErr.response?.data?.detail || authErr.message}`)
      }
      try {
        await Promise.all([fetchTopUniverse(marketValue), fetchTrackingOverview(), fetchMarketPulse(), fetchExplainability(), fetchTelegramRecipients(), fetchTelegramHistory(), fetchAdviceActions(), fetchPaperTrades(), fetchPositionHistory(), fetchBudget()])
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

  const fetchTopUniverse = async (market) => {
    const m = market || preferredMarket || 'AU'
    const r = await axios.get(`${API_BASE}/universe/top`, { ...authHeaders(), params: { market: m } })
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
    try {
      const r = await axios.get(`${API_BASE}/v1/market/pulse`, authHeaders())
      if (r.data) setMarketPulse(r.data)
    } catch { setError('Failed to load market pulse') }
  }
  const fetchExplainability = async () => {
    const r = await axios.get(`${API_BASE}/v1/backtest/summary`, authHeaders())
    setExplainability(r.data)
  }

  const fetchBudget = async () => {
    setBudgetLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/user/investment-budget`, authHeaders())
      setBudgetInfo(r.data)
      if (r.data?.budget_set) {
        setBudgetForm({
          total_budget: r.data.total_budget?.toString() || '',
          max_position_pct: r.data.max_position_pct?.toString() || '10',
          currency: r.data.currency || 'AUD',
        })
      }
    } catch {
      setBudgetInfo(null)
    } finally {
      setBudgetLoading(false)
    }
  }

  const saveBudget = async () => {
    setBudgetLoading(true)
    try {
      const r = await axios.put(`${API_BASE}/user/investment-budget`, {
        total_budget: parseFloat(budgetForm.total_budget) || 0,
        max_position_pct: parseFloat(budgetForm.max_position_pct) || 10,
        currency: budgetForm.currency || 'AUD',
      }, authHeaders())
      if (r.data?.ok) {
        await fetchBudget()
      }
    } catch (e) {
      alert(`Failed to save budget: ${e.response?.data?.detail || e.message}`)
    } finally {
      setBudgetLoading(false)
    }
  }

  const fetchHedgeSuggestions = async () => {
    setHedgeLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/positions/hedge-suggestions`, authHeaders())
      setHedgeSuggestions(r.data)
    } catch {
      setHedgeSuggestions(null)
    } finally {
      setHedgeLoading(false)
    }
  }

  const fetchSentimentScan = async () => {
    setSentimentLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/positions/sentiment-scan`, authHeaders())
      setSentimentScan(r.data)
    } catch {
      setSentimentScan(null)
    } finally {
      setSentimentLoading(false)
    }
  }

  const fetchTelegramRecipients = async () => {
    try {
      const r = await axios.get(`${API_BASE}/telegram/recipients`, authHeaders())
      setTelegramRecipients(r.data?.recipients || [])
    } catch {
      setTelegramRecipients([])
    }
  }

  const fetchTelegramHistory = async () => {
    setTelegramHistoryLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/telegram/history`, authHeaders())
      setTelegramHistory(r.data?.items || [])
    } catch {
      setTelegramHistory([])
    } finally {
      setTelegramHistoryLoading(false)
    }
  }

  const addTelegramRecipient = async () => {
    if (!newTelegramChatId.trim()) {
      setError('Enter a Telegram chat ID first.')
      return
    }
    setTelegramRecipientSaving(true)
    try {
      await axios.post(`${API_BASE}/telegram/recipients`, {
        chat_id: newTelegramChatId.trim(),
        label: newTelegramLabel.trim() || null,
      }, authHeaders())
      setNewTelegramChatId('')
      setNewTelegramLabel('')
      await Promise.all([fetchTelegramRecipients(), fetchTelegramHistory()])
      setError(null)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to add Telegram chat mapping.')
    } finally {
      setTelegramRecipientSaving(false)
    }
  }

  const removeTelegramRecipient = async (recipientId) => {
    try {
      await axios.delete(`${API_BASE}/telegram/recipients/${recipientId}`, authHeaders())
      await Promise.all([fetchTelegramRecipients(), fetchTelegramHistory()])
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to remove Telegram chat mapping.')
    }
  }

  const fetchPositionHistory = async () => {
    setPositionHistoryLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/positions/history`, authHeaders())
      setPositionHistory(r.data?.items || [])
    } catch {
      setPositionHistory([])
    } finally {
      setPositionHistoryLoading(false)
    }
  }

  const fetchAdviceActions = async () => {
    setAdviceActionsLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/advice/actions`, authHeaders())
      setAdviceActions(r.data?.items || [])
      setAdviceHoldings(r.data?.holdings || [])
    } catch {
      setAdviceActions([])
      setAdviceHoldings([])
    } finally {
      setAdviceActionsLoading(false)
    }
  }

  useEffect(() => {
    if (!token || activeTab !== 'markets') return
    const timer = setInterval(() => {
      fetchAdviceActions()
    }, 15000)
    return () => clearInterval(timer)
  }, [token, activeTab])

  useEffect(() => {
    if (!telegramAdvice?.items?.length) return
    setAdviceActionDrafts(prev => {
      const next = { ...prev }
      telegramAdvice.items.forEach((item) => {
        if (!next[item.symbol]) {
          next[item.symbol] = {
            quantity: '',
            execution_price: '',
            commission: '',
            notes: '',
          }
        }
      })
      return next
    })
  }, [telegramAdvice])

  const logAdviceAction = async (item, forcedActionType = null) => {
    const symbol = item?.symbol
    if (!symbol) return
    const draft = adviceActionDrafts[symbol] || {}
    const quantity = Number(draft.quantity)
    const executionPrice = Number(draft.execution_price)
    const commission = draft.commission === '' || draft.commission == null ? null : Number(draft.commission)
    if (!Number.isFinite(quantity) || quantity <= 0) {
      setError(`Enter a valid quantity for ${symbol}.`)
      return
    }
    if (!Number.isFinite(executionPrice) || executionPrice <= 0) {
      setError(`Enter a valid execution price for ${symbol}.`)
      return
    }

    const actionType = (forcedActionType || item.action || 'BUY').toUpperCase()
    setAdviceActionSaving(true)
    try {
      await axios.post(`${API_BASE}/advice/actions`, {
        symbol,
        market: preferredMarket,
        action_type: actionType,
        quantity,
        execution_price: executionPrice,
        commission,
        advice_cache_key: telegramAdvice?.cache_key || null,
        source_message_type: 'hedge_advice',
        notes: (draft.notes || '').trim() || null,
      }, authHeaders())
      await Promise.all([fetchAdviceActions(), fetchTrackingOverview()])
      setAdviceActionDrafts(prev => ({
        ...prev,
        [symbol]: { quantity: '', execution_price: '', commission: '', notes: '' },
      }))
      setError(null)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to record executed action.')
    } finally {
      setAdviceActionSaving(false)
    }
  }

  const fetchPaperTrades = async () => {
    try {
      const r = await axios.get(`${API_BASE}/paper-trades`, authHeaders())
      setPaperTrades(r.data?.items || [])
      const draftMap = {}
      ;(r.data?.items || []).forEach((trade) => {
        draftMap[trade.id] = {
          stop_loss_price: trade.stop_loss_price ?? '',
          take_profit_price: trade.take_profit_price ?? '',
          trailing_stop_pct: trade.trailing_stop_pct ?? 3,
          position_stage: trade.position_stage || 'entered',
        }
      })
      setTradeDrafts(draftMap)
    } catch {
      setPaperTrades([])
      setTradeDrafts({})
    }
  }

  const updatePaperTradePlan = async (tradeId) => {
    const draft = tradeDrafts[tradeId]
    if (!draft) return
    try {
      setPaperTradeLoading(true)
      await axios.patch(`${API_BASE}/paper-trades/${tradeId}`, {
        stop_loss_price: draft.stop_loss_price === '' ? null : Number(draft.stop_loss_price),
        take_profit_price: draft.take_profit_price === '' ? null : Number(draft.take_profit_price),
        trailing_stop_pct: draft.trailing_stop_pct === '' ? null : Number(draft.trailing_stop_pct),
        position_stage: draft.position_stage || null,
      }, authHeaders())
      await Promise.all([fetchPaperTrades(), fetchPositionHistory()])
      setError(null)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to update trade plan.')
    } finally {
      setPaperTradeLoading(false)
    }
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
      const r = await axios.get(`${API_BASE}/ai/analyze/${analyzeSymbol.trim().toUpperCase()}`, {
        ...authHeaders(),
        params: { market: analyzeMarket },
        timeout: 150000,
      })
      setAnalyzeData(r.data)
      loadNotes(analyzeSymbol.trim().toUpperCase())
    } catch (err) {
      setError(err.response?.data?.detail || (err.code === 'ECONNABORTED' ? 'Analysis timed out – try again' : 'Failed to analyze share'))
    }
    setAnalyzeLoading(false)
  }

  // ─── Phase 6 Fetch Functions ─────────────────────────────────────────────────
  const formatCurrency = (val, market) => {
    if (val == null) return 'N/A'
    if (market === 'IN') return '₹' + Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    return '$' + Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  const fetchWeeklyDigest = async () => {
    setWeeklyLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/weekly/latest?market=${preferredMarket}`, authHeaders())
      const raw = r.data
      // Flatten picks_by_cap into a list with cap_tier field
      const picks = []
      let llm_summary = ''
      if (raw.picks_by_cap) {
        for (const [tier, data] of Object.entries(raw.picks_by_cap)) {
          if (data.picks) {
            for (const p of data.picks) picks.push({ ...p, cap_tier: tier })
          }
          if (data.ai_summary && !llm_summary) llm_summary = data.ai_summary
        }
      }
      setWeeklyData({
        picks,
        llm_summary,
        week_label: raw.week,
        generated_at: raw.generated_at || new Date().toISOString(),
        market: raw.market,
        status: raw.status,
        message: raw.message,
        picks_by_sector: raw.picks_by_sector || {},
      })
    } catch { setWeeklyData(null) }
    setWeeklyLoading(false)
  }

  const fetchWeeklySectors = async () => {
    setWeeklySectorsLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/weekly/sectors?market=${preferredMarket}`, authHeaders())
      setWeeklySectors(r.data)
    } catch { setWeeklySectors(null) }
    setWeeklySectorsLoading(false)
  }

  const fetchBacktest = async () => {
    setBacktestLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/weekly/backtest?market=${preferredMarket}&weeks=8`, authHeaders())
      setBacktestData(r.data)
    } catch { setBacktestData(null) }
    setBacktestLoading(false)
  }

  const generateWeekly = async () => {
    setWeeklyLoading(true)
    try {
      await axios.post(`${API_BASE}/weekly/generate`, { market: preferredMarket }, authHeaders())
      await fetchWeeklyDigest()
    } catch { setError('Failed to generate weekly digest') }
    setWeeklyLoading(false)
  }

  const fetchCryptoMarket = async () => {
    setCryptoLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/crypto/market`, authHeaders())
      setCryptoMarket(r.data)
    } catch { setCryptoMarket(null) }
    setCryptoLoading(false)
  }

  const fetchCryptoDetail = async (coinId) => {
    setCryptoDetailLoading(true)
    setCryptoAnalysis(null)
    try {
      const r = await axios.get(`${API_BASE}/crypto/${coinId}`, authHeaders())
      setCryptoDetail(r.data)
    } catch { setCryptoDetail(null) }
    setCryptoDetailLoading(false)
  }

  const searchCrypto = async () => {
    if (!cryptoSearch.trim()) return
    try {
      const r = await axios.get(`${API_BASE}/crypto/search?q=${encodeURIComponent(cryptoSearch)}`, authHeaders())
      setCryptoSearchResults(r.data?.results || [])
    } catch { setCryptoSearchResults([]) }
  }

  const fetchCryptoWatchlist = async () => {
    try {
      const r = await axios.get(`${API_BASE}/crypto/watchlist`, authHeaders())
      setCryptoWatchlist(r.data?.coins || [])
    } catch { setCryptoWatchlist([]) }
  }

  const addCryptoWatchlist = async (coinId) => {
    try {
      await axios.post(`${API_BASE}/crypto/watchlist`, { coin_id: coinId }, authHeaders())
      await fetchCryptoWatchlist()
    } catch { /* already exists */ }
  }

  const removeCryptoWatchlist = async (coinId) => {
    try {
      await axios.delete(`${API_BASE}/crypto/watchlist/${coinId}`, authHeaders())
      await fetchCryptoWatchlist()
    } catch {}
  }

  const analyzeCrypto = async (coinId) => {
    setCryptoAnalysisLoading(true)
    try {
      const r = await axios.post(`${API_BASE}/crypto/${coinId}/analyze`, {}, authHeaders())
      setCryptoAnalysis(r.data)
    } catch { setError('Failed to analyze crypto') }
    setCryptoAnalysisLoading(false)
  }

  const fetchEtfList = async () => {
    setEtfLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/etf/list?market=${preferredMarket}`, authHeaders())
      // Normalize: backend returns {categories: {...}} or {etfs: {...}}
      const cats = r.data?.categories || r.data?.etfs || r.data || {}
      setEtfList({ etfs: cats })
    } catch { setEtfList(null) }
    setEtfLoading(false)
  }

  const fetchEtfDetail = async (ticker) => {
    setEtfDetailLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/etf/${encodeURIComponent(ticker)}`, authHeaders())
      setEtfDetail(r.data)
    } catch { setEtfDetail(null) }
    setEtfDetailLoading(false)
  }

  const compareEtfs = async () => {
    if (!etfCompareTickers.trim()) return
    setEtfCompareLoading(true)
    try {
      const tickers = etfCompareTickers.split(',').map(t => t.trim()).filter(Boolean)
      const r = await axios.post(`${API_BASE}/etf/compare`, { tickers }, authHeaders())
      setEtfCompare(r.data)
    } catch { setError('Failed to compare ETFs') }
    setEtfCompareLoading(false)
  }

  const fetchEtfSpotlight = async () => {
    setEtfSpotlightLoading(true)
    try {
      const r = await axios.get(`${API_BASE}/etf/spotlight?market=${preferredMarket}`, authHeaders())
      setEtfSpotlight(r.data)
    } catch { setEtfSpotlight(null) }
    setEtfSpotlightLoading(false)
  }

  const fetchAiBanner = async () => {
    try {
      const r = await axios.get(`${API_BASE}/ai/weekly-summary?market=${preferredMarket}`, authHeaders())
      setAiBanner(r.data)
    } catch { setAiBanner(null) }
  }

  const getAdviceSymbols = () => {
    const selected = Object.entries(selectedSymbols).filter(([, checked]) => checked).map(([symbol]) => symbol)
    if (selected.length > 0) return selected
    const tracked = (trackingOverview || []).map(item => item.symbol).filter(Boolean)
    if (tracked.length > 0) return tracked.slice(0, 5)
    return rankedItems.slice(0, 5).map(item => item.symbol)
  }

  const requestTelegramAdvice = async (sendTelegram = false) => {
    const symbols = getAdviceSymbols()
    if (!symbols.length) {
      setError('Rank some shares or select rows first.')
      return
    }
    setTelegramAdviceLoading(true)
    try {
      const r = await axios.post(`${API_BASE}/ai/hedge-advice`, {
        symbols,
        market: preferredMarket,
        limit: 5,
        send_telegram: sendTelegram,
      }, { ...authHeaders(), timeout: 30000 })
      setTelegramAdvice(r.data)
      await Promise.all([fetchTelegramRecipients(), fetchTelegramHistory()])
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to generate Telegram advice.')
    } finally {
      setTelegramAdviceLoading(false)
    }
  }

  const requestDailyDigest = async (sendTelegram = false) => {
    setTelegramDigestLoading(true)
    try {
      const r = await axios.post(`${API_BASE}/ai/daily-digest`, {
        market: preferredMarket,
        limit: 5,
        send_telegram: sendTelegram,
      }, { ...authHeaders(), timeout: 30000 })
      setTelegramDigest(r.data)
      await Promise.all([fetchTelegramRecipients(), fetchTelegramHistory()])
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to generate daily digest.')
    } finally {
      setTelegramDigestLoading(false)
    }
  }

  const openPaperTrade = async (item) => {
    try {
      setPaperTradeLoading(true)
      const side = item.action === 'REDUCE' ? 'SHORT' : 'LONG'
      await axios.post(`${API_BASE}/paper-trades`, {
        symbol: item.symbol,
        market: preferredMarket,
        side,
        quantity: 100,
        notes: `Opened from ${item.action} signal`,
      }, authHeaders())
      await Promise.all([fetchPaperTrades(), fetchPositionHistory()])
      setError(null)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to open paper trade.')
    } finally {
      setPaperTradeLoading(false)
    }
  }

  const closePaperTrade = async (tradeId) => {
    try {
      setPaperTradeLoading(true)
      await axios.post(`${API_BASE}/paper-trades/${tradeId}/close`, {}, authHeaders())
      await Promise.all([fetchPaperTrades(), fetchPositionHistory()])
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to close paper trade.')
    } finally {
      setPaperTradeLoading(false)
    }
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
    setAnalyzeMarket(market)
    try {
      await axios.put(`${API_BASE}/auth/me/market`, { market }, authHeaders())
    } catch { /* non-critical */ }
    fetchTopUniverse(market)
  }

  const logout = () => {
    localStorage.removeItem('asx_token')
    setToken(''); setUser(null)
    setTopUniverse([]); setAiSuggestions([]); setRankedItems([]); setTrackingOverview([])
  }

  const handleForgotPassword = async (e) => {
    e.preventDefault()
    setAuthLoading(true)
    setError(null)
    try {
      const r = await axios.post(`${API_BASE}/auth/forgot-password`, { email: forgotPasswordEmail })
      setResetToken(r.data.reset_token)
      setResetInstructions(r.data.instructions)
      setAuthMode('reset')
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to send reset token')
    } finally {
      setAuthLoading(false)
    }
  }

  const handleResetPassword = async (e) => {
    e.preventDefault()
    setAuthLoading(true)
    setError(null)
    try {
      await axios.post(`${API_BASE}/auth/reset-password`, { 
        token: resetToken, 
        new_password: newPassword 
      })
      setResetToken('')
      setNewPassword('')
      setForgotPasswordEmail('')
      setAuthMode('login')
      setError(null)
      setTimeout(() => alert('✅ Password reset successfully! Please log in with your new password.'), 500)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to reset password')
    } finally {
      setAuthLoading(false)
    }
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
          {authMode !== 'reset' && (
            <>
              <button type="button" className="auth-switch" onClick={() => setAuthMode(authMode === 'register' ? 'login' : 'register')}>
                {authMode === 'register' ? 'Already have an account? Sign in' : 'Need an account? Register'}
              </button>
              {authMode === 'login' && (
                <button type="button" className="auth-switch" onClick={() => setAuthMode('forgot')} style={{ marginTop: 8, fontSize: '12px', opacity: 0.8 }}>
                  Forgot your password?
                </button>
              )}
            </>
          )}
          {error && <p className="auth-error">{error}</p>}
        </div>
      </div>
    )
  }

  if (authMode === 'forgot') {
    return (
      <div className="auth-shell">
        <div className="auth-panel">
          <div className="auth-brand">
            <p className="auth-eyebrow">Reset Your Password</p>
            <h1>Enter your email</h1>
            <p>We'll send you a reset token to create a new password.</p>
          </div>
          <form className="auth-form" onSubmit={handleForgotPassword}>
            <input 
              type="email" 
              placeholder="Enter your email" 
              value={forgotPasswordEmail}
              onChange={(e) => setForgotPasswordEmail(e.target.value)} 
              className="search-input" 
              required 
            />
            <button type="submit" className="add-btn" disabled={authLoading}>
              {authLoading ? 'Sending...' : 'Send reset token'}
            </button>
          </form>
          <button type="button" className="auth-switch" onClick={() => { setAuthMode('login'); setForgotPasswordEmail(''); setError(null); }}>
            Back to sign in
          </button>
          {error && <p className="auth-error">{error}</p>}
        </div>
      </div>
    )
  }

  if (authMode === 'reset') {
    return (
      <div className="auth-shell">
        <div className="auth-panel">
          <div className="auth-brand">
            <p className="auth-eyebrow">Reset Your Password</p>
            <h1>Create a new password</h1>
            <p>{resetInstructions}</p>
          </div>
          <form className="auth-form" onSubmit={handleResetPassword}>
            <input 
              type="password" 
              placeholder="New password (min 8 characters)" 
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)} 
              className="search-input" 
              required 
              minLength="8"
            />
            <input 
              type="text" 
              placeholder="Reset token" 
              value={resetToken}
              onChange={(e) => setResetToken(e.target.value)} 
              className="search-input" 
              required 
            />
            <button type="submit" className="add-btn" disabled={authLoading}>
              {authLoading ? 'Resetting...' : 'Reset password'}
            </button>
          </form>
          <button type="button" className="auth-switch" onClick={() => { setAuthMode('forgot'); setNewPassword(''); setResetToken(''); setError(null); }}>
            Back
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
          <div className="nav-scroll-wrap">
          <nav className="nav">
            <button className={`nav-btn ${activeTab === 'smsf-brief' ? 'active' : ''}`} onClick={() => setActiveTab('smsf-brief')} title="Dashboard"><span className="nav-icon">📊</span><span className="nav-label-text">Dashboard</span></button>
            <button className={`nav-btn ${activeTab === 'smsf-screener' ? 'active' : ''}`} onClick={() => setActiveTab('smsf-screener')} title="Screener"><span className="nav-icon">🔍</span><span className="nav-label-text">Screener</span></button>
            <button className={`nav-btn ${activeTab === 'smsf-portfolio' ? 'active' : ''}`} onClick={() => setActiveTab('smsf-portfolio')} title="Portfolio"><span className="nav-icon">💼</span><span className="nav-label-text">Portfolio</span></button>
            <button className={`nav-btn ${activeTab === 'smsf-wealth' ? 'active' : ''}`} onClick={() => setActiveTab('smsf-wealth')} title="Wealth"><span className="nav-icon">📈</span><span className="nav-label-text">Wealth</span></button>
            <button className={`nav-btn ${activeTab === 'smsf' ? 'active' : ''}`} onClick={() => setActiveTab('smsf')} title="SMSF Dashboard (legacy)"><span className="nav-icon">🏠</span><span className="nav-label-text">Legacy SMSF</span></button>
            <button className={`nav-btn ${activeTab === 'markets' ? 'active' : ''}`} onClick={() => setActiveTab('markets')} title="Global Markets"><span className="nav-icon">🌍</span><span className="nav-label-text">Markets</span></button>
            <button className={`nav-btn ${activeTab === 'analyze' ? 'active' : ''}`} onClick={() => setActiveTab('analyze')} title="Analyze"><span className="nav-icon">🔍</span><span className="nav-label-text">Analyze</span></button>
            <div style={{ display: 'flex', gap: 4, flexShrink: 0 }} className="market-selector-nav">
              {['AU'].map(m => (
                <button key={m}
                  className={`nav-btn ${preferredMarket === m ? 'active' : ''}`}
                  onClick={() => updateMarket(m)}
                  title={EXCHANGE_LABELS[m]}>
                  <span className="nav-icon">{MARKET_FLAGS[m]}</span><span className="nav-label-text">ASX</span>
                </button>
              ))}
            </div>
            <span className="nav-user">{user?.email}</span>
            <button className="nav-btn" style={{ flexShrink: 0 }} onClick={logout} title="Logout"><span className="nav-icon">🚪</span><span className="nav-label-text">Logout</span></button>
          </nav>
          </div>
        </div>
      </header>

      <main className="main">
        {/* ── SMSF UI Uplift (Fix 33): new screens, legacy intact ── */}
        {['smsf-brief', 'smsf-screener', 'smsf-portfolio', 'smsf-wealth'].includes(activeTab) && (
          <SmsfUplift screen={activeTab} token={token}
            onRunScan={() => setActiveTab('smsf-screener')} />
        )}
        {/* AI Weekly Banner */}
        {aiBanner?.summary && activeTab === 'markets' && (
          <div style={{ background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)', border: '1px solid #0f3460', borderRadius: 8, padding: '16px 20px', marginBottom: 16, color: '#e0e0e0' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <span style={{ fontSize: 20 }}>🤖</span>
              <strong style={{ color: '#00d2ff' }}>AI Weekly Insight</strong>
              <span style={{ fontSize: 12, color: '#888', marginLeft: 'auto' }}>{aiBanner.market} • {aiBanner.generated_at?.slice(0, 10)}</span>
            </div>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6 }}>{aiBanner.summary}</p>
          </div>
        )}
        <div style={{ display: activeTab === 'smsf' ? 'block' : 'none' }}>
          <SmsfTab />
        </div>
        {activeTab === 'markets' && <GlobalMarketsTab />}
        {activeTab === 'candidates' && (
            <>
            <section className="tab-grid">
              <div className="panel">
                <div className="section-header"><h2>Top ASX Set (Default)</h2></div>
                <p className="section-note">Preloaded top ASX names from Broad Market context. Click Add to track directly.</p>
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
              <p className="section-note">Sorted by composite score using probability of return &ge;5%, trend, quality, regime fit, and liquidity. Signals are locked daily &mdash; the same prediction holds for the full trading day.</p>
              {rankLoading ? <p>Ranking symbols...</p> : (
                <div className="table-wrap"><table className="data-table"><thead><tr><th>Keep</th><th>Symbol</th><th>P(&ge;5%)</th><th>Expected 3M</th><th>Trend</th><th>Score</th></tr></thead><tbody>
                  {rankedItems.map(item => {
                    const warningLabels = {
                      volume_divergence: 'Price jumped without volume support — spike may not hold.',
                      drawdown_severe: `Elevated downside risk detected (~${item.warning_message?.match(/\d+/)?.[0] ?? '?'}%). Consider position sizing carefully.`,
                      drawdown_moderate: 'Moderate downside volatility observed. Monitor closely.',
                      extreme_projection: 'Model output was unusually large and has been capped. Treat with caution.',
                      overbought: 'RSI above 70 — the stock may be overbought and due for a pullback.',
                    }
                    const friendlyWarning = warningLabels[item.warning_type] || item.warning_message || 'Elevated risk detected. Review before trading.'
                    return (
                      <tr key={item.symbol}>
                        <td><input type="checkbox" checked={!!selectedSymbols[item.symbol]} onChange={e => setSelectedSymbols(prev => ({ ...prev, [item.symbol]: e.target.checked }))} /></td>
                        <td>
                          <strong>{item.symbol}</strong>
                          {item.high_volatility_warning && (
                            <span title={friendlyWarning} style={{marginLeft: '6px', fontSize: '14px', cursor: 'help'}}>⚠️</span>
                          )}
                        </td>
                        <td>{item.prob_ge_5pct.toFixed(2)}%</td>
                        <td className={item.expected_return_3m_pct >= 0 ? 'positive' : 'negative'}>{item.expected_return_3m_pct.toFixed(2)}%</td>
                        <td>
                          <span style={pillStyle(item.trend)}>{item.trend}</span>
                          {item.streak_label && (
                            <div style={{fontSize: '11px', color: 'var(--ig-muted, #666)', marginTop: '3px', fontWeight: 'normal'}}>{item.streak_label}</div>
                          )}
                        </td>
                        <td>{item.score.toFixed(2)}</td>
                      </tr>
                    )
                  })}
                </tbody></table></div>
              )}
            </section>

            </>
        )}


        {activeTab === 'analyze' && (
          <>
            <section className="panel">
              <div className="section-header"><h2>Individual Share Analysis</h2></div>
              <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
                <select
                  value={analyzeMarket}
                  onChange={e => setAnalyzeMarket(e.target.value)}
                  style={{ ...inputStyle, width: 150, fontSize: 15 }}
                >
                  <option value="AU">🇦🇺 ASX (AU)</option>
                  <option value="US">🇺🇸 NASDAQ (US)</option>
                  <option value="IN">🇮🇳 BSE/NSE (IN)</option>
                </select>
                <input style={{ ...inputStyle, fontSize: 16, flex: 1 }} type="text"
                  placeholder={analyzeMarket === 'AU' ? 'e.g. BHP, SUN, PMGOLD, NDQ' : analyzeMarket === 'US' ? 'e.g. AAPL, MSFT, NVDA' : 'e.g. RELIANCE, TCS, INFY'}
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
                              plugins: { legend: { display: true, labels: { color: 'var(--ig-medium)' } }, tooltip: { backgroundColor: 'rgba(0,0,0,0.85)', titleColor: 'var(--bg-secondary)', bodyColor: '#ccc', borderColor: '#333', borderWidth: 1, callbacks: { label: ctx => `${ctx.dataset.label}: $${ctx.parsed.y.toFixed(2)}` } } },
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
                      
                      <div style={{ background:'var(--bg-secondary)1f2', borderLeft:'4px solid #e11d48', padding:'12px 16px', marginBottom:16, borderRadius:'0 6px 6px 0' }}>
                        <div style={{ display:'flex', alignItems:'center', gap:8, color:'#e11d48', fontWeight:700, marginBottom:4 }}>
                          <span>🛑 STRICT RISK MANAGEMENT</span>
                        </div>
                        <div style={{ fontSize:13, color:'#881337', lineHeight:1.5 }}>
                          Do not fall in love with the AI narrative. If you enter this trade, the algorithmic model <strong>REQUIRES</strong> a strict stop loss to maintain its mathematical edge:<br/>
                          • Large/Mid Cap (&gt;$5M volume): <strong>-5.0% Stop</strong><br/>
                          • Small Cap (&lt;$5M volume): <strong>-6.0% Stop</strong><br/>
                          If it breaches these levels, exit immediately.
                        </div>
                      </div>

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

        {activeTab === 'weekly' && (
          <>
            <section className="panel">
              <div className="section-header">
                <h2>📅 Weekly Stock Predictions — {EXCHANGE_LABELS[preferredMarket]}</h2>
                <button style={btnGreen} onClick={generateWeekly} disabled={weeklyLoading}>
                  {weeklyLoading ? 'Generating...' : '🔄 Generate Fresh'}
                </button>
              </div>
              {weeklyLoading && <p>Loading weekly digest...</p>}
              {!weeklyLoading && !weeklyData?.picks?.length && (
                <p className="section-note">No weekly digest available yet. Click "Generate Fresh" to create one for {EXCHANGE_LABELS[preferredMarket]}.</p>
              )}
              {weeklyData?.picks?.length > 0 && (
                <>
                  {weeklyData.llm_summary && (
                    <div style={{ background: 'var(--ig-light-grey)', border: '1px solid var(--ig-border)', borderRadius: 6, padding: 14, marginBottom: 16 }}>
                      <strong style={{ color: 'var(--ig-red)' }}>AI Summary:</strong>
                      <p style={{ margin: '8px 0 0 0', lineHeight: 1.7 }}>{weeklyData.llm_summary}</p>
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                    {['all', 'large_cap', 'mid_cap', 'small_cap'].map(cap => (
                      <button key={cap} className={`nav-btn ${weeklyCap === cap ? 'active' : ''}`}
                        onClick={() => setWeeklyCap(cap)} style={{ padding: '6px 14px', fontSize: 13 }}>
                        {cap === 'all' ? 'All' : cap.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}
                      </button>
                    ))}
                  </div>
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead><tr><th>Rank</th><th>Symbol</th><th>Name</th><th>Cap</th><th>Score</th><th>P(≥5%)</th><th>Expected 3M</th><th>Trend</th></tr></thead>
                      <tbody>
                        {weeklyData.picks
                          .filter(p => weeklyCap === 'all' || p.cap_tier === weeklyCap)
                          .map((p, i) => (
                            <tr key={p.symbol}>
                              <td>{i + 1}</td>
                              <td>
                                <strong>{p.symbol}</strong>
                                {p.high_volatility_warning && (
                                  <span title={p.warning_message || "Highly Volatile/Overbought Anomaly"} style={{marginLeft: '6px', fontSize: '14px', cursor: 'help'}}>⚠️</span>
                                )}
                              </td>
                              <td>{p.name || '-'}</td>
                              <td><span style={{ padding: '2px 8px', borderRadius: 10, fontSize: 11, background: p.cap_tier === 'large_cap' ? '#0066cc22' : p.cap_tier === 'mid_cap' ? '#cc660022' : '#00cc6622', color: p.cap_tier === 'large_cap' ? '#0066cc' : p.cap_tier === 'mid_cap' ? '#cc6600' : '#00cc66' }}>
                                {p.cap_tier?.replace('_', ' ')}</span></td>
                              <td><strong>{p.score?.toFixed(2)}</strong></td>
                              <td>{p.prob_ge_5pct?.toFixed(1)}%</td>
                              <td style={{ color: (p.expected_return_3m_pct ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>
                                {p.expected_return_3m_pct?.toFixed(2)}%</td>
                              <td style={{ color: p.trend === 'bullish' ? 'var(--ig-gain)' : p.trend === 'bearish' ? 'var(--ig-red)' : '#d4ac0d' }}>
                                {p.trend?.toUpperCase()}</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="section-note" style={{ marginTop: 10 }}>
                    Generated: {weeklyData.generated_at?.slice(0, 16)?.replace('T', ' ')} • Week: {weeklyData.week_label}
                  </p>
                </>
              )}
            </section>

            <section className="panel">
              <div className="section-header">
                <h2>📊 Sector Breakdown</h2>
              </div>
              {weeklyData?.picks_by_sector && Object.keys(weeklyData.picks_by_sector).length > 0 ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
                  {Object.entries(weeklyData.picks_by_sector).map(([sector, data]) => (
                    <div key={sector} style={cardStyle}>
                      <strong style={{ fontSize: 13, color: 'var(--ig-red)', textTransform: 'uppercase', letterSpacing: 1 }}>{sector.replace('_',' ')}</strong>
                      {data.picks?.length > 0 && (
                        <div style={{ marginTop: 8 }}>
                          {data.picks.slice(0, 3).map((p, i) => (
                            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', borderBottom: '1px solid var(--ig-border)' }}>
                              <strong style={{ fontSize: 13 }}>{p.symbol}</strong>
                              <span style={{ fontSize: 12, color: (p.expected_return_3m_pct ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>
                                {p.expected_return_3m_pct?.toFixed(1)}%
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                      {data.ai_summary && (
                        <p style={{ fontSize: 11, color: 'var(--ig-medium)', marginTop: 6, marginBottom: 0, lineHeight: 1.5 }}>
                          {data.ai_summary.slice(0, 80)}{data.ai_summary.length > 80 ? '...' : ''}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
                  {weeklySectors?.sectors && Object.entries(weeklySectors.sectors).map(([sector, tickers]) => (
                    <div key={sector} style={{ ...cardStyle, textAlign: 'center' }}>
                      <strong style={{ fontSize: 13, color: 'var(--ig-red)', textTransform: 'uppercase', letterSpacing: 1 }}>{sector}</strong>
                      <div style={{ fontSize: 11, color: 'var(--ig-medium)', marginTop: 8 }}>
                        {Array.isArray(tickers) ? tickers.slice(0, 4).join(', ') : Object.keys(tickers).slice(0, 4).join(', ')}
                      </div>
                    </div>
                  ))}
                  {!weeklySectors?.sectors && <p className="section-note">Generate weekly picks to see sector breakdown.</p>}
                </div>
              )}
            </section>
          </>
        )}

        {activeTab === 'crypto' && (
          <>
            <section className="stats-grid">
              {cryptoMarket?.global_stats && (
                <>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Total Market Cap</span><span className="stat-value">${(cryptoMarket.global_stats.total_market_cap / 1e12).toFixed(2)}T</span></div></div>
                  <div className="stat-card green"><div className="stat-content"><span className="stat-title">24h Volume</span><span className="stat-value">${(cryptoMarket.global_stats.total_volume / 1e9).toFixed(1)}B</span></div></div>
                  <div className="stat-card red"><div className="stat-content"><span className="stat-title">BTC Dominance</span><span className="stat-value">{cryptoMarket.global_stats.btc_dominance?.toFixed(1)}%</span></div></div>
                  <div className="stat-card blue"><div className="stat-content"><span className="stat-title">Fear & Greed</span><span className="stat-value">{cryptoMarket.fear_greed?.value ?? 'N/A'} — {cryptoMarket.fear_greed?.label ?? ''}</span></div></div>
                </>
              )}
            </section>

            <section className="panel">
              <div className="section-header">
                <h2>🔍 Search Crypto</h2>
                <button style={btnGreen} onClick={fetchCryptoMarket} disabled={cryptoLoading}>
                  {cryptoLoading ? 'Loading...' : '🔄 Refresh Market'}
                </button>
              </div>
              <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
                <input style={inputStyle} type="text" placeholder="Search coins (e.g., solana, cardano)"
                  value={cryptoSearch} onChange={e => setCryptoSearch(e.target.value)}
                  onKeyPress={e => e.key === 'Enter' && searchCrypto()} />
                <button onClick={searchCrypto} style={btnGreen}>Search</button>
              </div>
              {cryptoSearchResults.length > 0 && (
                <div className="chips-grid" style={{ marginBottom: 16 }}>
                  {cryptoSearchResults.slice(0, 12).map(c => (
                    <div key={c.id} className="chip-card" style={{ cursor: 'pointer' }} onClick={() => fetchCryptoDetail(c.id)}>
                      <div>
                        <strong>{c.symbol?.toUpperCase()}</strong>
                        <p style={{ fontSize: 12 }}>{c.name}</p>
                      </div>
                      <button className="add-btn small" onClick={(e) => { e.stopPropagation(); addCryptoWatchlist(c.id) }}>★</button>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel">
              <div className="section-header"><h2>📈 Top Coins</h2></div>
              {cryptoLoading && <p>Loading market data...</p>}
              {cryptoMarket?.coins?.length > 0 && (
                <div className="table-wrap">
                  <table className="data-table">
                    <thead><tr><th>#</th><th>Coin</th><th>Price</th><th>24h</th><th>7d</th><th>Market Cap</th><th>Actions</th></tr></thead>
                    <tbody>
                      {cryptoMarket.coins.map((c, i) => (
                        <tr key={c.id || i}>
                          <td>{i + 1}</td>
                          <td style={{ cursor: 'pointer', color: 'var(--ig-red)' }} onClick={() => fetchCryptoDetail(c.id)}>
                            <strong>{c.symbol?.toUpperCase()}</strong> <span style={{ color: 'var(--ig-medium)', fontSize: 12 }}>{c.name}</span>
                          </td>
                          <td>${c.current_price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</td>
                          <td style={{ color: (c.price_change_24h_pct ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>
                            {c.price_change_24h_pct?.toFixed(2)}%</td>
                          <td style={{ color: (c.price_change_7d_pct ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>
                            {c.price_change_7d_pct?.toFixed(2)}%</td>
                          <td>${(c.market_cap / 1e9)?.toFixed(2)}B</td>
                          <td>
                            <button className="add-btn small" onClick={() => addCryptoWatchlist(c.id)} title="Add to watchlist">★</button>
                            <button className="add-btn small" style={{ marginLeft: 4 }} onClick={() => { fetchCryptoDetail(c.id) }}>View</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {cryptoWatchlist.length > 0 && (
              <section className="panel">
                <div className="section-header"><h2>⭐ Watchlist</h2></div>
                <div className="chips-grid">
                  {cryptoWatchlist.map(c => (
                    <div key={c.coin_id} className="chip-card">
                      <div style={{ cursor: 'pointer' }} onClick={() => fetchCryptoDetail(c.coin_id)}>
                        <strong>{c.coin_id}</strong>
                      </div>
                      <button className="add-btn small" style={{ background: 'var(--ig-red)', color: 'white' }}
                        onClick={() => removeCryptoWatchlist(c.coin_id)}>✕</button>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {cryptoDetail && (
              <section className="panel">
                <div className="section-header">
                  <h2>🪙 {cryptoDetail.name} ({cryptoDetail.symbol?.toUpperCase()})</h2>
                  <button style={btnGreen} onClick={() => analyzeCrypto(cryptoDetail.id)} disabled={cryptoAnalysisLoading}>
                    {cryptoAnalysisLoading ? 'Analyzing...' : '🤖 AI Analysis'}
                  </button>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>Price</span><br/><strong style={{ fontSize: 22 }}>${cryptoDetail.market_data?.current_price?.toLocaleString()}</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>24h Change</span><br/><strong style={{ fontSize: 22, color: (cryptoDetail.market_data?.price_change_24h ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>{cryptoDetail.market_data?.price_change_24h?.toFixed(2)}%</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>Market Cap</span><br/><strong>${(cryptoDetail.market_data?.market_cap / 1e9)?.toFixed(2)}B</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>ATH</span><br/><strong>${cryptoDetail.market_data?.ath?.toLocaleString()}</strong><br/><small style={{ color: 'var(--ig-red)' }}>{cryptoDetail.market_data?.ath_change?.toFixed(1)}% from ATH</small></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>24h Volume</span><br/><strong>${(cryptoDetail.market_data?.total_volume / 1e9)?.toFixed(2)}B</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>Rank</span><br/><strong>#{cryptoDetail.market_cap_rank}</strong></div>
                </div>
                {cryptoDetail.description && (
                  <div style={{ ...subPanelStyle, marginTop: 16 }}>
                    <p style={{ lineHeight: 1.7, fontSize: 14, maxHeight: 120, overflow: 'hidden' }}>{cryptoDetail.description}</p>
                  </div>
                )}
                {cryptoAnalysis?.analysis && (
                  <div style={{ ...subPanelStyle, marginTop: 16, borderLeft: '3px solid var(--ig-gain)' }}>
                    <strong style={{ color: 'var(--ig-gain)' }}>🤖 AI Analysis</strong>
                    <p style={{ lineHeight: 1.8, marginTop: 8 }}>{cryptoAnalysis.analysis}</p>
                  </div>
                )}
              </section>
            )}
          </>
        )}

        {activeTab === 'etf' && (
          <>
            <section className="panel">
              <div className="section-header">
                <h2>🏦 ETF Explorer — {EXCHANGE_LABELS[preferredMarket]}</h2>
                <button style={btnGreen} onClick={fetchEtfList} disabled={etfLoading}>
                  {etfLoading ? 'Loading...' : '🔄 Refresh'}
                </button>
              </div>

              {/* ETF Search */}
              <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
                <input style={inputStyle} type="text" placeholder={`Search ETFs (e.g., ${preferredMarket === 'AU' ? 'IOZ, STW' : preferredMarket === 'IN' ? 'NIFTYBEES, GOLDBEES' : 'SPY, QQQ'})`}
                  value={etfSearch} onChange={e => setEtfSearch(e.target.value)} />
              </div>

              {etfLoading && <p>Loading ETF data...</p>}

              {etfList?.etfs && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14 }}>
                  {Object.entries(etfList.etfs)
                    .filter(([cat]) => !etfSearch || cat.toLowerCase().includes(etfSearch.toLowerCase()))
                    .map(([category, funds]) => (
                      <div key={category} style={cardStyle}>
                        <strong style={{ fontSize: 13, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--ig-red)' }}>{category.replace(/_/g, ' ')}</strong>
                        <div style={{ marginTop: 10 }}>
                          {funds.map(f => (
                            <div key={f.ticker} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--ig-border)' }}>
                              <div>
                                <strong style={{ cursor: 'pointer', color: 'var(--ig-dark)' }} onClick={() => fetchEtfDetail(f.ticker)}>{f.ticker}</strong>
                                <span style={{ fontSize: 12, color: 'var(--ig-medium)', display: 'block' }}>{f.name}</span>
                              </div>
                              <button className="add-btn small" onClick={() => fetchEtfDetail(f.ticker)}>View</button>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                </div>
              )}
            </section>

            {etfDetail && (
              <section className="panel">
                <div className="section-header">
                  <h2>📊 {etfDetail.ticker} — {etfDetail.name}</h2>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>Price</span><br/><strong style={{ fontSize: 20 }}>{formatCurrency(etfDetail.current_price, preferredMarket)}</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>1Y Return</span><br/><strong style={{ fontSize: 20, color: (etfDetail.one_year_return_pct ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>{etfDetail.one_year_return_pct?.toFixed(2)}%</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>YTD Return</span><br/><strong style={{ color: (etfDetail.ytd_return_pct ?? 0) >= 0 ? 'var(--ig-gain)' : 'var(--ig-red)' }}>{etfDetail.ytd_return_pct?.toFixed(2)}%</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>52W High</span><br/><strong>{formatCurrency(etfDetail['52_week_high'], preferredMarket)}</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>Expense Ratio</span><br/><strong>{etfDetail.expense_ratio != null ? (etfDetail.expense_ratio * 100).toFixed(2) + '%' : 'N/A'}</strong></div>
                  <div style={cardStyle}><span style={{ color: 'var(--ig-medium)' }}>Dividend Yield</span><br/><strong>{etfDetail.dividend_yield != null ? (etfDetail.dividend_yield * 100).toFixed(2) + '%' : 'N/A'}</strong></div>
                </div>
                <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {etfDetail.category && <span style={{ padding: '3px 12px', borderRadius: 10, background: 'var(--ig-light-grey)', border: '1px solid var(--ig-border)', fontSize: 12 }}>{etfDetail.category}</span>}
                  {etfDetail.exchange && <span style={{ padding: '3px 12px', borderRadius: 10, background: 'var(--ig-light-grey)', border: '1px solid var(--ig-border)', fontSize: 12 }}>{etfDetail.exchange}</span>}
                </div>
              </section>
            )}

            <section className="panel">
              <div className="section-header"><h2>⚖️ Compare ETFs</h2></div>
              <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
                <input style={inputStyle} type="text" placeholder={`Tickers separated by commas (e.g., ${preferredMarket === 'IN' ? 'NIFTYBEES.NS, GOLDBEES.NS' : preferredMarket === 'AU' ? 'IOZ.AX, STW.AX' : 'SPY, QQQ'})`}
                  value={etfCompareTickers} onChange={e => setEtfCompareTickers(e.target.value)} />
                <button style={btnGreen} onClick={compareEtfs} disabled={etfCompareLoading}>
                  {etfCompareLoading ? 'Comparing...' : 'Compare'}
                </button>
              </div>
              {etfCompare?.comparison?.length > 0 && (
                <div className="table-wrap">
                  <table className="data-table">
                    <thead><tr><th>Ticker</th><th>Name</th><th>Price</th><th>Expense Ratio</th><th>Dividend Yield</th><th>Total Assets</th></tr></thead>
                    <tbody>
                      {etfCompare.comparison.map(e => (
                        <tr key={e.ticker}>
                          <td><strong>{e.ticker}</strong></td>
                          <td style={{ fontSize: 12 }}>{e.name}</td>
                          <td>{formatCurrency(e.current_price, preferredMarket)}</td>
                          <td>{e.expense_ratio != null ? (e.expense_ratio * 100).toFixed(2) + '%' : 'N/A'}</td>
                          <td>{e.dividend_yield != null ? (e.dividend_yield * 100).toFixed(2) + '%' : 'N/A'}</td>
                          <td>{e.total_assets != null ? '$' + (e.total_assets / 1e9).toFixed(2) + 'B' : 'N/A'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {etfSpotlight?.theme && (
              <section className="panel">
                <div className="section-header"><h2>💡 ETF Spotlight — {etfSpotlight.theme}</h2></div>
                <div style={{ background: 'var(--ig-light-grey)', border: '1px solid var(--ig-border)', borderRadius: 6, padding: 16, lineHeight: 1.7 }}>
                  <p style={{ marginTop: 0 }}>{etfSpotlight.commentary}</p>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 12 }}>
                    {etfSpotlight.au_etf && (
                      <div style={cardStyle}>
                        <span style={{ fontSize: 11, color: 'var(--ig-medium)', display: 'block' }}>🇦🇺 AU</span>
                        <strong style={{ cursor: 'pointer', color: 'var(--ig-red)' }} onClick={() => fetchEtfDetail(etfSpotlight.au_etf)}>{etfSpotlight.au_etf}</strong>
                      </div>
                    )}
                    {etfSpotlight.us_etf && (
                      <div style={cardStyle}>
                        <span style={{ fontSize: 11, color: 'var(--ig-medium)', display: 'block' }}>🇺🇸 US</span>
                        <strong style={{ cursor: 'pointer', color: 'var(--ig-red)' }} onClick={() => fetchEtfDetail(etfSpotlight.us_etf)}>{etfSpotlight.us_etf}</strong>
                      </div>
                    )}
                    {etfSpotlight.in_etf && (
                      <div style={cardStyle}>
                        <span style={{ fontSize: 11, color: 'var(--ig-medium)', display: 'block' }}>🇮🇳 IN</span>
                        <strong style={{ cursor: 'pointer', color: 'var(--ig-red)' }} onClick={() => fetchEtfDetail(etfSpotlight.in_etf)}>{etfSpotlight.in_etf}</strong>
                      </div>
                    )}
                  </div>
                </div>
              </section>

              )}

          </>
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

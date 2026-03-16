import React, { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import axios from 'axios'
import './App.css'

// Register Chart.js components
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
)

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'

function App() {
  const [shares, setShares] = useState([])
  const [selectedShare, setSelectedShare] = useState(null)
  const [shareDetails, setShareDetails] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchShares()
  }, [])

  const fetchShares = async () => {
    try {
      setLoading(true)
      const response = await axios.get(`${API_BASE}/shares`)
      setShares(response.data)
      setError(null)
    } catch (err) {
      setError('Failed to fetch shares. Make sure the backend is running.')
    } finally {
      setLoading(false)
    }
  }

  const fetchShareDetails = async (symbol) => {
    try {
      const response = await axios.get(`${API_BASE}/shares/${symbol}`)
      setShareDetails(response.data)
    } catch (err) {
      console.error('Failed to fetch share details:', err)
    }
  }

  const handleSearch = async (query) => {
    setSearchQuery(query)
    if (query.length > 0) {
      try {
        const response = await axios.get(`${API_BASE}/search?query=${query}`)
        setSearchResults(response.data)
      } catch (err) {
        console.error('Search failed:', err)
      }
    } else {
      setSearchResults([])
    }
  }

  const addShare = async (symbol) => {
    try {
      await axios.post(`${API_BASE}/shares`, null, { params: { symbol } })
      setShowAddModal(false)
      setSearchQuery('')
      setSearchResults([])
      fetchShares()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to add share')
    }
  }

  const removeShare = async (symbol) => {
    try {
      await axios.delete(`${API_BASE}/shares/${symbol}`)
      fetchShares()
      if (selectedShare === symbol) {
        setSelectedShare(null)
        setShareDetails(null)
      }
    } catch (err) {
      console.error('Failed to remove share:', err)
    }
  }

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-content">
          <div className="logo">
            <span className="logo-icon">📈</span>
            <h1>ASX Stock Predictor</h1>
          </div>
          <nav className="nav">
            <button className="nav-btn active">Dashboard</button>
            <button className="nav-btn">About</button>
          </nav>
        </div>
      </header>

      {/* Main Content */}
      <main className="main">
        {/* Stats Cards */}
        <section className="stats-section">
          <div className="stats-grid">
            <StatCard
              title="Total Shares"
              value={shares.length}
              icon="📊"
              color="blue"
            />
            <StatCard
              title="Best Performer"
              value={shares.length > 0 ? getBestPerformer(shares) : '-'}
              icon="🚀"
              color="green"
            />
            <StatCard
              title="Worst Performer"
              value={shares.length > 0 ? getWorstPerformer(shares) : '-'}
              icon="📉"
              color="red"
            />
            <StatCard
              title="Avg. Change"
              value={shares.length > 0 ? `${getAvgChange(shares).toFixed(2)}%` : '-'}
              icon="⚖️"
              color={shares.length > 0 && getAvgChange(shares) >= 0 ? 'green' : 'red'}
            />
          </div>
        </section>

        {/* Shares Grid */}
        <section className="shares-section">
          <div className="section-header">
            <h2>Your Shares</h2>
            <button 
              className="add-btn"
              onClick={() => setShowAddModal(true)}
            >
              <span>+</span> Add Share
            </button>
          </div>

          {loading ? (
            <div className="loading">
              <div className="spinner"></div>
              <p>Loading shares...</p>
            </div>
          ) : shares.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">📈</div>
              <h3>No shares yet</h3>
              <p>Add your first ASX share to start tracking and predicting prices</p>
              <button 
                className="add-btn"
                onClick={() => setShowAddModal(true)}
              >
                Add Your First Share
              </button>
            </div>
          ) : (
            <div className="shares-grid">
              {shares.map((share) => (
                <ShareCard
                  key={share.symbol}
                  share={share}
                  onClick={() => {
                    setSelectedShare(share.symbol)
                    fetchShareDetails(share.symbol)
                  }}
                  onDelete={() => removeShare(share.symbol)}
                />
              ))}
            </div>
          )}
        </section>

        {/* Selected Share Details */}
        <AnimatePresence>
          {selectedShare && shareDetails && (
            <motion.section 
              className="details-section"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 20 }}
            >
              <div className="section-header">
                <h2>{shareDetails.symbol} - {shareDetails.name}</h2>
                <button 
                  className="close-btn"
                  onClick={() => {
                    setSelectedShare(null)
                    setShareDetails(null)
                  }}
                >
                  ✕
                </button>
              </div>

              <div className="details-grid">
                {/* Price Info */}
                <div className="detail-card">
                  <h3>Current Price</h3>
                  <div className="price-display">
                    <span className="price">${shareDetails.current_price.toFixed(2)}</span>
                    <span className={`change ${shareDetails.change_percent >= 0 ? 'positive' : 'negative'}`}>
                      {shareDetails.change_percent >= 0 ? '▲' : '▼'} {Math.abs(shareDetails.change_percent).toFixed(2)}%
                    </span>
                  </div>
                </div>

                {/* Prediction */}
                <div className="detail-card prediction-card">
                  <h3>3-Month Prediction</h3>
                  <div className="prediction-display">
                    <div className="pred-price">
                      <span className="label">Predicted</span>
                      <span className="value">${shareDetails.prediction_3m.predicted_price.toFixed(2)}</span>
                    </div>
                    <div className="pred-range">
                      <span className="label">Range</span>
                      <span className="range">
                        ${shareDetails.prediction_3m.confidence_low.toFixed(2)} - ${shareDetails.prediction_3m.confidence_high.toFixed(2)}
                      </span>
                    </div>
                    <div className={`pred-trend ${shareDetails.prediction_3m.trend}`}>
                      {shareDetails.prediction_3m.trend === 'bullish' ? '🐂' : shareDetails.prediction_3m.trend === 'bearish' ? '🐻' : '➡️'} 
                      {shareDetails.prediction_3m.trend.toUpperCase()}
                    </div>
                  </div>
                </div>

                {/* LLM Analysis */}
                <div className="detail-card analysis-card">
                  <h3>AI Analysis</h3>
                  <p className="llm-analysis">{shareDetails.prediction_3m.llm_analysis}</p>
                </div>
              </div>

              {/* Chart */}
              <div className="chart-container">
                <h3>Weekly Prediction vs Actual</h3>
                <WeeklyChart data={shareDetails.weekly_data} />
              </div>

              {/* Technical Indicators */}
              {shareDetails.technical_indicators && Object.keys(shareDetails.technical_indicators).length > 0 && (
                <div className="indicators-grid">
                  <h3>Technical Indicators</h3>
                  <div className="indicators">
                    {Object.entries(shareDetails.technical_indicators).map(([key, value]) => (
                      <div key={key} className="indicator-item">
                        <span className="indicator-key">{formatIndicatorKey(key)}</span>
                        <span className="indicator-value">
                          {typeof value === 'number' ? value.toFixed(2) : value}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </motion.section>
          )}
        </AnimatePresence>
      </main>

      {/* Add Share Modal */}
      <AnimatePresence>
        {showAddModal && (
          <motion.div 
            className="modal-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setShowAddModal(false)}
          >
            <motion.div 
              className="modal"
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="modal-header">
                <h2>Add ASX Share</h2>
                <button 
                  className="close-btn"
                  onClick={() => setShowAddModal(false)}
                >
                  ✕
                </button>
              </div>
              <div className="modal-body">
                <input
                  type="text"
                  placeholder="Search for ASX shares (e.g., BHP, CBA)..."
                  value={searchQuery}
                  onChange={(e) => handleSearch(e.target.value)}
                  className="search-input"
                  autoFocus
                />
                <div className="search-results">
                  {searchResults.map((result) => (
                    <button
                      key={result.symbol}
                      className="search-result"
                      onClick={() => addShare(result.symbol)}
                    >
                      <span className="result-symbol">{result.symbol}</span>
                      <span className="result-name">{result.name}</span>
                    </button>
                  ))}
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Error Toast */}
      {error && (
        <div className="error-toast">
          <p>{error}</p>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}
    </div>
  )
}

// Helper Components

function StatCard({ title, value, icon, color }) {
  return (
    <motion.div 
      className={`stat-card ${color}`}
      whileHover={{ scale: 1.02 }}
    >
      <div className="stat-icon">{icon}</div>
      <div className="stat-content">
        <span className="stat-title">{title}</span>
        <span className="stat-value">{value}</span>
      </div>
    </motion.div>
  )
}

function ShareCard({ share, onClick, onDelete }) {
  const isPositive = share.change_percent >= 0

  return (
    <motion.div 
      className="share-card"
      whileHover={{ scale: 1.02, y: -4 }}
      onClick={onClick}
    >
      <button 
        className="delete-btn"
        onClick={(e) => {
          e.stopPropagation()
          onDelete()
        }}
      >
        ✕
      </button>
      <div className="share-header">
        <span className="share-symbol">{share.symbol}</span>
        <span className={`share-change ${isPositive ? 'positive' : 'negative'}`}>
          {isPositive ? '▲' : '▼'} {Math.abs(share.change_percent).toFixed(2)}%
        </span>
      </div>
      <div className="share-name">{share.name}</div>
      <div className="share-price">${share.current_price.toFixed(2)}</div>
      {share.weekly_data && share.weekly_data.length > 0 && (
        <div className="mini-chart">
          <MiniSparkline data={share.weekly_data} />
        </div>
      )}
    </motion.div>
  )
}

function MiniSparkline({ data }) {
  const chartData = {
    labels: data.map((_, i) => i),
    datasets: [{
      data: data.map(d => d.actual_price),
      borderColor: data[data.length - 1]?.actual_price >= data[0]?.actual_price ? '#3FB950' : '#F85149',
      borderWidth: 2,
      fill: false,
      tension: 0.4,
      pointRadius: 0,
    }]
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    scales: { x: { display: false }, y: { display: false } },
  }

  return <Line data={chartData} options={options} />
}

function WeeklyChart({ data }) {
  const chartData = {
    labels: data.map(d => d.date),
    datasets: [
      {
        label: 'Actual Price',
        data: data.map(d => d.actual_price),
        borderColor: '#58A6FF',
        backgroundColor: 'rgba(88, 166, 255, 0.1)',
        fill: true,
        tension: 0.4,
        pointRadius: 6,
        pointHoverRadius: 8,
      },
      {
        label: 'Predicted Price',
        data: data.map(d => d.predicted_price),
        borderColor: '#A371F7',
        backgroundColor: 'rgba(163, 113, 247, 0.1)',
        fill: true,
        tension: 0.4,
        pointRadius: 6,
        pointHoverRadius: 8,
      }
    ]
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top',
        labels: { color: '#E6EDF3', font: { family: 'Outfit' } }
      },
      tooltip: {
        backgroundColor: '#161B22',
        titleColor: '#E6EDF3',
        bodyColor: '#8B949E',
        borderColor: '#30363D',
        borderWidth: 1,
      }
    },
    scales: {
      x: {
        grid: { color: '#30363D' },
        ticks: { color: '#8B949E' }
      },
      y: {
        grid: { color: '#30363D' },
        ticks: { 
          color: '#8B949E',
          callback: (value) => `$${value.toFixed(2)}`
        }
      }
    }
  }

  return <Line data={chartData} options={options} />
}

// Helper Functions

function getBestPerformer(shares) {
  if (shares.length === 0) return '-'
  const best = shares.reduce((a, b) => a.change_percent > b.change_percent ? a : b)
  return `${best.symbol} (${best.change_percent.toFixed(2)}%)`
}

function getWorstPerformer(shares) {
  if (shares.length === 0) return '-'
  const worst = shares.reduce((a, b) => a.change_percent < b.change_percent ? a : b)
  return `${worst.symbol} (${worst.change_percent.toFixed(2)}%)`
}

function getAvgChange(shares) {
  if (shares.length === 0) return 0
  return shares.reduce((sum, s) => sum + s.change_percent, 0) / shares.length
}

function formatIndicatorKey(key) {
  const keyMap = {
    sma_20: 'SMA 20',
    sma_50: 'SMA 50',
    sma_200: 'SMA 200',
    rsi: 'RSI (14)',
    macd: 'MACD',
    macd_signal: 'MACD Signal',
    volatility: 'Volatility',
    momentum_20: 'Momentum (20d)',
    bb_upper: 'Bollinger Upper',
    bb_lower: 'Bollinger Lower',
  }
  return keyMap[key] || key.replace(/_/g, ' ').toUpperCase()
}

export default App

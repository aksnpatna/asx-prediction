import React, { useState, useEffect } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || '/api'

export default function GlobalMarketsTab() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchMarkets()
    const interval = setInterval(fetchMarkets, 300000) // 5 minutes refresh
    return () => clearInterval(interval)
  }, [])

  const fetchMarkets = async () => {
    try {
      const token = localStorage.getItem('asx_token')
      const { data } = await axios.get(`${API}/markets/global`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      setData(data)
      setError(null)
    } catch (e) {
      console.error(e)
      setError('Failed to load Global Markets data.')
    } finally {
      setLoading(false)
    }
  }

  if (loading) return <div className="loading-spinner" style={{ padding: 40, color: '#94a3b8', textAlign: 'center' }}>Loading Global Markets...</div>
  if (error) return <div className="error-banner" style={{ padding: 20, color: '#f87171', background: '#450a0a', textAlign: 'center' }}>{error}</div>
  if (!data) return null

  return (
    <div className="global-markets-dashboard" style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto', color: '#e2e8f0' }}>
      
      <div className="gm-header" style={{ marginBottom: 32, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '28px', fontWeight: 700, color: '#f8fafc' }}>Macro Rotation Tracker</h1>
          <p style={{ margin: '8px 0 0', color: '#94a3b8', fontSize: '14px' }}>Real-time overview of global capital flows across Equities, Commodities, and FX.</p>
        </div>
        <div style={{ background: '#1e293b', padding: '8px 16px', borderRadius: '20px', border: '1px solid #334155', fontSize: '12px', color: '#cbd5e1' }}>
          Live feed active
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
        <MarketSection title="Global Equities" items={data.Equities || []} icon="🌍" />
        <MarketSection title="Commodities" items={data.Commodities || []} icon="🛢️" />
        <MarketSection title="Currencies" items={data.Currencies || []} icon="💱" />
      </div>

    </div>
  )
}

function MarketSection({ title, items, icon }) {
  if (!items || items.length === 0) return null

  return (
    <section>
      <h2 style={{ fontSize: '18px', fontWeight: 600, margin: '0 0 16px 0', borderBottom: '1px solid #334155', paddingBottom: '12px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span>{icon}</span> {title}
      </h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '16px' }}>
        {items.map((item, idx) => (
          <MarketCard key={idx} item={item} />
        ))}
      </div>
    </section>
  )
}

function MarketCard({ item }) {
  const isDailyUp = item.daily_pct >= 0
  const isMonthUp = item.month_pct >= 0

  return (
    <div style={{
      background: 'linear-gradient(145deg, #1e293b, #0f172a)',
      borderRadius: '12px',
      padding: '16px',
      border: '1px solid #334155',
      boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
      transition: 'transform 0.2s',
      cursor: 'default'
    }}
    onMouseEnter={(e) => e.currentTarget.style.transform = 'translateY(-2px)'}
    onMouseLeave={(e) => e.currentTarget.style.transform = 'translateY(0)'}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
        <span style={{ fontWeight: 600, fontSize: '15px', color: '#f8fafc' }}>{item.name}</span>
        <span style={{ fontSize: '11px', color: '#64748b', background: '#0f172a', padding: '2px 6px', borderRadius: '4px' }}>{item.symbol.replace('^', '').replace('=F', '').replace('=X', '')}</span>
      </div>
      
      <div style={{ fontSize: '24px', fontWeight: 700, marginBottom: '16px', color: '#e2e8f0', display: 'flex', alignItems: 'baseline', gap: 4 }}>
        {item.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', paddingTop: '12px', borderTop: '1px solid #334155' }}>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ color: '#94a3b8', fontSize: '10px', textTransform: 'uppercase', marginBottom: '2px' }}>1 Day</span>
          <span style={{ color: isDailyUp ? '#34d399' : '#f87171', fontWeight: 600 }}>
            {isDailyUp ? '▲' : '▼'} {Math.abs(item.daily_pct).toFixed(2)}%
          </span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
          <span style={{ color: '#94a3b8', fontSize: '10px', textTransform: 'uppercase', marginBottom: '2px' }}>1 Month</span>
          <span style={{ color: isMonthUp ? '#34d399' : '#f87171', fontWeight: 600 }}>
             {Math.abs(item.month_pct).toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  )
}

import React, { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard'
import Customers from './components/Customers'
import Meetings from './components/Meetings'
import VoiceRecorder from './components/VoiceRecorder'
import AllLeads from './components/AllLeads'
import WhatsAppInbox from './components/WhatsAppInbox'
import BrokerSetup from './components/BrokerSetup'
import './App.css'

const API_BASE_URL = ''

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [token, setToken] = useState(localStorage.getItem('token'))
  const [brokerId, setBrokerId] = useState(localStorage.getItem('brokerId'))
  const [loginData, setLoginData] = useState({ identifier: '', password: '' })
  const [registerData, setRegisterData] = useState({ email: '', username: '', password: '' })
  const [isRegistering, setIsRegistering] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [setupComplete, setSetupComplete] = useState(null) // null = loading

  // Check setup status whenever token changes
  useEffect(() => {
    if (!token) { setSetupComplete(null); return }
    fetch(`${API_BASE_URL}/api/broker/profile`, {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then(r => r.json())
      .then(d => setSetupComplete(d.setup_complete === true))
      .catch(() => setSetupComplete(true)) // on error, don't block
  }, [token])

  const apiCall = async (endpoint, options = {}) => {
    const headers = { 'Content-Type': 'application/json', ...options.headers }
    if (token) headers['Authorization'] = `Bearer ${token}`
    const response = await fetch(`${API_BASE_URL}${endpoint}`, { ...options, headers })
    if (response.status === 401) { handleLogout(); throw new Error('Unauthorized') }
    return response
  }

  const handleRegister = async (e) => {
    e.preventDefault()
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/broker/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: registerData.email, username: registerData.username, password: registerData.password })
      })
      const data = await response.json()
      if (response.ok) {
        localStorage.setItem('token', data.access_token)
        localStorage.setItem('brokerId', data.broker_id)
        setToken(data.access_token)
        setBrokerId(data.broker_id)
        setSetupComplete(false) // New account → show setup wizard
      } else {
        setError(data.detail || 'Registration failed')
      }
    } catch (err) { setError('Network error: ' + err.message) }
  }

  const handleLogin = async (e) => {
    e.preventDefault()
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/broker/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier: loginData.identifier, password: loginData.password })
      })
      const data = await response.json()
      if (response.ok) {
        localStorage.setItem('token', data.access_token)
        localStorage.setItem('brokerId', data.broker_id)
        setToken(data.access_token)
        setBrokerId(data.broker_id)
        // setupComplete will be set by the useEffect above
      } else {
        setError(data.detail || 'Login failed')
      }
    } catch (err) { setError('Network error: ' + err.message) }
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('brokerId')
    setToken(null)
    setBrokerId(null)
    setSetupComplete(null)
  }

  // ── Not logged in ─────────────────────────────────────────────────────────
  if (!token) {
    return (
      <div className="auth-container">
        <div className="auth-card">
          <h1>Inbox Intelligence</h1>
          {error && <div className="alert alert-error">{error}</div>}
          {success && <div className="alert alert-success">{success}</div>}

          <div className="auth-tabs">
            <button className={`auth-tab ${!isRegistering ? 'active' : ''}`} onClick={() => setIsRegistering(false)}>Login</button>
            <button className={`auth-tab ${isRegistering ? 'active' : ''}`} onClick={() => setIsRegistering(true)}>Register</button>
          </div>

          {!isRegistering ? (
            <form onSubmit={handleLogin}>
              <div className="form-group">
                <label>Email or User ID</label>
                <input
                  type="text"
                  value={loginData.identifier}
                  onChange={(e) => setLoginData({ ...loginData, identifier: e.target.value })}
                  placeholder="you@email.com or your-broker-id"
                  autoComplete="username"
                  required
                />
              </div>
              <div className="form-group">
                <label>Password</label>
                <input
                  type="password"
                  value={loginData.password}
                  onChange={(e) => setLoginData({ ...loginData, password: e.target.value })}
                  required
                />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%' }}>Login</button>
            </form>
          ) : (
            <form onSubmit={handleRegister}>
              <div className="form-group">
                <label>Name</label>
                <input value={registerData.username} onChange={(e) => setRegisterData({ ...registerData, username: e.target.value })} placeholder="Your full name" required />
              </div>
              <div className="form-group">
                <label>Email</label>
                <input type="email" value={registerData.email} onChange={(e) => setRegisterData({ ...registerData, email: e.target.value })} placeholder="you@example.com" required />
              </div>
              <div className="form-group">
                <label>Password</label>
                <input type="password" value={registerData.password} onChange={(e) => setRegisterData({ ...registerData, password: e.target.value })} placeholder="Choose a password" required />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%' }}>Create Account</button>
            </form>
          )}
        </div>
      </div>
    )
  }

  // ── Checking setup status ─────────────────────────────────────────────────
  if (setupComplete === null) {
    return (
      <div style={{ minHeight: '100vh', background: '#0F172A', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: '#94A3B8', fontSize: 14 }}>Loading...</p>
      </div>
    )
  }

  // ── First-time setup wizard ───────────────────────────────────────────────
  if (!setupComplete) {
    return (
      <BrokerSetup
        apiCall={apiCall}
        brokerId={brokerId}
        onSetupComplete={() => setSetupComplete(true)}
      />
    )
  }

  // ── Main app ──────────────────────────────────────────────────────────────
  return (
    <div className="app">
      <header className="header">
        <div className="header-content">
          <div>
            <h1>Inbox Intelligence</h1>
            <p>ID: {brokerId}</p>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              onClick={() => setSetupComplete(false)}
              className="btn btn-secondary"
              style={{ fontSize: 11, padding: '5px 12px' }}
            >
              ⚙️ Setup
            </button>
            <button onClick={handleLogout} className="btn btn-secondary" style={{ fontSize: 12, padding: '6px 14px' }}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      <nav className="nav-tabs">
        <button className={`nav-tab ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>Dashboard</button>
        <button className={`nav-tab ${activeTab === 'whatsapp' ? 'active' : ''}`} onClick={() => setActiveTab('whatsapp')}>WhatsApp</button>
        <button className={`nav-tab ${activeTab === 'leads' ? 'active' : ''}`} onClick={() => setActiveTab('leads')}>Leads</button>
        <button className={`nav-tab ${activeTab === 'voice' ? 'active' : ''}`} onClick={() => setActiveTab('voice')}>Voice Recorder</button>
        <button className={`nav-tab ${activeTab === 'customers' ? 'active' : ''}`} onClick={() => setActiveTab('customers')}>Customers</button>
        <button className={`nav-tab ${activeTab === 'meetings' ? 'active' : ''}`} onClick={() => setActiveTab('meetings')}>Meetings</button>
        <button className={`nav-tab ${activeTab === 'leads' ? 'active' : ''}`} onClick={() => setActiveTab('leads')}>Leads</button>
      </nav>

      <div className="container">
        {error && <div className="alert alert-error">{error}</div>}
        {success && <div className="alert alert-success">{success}</div>}

        {activeTab === 'dashboard'  && <Dashboard token={token} apiCall={apiCall} brokerId={brokerId} />}
        {activeTab === 'leads'      && <AllLeads apiCall={apiCall} setError={setError} setSuccess={setSuccess} />}
        {activeTab === 'voice'      && <VoiceRecorder token={token} apiCall={apiCall} setError={setError} setSuccess={setSuccess} />}
        {activeTab === 'customers'  && <Customers token={token} apiCall={apiCall} setError={setError} setSuccess={setSuccess} />}
        {activeTab === 'meetings'   && <Meetings token={token} apiCall={apiCall} setError={setError} setSuccess={setSuccess} />}
        {activeTab === 'whatsapp'   && <WhatsAppInbox apiCall={apiCall} setError={setError} setSuccess={setSuccess} />}
      </div>
    </div>
  )
}

export default App

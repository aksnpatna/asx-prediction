import React, { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard'
import Customers from './components/Customers'
import Meetings from './components/Meetings'
import VoiceRecorder from './components/VoiceRecorder'
import AllLeads from './components/AllLeads'
import './App.css'

// API Configuration
// Use relative path /api which gets proxied through nginx to broker-backend:8001
const API_BASE_URL = ''

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [token, setToken] = useState(localStorage.getItem('token'))
  const [brokerId, setBrokerId] = useState(localStorage.getItem('brokerId'))
  const [showLoginModal, setShowLoginModal] = useState(!token)
  const [loginData, setLoginData] = useState({ email: '', password: '' })
  const [registerData, setRegisterData] = useState({ email: '', username: '', password: '', brokerId: '' })
  const [isRegistering, setIsRegistering] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const handleRegister = async (e) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    try {
      console.log('📝 Attempting registration for:', registerData.email)
      const response = await fetch(`${API_BASE_URL}/api/broker/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: registerData.email,
          username: registerData.username,
          password: registerData.password,
          broker_id: registerData.brokerId
        })
      })

      const data = await response.json()
      console.log('✅ Register response status:', response.status, 'Data:', data)

      if (response.ok) {
        console.log('🔐 Registration successful, token received')
        localStorage.setItem('token', data.access_token)
        localStorage.setItem('brokerId', data.broker_id)
        setToken(data.access_token)
        setBrokerId(data.broker_id)
        setShowLoginModal(false)
        setSuccess('Registration successful!')
        console.log('✨ Registration complete, modal closed')
        setTimeout(() => setSuccess(''), 3000)
      } else {
        const errorMsg = data.detail || 'Registration failed'
        console.error('❌ Registration failed:', errorMsg)
        setError(errorMsg)
      }
    } catch (err) {
      console.error('❌ Network/Parse error:', err)
      setError('Network error: ' + err.message)
    }
  }

  const handleLogin = async (e) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    try {
      console.log('📝 Attempting login for:', loginData.email)
      const response = await fetch(`${API_BASE_URL}/api/broker/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: loginData.email,
          password: loginData.password
        })
      })

      const data = await response.json()
      console.log('✅ Login response status:', response.status, 'Data:', data)

      if (response.ok) {
        console.log('🔐 Token received, storing in localStorage')
        localStorage.setItem('token', data.access_token)
        localStorage.setItem('brokerId', data.broker_id)
        setToken(data.access_token)
        setBrokerId(data.broker_id)
        setShowLoginModal(false)
        setSuccess('Login successful!')
        console.log('✨ Login complete, modal closed')
        setTimeout(() => setSuccess(''), 3000)
      } else {
        const errorMsg = data.detail || 'Login failed'
        console.error('❌ Login failed:', errorMsg)
        setError(errorMsg)
      }
    } catch (err) {
      console.error('❌ Network/Parse error:', err)
      setError('Network error: ' + err.message)
    }
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('brokerId')
    setToken(null)
    setBrokerId(null)
    setShowLoginModal(true)
    setShowLoginModal(true)
  }

  const apiCall = async (endpoint, options = {}) => {
    const headers = {
      'Content-Type': 'application/json',
      ...options.headers
    }

    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers
    })

    if (response.status === 401) {
      handleLogout()
      throw new Error('Unauthorized')
    }

    return response
  }

  if (!token) {
    return (
      <div className="auth-container">
        <div className="auth-card">
          <h1>Broker CRM</h1>
          {error && <div className="alert alert-error">{error}</div>}
          {success && <div className="alert alert-success">{success}</div>}

          <div className="auth-tabs">
            <button
              className={`auth-tab ${!isRegistering ? 'active' : ''}`}
              onClick={() => setIsRegistering(false)}
            >
              Login
            </button>
            <button
              className={`auth-tab ${isRegistering ? 'active' : ''}`}
              onClick={() => setIsRegistering(true)}
            >
              Register
            </button>
          </div>

          {!isRegistering ? (
            <form onSubmit={handleLogin}>
              <div className="form-group">
                <label>Email</label>
                <input
                  type="email"
                  value={loginData.email}
                  onChange={(e) => setLoginData({ ...loginData, email: e.target.value })}
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
              <button type="submit" className="btn btn-primary" style={{ width: '100%' }}>
                Login
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister}>
              <div className="form-group">
                <label>Broker ID</label>
                <input
                  value={registerData.brokerId}
                  onChange={(e) => setRegisterData({ ...registerData, brokerId: e.target.value })}
                  required
                />
              </div>
              <div className="form-group">
                <label>Username</label>
                <input
                  value={registerData.username}
                  onChange={(e) => setRegisterData({ ...registerData, username: e.target.value })}
                  required
                />
              </div>
              <div className="form-group">
                <label>Email</label>
                <input
                  type="email"
                  value={registerData.email}
                  onChange={(e) => setRegisterData({ ...registerData, email: e.target.value })}
                  required
                />
              </div>
              <div className="form-group">
                <label>Password</label>
                <input
                  type="password"
                  value={registerData.password}
                  onChange={(e) => setRegisterData({ ...registerData, password: e.target.value })}
                  required
                />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%' }}>
                Register
              </button>
            </form>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-content">
          <div>
            <h1>🎯 Broker CRM</h1>
            <p>Broker ID: {brokerId}</p>
          </div>
          <button onClick={handleLogout} className="btn btn-secondary">
            Logout
          </button>
        </div>
      </header>

      <nav className="nav-tabs">
        <button
          className={`nav-tab ${activeTab === 'dashboard' ? 'active' : ''}`}
          onClick={() => setActiveTab('dashboard')}
        >
          📊 Dashboard
        </button>
        <button
          className={`nav-tab ${activeTab === 'leads' ? 'active' : ''}`}
          onClick={() => setActiveTab('leads')}
        >
          📋 All Leads
        </button>
        <button
          className={`nav-tab ${activeTab === 'voice' ? 'active' : ''}`}
          onClick={() => setActiveTab('voice')}
        >
          🎤 Voice Recorder
        </button>
        <button
          className={`nav-tab ${activeTab === 'customers' ? 'active' : ''}`}
          onClick={() => setActiveTab('customers')}
        >
          👥 Customers
        </button>
        <button
          className={`nav-tab ${activeTab === 'meetings' ? 'active' : ''}`}
          onClick={() => setActiveTab('meetings')}
        >
          📅 Meetings
        </button>
      </nav>

      <div className="container">
        {error && <div className="alert alert-error">{error}</div>}
        {success && <div className="alert alert-success">{success}</div>}

        {activeTab === 'dashboard' && (
          <Dashboard token={token} apiCall={apiCall} brokerId={brokerId} />
        )}
        {activeTab === 'leads' && (
          <AllLeads apiCall={apiCall} setError={setError} setSuccess={setSuccess} />
        )}
        {activeTab === 'voice' && (
          <VoiceRecorder token={token} apiCall={apiCall} setError={setError} setSuccess={setSuccess} />
        )}
        {activeTab === 'customers' && (
          <Customers token={token} apiCall={apiCall} setError={setError} setSuccess={setSuccess} />
        )}
        {activeTab === 'meetings' && (
          <Meetings token={token} apiCall={apiCall} setError={setError} setSuccess={setSuccess} />
        )}
      </div>
    </div>
  )
}

export default App

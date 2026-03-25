import React, { useState, useEffect } from 'react'

function Dashboard({ token, apiCall, brokerId }) {
  const [meetings, setMeetings] = useState([])
  const [loading, setLoading] = useState(true)
  const [metrics, setMetrics] = useState({
    total_meetings: 0,
    total_customers: 0,
    meetings_this_month: 0,
    avg_meeting_duration: 0,
    busiest_day: 'N/A',
    upcoming_meetings: 0
  })
  const [csvFile, setCsvFile] = useState(null)
  const [csvLoading, setCsvLoading] = useState(false)
  const [csvMessage, setCsvMessage] = useState('')
  const [csvMatches, setCsvMatches] = useState(null)
  const [calendarStatus, setCalendarStatus] = useState(null)
  const [calendarLoading, setCalendarLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('overview') // overview, csv, transcripts, analytics, calendar
  const [upcomingLeads, setUpcomingLeads] = useState([])

  useEffect(() => {
    loadDashboardData()
  }, [])

  const loadDashboardData = async () => {
    try {
      setLoading(true)
      
      // Load metrics
      const metricsRes = await apiCall('/api/broker/dashboard/metrics')
      const metricsData = await metricsRes.json()
      setMetrics(metricsData)
      
      // Load today's meetings
      const meetingsRes = await apiCall('/api/broker/dashboard/today')
      const meetingsData = await meetingsRes.json()
      setMeetings(meetingsData.meetings || [])

      // Load upcoming leads (next 7 days)
      try {
        const leadsRes = await apiCall('/api/broker/leads/upcoming?days=7')
        const leadsData = await leadsRes.json()
        setUpcomingLeads(Array.isArray(leadsData) ? leadsData : [])
      } catch (e) {
        console.warn('Could not load upcoming leads:', e.message)
      }
    } catch (err) {
      console.error('Error loading dashboard:', err)
    } finally {
      setLoading(false)
    }
  }

  const loadCalendarStatus = async () => {
    try {
      setCalendarLoading(true)
      const response = await apiCall('/api/broker/calendar/status')
      const data = await response.json()
      setCalendarStatus(data)
    } catch (err) {
      console.error('Error loading calendar status:', err)
    } finally {
      setCalendarLoading(false)
    }
  }

  const handleCsvUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return

    setCsvLoading(true)
    setCsvMessage('')

    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch(
        `/api/broker/csv/import-smart?token=${token}`,
        {
          method: 'POST',
          body: formData
        }
      )

      const data = await response.json()
      if (response.ok) {
        setCsvMatches(data)
        const msg = `✅ Imported ${data.imported_records} records • ${data.matched_customers} matched • ${data.new_customers} new customers`
        setCsvMessage(msg)
        setCsvFile(null)
        setTimeout(() => loadDashboardData(), 1000) // Refresh metrics
      } else {
        setCsvMessage(`❌ Error: ${data.detail}`)
      }
    } catch (err) {
      setCsvMessage(`❌ Upload failed: ${err.message}`)
    } finally {
      setCsvLoading(false)
    }
  }

  return (
    <div>
      <h2>Broker Dashboard</h2>

      {/* Tab Navigation */}
      <div style={{ display: 'flex', gap: '1rem', marginBottom: '2rem', borderBottom: '2px solid #e0e0e0', paddingBottom: '1rem' }}>
        <button
          onClick={() => setActiveTab('overview')}
          style={{
            padding: '0.75rem 1.5rem',
            background: activeTab === 'overview' ? '#667eea' : 'transparent',
            color: activeTab === 'overview' ? 'white' : '#333',
            border: 'none',
            cursor: 'pointer',
            borderRadius: '4px 4px 0 0',
            fontWeight: activeTab === 'overview' ? 'bold' : 'normal'
          }}
        >
          📊 Overview
        </button>
        <button
          onClick={() => setActiveTab('csv')}
          style={{
            padding: '0.75rem 1.5rem',
            background: activeTab === 'csv' ? '#667eea' : 'transparent',
            color: activeTab === 'csv' ? 'white' : '#333',
            border: 'none',
            cursor: 'pointer',
            borderRadius: '4px 4px 0 0',
            fontWeight: activeTab === 'csv' ? 'bold' : 'normal'
          }}
        >
          📥 CSV Import
        </button>
        <button
          onClick={() => setActiveTab('transcripts')}
          style={{
            padding: '0.75rem 1.5rem',
            background: activeTab === 'transcripts' ? '#667eea' : 'transparent',
            color: activeTab === 'transcripts' ? 'white' : '#333',
            border: 'none',
            cursor: 'pointer',
            borderRadius: '4px 4px 0 0',
            fontWeight: activeTab === 'transcripts' ? 'bold' : 'normal'
          }}
        >
          📝 Transcripts
        </button>
        <button
          onClick={() => setActiveTab('analytics')}
          style={{
            padding: '0.75rem 1.5rem',
            background: activeTab === 'analytics' ? '#667eea' : 'transparent',
            color: activeTab === 'analytics' ? 'white' : '#333',
            border: 'none',
            cursor: 'pointer',
            borderRadius: '4px 4px 0 0',
            fontWeight: activeTab === 'analytics' ? 'bold' : 'normal'
          }}
        >
          📈 Analytics
        </button>
        <button
          onClick={() => {
            setActiveTab('calendar')
            loadCalendarStatus()
          }}
          style={{
            padding: '0.75rem 1.5rem',
            background: activeTab === 'calendar' ? '#667eea' : 'transparent',
            color: activeTab === 'calendar' ? 'white' : '#333',
            border: 'none',
            cursor: 'pointer',
            borderRadius: '4px 4px 0 0',
            fontWeight: activeTab === 'calendar' ? 'bold' : 'normal'
          }}
        >
          📅 Calendar
        </button>
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <div>
          {/* Metrics Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem', marginBottom: '2rem' }}>
            <div className="card" style={{ textAlign: 'center', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', padding: '2rem' }}>
              <h3 style={{ margin: '0.5rem 0', fontSize: '2.5rem' }}>{metrics.total_customers}</h3>
              <p style={{ margin: '0.5rem 0', fontSize: '1rem' }}>Total Customers</p>
            </div>
            <div className="card" style={{ textAlign: 'center', background: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)', color: 'white', padding: '2rem' }}>
              <h3 style={{ margin: '0.5rem 0', fontSize: '2.5rem' }}>{metrics.meetings_this_month}</h3>
              <p style={{ margin: '0.5rem 0', fontSize: '1rem' }}>Meetings This Month</p>
            </div>
            <div className="card" style={{ textAlign: 'center', background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', color: 'white', padding: '2rem' }}>
              <h3 style={{ margin: '0.5rem 0', fontSize: '2.5rem' }}>{metrics.upcoming_meetings}</h3>
              <p style={{ margin: '0.5rem 0', fontSize: '1rem' }}>Upcoming Meetings</p>
            </div>
          </div>

          {/* Additional Metrics */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem', marginBottom: '2rem' }}>
            <div className="card" style={{ padding: '1.5rem' }}>
              <p style={{ color: '#999', margin: '0 0 0.5rem 0' }}>Average Meeting Duration</p>
              <h3 style={{ color: '#667eea', margin: '0.5rem 0' }}>{metrics.avg_meeting_duration.toFixed(1)} min</h3>
            </div>
            <div className="card" style={{ padding: '1.5rem' }}>
              <p style={{ color: '#999', margin: '0 0 0.5rem 0' }}>Busiest Day</p>
              <h3 style={{ color: '#667eea', margin: '0.5rem 0' }}>{metrics.busiest_day}</h3>
            </div>
            <div className="card" style={{ padding: '1.5rem' }}>
              <p style={{ color: '#999', margin: '0 0 0.5rem 0' }}>Total Meetings</p>
              <h3 style={{ color: '#667eea', margin: '0.5rem 0' }}>{metrics.total_meetings}</h3>
            </div>
          </div>

          {/* 7-Day Upcoming Leads */}
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ margin: 0 }}>📅 Upcoming Follow-ups — Next 7 Days</h3>
              <span style={{ fontSize: '0.85rem', color: '#999' }}>{upcomingLeads.length} lead{upcomingLeads.length !== 1 ? 's' : ''}</span>
            </div>
            {loading ? (
              <div style={{ color: '#999', padding: '1rem 0' }}>Loading...</div>
            ) : upcomingLeads.length === 0 ? (
              <p style={{ color: '#999' }}>No upcoming follow-ups in the next 7 days.</p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Customer</th>
                      <th>Next Action</th>
                      <th>Follow Up</th>
                      <th>Priority</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {upcomingLeads.map(lead => (
                      <tr key={lead.id}>
                        <td style={{ fontWeight: 600 }}>{lead.customer_name}</td>
                        <td style={{ maxWidth: '300px', fontSize: '0.9rem', color: '#555' }}>
                          {lead.action_items && lead.action_items.length > 0
                            ? lead.action_items[0]
                            : lead.discussion_summary
                              ? lead.discussion_summary.substring(0, 100) + (lead.discussion_summary.length > 100 ? '…' : '')
                              : '—'}
                        </td>
                        <td style={{ whiteSpace: 'nowrap', color: '#667eea', fontWeight: 500 }}>
                          📅 {lead.next_meeting}
                        </td>
                        <td>
                          <span style={{
                            display: 'inline-block', padding: '0.2rem 0.6rem',
                            borderRadius: '10px', fontSize: '0.8rem', fontWeight: 600,
                            background: lead.priority === 'high' ? '#f8d7da' : lead.priority === 'low' ? '#e2e3e5' : '#fff3cd',
                            color: lead.priority === 'high' ? '#721c24' : lead.priority === 'low' ? '#383d41' : '#856404'
                          }}>{lead.priority}</span>
                        </td>
                        <td>
                          <span style={{
                            display: 'inline-block', padding: '0.2rem 0.6rem',
                            borderRadius: '10px', fontSize: '0.8rem', fontWeight: 600,
                            background: lead.status === 'completed' ? '#d4edda' : lead.status === 'in_progress' ? '#cce5ff' : '#fff3cd',
                            color: lead.status === 'completed' ? '#155724' : lead.status === 'in_progress' ? '#004085' : '#856404'
                          }}>{lead.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Today's Meetings */}
          <div className="card">
            <h3>Today's Meetings</h3>
            {loading ? (
              <div className="loading">Loading...</div>
            ) : meetings.length === 0 ? (
              <p>No meetings scheduled for today</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Title</th>
                    <th>Duration</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {meetings.map((meeting) => (
                    <tr key={meeting.id}>
                      <td>{new Date(meeting.scheduled_at).toLocaleTimeString()}</td>
                      <td>{meeting.title}</td>
                      <td>{meeting.duration_minutes} min</td>
                      <td>
                        <span className="status-badge success">Scheduled</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* CSV Import Tab */}
      {activeTab === 'csv' && (
        <div className="card">
          <h3>📥 Bulk Import from CSV</h3>
          <p style={{ color: '#666', marginBottom: '1rem' }}>
            Upload a CSV file to import customers and meetings in bulk.
          </p>
          
          <div style={{
            border: '2px dashed #667eea',
            borderRadius: '8px',
            padding: '2rem',
            textAlign: 'center',
            cursor: 'pointer',
            background: '#f8f9ff',
            marginBottom: '1rem'
          }}>
            <input
              type="file"
              accept=".csv"
              onChange={handleCsvUpload}
              style={{ display: 'none' }}
              id="csv-upload"
              disabled={csvLoading}
            />
            <label htmlFor="csv-upload" style={{ cursor: 'pointer' }}>
              <p style={{ margin: '0.5rem 0', fontSize: '1.2rem' }}>📁 Click to upload or drag and drop</p>
              <p style={{ margin: '0.5rem 0', color: '#999', fontSize: '0.9rem' }}>CSV format with columns: name, email, phone, company, title</p>
            </label>
          </div>

          {csvMessage && (
            <div style={{
              padding: '1rem',
              borderRadius: '4px',
              background: csvMessage.includes('❌') ? '#ffe0e0' : '#e0ffe0',
              color: csvMessage.includes('❌') ? '#d32f2f' : '#2e7d32',
              marginBottom: '1rem'
            }}>
              {csvMessage}
            </div>
          )}

          <div style={{ marginTop: '2rem', padding: '1rem', background: '#f5f5f5', borderRadius: '4px' }}>
            <h4>CSV Format Example:</h4>
            <pre style={{ overflow: 'auto', fontSize: '0.85rem' }}>
name,email,phone,company,title
John Doe,john@example.com,555-1234,ABC Corp,Q1 Planning
Jane Smith,jane@example.com,555-5678,XYZ Inc,Budget Review
            </pre>
          </div>
        </div>
      )}

      {/* Transcripts Tab */}
      {activeTab === 'transcripts' && (
        <TranscriptsList apiCall={apiCall} token={token} />
      )}

      {/* Analytics Tab */}
      {activeTab === 'analytics' && (
        <AnalyticsView apiCall={apiCall} token={token} meetings={meetings} />
      )}

      {/* Calendar Tab */}
      {activeTab === 'calendar' && (
        <CalendarSettings apiCall={apiCall} token={token} calendarStatus={calendarStatus} calendarLoading={calendarLoading} meetings={meetings} />
      )}
    </div>
  )
}

function TranscriptsList({ apiCall, token }) {
  const [transcripts, setTranscripts] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [selectedTranscript, setSelectedTranscript] = useState(null)

  useEffect(() => {
    loadTranscripts()
  }, [])

  const loadTranscripts = async () => {
    try {
      setLoading(true)
      const url = search
        ? `/api/broker/transcripts?search=${encodeURIComponent(search)}`
        : '/api/broker/transcripts'
      const response = await apiCall(url)
      const data = await response.json()
      setTranscripts(data)
    } catch (err) {
      console.error('Error loading transcripts:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = (e) => {
    setSearch(e.target.value)
    if (e.target.value.length > 2) {
      setTimeout(loadTranscripts, 500)
    }
  }

  if (selectedTranscript) {
    return (
      <div className="card">
        <button onClick={() => setSelectedTranscript(null)} style={{ marginBottom: '1rem', padding: '0.5rem 1rem', background: '#667eea', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
          ← Back to Transcripts
        </button>
        
        <h3>{selectedTranscript.title}</h3>
        <p style={{ color: '#999' }}>Created: {new Date(selectedTranscript.created_at).toLocaleString()}</p>

        <div style={{ marginTop: '1rem', padding: '1rem', background: '#f9f9f9', borderRadius: '4px', maxHeight: '400px', overflow: 'auto' }}>
          <h4>Transcript:</h4>
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', color: '#333' }}>
            {typeof selectedTranscript.transcript === 'string' && selectedTranscript.transcript.startsWith('{')
              ? selectedTranscript.transcript
              : selectedTranscript.transcript || 'No transcript available'}
          </p>
        </div>

        {selectedTranscript.action_items && selectedTranscript.action_items.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <h4>Action Items:</h4>
            <ul style={{ paddingLeft: '1.5rem' }}>
              {selectedTranscript.action_items.map((item, idx) => (
                <li key={idx} style={{ marginBottom: '0.5rem' }}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {selectedTranscript.key_points && selectedTranscript.key_points.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <h4>Key Points:</h4>
            <ul style={{ paddingLeft: '1.5rem' }}>
              {selectedTranscript.key_points.map((point, idx) => (
                <li key={idx} style={{ marginBottom: '0.5rem' }}>{point}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="card">
      <h3>📝 Meeting Transcripts</h3>
      
      <input
        type="text"
        placeholder="Search transcripts..."
        value={search}
        onChange={handleSearch}
        style={{
          width: '100%',
          padding: '0.75rem',
          marginBottom: '1rem',
          border: '1px solid #ddd',
          borderRadius: '4px',
          fontSize: '1rem'
        }}
      />

      {loading ? (
        <div className="loading">Loading transcripts...</div>
      ) : transcripts.length === 0 ? (
        <p>No transcripts found</p>
      ) : (
        <div>
          {transcripts.map((transcript) => (
            <div
              key={transcript.id}
              onClick={() => setSelectedTranscript(transcript)}
              style={{
                padding: '1rem',
                border: '1px solid #ddd',
                borderRadius: '4px',
                marginBottom: '0.5rem',
                cursor: 'pointer',
                background: '#f9f9f9',
                transition: 'all 0.3s'
              }}
              onMouseOver={(e) => e.currentTarget.style.boxShadow = '0 4px 8px rgba(0,0,0,0.1)'}
              onMouseOut={(e) => e.currentTarget.style.boxShadow = 'none'}
            >
              <h4 style={{ margin: '0.5rem 0' }}>{transcript.title}</h4>
              <p style={{ margin: '0.25rem 0', color: '#999', fontSize: '0.9rem' }}>
                {new Date(transcript.created_at).toLocaleString()}
              </p>
              {transcript.action_items && transcript.action_items.length > 0 && (
                <p style={{ margin: '0.25rem 0', color: '#667eea', fontSize: '0.9rem' }}>
                  ✓ {transcript.action_items.length} action items
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function AnalyticsView({ apiCall, token, meetings }) {
  const [analytics, setAnalytics] = useState(null)
  const [loading, setLoading] = useState(false)
  const [selectedMeetingId, setSelectedMeetingId] = useState(null)

  const loadAnalytics = async (meetingId) => {
    try {
      setLoading(true)
      const response = await apiCall(`/api/broker/meetings/${meetingId}/analytics`)
      const data = await response.json()
      setAnalytics(data)
      setSelectedMeetingId(meetingId)
    } catch (err) {
      console.error('Error loading analytics:', err)
    } finally {
      setLoading(false)
    }
  }

  if (analytics && selectedMeetingId) {
    const sentimentColor = analytics.sentiment === 'positive' ? '#4caf50' : analytics.sentiment === 'negative' ? '#f44336' : '#ff9800'
    
    return (
      <div className="card">
        <button onClick={() => setAnalytics(null)} style={{ marginBottom: '1rem', padding: '0.5rem 1rem', background: '#667eea', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
          ← Back to Analytics
        </button>

        <h3>{analytics.title}</h3>
        
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '1rem', marginBottom: '2rem' }}>
          <div className="card" style={{ padding: '1.5rem', textAlign: 'center' }}>
            <p style={{ color: '#999' }}>Duration</p>
            <h3 style={{ color: '#667eea', margin: '0.5rem 0' }}>{analytics.duration_minutes} minutes</h3>
          </div>
          <div className="card" style={{ padding: '1.5rem', textAlign: 'center' }}>
            <p style={{ color: '#999' }}>Sentiment</p>
            <h3 style={{ color: sentimentColor, margin: '0.5rem 0', textTransform: 'capitalize' }}>
              {analytics.sentiment} ({(analytics.sentiment_score * 100).toFixed(0)}%)
            </h3>
          </div>
        </div>

        {analytics.action_items && analytics.action_items.length > 0 && (
          <div style={{ marginBottom: '1.5rem' }}>
            <h4>📋 Action Items:</h4>
            <ul style={{ paddingLeft: '1.5rem' }}>
              {analytics.action_items.map((item, idx) => (
                <li key={idx} style={{ marginBottom: '0.5rem' }}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {analytics.key_points && analytics.key_points.length > 0 && (
          <div style={{ marginBottom: '1.5rem' }}>
            <h4>🎯 Key Points:</h4>
            <ul style={{ paddingLeft: '1.5rem' }}>
              {analytics.key_points.map((point, idx) => (
                <li key={idx} style={{ marginBottom: '0.5rem' }}>{point}</li>
              ))}
            </ul>
          </div>
        )}

        {Object.keys(analytics.keyword_frequency).length > 0 && (
          <div>
            <h4>🔤 Top Keywords:</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {Object.entries(analytics.keyword_frequency).map(([keyword, count]) => (
                <span
                  key={keyword}
                  style={{
                    padding: '0.5rem 1rem',
                    background: '#e0e7ff',
                    color: '#667eea',
                    borderRadius: '20px',
                    fontSize: '0.9rem'
                  }}
                >
                  {keyword} ({count})
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="card">
      <h3>📈 Meeting Analytics</h3>
      <p style={{ color: '#666', marginBottom: '1rem' }}>
        Select a meeting to view detailed analytics including sentiment, keywords, and action items.
      </p>

      {loading ? (
        <div className="loading">Loading analytics...</div>
      ) : meetings.length === 0 ? (
        <p>No meetings available for analysis</p>
      ) : (
        <div>
          {meetings.map((meeting) => (
            <div
              key={meeting.id}
              onClick={() => loadAnalytics(meeting.id)}
              style={{
                padding: '1rem',
                border: '1px solid #ddd',
                borderRadius: '4px',
                marginBottom: '0.5rem',
                cursor: 'pointer',
                background: '#f9f9f9',
                transition: 'all 0.3s'
              }}
              onMouseOver={(e) => e.currentTarget.style.boxShadow = '0 4px 8px rgba(0,0,0,0.1)'}
              onMouseOut={(e) => e.currentTarget.style.boxShadow = 'none'}
            >
              <h4 style={{ margin: '0.5rem 0' }}>{meeting.title}</h4>
              <p style={{ margin: '0.25rem 0', color: '#999', fontSize: '0.9rem' }}>
                {new Date(meeting.scheduled_at).toLocaleString()} • {meeting.duration_minutes} min
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// Calendar Settings Component
function CalendarSettings({ apiCall, token, calendarStatus, calendarLoading, meetings }) {
  const [setupMode, setSetupMode] = useState(false)
  const [calendarType, setCalendarType] = useState('google')
  const [email, setEmail] = useState('')
  const [syncMessage, setSyncMessage] = useState('')
  const [syncing, setSyncing] = useState(false)

  const handleAutoSync = async () => {
    try {
      setSyncing(true)
      const response = await apiCall('/api/broker/meetings/auto-sync', {
        method: 'GET'
      })
      const data = await response.json()
      setSyncMessage(`✅ ${data.message}`)
    } catch (err) {
      setSyncMessage(`❌ Sync failed: ${err.message}`)
    } finally {
      setSyncing(false)
    }
  }

  if (calendarLoading) {
    return (
      <div className="card">
        <div style={{ textAlign: 'center', padding: '2rem' }}>
          <p>Loading calendar status...</p>
        </div>
      </div>
    )
  }

  if (calendarStatus?.connected) {
    return (
      <div className="card">
        <h3>📅 Calendar Integration</h3>
        
        <div style={{ padding: '1.5rem', background: '#e8f5e9', borderRadius: '4px', marginBottom: '1.5rem' }}>
          <p style={{ color: '#2e7d32', margin: '0.5rem 0', fontWeight: 'bold' }}>✅ Connected</p>
          <p style={{ color: '#555', margin: '0.5rem 0' }}>
            <strong>Service:</strong> {calendarStatus.calendar_type === 'google' ? '🔵 Google Calendar' : '🔵 Microsoft Outlook'}
          </p>
          <p style={{ color: '#555', margin: '0.5rem 0' }}>
            <strong>Email:</strong> {calendarStatus.email}
          </p>
          <p style={{ color: '#555', margin: '0.5rem 0' }}>
            <strong>Synced Meetings:</strong> {calendarStatus.synced_meetings}
          </p>
          <p style={{ color: '#555', margin: '0.5rem 0' }}>
            <strong>Auto-Sync:</strong> {calendarStatus.auto_sync ? '✅ Enabled' : '⛔ Disabled'}
          </p>
        </div>

        <button
          onClick={handleAutoSync}
          disabled={syncing}
          style={{
            padding: '0.75rem 1.5rem',
            background: '#667eea',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            marginRight: '1rem',
            fontSize: '1rem'
          }}
        >
          {syncing ? '⏳ Syncing...' : '🔄 Auto-Sync Meetings Now'}
        </button>

        <button
          onClick={() => setSetupMode(!setupMode)}
          style={{
            padding: '0.75rem 1.5rem',
            background: '#999',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '1rem'
          }}
        >
          {setupMode ? '✕ Cancel' : '⚙️ Change Calendar'}
        </button>

        {syncMessage && (
          <div style={{
            marginTop: '1rem',
            padding: '1rem',
            background: syncMessage.includes('❌') ? '#ffe0e0' : '#e0ffe0',
            color: syncMessage.includes('❌') ? '#d32f2f' : '#2e7d32',
            borderRadius: '4px'
          }}>
            {syncMessage}
          </div>
        )}

        {setupMode && (
          <CalendarSetupForm apiCall={apiCall} token={token} onComplete={() => setSetupMode(false)} />
        )}
      </div>
    )
  }

  return (
    <div className="card">
      <h3>📅 Calendar Integration</h3>
      <p style={{ color: '#666', marginBottom: '1.5rem' }}>
        Connect your calendar to automatically sync broker meetings. Your meetings will sync to your personal calendar (no external emails sent).
      </p>

      <div style={{ 
        padding: '2rem', 
        background: '#fff3e0', 
        borderRadius: '4px', 
        textAlign: 'center',
        marginBottom: '1.5rem'
      }}>
        <p style={{ margin: '0.5rem 0', color: '#e65100', fontWeight: 'bold' }}>⛔ Not Connected</p>
        <p style={{ margin: '0.5rem 0', color: '#666' }}>Set up calendar integration to sync your meetings automatically.</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
        <button
          onClick={() => {
            setCalendarType('google')
            setSetupMode(true)
          }}
          style={{
            padding: '1rem',
            background: '#fff',
            border: '2px solid #4285F4',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '1rem'
          }}
        >
          <p style={{ margin: '0.5rem 0', fontSize: '1.5rem' }}>🔵 Google Calendar</p>
          <p style={{ margin: '0.5rem 0', color: '#666', fontSize: '0.9rem' }}>Connect via Google</p>
        </button>

        <button
          onClick={() => {
            setCalendarType('outlook')
            setSetupMode(true)
          }}
          style={{
            padding: '1rem',
            background: '#fff',
            border: '2px solid #0078d4',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '1rem'
          }}
        >
          <p style={{ margin: '0.5rem 0', fontSize: '1.5rem' }}>🔵 Microsoft Outlook</p>
          <p style={{ margin: '0.5rem 0', color: '#666', fontSize: '0.9rem' }}>Connect via Office 365</p>
        </button>
      </div>

      {setupMode && (
        <div style={{ marginTop: '2rem' }}>
          <CalendarSetupForm apiCall={apiCall} token={token} onComplete={() => setSetupMode(false)} calendarType={calendarType} />
        </div>
      )}

      <div style={{ marginTop: '2rem', padding: '1rem', background: '#f5f5f5', borderRadius: '4px' }}>
        <h4>How it works:</h4>
        <ul>
          <li>✅ Meetings are synced to your personal calendar only</li>
          <li>✅ No emails sent to customers</li>
          <li>✅ Auto-sync new meetings when enabled</li>
          <li>✅ Broker-only access and control</li>
          <li>✅ Secure OAuth2 token storage</li>
        </ul>
      </div>
    </div>
  )
}

function CalendarSetupForm({ apiCall, token, onComplete, calendarType }) {
  const [email, setEmail] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [refreshToken, setRefreshToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')

  const handleSetup = async () => {
    if (!email || !accessToken) {
      setMessage('❌ Please fill in all fields')
      return
    }

    try {
      setLoading(true)
      const setupData = {
        calendar_type: calendarType || 'google',
        access_token: accessToken,
        refresh_token: refreshToken || '',
        calendar_id: email,
        email: email
      }

      const response = await fetch(
        `/api/broker/calendar/setup?token=${token}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(setupData)
        }
      )

      if (response.ok) {
        setMessage(`✅ Calendar connected! Syncing started...`)
        setTimeout(() => onComplete(), 1500)
      } else {
        const err = await response.json()
        setMessage(`❌ Error: ${err.detail}`)
      }
    } catch (err) {
      setMessage(`❌ Setup failed: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: '1rem', background: '#f9f9f9', borderRadius: '4px', border: '1px solid #ddd' }}>
      <h4>Setup {calendarType === 'google' ? 'Google Calendar' : 'Outlook'}</h4>
      
      <div style={{ marginBottom: '1rem' }}>
        <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 'bold' }}>
          Calendar Email:
        </label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="your.email@gmail.com"
          style={{
            width: '100%',
            padding: '0.75rem',
            border: '1px solid #ddd',
            borderRadius: '4px',
            fontSize: '1rem'
          }}
        />
      </div>

      <div style={{ marginBottom: '1rem' }}>
        <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 'bold' }}>
          Access Token:
        </label>
        <textarea
          value={accessToken}
          onChange={(e) => setAccessToken(e.target.value)}
          placeholder="Paste OAuth access token"
          style={{
            width: '100%',
            padding: '0.75rem',
            border: '1px solid #ddd',
            borderRadius: '4px',
            fontSize: '0.9rem',
            fontFamily: 'monospace',
            height: '80px'
          }}
        />
      </div>

      <div style={{ marginBottom: '1rem' }}>
        <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 'bold' }}>
          Refresh Token (optional):
        </label>
        <textarea
          value={refreshToken}
          onChange={(e) => setRefreshToken(e.target.value)}
          placeholder="Paste refresh token if available"
          style={{
            width: '100%',
            padding: '0.75rem',
            border: '1px solid #ddd',
            borderRadius: '4px',
            fontSize: '0.9rem',
            fontFamily: 'monospace',
            height: '60px'
          }}
        />
      </div>

      {message && (
        <div style={{
          marginBottom: '1rem',
          padding: '1rem',
          background: message.includes('❌') ? '#ffe0e0' : '#e0ffe0',
          color: message.includes('❌') ? '#d32f2f' : '#2e7d32',
          borderRadius: '4px'
        }}>
          {message}
        </div>
      )}

      <button
        onClick={handleSetup}
        disabled={loading}
        style={{
          padding: '0.75rem 1.5rem',
          background: '#667eea',
          color: 'white',
          border: 'none',
          borderRadius: '4px',
          cursor: 'pointer',
          fontSize: '1rem'
        }}
      >
        {loading ? '⏳ Connecting...' : '✅ Connect Calendar'}
      </button>

      <div style={{ marginTop: '1rem', padding: '1rem', background: '#e3f2fd', borderRadius: '4px', fontSize: '0.85rem', color: '#1565c0' }}>
        <p><strong>ℹ️ How to get OAuth tokens:</strong></p>
        <p>1. Visit Google/Microsoft OAuth authorization page</p>
        <p>2. Authorize the broker app</p>
        <p>3. Copy the access token from the redirect URL</p>
        <p>4. Paste it here along with refresh token if available</p>
      </div>
    </div>
  )
}

export default Dashboard

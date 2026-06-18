import React, { useState, useEffect, useCallback } from 'react'

const STATUS_OPTIONS = ['all', 'pending', 'in_progress', 'completed', 'closed']
const PRIORITY_OPTIONS = ['low', 'medium', 'high']

const STATUS_LABELS = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  closed: 'Closed',
}

const STATUS_COLORS = {
  pending:     { background: '#854d0e44', color: '#FDE68A', border: '1px solid #F59E0B55' },
  in_progress: { background: '#1e3a8a44', color: '#93C5FD', border: '1px solid #3B82F655' },
  completed:   { background: '#14532d44', color: '#86EFAC', border: '1px solid #10B98155' },
  closed:      { background: '#33415544', color: '#94A3B8', border: '1px solid #33415588' },
}

const PRIORITY_COLORS = {
  low:    { background: '#33415544', color: '#94A3B8', border: '1px solid #33415588' },
  medium: { background: '#854d0e44', color: '#FDE68A', border: '1px solid #F59E0B55' },
  high:   { background: '#7f1d1d44', color: '#FCA5A5', border: '1px solid #EF444455' },
}

function Badge({ text, colorMap }) {
  const style = colorMap[text] || { background: '#33415544', color: '#94A3B8' }
  return (
    <span style={{
      display: 'inline-block',
      padding: '0.2rem 0.65rem',
      borderRadius: '12px',
      fontSize: '0.8rem',
      fontWeight: 600,
      ...style
    }}>
      {text}
    </span>
  )
}

// Strip legacy 'WA:' prefix; return '+phone' if purely numeric, else original name
function cleanCustomerName(name) {
  if (!name || name === 'Unknown') return null
  const stripped = name.startsWith('WA:') ? name.slice(3) : name
  if (/^\d{6,}$/.test(stripped)) return `+${stripped}`
  return name
}

function isPhoneOnlyName(name) {
  if (!name) return false
  const stripped = name.startsWith('WA:') ? name.slice(3) : name
  return /^\d{6,}$/.test(stripped)
}

function AllLeads({ apiCall, setError, setSuccess }) {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('pending')
  const [expandedId, setExpandedId] = useState(null)
  const [updating, setUpdating] = useState(null)

  const loadLeads = useCallback(async () => {
    try {
      setLoading(true)
      const params = new URLSearchParams()
      if (search) params.append('search', search)
      if (statusFilter !== 'all') params.append('status_filter', statusFilter)
      const response = await apiCall(`/api/broker/leads?${params.toString()}`)
      const data = await response.json()
      setLeads(Array.isArray(data) ? data : [])
    } catch (err) {
      setError('Failed to load leads: ' + err.message)
    } finally {
      setLoading(false)
    }
  }, [search, statusFilter, apiCall, setError])

  useEffect(() => {
    const timer = setTimeout(loadLeads, 300)
    return () => clearTimeout(timer)
  }, [loadLeads])

  const updateLead = async (id, field, value) => {
    setUpdating(`${id}-${field}`)
    try {
      const response = await apiCall(`/api/broker/leads/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ [field]: value })
      })
      if (response.ok) {
        setLeads(prev => prev.map(l => l.id === id ? { ...l, [field]: value } : l))
      } else {
        setError('Failed to update lead')
      }
    } catch (err) {
      setError('Update failed: ' + err.message)
    } finally {
      setUpdating(null)
    }
  }

  const formatDate = (dateStr) => {
    if (!dateStr) return '—'
    // Try to parse ISO date
    const d = new Date(dateStr)
    if (!isNaN(d)) {
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    }
    return dateStr
  }

  return (
    <div>
      {/* Page header */}
      <div style={{ marginBottom: '1.5rem' }}>
        <h2 style={{ marginBottom: '0.25rem' }}>📋 All Leads</h2>
        <p style={{ color: '#94A3B8', fontSize: '0.95rem' }}>
          Manage and track all your customer interactions.
        </p>
      </div>

      {/* Toolbar */}
      <div className="card" style={{ padding: '1rem 1.5rem', marginBottom: '1rem' }}>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="Search customers..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              flex: 1, minWidth: '200px', padding: '0.6rem 1rem',
              border: '1px solid #334155', borderRadius: '6px', fontSize: '0.95rem',
              background: '#0F172A', color: '#F1F5F9',
            }}
          />
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            style={{
              padding: '0.6rem 1rem', border: '1px solid #334155',
              borderRadius: '6px', fontSize: '0.95rem', background: '#1E293B', color: '#F1F5F9'
            }}
          >
            {STATUS_OPTIONS.map(s => (
              <option key={s} value={s}>
                {s === 'all' ? 'All (incl. empty messages)' : STATUS_LABELS[s]}
              </option>
            ))}
          </select>
          <button className="btn btn-secondary" onClick={loadLeads} style={{ whiteSpace: 'nowrap' }}>
            🔄 Refresh
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: '#94A3B8' }}>
            Loading leads...
          </div>
        ) : leads.length === 0 ? (
          <div style={{ padding: '3rem', textAlign: 'center', color: '#94A3B8' }}>
            <p style={{ fontSize: '1.1rem', marginBottom: '0.5rem' }}>No leads found.</p>
            <p style={{ fontSize: '0.9rem' }}>
              Record a meeting with the Voice Recorder and the extracted data will appear here.
            </p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table" style={{ marginBottom: 0 }}>
              <thead>
                <tr>
                  <th style={{ minWidth: '160px' }}>Customer</th>
                  <th style={{ minWidth: '260px' }}>Next Action</th>
                  <th style={{ minWidth: '140px' }}>Follow Up</th>
                  <th style={{ width: '100px' }}>Priority</th>
                  <th style={{ width: '130px' }}>Status</th>
                  <th style={{ width: '80px' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {leads.map(lead => (
                  <React.Fragment key={lead.id}>
                    <tr style={{ cursor: 'pointer' }}>
                      {/* Customer */}
                      <td>
                        <div style={{ fontWeight: 600, color: isPhoneOnlyName(lead.customer_name) ? '#94A3B8' : '#F1F5F9', fontFamily: isPhoneOnlyName(lead.customer_name) ? 'monospace' : 'inherit' }}>
                          {cleanCustomerName(lead.customer_name) || 'Unknown'}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: '#94A3B8', marginTop: '2px' }}>
                          {formatDate(lead.created_at)}
                        </div>
                      </td>

                      {/* Next Action = first action item or discussion summary */}
                      <td>
                        <div style={{ fontSize: '0.9rem', color: '#CBD5E1', lineHeight: 1.4 }}>
                          {lead.action_items && lead.action_items.length > 0
                            ? lead.action_items[0]
                            : lead.discussion_summary
                              ? lead.discussion_summary.substring(0, 120) + (lead.discussion_summary.length > 120 ? '…' : '')
                              : '—'
                          }
                        </div>
                        {lead.follow_ups && lead.follow_ups !== 'None mentioned.' && (
                          <div style={{ fontSize: '0.8rem', color: '#94A3B8', marginTop: '4px' }}>
                            ↩ {lead.follow_ups.substring(0, 100)}{lead.follow_ups.length > 100 ? '…' : ''}
                          </div>
                        )}
                      </td>

                      {/* Follow Up date */}
                      <td>
                        <div style={{ fontSize: '0.9rem', color: lead.next_meeting ? '#F1F5F9' : '#475569' }}>
                          {lead.next_meeting ? (
                            <>
                              📅 <span style={{ fontWeight: 500 }}>{lead.next_meeting}</span>
                            </>
                          ) : '—'}
                        </div>
                      </td>

                      {/* Priority selector */}
                      <td>
                        <select
                          value={lead.priority || 'medium'}
                          disabled={updating === `${lead.id}-priority`}
                          onChange={e => updateLead(lead.id, 'priority', e.target.value)}
                          style={{
                            padding: '0.25rem 0.5rem',
                            border: '1px solid #334155',
                            borderRadius: '6px',
                            fontSize: '0.82rem',
                            cursor: 'pointer',
                            ...(PRIORITY_COLORS[lead.priority] || { background: '#1E293B', color: '#F1F5F9' })
                          }}
                        >
                          {PRIORITY_OPTIONS.map(p => (
                            <option key={p} value={p}>{p}</option>
                          ))}
                        </select>
                      </td>

                      {/* Status selector */}
                      <td>
                        <select
                          value={lead.status || 'pending'}
                          disabled={updating === `${lead.id}-status`}
                          onChange={e => updateLead(lead.id, 'status', e.target.value)}
                          style={{
                            padding: '0.25rem 0.5rem',
                            border: '1px solid #334155',
                            borderRadius: '6px',
                            fontSize: '0.82rem',
                            cursor: 'pointer',
                            ...(STATUS_COLORS[lead.status] || { background: '#1E293B', color: '#F1F5F9' })
                          }}
                        >
                          {Object.entries(STATUS_LABELS).map(([val, label]) => (
                            <option key={val} value={val}>{label}</option>
                          ))}
                        </select>
                      </td>

                      {/* Actions */}
                      <td>
                        <button
                          className="btn btn-secondary"
                          style={{ padding: '0.3rem 0.7rem', fontSize: '0.8rem' }}
                          onClick={() => setExpandedId(expandedId === lead.id ? null : lead.id)}
                        >
                          {expandedId === lead.id ? '▲' : '▼'}
                        </button>
                      </td>
                    </tr>

                    {/* Expanded detail row */}
                    {expandedId === lead.id && (
                      <tr style={{ background: '#162032' }}>
                        <td colSpan={6} style={{ padding: '1.25rem 1.5rem' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
                            {/* Discussion Summary */}
                            <div>
                            <div style={{ fontWeight: 600, marginBottom: '0.4rem', color: '#94A3B8' }}>
                                💬 Discussion Summary
                              </div>
                              <p style={{ fontSize: '0.9rem', color: '#CBD5E1', lineHeight: 1.5 }}>
                                {lead.discussion_summary || '—'}
                              </p>
                            </div>

                            {/* Action Items */}
                            <div>
                              <div style={{ fontWeight: 600, marginBottom: '0.4rem', color: '#94A3B8' }}>
                                ✅ Action Items
                              </div>
                              {lead.action_items && lead.action_items.length > 0 ? (
                                <ul style={{ paddingLeft: '1.2rem', fontSize: '0.9rem', color: '#CBD5E1' }}>
                                  {lead.action_items.map((item, i) => (
                                    <li key={i} style={{ marginBottom: '0.25rem' }}>{item}</li>
                                  ))}
                                </ul>
                              ) : <p style={{ color: '#475569', fontSize: '0.9rem' }}>None</p>}
                            </div>

                            {/* Key Points */}
                            <div>
                              <div style={{ fontWeight: 600, marginBottom: '0.4rem', color: '#94A3B8' }}>
                                🔑 Key Points
                              </div>
                              {lead.key_points && lead.key_points.length > 0 ? (
                                <ul style={{ paddingLeft: '1.2rem', fontSize: '0.9rem', color: '#CBD5E1' }}>
                                  {lead.key_points.map((pt, i) => (
                                    <li key={i} style={{ marginBottom: '0.25rem' }}>{pt}</li>
                                  ))}
                                </ul>
                              ) : <p style={{ color: '#475569', fontSize: '0.9rem' }}>None</p>}
                            </div>

                            {/* Follow-ups */}
                            <div>
                              <div style={{ fontWeight: 600, marginBottom: '0.4rem', color: '#94A3B8' }}>
                                ↩ Follow-ups
                              </div>
                              <p style={{ fontSize: '0.9rem', color: '#CBD5E1', lineHeight: 1.5 }}>
                                {lead.follow_ups || '—'}
                              </p>
                            </div>
                          </div>

                          {/* Transcript */}
                          {lead.transcript && (
                            <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid #334155' }}>
                              <div style={{ fontWeight: 600, marginBottom: '0.4rem', color: '#94A3B8' }}>🎙 Transcript / Message</div>
                              <p style={{ fontSize: '0.875rem', color: '#CBD5E1', lineHeight: 1.6, background: '#0F172A', padding: '0.75rem 1rem', borderRadius: '6px', margin: 0 }}>
                                {lead.transcript}
                              </p>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={{ textAlign: 'right', color: '#94A3B8', fontSize: '0.85rem', marginTop: '0.5rem' }}>
        {leads.length} lead{leads.length !== 1 ? 's' : ''} found
      </div>
    </div>
  )
}

export default AllLeads

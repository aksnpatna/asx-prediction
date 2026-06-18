import React, { useState, useEffect, useCallback, useMemo } from 'react'

// ── Design tokens (skill: frontend-design) ─────────────────────────────────
const T = {
  bg:           '#0F172A',
  surface:      '#1E293B',
  surfaceHover: '#243246',
  border:       '#334155',
  fg:           '#F1F5F9',
  fgAlt:        '#94A3B8',
  primary:      '#3B82F6',
  green:        '#10B981',
  red:          '#EF4444',
  amber:        '#F59E0B',
  waGreen:      '#25d366',
  mono:         "'IBM Plex Mono', 'JetBrains Mono', monospace",
  sans:         "'IBM Plex Sans', system-ui, sans-serif",
}

const STATUS = {
  pending:    { color: '#F59E0B', label: 'Queued'     },
  processing: { color: '#3B82F6', label: 'Processing' },
  done:       { color: '#10B981', label: 'Done'       },
  failed:     { color: '#EF4444', label: 'Failed'     },
  replied:    { color: '#94A3B8', label: 'Replied'    },
}

function StatusBadge({ status }) {
  const s = STATUS[status] || { color: T.fgAlt, label: status }
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 99,
      fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
      background: s.color + '22', color: s.color, border: `1px solid ${s.color}44`,
    }}>{s.label}</span>
  )
}

function Avatar({ name }) {
  const label = name
    ? name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
    : '?'
  return (
    <div style={{
      width: 40, height: 40, borderRadius: '50%', flexShrink: 0,
      background: T.primary + '33', border: `2px solid ${T.primary}55`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 14, fontWeight: 700, color: T.primary, fontFamily: T.sans,
    }}>{label}</div>
  )
}

function relTime(isoStr) {
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000
  if (diff < 60)    return 'just now'
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return new Date(isoStr).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function DetailSection({ title, children }) {
  return (
    <div style={{ background: T.bg, borderRadius: 8, padding: '10px 14px' }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: T.fgAlt, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function UrgencyBadge({ urgency }) {
  const map = { low: T.green, medium: T.amber, high: T.red }
  const c = map[urgency] || T.amber
  const icon = urgency === 'high' ? '🔴' : urgency === 'medium' ? '🟡' : '🟢'
  return (
    <span style={{ padding: '3px 10px', borderRadius: 99, background: c + '22', color: c, fontSize: 12, fontWeight: 600, border: `1px solid ${c}44` }}>
      {icon} {urgency} urgency
    </span>
  )
}

export default function WhatsAppInbox({ apiCall, setError, setSuccess }) {
  const [jobs, setJobs]               = useState([])
  const [loading, setLoading]         = useState(false)
  const [selectedPhone, setSelectedPhone] = useState(null)
  const [expandedJob, setExpandedJob] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [search, setSearch]           = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [sendingReply, setSendingReply] = useState(null)
  const [retrying, setRetrying]       = useState(null)
  const [isMobile, setIsMobile]       = useState(window.innerWidth < 768)

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const fetchJobs = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await apiCall('/api/whatsapp/jobs?limit=200')
      if (resp.ok) setJobs(await resp.json())
    } catch (e) {
      setError('Failed to load messages: ' + e.message)
    } finally {
      setLoading(false)
    }
  }, []) // eslint-disable-line

  useEffect(() => { fetchJobs() }, []) // eslint-disable-line

  useEffect(() => {
    if (!autoRefresh) return
    const hasPending = jobs.some(j => j.status === 'pending' || j.status === 'processing')
    const timer = setInterval(fetchJobs, hasPending ? 8000 : 20000)
    return () => clearInterval(timer)
  }, [autoRefresh, jobs, fetchJobs])

  // Group jobs by from_phone, sorted by latest message
  const contacts = useMemo(() => {
    const map = {}
    jobs.forEach(j => {
      if (!map[j.from_phone]) {
        map[j.from_phone] = { phone: j.from_phone, name: null, jobs: [], latestAt: j.created_at }
      }
      map[j.from_phone].jobs.push(j)
      if (j.extracted_data?.customer_name) map[j.from_phone].name = j.extracted_data.customer_name
      if (new Date(j.created_at) > new Date(map[j.from_phone].latestAt)) map[j.from_phone].latestAt = j.created_at
    })
    return Object.values(map).sort((a, b) => new Date(b.latestAt) - new Date(a.latestAt))
  }, [jobs])

  const filteredContacts = useMemo(() => {
    if (!search.trim()) return contacts
    const q = search.toLowerCase()
    return contacts.filter(c => c.phone.includes(q) || (c.name && c.name.toLowerCase().includes(q)))
  }, [contacts, search])

  const selectedContact = contacts.find(c => c.phone === selectedPhone)

  const threadJobs = useMemo(() => {
    if (!selectedContact) return []
    return [...selectedContact.jobs]
      .filter(j => statusFilter === 'all' || j.status === statusFilter)
      .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
  }, [selectedContact, statusFilter])

  const handleSendReply = async (job) => {
    setSendingReply(job.id)
    try {
      const resp = await apiCall(`/api/whatsapp/send-reply/${job.id}`, { method: 'POST' })
      const data = await resp.json()
      if (resp.ok) { setSuccess(`Reply sent to +${job.from_phone}`); fetchJobs() }
      else setError(data.detail || 'Failed to send reply')
    } catch (e) {
      setError('Send error: ' + e.message)
    } finally {
      setSendingReply(null)
    }
  }

  const handleRetry = async (job) => {
    setRetrying(job.id)
    try {
      const resp = await apiCall(`/api/whatsapp/retry/${job.id}`, { method: 'POST' })
      if (resp.ok) { setSuccess('Retrying…'); fetchJobs() }
    } catch (e) {
      setError('Retry error: ' + e.message)
    } finally {
      setRetrying(null)
    }
  }

  return (
    <div style={{ display: 'flex', height: isMobile ? 'calc(100vh - 80px)' : 'calc(100vh - 116px)', background: T.bg, borderRadius: 14, overflow: 'hidden', border: `1px solid ${T.border}`, fontFamily: T.sans, flexDirection: isMobile ? 'column' : 'row' }}>

      {/* Mobile: show contact list or thread, not both */}
      {isMobile && selectedContact && (
        <MobileThreadView contact={selectedContact} threadJobs={threadJobs} setSelectedPhone={setSelectedPhone}
          expandedJob={expandedJob} setExpandedJob={setExpandedJob} statusFilter={statusFilter} setStatusFilter={setStatusFilter}
          sendingReply={sendingReply} retrying={retrying} handleSendReply={handleSendReply} handleRetry={handleRetry}
        />
      )}

      {/* Desktop: always show; Mobile: show only if no contact selected */}
      {(!isMobile || !selectedContact) && (
      <div style={{ width: isMobile ? '100%' : 280, borderRight: isMobile ? 'none' : `1px solid ${T.border}`, display: 'flex', flexDirection: 'column', flexShrink: 0, height: isMobile ? '100%' : 'auto' }}>
        {/* Header */}
        <div style={{ padding: '16px 16px 12px', borderBottom: `1px solid ${T.border}` }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ color: T.fg, fontWeight: 700, fontSize: 15 }}>
              Messages
              {loading && <span style={{ marginLeft: 8, width: 7, height: 7, borderRadius: '50%', background: T.primary, display: 'inline-block', verticalAlign: 'middle', opacity: 0.8 }} />}
            </span>
            <button onClick={fetchJobs} disabled={loading} style={{ padding: '4px 8px', borderRadius: 6, border: `1px solid ${T.border}`, background: 'transparent', color: T.fgAlt, cursor: 'pointer', fontSize: 13, lineHeight: 1 }}>↻</button>
          </div>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search contacts…" style={{ width: '100%', boxSizing: 'border-box', padding: '7px 10px', borderRadius: 8, border: `1px solid ${T.border}`, background: T.surface, color: T.fg, fontSize: 13, outline: 'none', fontFamily: T.sans }} />
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {filteredContacts.length === 0 && <div style={{ padding: 32, textAlign: 'center', color: T.fgAlt, fontSize: 13 }}>{loading ? 'Loading…' : 'No messages yet'}</div>}
          {filteredContacts.map(contact => {
            const latest = contact.jobs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0]
            const pendingCount = contact.jobs.filter(j => j.status === 'pending' || j.status === 'processing').length
            const isActive = selectedPhone === contact.phone
            const preview = latest?.extracted_data?.summary || latest?.transcript || '—'
            return (
              <button key={contact.phone} onClick={() => { setSelectedPhone(contact.phone); setExpandedJob(null) }}
                style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', width: '100%', border: 'none', borderBottom: `1px solid ${T.border}`, background: isActive ? T.primary + '22' : 'transparent', borderLeft: isActive ? `3px solid ${T.primary}` : '3px solid transparent', cursor: 'pointer', textAlign: 'left', transition: 'background 0.1s' }}>
                <Avatar name={contact.name || contact.phone} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
                    <span style={{ color: T.fg, fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{contact.name || `+${contact.phone}`}</span>
                    <span style={{ color: T.fgAlt, fontSize: 11, flexShrink: 0, marginLeft: 6 }}>{relTime(contact.latestAt)}</span>
                  </div>
                  {contact.name && <div style={{ color: T.fgAlt, fontSize: 11, fontFamily: T.mono, marginBottom: 2 }}>+{contact.phone}</div>}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: T.fgAlt, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{preview.slice(0, 52)}</span>
                    {pendingCount > 0 && <span style={{ marginLeft: 6, background: T.amber, color: '#000', borderRadius: 99, fontSize: 10, fontWeight: 700, padding: '1px 6px', flexShrink: 0 }}>{pendingCount}</span>}
                  </div>
                </div>
              </button>
            )
          })}
        </div>
        <div style={{ padding: '8px 16px', borderTop: `1px solid ${T.border}` }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', color: T.fgAlt, fontSize: 12 }}>
            <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} style={{ accentColor: T.primary }} />
            Auto-refresh
          </label>
        </div>
      </div>
      )}

      {/* Desktop right pane: thread */}
      {!isMobile && !selectedContact && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: T.fgAlt }}>
          <div style={{ fontSize: 48, marginBottom: 12, opacity: 0.3 }}>💬</div>
          <div style={{ fontSize: 14 }}>Select a contact to view messages</div>
        </div>
      )}

      {!isMobile && selectedContact && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '14px 20px', borderBottom: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0 }}>
            <Avatar name={selectedContact.name || selectedContact.phone} />
            <div style={{ flex: 1 }}>
              <div style={{ color: T.fg, fontWeight: 700, fontSize: 15 }}>{selectedContact.name || `+${selectedContact.phone}`}</div>
              <div style={{ color: T.fgAlt, fontSize: 12, fontFamily: T.mono }}>+{selectedContact.phone} · {selectedContact.jobs.length} message{selectedContact.jobs.length !== 1 ? 's' : ''}</div>
            </div>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ padding: '5px 10px', borderRadius: 8, border: `1px solid ${T.border}`, background: T.surface, color: T.fgAlt, fontSize: 12, cursor: 'pointer', outline: 'none' }}>
              <option value="all">All</option>
              <option value="done">Done</option>
              <option value="pending">Pending</option>
              <option value="failed">Failed</option>
              <option value="replied">Replied</option>
            </select>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {threadJobs.length === 0 && <div style={{ textAlign: 'center', color: T.fgAlt, padding: 32, fontSize: 13 }}>No messages match this filter</div>}
            {threadJobs.map(job => {
              const isExpandedRender = expandedJob === job.id
              const extRender = job.extracted_data || {}
              return (
                <div key={job.id} style={{ background: T.surface, border: `1px solid ${isExpandedRender ? T.primary + '66' : T.border}`, borderRadius: 12, overflow: 'hidden', transition: 'border-color 0.15s' }}>
                  <button onClick={() => setExpandedJob(isExpandedRender ? null : job.id)} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', width: '100%', border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left' }}>
                    <span style={{ fontSize: 18, flexShrink: 0 }}>{job.message_type === 'text' ? '💬' : '🎤'}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ color: T.fgAlt, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{extRender.summary || job.transcript || 'Processing…'}</div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                      {extRender.next_meeting_date && <span style={{ fontSize: 11, color: T.primary, fontWeight: 600 }}>📅 {extRender.next_meeting_date}</span>}
                      <StatusBadge status={job.status} />
                      <span style={{ color: T.fgAlt, fontSize: 11 }}>{relTime(job.created_at)}</span>
                      <span style={{ color: T.fgAlt, fontSize: 12 }}>{isExpandedRender ? '▲' : '▼'}</span>
                    </div>
                  </button>
                  {isExpandedRender && (
                    <div style={{ borderTop: `1px solid ${T.border}`, padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {job.transcript && <DetailSection title={job.message_type === 'text' ? 'Message' : 'Transcript'}><p style={{ margin: 0, fontSize: 14, color: T.fg, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{job.transcript}</p></DetailSection>}
                      {extRender.summary && <DetailSection title="Summary"><p style={{ margin: 0, fontSize: 13, color: T.fg, lineHeight: 1.6 }}>{extRender.summary}</p></DetailSection>}
                      {extRender.intent && <div style={{ fontSize: 12, color: '#60A5FA', fontStyle: 'italic', padding: '6px 10px', background: '#0F172A', borderRadius: 6 }}>{extRender.intent}</div>}
                      {extRender.category && <div style={{ fontSize: 12, color: '#3B82F6', fontWeight: 600 }}>📂 {extRender.category}</div>}
                      {extRender.next_meeting_date && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', background: T.primary + '18', border: `1px solid ${T.primary}44`, borderRadius: 8 }}>
                          <span style={{ fontSize: 22 }}>📅</span>
                          <div style={{ flex: 1 }}><div style={{ fontSize: 11, color: T.fgAlt, textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>Next Meeting</div><div style={{ fontSize: 15, color: T.fg, fontWeight: 700 }}>{extRender.next_meeting_date}</div></div>
                        </div>
                      )}
                      <UrgencyFollowup ext={extRender} />
                      {extRender.discussed_topics?.length > 0 && <TopicSection title="Discussed" color={T.fg} items={extRender.discussed_topics} />}
                      {extRender.action_items?.length > 0 && <TopicSection title="Action Items" color={T.green} items={extRender.action_items} />}
                      {job.reply_text && (
                        <DetailSection title={job.wa_replied_at ? '✓ Reply Sent' : 'Suggested Reply'}>
                          {job.wa_replied_at && <div style={{ fontSize: 11, color: T.green, marginBottom: 6 }}>Sent {relTime(job.wa_replied_at)}</div>}
                          <div style={{ background: T.waGreen + '18', border: `1px solid ${T.waGreen}44`, borderRadius: 8, padding: '10px 14px', fontSize: 13, color: T.fg, lineHeight: 1.6 }}>{job.reply_text}</div>
                          {job.status === 'done' && !job.wa_replied_at && (
                            <button onClick={() => handleSendReply(job)} disabled={sendingReply === job.id} style={{ marginTop: 8, padding: '7px 16px', borderRadius: 8, border: 'none', background: T.waGreen, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 13 }}>
                              {sendingReply === job.id ? '⏳ Sending…' : '📲 Send Reply'}
                            </button>
                          )}
                        </DetailSection>
                      )}
                      {job.status === 'failed' && (
                        <div style={{ padding: '10px 14px', background: T.red + '18', border: `1px solid ${T.red}44`, borderRadius: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
                          <span style={{ fontSize: 13, color: T.red, flex: 1 }}>❌ {job.error_msg || 'Processing failed'}</span>
                          <button onClick={() => handleRetry(job)} disabled={retrying === job.id} style={{ padding: '4px 12px', borderRadius: 6, border: 'none', background: T.red, color: '#fff', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>{retrying === job.id ? 'Retrying…' : '↺ Retry'}</button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Helper components ───────────────────────────────────────────────────
function UrgencyFollowup({ ext }) {
  if (!ext.urgency && !ext.follow_up_required) return null
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {ext.urgency && <UrgencyBadge urgency={ext.urgency} />}
      {ext.follow_up_required && <span style={{ padding: '3px 10px', borderRadius: 99, background: '#F59E0B22', color: '#F59E0B', fontSize: 12, fontWeight: 600, border: '1px solid #F59E0B44' }}>🔔 Follow-up required</span>}
    </div>
  )
}
function TopicSection({ title, color, items }) {
  return <DetailSection title={title}><ul style={{ margin: 0, paddingLeft: 16, fontSize: 12, color, lineHeight: 1.8 }}>{items.map((t, i) => <li key={i}>{t}</li>)}</ul></DetailSection>
}

// ── Mobile Thread View ───────────────────────────────────────────────────
function MobileThreadView({ contact, threadJobs, setSelectedPhone, expandedJob, setExpandedJob,
  statusFilter, setStatusFilter, sendingReply, retrying, handleSendReply, handleRetry }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '10px 14px', borderBottom: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <button onClick={() => setSelectedPhone(null)} style={{ background: 'none', border: 'none', color: '#3B82F6', fontSize: 18, cursor: 'pointer', padding: 4 }}>←</button>
        <Avatar name={contact.name || contact.phone} />
        <div style={{ flex: 1 }}>
          <div style={{ color: T.fg, fontWeight: 700, fontSize: 14 }}>{contact.name || `+${contact.phone}`}</div>
          <div style={{ color: T.fgAlt, fontSize: 11, fontFamily: T.mono }}>+{contact.phone} · {contact.jobs.length} msgs</div>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px' }}>
        {threadJobs.length === 0 ? (
          <div style={{ textAlign: 'center', color: T.fgAlt, padding: 32, fontSize: 13 }}>No messages</div>
        ) : (
          threadJobs.map(job => {
            const isExpanded = expandedJob === job.id
            const ext = job.extracted_data || {}
            return (
              <div key={job.id} style={{ background: T.surface, border: `1px solid ${isExpanded ? T.primary + '66' : T.border}`, borderRadius: 10, overflow: 'hidden', marginBottom: 8 }}>
                <button onClick={() => setExpandedJob(isExpanded ? null : job.id)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', width: '100%', border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontFamily: T.sans }}>
                  <span style={{ fontSize: 16 }}>{job.message_type === 'text' ? '💬' : '🎤'}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ color: T.fgAlt, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ext.summary || job.transcript || 'Processing…'}</div>
                  </div>
                  <StatusBadge status={job.status} />
                  <span style={{ color: T.fgAlt, fontSize: 11 }}>{relTime(job.created_at)}</span>
                  <span style={{ color: T.fgAlt, fontSize: 11 }}>{isExpanded ? '▲' : '▼'}</span>
                </button>
                {isExpanded && (
                  <div style={{ borderTop: `1px solid ${T.border}`, padding: 10, display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
                    {job.transcript && <DetailSection title="Transcript"><p style={{ margin: 0, color: T.fg, fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{job.transcript}</p></DetailSection>}
                    {ext.summary && <DetailSection title="Summary"><p style={{ margin: 0, color: T.fg, fontSize: 12, lineHeight: 1.5 }}>{ext.summary}</p></DetailSection>}
                    {ext.category && <div style={{ fontSize: 11, color: '#3B82F6', fontWeight: 600 }}>📂 {ext.category}</div>}
                    {job.reply_text && !job.wa_replied_at && (
                      <button onClick={() => handleSendReply(job)} disabled={sendingReply === job.id} style={{ padding: '6px 12px', borderRadius: 6, border: 'none', background: '#25d366', color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 12, opacity: sendingReply === job.id ? 0.7 : 1 }}>
                        {sendingReply === job.id ? '⏳...' : '📲 Send Reply'}
                      </button>
                    )}
                    {job.status === 'failed' && (
                      <div style={{ padding: '8px 10px', background: '#7f1d1d44', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 12, color: '#FCA5A5', flex: 1 }}>❌ {job.error_msg || 'Failed'}</span>
                        <button onClick={() => handleRetry(job)} disabled={retrying === job.id} style={{ padding: '3px 10px', borderRadius: 4, border: 'none', background: '#EF4444', color: '#fff', cursor: 'pointer', fontSize: 11 }}>Retry</button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

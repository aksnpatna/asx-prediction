import React, { useState, useEffect } from 'react'

const PRI_COLORS = { high: '#EF4444', medium: '#F59E0B', low: '#22C55E' }
const CAT_META = {
  meeting:   { icon: '📅', color: '#3B82F6', label: 'Meeting' },
  financial: { icon: '💰', color: '#10B981', label: 'Financial' },
  planning:  { icon: '📋', color: '#F59E0B', label: 'Planning' },
  process:   { icon: '⚙️', color: '#06B6D4', label: 'Process' },
  inquiry:   { icon: '❓', color: '#EC4899', label: 'Inquiry' },
  update:    { icon: '📝', color: '#64748B', label: 'Update' },
  general:   { icon: '💬', color: '#94A3B8', label: 'General' },
}
const CARD = { background: '#1E293B', border: '1px solid #334155', borderRadius: 12, padding: '1.25rem' }

function Dashboard({ token, apiCall, brokerId }) {
  const [metrics, setMetrics] = useState(null)
  const [summaries, setSummaries] = useState([])
  const [categories, setCategories] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')
  const [catFilter, setCatFilter] = useState('all')
  const [csvFile, setCsvFile] = useState(null)
  const [csvLoading, setCsvLoading] = useState(false)
  const [csvMessage, setCsvMessage] = useState('')
  const [recordMode, setRecordMode] = useState(false)

  useEffect(() => { loadAll() }, [])

  const loadAll = async () => {
    setLoading(true)
    try {
      const [mR, cR] = await Promise.all([
        apiCall('/api/broker/dashboard/metrics'),
        apiCall('/api/broker/dashboard/categories').catch(() => ({ json: () => ({ categories: [] }) })),
      ])
      const md = await mR.json()
      setMetrics(md)
      setSummaries(md.recent_summaries || [])
      const cd = await cR.json()
      setCategories(cd.categories || [])
    } catch (err) { console.error(err) }
    finally { setLoading(false) }
  }

  const handleCsvUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setCsvLoading(true)
    setCsvMessage('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch(`/api/broker/csv/import-smart?token=${token}`, { method: 'POST', body: fd })
      const d = await r.json()
      if (r.ok) { setCsvMessage(`Imported ${d.imported_records} records - ${d.matched_customers} matched`); setTimeout(loadAll, 1000) }
      else setCsvMessage(`Error: ${d.detail}`)
    } catch (err) { setCsvMessage(`Failed: ${err.message}`) }
    finally { setCsvLoading(false) }
  }

  if (loading) return <div style={{ padding: '2rem', textAlign: 'center', color: '#94A3B8' }}>Loading...</div>
  if (recordMode) return <VoiceRecorder apiCall={apiCall} onClose={() => { setRecordMode(false); loadAll() }} />

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap', gap: 8 }}>
        <h2 style={{ margin: 0 }}>Dashboard</h2>
        <button onClick={() => setRecordMode(true)} style={{ background: '#25D366', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 20px', fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>🎤 Record Voice Note</button>
      </div>

      <div className="dashboard-subtabs" style={{ marginBottom: '1rem' }}>
        {['overview','categories','summaries','csv'].map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} style={{
            padding: '0.5rem 1rem', border: 'none', cursor: 'pointer', background: activeTab === tab ? '#3B82F6' : 'transparent',
            color: activeTab === tab ? '#fff' : '#94A3B8', borderRadius: '6px 6px 0 0', fontWeight: activeTab === tab ? 600 : 400,
            fontFamily: 'inherit', fontSize: 13,
          }}>{tab === 'overview' ? '📊 Overview' : tab === 'categories' ? '🏷 Categories' : tab === 'summaries' ? '🤖 Summaries' : '📥 Import'}</button>
        ))}
      </div>

      {activeTab === 'overview' && <OverviewTab metrics={metrics} summaries={summaries} categories={categories} />}
      {activeTab === 'categories' && <CategoriesTab categories={categories} summaries={summaries} catFilter={catFilter} setCatFilter={setCatFilter} />}
      {activeTab === 'summaries' && <SummariesTab summaries={summaries} catFilter={catFilter} setCatFilter={setCatFilter} />}
      {activeTab === 'csv' && <CSVTab csvMessage={csvMessage} csvLoading={csvLoading} handleCsvUpload={handleCsvUpload} />}
    </div>
  )
}

function OverviewTab({ metrics, summaries, categories }) {
  const m = metrics || {}
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '0.75rem', marginBottom: '1.25rem' }}>
        <StatCard label="Today" value={m.messages_today} color="#3B82F6" />
        <StatCard label="Total" value={m.total_messages} color="#8B5CF6" />
        <StatCard label="Processed" value={m.done_messages} color="#10B981" />
        <StatCard label="Pending" value={m.pending_messages} color="#F59E0B" />
        <StatCard label="Open" value={m.open_leads} color="#EF4444" />
        <StatCard label="Customers" value={m.total_customers} color="#EC4899" />
        <StatCard label="Meetings" value={m.total_meetings} color="#06B6D4" />
      </div>

      {categories.length > 0 && (
        <div style={{ marginBottom: '1.25rem' }}>
          <h3 style={{ marginBottom: '0.5rem', fontSize: 14 }}>Category Breakdown</h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            {categories.filter(c => c.count > 0).map(c => (
              <CategoryBadge key={c.category} cat={c} />
            ))}
          </div>
        </div>
      )}

      {summaries.length > 0 && (
        <div>
          <h3 style={{ marginBottom: '0.5rem', fontSize: 14 }}>Latest Summaries</h3>
          {summaries.slice(0, 3).map(s => <SummaryCard key={s.id} s={s} compact />)}
        </div>
      )}
    </div>
  )
}

function CategoriesTab({ categories, summaries, catFilter, setCatFilter }) {
  const filtered = catFilter === 'all' ? summaries : summaries.filter(s => s.category === catFilter)
  const activeCat = categories.find(c => c.category === catFilter)

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem' }}>
        <CategoryBadge cat={{ category: 'all', count: summaries.length, color: '#64748B', icon: '📋', label: 'All' }} active={catFilter === 'all'} onClick={() => setCatFilter('all')} />
        {categories.filter(c => c.count > 0).map(c => (
          <CategoryBadge key={c.category} cat={c} active={catFilter === c.category} onClick={() => setCatFilter(c.category)} />
        ))}
      </div>

      {activeCat && catFilter !== 'all' && (
        <div style={{ marginBottom: '0.75rem', padding: '0.5rem 0.75rem', borderRadius: 8, background: activeCat.color + '22', border: `1px solid ${activeCat.color}44`, display: 'inline-block' }}>
          <span style={{ fontSize: 13, color: activeCat.color, fontWeight: 600 }}>{activeCat.icon} {activeCat.label}: {activeCat.count} messages ({activeCat.pct}%)</span>
        </div>
      )}

      {filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '2rem', color: '#94A3B8', fontSize: 14 }}>No messages in this category yet.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {filtered.map(s => <SummaryCard key={s.id} s={s} />)}
        </div>
      )}
    </div>
  )
}

function SummariesTab({ summaries, catFilter, setCatFilter }) {
  const filtered = catFilter === 'all' ? summaries : summaries.filter(s => s.category === catFilter)
  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem' }}>
        <CategoryBadge cat={{ category: 'all', count: summaries.length, color: '#64748B', icon: '📋', label: 'All' }} active={catFilter === 'all'} onClick={() => setCatFilter('all')} />
        {[...new Set(summaries.map(s => s.category))].map(cat => {
          const meta = CAT_META[cat] || CAT_META.general
          const cnt = summaries.filter(s => s.category === cat).length
          return <CategoryBadge key={cat} cat={{ category: cat, count: cnt, color: meta.color, icon: meta.icon, label: meta.label }} active={catFilter === cat} onClick={() => setCatFilter(cat)} />
        })}
      </div>
      {filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '3rem', color: '#94A3B8' }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🤖</div>
          <h3>No summaries yet</h3>
          <p style={{ fontSize: 14 }}>Send a voice or text message via WhatsApp to get AI-powered summaries.</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {filtered.map(s => <SummaryCard key={s.id} s={s} />)}
        </div>
      )}
    </div>
  )
}

function CategoryBadge({ cat, active, onClick }) {
  const meta = CAT_META[cat.category] || { icon: cat.icon || '💬', color: cat.color || '#94A3B8' }
  return (
    <button onClick={onClick} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 14px',
      borderRadius: 20, border: `1.5px solid ${active ? meta.color : '#334155'}`,
      background: active ? meta.color + '22' : 'transparent', color: active ? meta.color : '#94A3B8',
      fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
    }}>
      {meta.icon} {cat.label || cat.category} <span style={{ opacity: 0.7, fontSize: 11 }}>{cat.count}</span>
    </button>
  )
}

function SummaryCard({ s, compact }) {
  const meta = CAT_META[s.category] || CAT_META.general
  const [expanded, setExpanded] = useState(false)
  const detail = s.detailed_summary || {}
  const entities = s.entities || {}

  return (
    <div style={{ ...CARD, borderLeft: `4px solid ${meta.color}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6, flexWrap: 'wrap', gap: 6 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 600, background: meta.color + '22', color: meta.color }}>
              {meta.icon} {meta.label}
            </span>
            {s.urgency && (
              <span style={{ padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 600, background: PRI_COLORS[s.urgency] + '22', color: PRI_COLORS[s.urgency] }}>
                {s.urgency}
              </span>
            )}
            {s.sentiment && <span style={{ fontSize: 11, color: '#64748B' }}>{s.sentiment}</span>}
          </div>
          <div style={{ fontWeight: 600, fontSize: 14, color: '#F1F5F9' }}>
            {s.customer_name || s.from_phone}
          </div>
        </div>
        <div style={{ fontSize: 11, color: '#64748B' }}>
          {s.processed_at ? new Date(s.processed_at).toLocaleString() : ''}
          <span style={{ marginLeft: 8, color: s.provider === 'groq' ? '#10B981' : '#F59E0B' }}>{s.provider ? 'via ' + s.provider : ''}</span>
        </div>
      </div>

      {s.intent && (
        <div style={{ fontSize: 13, color: '#CBD5E1', marginBottom: 8, fontStyle: 'italic', padding: '6px 10px', background: '#0F172A', borderRadius: 6 }}>
          {s.intent}
        </div>
      )}

      {s.summary && (
        <p style={{ margin: '0 0 8px 0', fontSize: 13, color: '#CBD5E1', lineHeight: 1.6 }}>
          {compact ? s.summary.substring(0, 200) + (s.summary.length > 200 ? '...' : '') : s.summary}
        </p>
      )}

      {!compact && (
        <>
          {detail.key_points && detail.key_points.length > 0 && (
            <Section title="Key Points" color="#60A5FA">
              {detail.key_points.map((p, i) => <li key={i}>{p}</li>)}
            </Section>
          )}
          {detail.decisions && detail.decisions.length > 0 && (
            <Section title="Decisions" color="#10B981">
              {detail.decisions.map((d, i) => <li key={i}>{d}</li>)}
            </Section>
          )}
          {detail.risks_concerns && detail.risks_concerns.length > 0 && (
            <Section title="Risks & Concerns" color="#EF4444">
              {detail.risks_concerns.map((r, i) => <li key={i}>{r}</li>)}
            </Section>
          )}
          {detail.next_steps && detail.next_steps.length > 0 && (
            <Section title="Next Steps" color="#F59E0B">
              {detail.next_steps.map((n, i) => <li key={i}>{n}</li>)}
            </Section>
          )}
          {entities && Object.keys(entities).some(k => entities[k] && entities[k].length > 0) && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#64748B', marginBottom: 4 }}>Entities</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {entities.people && entities.people.map((p, i) => <Tag key={`p${i}`} icon="👤">{p}</Tag>)}
                {entities.dates && entities.dates.map((d, i) => <Tag key={`d${i}`} icon="📅">{d}</Tag>)}
                {entities.amounts && entities.amounts.map((a, i) => <Tag key={`a${i}`} icon="💵">{a}</Tag>)}
                {entities.documents && entities.documents.map((d, i) => <Tag key={`doc${i}`} icon="📄">{d}</Tag>)}
                {entities.locations && entities.locations.map((l, i) => <Tag key={`l${i}`} icon="📍">{l}</Tag>)}
              </div>
            </div>
          )}
          {s.tags && s.tags.length > 0 && (
            <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {s.tags.map((t, i) => <Tag key={i}>{t}</Tag>)}
            </div>
          )}
          {s.meeting_suggestion && (
            <div style={{ marginTop: 8, padding: '8px 12px', background: '#0A2A1A', borderRadius: 8, border: '1px solid #10B98133' }}>
              <div style={{ fontWeight: 600, fontSize: 12, color: '#10B981' }}>📅 Meeting Suggested</div>
              <div style={{ fontSize: 12, color: '#CBD5E1', marginTop: 2 }}>{s.meeting_suggestion}</div>
            </div>
          )}
        </>
      )}

      {compact && (s.tags?.length > 0 || s.entities?.people?.length > 0 || s.meeting_suggestion) && (
        <button onClick={() => setExpanded(!expanded)} style={{ background: 'none', border: 'none', color: '#3B82F6', cursor: 'pointer', fontSize: 12, padding: 0, fontFamily: 'inherit', marginTop: 4 }}>
          {expanded ? '▲ Less' : '▼ More details'}
        </button>
      )}

      {compact && expanded && (
        <div style={{ marginTop: 8 }}>
          {detail.key_points && detail.key_points.length > 0 && <Section title="Key Points" color="#60A5FA">{detail.key_points.map((p, i) => <li key={i}>{p}</li>)}</Section>}
          {detail.next_steps && detail.next_steps.length > 0 && <Section title="Next Steps" color="#F59E0B">{detail.next_steps.map((n, i) => <li key={i}>{n}</li>)}</Section>}
          {s.meeting_suggestion && <div style={{ marginTop: 6, fontSize: 12, color: '#10B981' }}>📅 {s.meeting_suggestion}</div>}
        </div>
      )}
    </div>
  )
}

function Section({ title, color, children }) {
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 11, fontWeight: 600, color, marginBottom: 3 }}>{title}</div>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#CBD5E1', lineHeight: 1.7 }}>{children}</ul>
    </div>
  )
}

function Tag({ icon, children }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px', borderRadius: 6, background: '#334155', color: '#94A3B8', fontSize: 11, fontFamily: 'inherit' }}>
      {icon ? icon + ' ' : ''}{children}
    </span>
  )
}

function StatCard({ label, value, color }) {
  return <div style={{ ...CARD, textAlign: 'center', padding: '0.75rem' }}><div style={{ fontSize: 24, fontWeight: 700, color }}>{value}</div><div style={{ fontSize: 11, color: '#94A3B8', marginTop: 2 }}>{label}</div></div>
}

function CSVTab({ csvMessage, csvLoading, handleCsvUpload }) {
  return (
    <div style={CARD}>
      <h3>📥 Bulk Import</h3>
      <p style={{ color: '#94A3B8', fontSize: 13 }}>Upload CSV (columns: name, email, phone, company, title)</p>
      <div style={{ border: '2px dashed #3B82F6', borderRadius: 8, padding: '2rem', textAlign: 'center', cursor: 'pointer', background: '#0F172A', margin: '1rem 0' }}>
        <input type="file" accept=".csv" onChange={handleCsvUpload} disabled={csvLoading} id="csv-upload" style={{ display: 'none' }} />
        <label htmlFor="csv-upload" style={{ cursor: 'pointer' }}><div style={{ fontSize: 32, marginBottom: 8 }}>📁</div><div style={{ color: '#94A3B8', fontSize: 14 }}>Click to upload CSV</div></label>
      </div>
      {csvMessage && <div style={{ padding: '8px 12px', borderRadius: 6, background: csvMessage.includes('Error') ? '#7f1d1d44' : '#14532d44', color: csvMessage.includes('Error') ? '#FCA5A5' : '#86EFAC', fontSize: 13 }}>{csvMessage}</div>}
    </div>
  )
}

// ── Voice Recorder ────────────────────────────────────────────────────────
function VoiceRecorder({ apiCall, onClose }) {
  const [recording, setRecording] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [mediaRecorder, setMediaRecorder] = useState(null)
  const [textMode, setTextMode] = useState(false)
  const [textInput, setTextInput] = useState('')

  const startRecording = async () => {
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      const chunks = []
      mr.ondataavailable = e => chunks.push(e.data)
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop())
        const blob = new Blob(chunks, { type: 'audio/webm' })
        const fd = new FormData()
        fd.append('file', blob, 'recording.webm')
        setLoading(true)
        try {
          const r = await apiCall('/api/broker/transcribe', { method: 'POST', body: fd, headers: {} })
          const d = await r.json()
          if (d.transcript) { setTranscript(d.transcript); await summarize(d.transcript) }
          else setError('No speech detected.')
        } catch (e) { setError('Transcription failed: ' + e.message) }
        finally { setLoading(false) }
      }
      mr.start()
      setMediaRecorder(mr)
      setRecording(true)
    } catch (e) { setError('Microphone access denied. Use WhatsApp or text mode.') }
  }

  const stopRecording = () => { if (mediaRecorder?.state === 'recording') { mediaRecorder.stop(); setRecording(false) } }

  const summarize = async (text) => {
    setLoading(true)
    try {
      const r = await apiCall('/api/broker/summarize', { method: 'POST', body: JSON.stringify({ transcript: text }) })
      const d = await r.json()
      setSummary(d.extracted_data)
    } catch (e) { setError('Summarization failed: ' + e.message) }
    finally { setLoading(false) }
  }

  const handleTextSubmit = async () => {
    if (!textInput.trim()) return
    setLoading(true)
    setTranscript(textInput)
    try {
      const r = await apiCall('/api/broker/summarize', { method: 'POST', body: JSON.stringify({ transcript: textInput }) })
      const d = await r.json()
      setSummary(d.extracted_data)
    } catch (e) { setError('Summarization failed: ' + e.message) }
    finally { setLoading(false) }
  }

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
        <h3 style={{ margin: 0 }}>🎤 Voice Note</h3>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#94A3B8', cursor: 'pointer', fontSize: 18 }}>✕</button>
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: '1rem' }}>
        <button onClick={() => setTextMode(false)} style={{ padding: '6px 16px', borderRadius: 6, border: 'none', background: !textMode ? '#3B82F6' : '#334155', color: '#fff', cursor: 'pointer', fontFamily: 'inherit', fontSize: 13 }}>🎤 Voice</button>
        <button onClick={() => setTextMode(true)} style={{ padding: '6px 16px', borderRadius: 6, border: 'none', background: textMode ? '#3B82F6' : '#334155', color: '#fff', cursor: 'pointer', fontFamily: 'inherit', fontSize: 13 }}>💬 Text</button>
      </div>
      {textMode ? (
        <div>
          <textarea value={textInput} onChange={e => setTextInput(e.target.value)} placeholder="Type or paste notes..." style={{ width: '100%', minHeight: 120, padding: 12, borderRadius: 8, border: '1px solid #334155', background: '#0F172A', color: '#F1F5F9', fontSize: 14, fontFamily: 'inherit', resize: 'vertical', boxSizing: 'border-box' }} />
          <button onClick={handleTextSubmit} disabled={loading || !textInput.trim()} style={{ marginTop: 8, padding: '10px 24px', background: '#3B82F6', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontFamily: 'inherit', opacity: loading ? 0.6 : 1, width: '100%' }}>{loading ? '⏳ Processing...' : '🤖 Summarize with AI'}</button>
        </div>
      ) : (
        <div style={{ textAlign: 'center', padding: '2rem', ...CARD }}>
          {recording ? (
            <div>
              <div style={{ width: 60, height: 60, borderRadius: '50%', background: '#EF4444', margin: '0 auto 1rem', animation: 'pulse 1.5s infinite' }} />
              <div style={{ color: '#EF4444', fontWeight: 600, marginBottom: '1rem' }}>🔴 Recording...</div>
              <button onClick={stopRecording} style={{ padding: '12px 32px', background: '#EF4444', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontFamily: 'inherit', fontSize: 15 }}>⏹ Stop</button>
            </div>
          ) : (
            <div>
              <button onClick={startRecording} style={{ width: 72, height: 72, borderRadius: '50%', background: '#25D366', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 0.75rem' }}><span style={{ fontSize: 28 }}>🎤</span></button>
              <div style={{ color: '#CBD5E1', fontSize: 13 }}>Tap to record</div>
            </div>
          )}
        </div>
      )}
      {loading && <div style={{ textAlign: 'center', padding: '2rem', color: '#94A3B8' }}>⏳ Processing with AI...</div>}
      {error && <div style={{ padding: '10px 14px', background: '#7f1d1d44', borderRadius: 8, color: '#FCA5A5', fontSize: 13, marginTop: 8 }}>{error}</div>}
      {summary && (
        <div style={{ marginTop: '1rem' }}>
          <SummaryCard s={{
            customer_name: summary.customer_name || 'Voice Note',
            category: summary.category || 'general',
            intent: summary.intent || '',
            summary: summary.summary || summary.detailed_summary?.overview || '',
            detailed_summary: summary.detailed_summary || {},
            entities: summary.entities || {},
            sentiment: summary.sentiment || '',
            tags: summary.tags || [],
            meeting_suggestion: summary.meeting_suggestion,
            urgency: summary.urgency || 'medium',
            processed_at: new Date().toISOString(),
            provider: summary._llm_provider || 'unknown',
          }} />
        </div>
      )}
    </div>
  )
}

export default Dashboard
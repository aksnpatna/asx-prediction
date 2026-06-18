import React, { useState, useEffect, useRef } from 'react'

/**
 * BrokerSetup — first-time onboarding wizard shown after registration/login
 * when setup_complete === false.
 *
 * Steps:
 *   1. Register your WhatsApp phone number
 *      → User enters their personal mobile number
 *      → App tells them to send a test message to the Meta business number
 *      → Polls /api/broker/profile/wa-verify until confirmed (or manual skip)
 *   2. Set up email notifications (SMTP)
 *      → User enters Gmail / SMTP details
 *      → Click "Send Test Email" — real send, show result
 *   3. Done → mark setup_complete, enter the app
 */

const T = {
  bg:      '#0F172A',
  surface: '#1E293B',
  surface2:'#243246',
  border:  '#334155',
  fg:      '#F1F5F9',
  fgAlt:   '#94A3B8',
  primary: '#3B82F6',
  green:   '#10B981',
  red:     '#EF4444',
  amber:   '#F59E0B',
}

function Step({ n, label, active, done }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, opacity: done || active ? 1 : 0.4 }}>
      <div style={{
        width: 32, height: 32, borderRadius: '50%',
        background: done ? T.green : active ? T.primary : T.border,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 14, fontWeight: 700, color: '#fff', flexShrink: 0,
      }}>
        {done ? '✓' : n}
      </div>
      <span style={{ fontSize: 14, fontWeight: active ? 600 : 400, color: active ? T.fg : T.fgAlt }}>
        {label}
      </span>
    </div>
  )
}

function Field({ label, children, hint }) {
  return (
    <div style={{ marginBottom: '1.1rem' }}>
      <label style={{
        display: 'block', fontSize: 11, fontWeight: 600,
        color: T.fgAlt, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6
      }}>{label}</label>
      {children}
      {hint && <p style={{ margin: '5px 0 0', fontSize: 12, color: T.fgAlt }}>{hint}</p>}
    </div>
  )
}

function Input({ value, onChange, placeholder, type = 'text', disabled }) {
  return (
    <input
      type={type}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      disabled={disabled}
      style={{
        width: '100%', padding: '9px 12px',
        border: `1px solid ${T.border}`, borderRadius: 8,
        background: T.bg, color: T.fg, fontSize: 14,
        fontFamily: 'inherit', outline: 'none',
        opacity: disabled ? 0.5 : 1,
      }}
    />
  )
}

function Btn({ onClick, disabled, children, variant = 'primary', loading }) {
  const bg = { primary: T.primary, secondary: T.border, green: T.green, danger: T.red }[variant] || T.primary
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      style={{
        padding: '9px 20px', border: 'none', borderRadius: 8,
        background: bg, color: '#fff', fontSize: 13, fontWeight: 600,
        fontFamily: 'inherit', cursor: disabled || loading ? 'not-allowed' : 'pointer',
        opacity: disabled || loading ? 0.6 : 1,
        transition: 'opacity 0.15s',
      }}
    >
      {loading ? '⏳ ...' : children}
    </button>
  )
}

function Alert({ type, children }) {
  const styles = {
    success: { bg: '#10B98122', border: '#10B98166', color: '#6EE7B7' },
    error:   { bg: '#EF444422', border: '#EF444466', color: '#FCA5A5' },
    info:    { bg: '#3B82F622', border: '#3B82F666', color: '#93C5FD' },
    warning: { bg: '#F59E0B22', border: '#F59E0B66', color: '#FDE68A' },
  }[type] || {}
  return (
    <div style={{
      padding: '10px 14px', borderRadius: 8, marginBottom: 12,
      background: styles.bg, border: `1px solid ${styles.border}`,
      color: styles.color, fontSize: 13, lineHeight: 1.5,
    }}>{children}</div>
  )
}

// ── Step 1: WhatsApp phone setup ──────────────────────────────────────────────
function StepWhatsApp({ apiCall, metaNumber, initialPhone, onComplete }) {
  const [phone, setPhone] = useState(initialPhone || '')
  const [saved, setSaved] = useState(!!initialPhone)
  const [saving, setSaving] = useState(false)
  const [verified, setVerified] = useState(false)
  const [polling, setPolling] = useState(false)
  const [err, setErr] = useState('')
  const [info, setInfo] = useState('')
  const pollRef = useRef(null)

  const savePhone = async () => {
    setErr('')
    setSaving(true)
    try {
      const r = await apiCall('/api/broker/profile/whatsapp-phone', {
        method: 'PUT',
        body: JSON.stringify({ phone })
      })
      if (!r.ok) {
        const d = await r.json()
        throw new Error(d.detail || 'Failed to save phone')
      }
      setSaved(true)
      setInfo(`✅ Phone saved — now send any WhatsApp message to ${metaNumber || 'the Meta business number'} from your phone and we'll confirm the connection.`)
      startPolling()
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  const startPolling = () => {
    if (pollRef.current) return
    setPolling(true)
    let attempts = 0
    pollRef.current = setInterval(async () => {
      attempts++
      try {
        const r = await apiCall('/api/broker/profile/wa-verify')
        const d = await r.json()
        if (d.verified) {
          clearInterval(pollRef.current)
          pollRef.current = null
          setPolling(false)
          setVerified(true)
          setInfo('🎉 WhatsApp connection verified!')
        }
      } catch {}
      if (attempts >= 24) { // 4 min timeout
        clearInterval(pollRef.current)
        pollRef.current = null
        setPolling(false)
      }
    }, 10000)
  }

  useEffect(() => () => pollRef.current && clearInterval(pollRef.current), [])

  return (
    <div>
      <h3 style={{ color: T.fg, marginBottom: 6, fontSize: '1.1rem' }}>📱 Connect Your WhatsApp</h3>
      <p style={{ color: T.fgAlt, fontSize: 14, marginBottom: 20, lineHeight: 1.5 }}>
        Enter the mobile number you'll use to send WhatsApp notes and voice messages. This connects your phone to your account.
      </p>

      {err && <Alert type="error">{err}</Alert>}
      {info && <Alert type={verified ? 'success' : 'info'}>{info}</Alert>}

      <Field
        label="Your Mobile Number"
        hint="Include country code without +  e.g. 61412345678 for Australian mobile 0412 345 678"
      >
        <div style={{ display: 'flex', gap: 8 }}>
          <Input
            value={phone}
            onChange={e => setPhone(e.target.value.replace(/\D/g, ''))}
            placeholder="61412345678"
            disabled={verified}
          />
          {!verified && (
            <Btn onClick={savePhone} loading={saving} disabled={phone.length < 7}>
              Save
            </Btn>
          )}
        </div>
      </Field>

      {saved && !verified && (
        <div style={{
          background: T.surface2, border: `1px solid ${T.border}`,
          borderRadius: 10, padding: '1rem 1.25rem', marginBottom: '1rem',
        }}>
          <p style={{ margin: '0 0 8px', fontWeight: 600, color: T.fg }}>
            📩 Now send a WhatsApp message
          </p>
          <p style={{ margin: '0 0 4px', color: T.fgAlt, fontSize: 13 }}>
            Open WhatsApp on your phone and send <strong style={{ color: T.fg }}>any message</strong> to:
          </p>
          <p style={{ margin: '4px 0 8px', fontSize: 18, fontWeight: 700, color: T.primary, letterSpacing: 1 }}>
            {metaNumber || 'the Meta business number'}
          </p>
          <p style={{ margin: 0, fontSize: 12, color: T.fgAlt }}>
            {polling ? '🔄 Waiting for message...' : 'Checking every 10 seconds.'}
          </p>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
        {verified ? (
          <Btn variant="green" onClick={onComplete}>Continue →</Btn>
        ) : (
          <>
            <Btn variant="secondary" onClick={onComplete}>Skip for now</Btn>
            {saved && !polling && (
              <Btn onClick={() => { startPolling(); setInfo('🔄 Checking for your message...') }}>
                Re-check
              </Btn>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Step 2: SMTP setup ────────────────────────────────────────────────────────
function StepSMTP({ apiCall, initialData, onComplete }) {
  const [form, setForm] = useState({
    smtp_host: initialData?.smtp_host || 'smtp.gmail.com',
    smtp_port: initialData?.smtp_port || 587,
    smtp_user: initialData?.smtp_user || '',
    smtp_password: '',
    smtp_from: initialData?.smtp_from || '',
  })
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saved, setSaved] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [err, setErr] = useState('')

  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const save = async () => {
    setErr('')
    setSaving(true)
    try {
      const r = await apiCall('/api/broker/profile/smtp', {
        method: 'PUT',
        body: JSON.stringify(form)
      })
      if (!r.ok) {
        const d = await r.json()
        throw new Error(d.detail || 'Failed to save SMTP')
      }
      setSaved(true)
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  const sendTest = async () => {
    setErr('')
    setTestResult(null)
    setTesting(true)
    try {
      const r = await apiCall('/api/broker/profile/test-email', {
        method: 'POST',
        body: JSON.stringify(form)
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Test failed')
      setTestResult({ ok: true, msg: `✅ Test email sent to ${d.sent_to}! Check your inbox.` })
      setSaved(true)
    } catch (e) {
      setTestResult({ ok: false, msg: `❌ ${e.message}` })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div>
      <h3 style={{ color: T.fg, marginBottom: 6, fontSize: '1.1rem' }}>✉️ Email Notifications</h3>
      <p style={{ color: T.fgAlt, fontSize: 14, marginBottom: 8, lineHeight: 1.5 }}>
        When a customer books a meeting via WhatsApp, we'll email you a calendar invite.
        Enter your <strong style={{ color: T.fg }}>Gmail App Password</strong> (not your regular password).
      </p>
      <Alert type="info">
        Gmail requires an <strong>App Password</strong>. Go to{' '}
        <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer"
           style={{ color: T.primary }}>
          myaccount.google.com/apppasswords
        </a>
        {' '}(2FA must be enabled) → create App Password → paste it below.
      </Alert>

      {err && <Alert type="error">{err}</Alert>}
      {testResult && <Alert type={testResult.ok ? 'success' : 'error'}>{testResult.msg}</Alert>}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, marginBottom: 12 }}>
        <Field label="SMTP Host">
          <Input value={form.smtp_host} onChange={e => update('smtp_host', e.target.value)} placeholder="smtp.gmail.com" />
        </Field>
        <Field label="Port">
          <input
            type="number"
            value={form.smtp_port}
            onChange={e => update('smtp_port', e.target.value)}
            style={{
              width: 80, padding: '9px 10px', border: `1px solid ${T.border}`,
              borderRadius: 8, background: T.bg, color: T.fg, fontSize: 14,
              fontFamily: 'inherit', outline: 'none',
            }}
          />
        </Field>
      </div>

      <Field label="Email (SMTP username)" hint="Your full Gmail address">
        <Input
          type="email"
          value={form.smtp_user}
          onChange={e => { update('smtp_user', e.target.value); if (!form.smtp_from) update('smtp_from', e.target.value) }}
          placeholder="you@gmail.com"
        />
      </Field>

      <Field label="App Password" hint="16-character Gmail App Password (spaces are ok)">
        <Input
          type="password"
          value={form.smtp_password}
          onChange={e => update('smtp_password', e.target.value)}
          placeholder="xxxx xxxx xxxx xxxx"
        />
      </Field>

      <Field label="From Address (optional)" hint="Leave blank to use email above">
        <Input
          type="email"
          value={form.smtp_from}
          onChange={e => update('smtp_from', e.target.value)}
          placeholder="you@gmail.com"
        />
      </Field>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 4 }}>
        <Btn onClick={sendTest} loading={testing} disabled={!form.smtp_user || !form.smtp_password}>
          📧 Send Test Email
        </Btn>
        <Btn variant="secondary" onClick={onComplete}>
          Skip for now
        </Btn>
        {(saved || testResult?.ok) && (
          <Btn variant="green" onClick={onComplete}>
            Continue →
          </Btn>
        )}
      </div>
    </div>
  )
}

// ── Main BrokerSetup wizard ───────────────────────────────────────────────────
export default function BrokerSetup({ apiCall, brokerId, onSetupComplete }) {
  const [step, setStep] = useState(1)
  const [profile, setProfile] = useState(null)

  useEffect(() => {
    apiCall('/api/broker/profile')
      .then(r => r.json())
      .then(d => setProfile(d))
      .catch(() => {})
  }, []) // eslint-disable-line

  const markComplete = async () => {
    try {
      await apiCall('/api/broker/profile/setup-complete', { method: 'POST' })
    } catch {}
    onSetupComplete()
  }

  return (
    <div style={{
      minHeight: '100vh', background: T.bg,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '1rem',
    }}>
      <div style={{
        background: T.surface, border: `1px solid ${T.border}`,
        borderRadius: 16, padding: '2rem', width: '100%', maxWidth: 520,
        boxShadow: '0 24px 64px rgba(0,0,0,0.4)',
      }}>
        {/* Header */}
        <div style={{ marginBottom: '1.75rem' }}>
          <h2 style={{ color: T.fg, margin: '0 0 6px', fontSize: '1.3rem' }}>
            👋 Welcome, {brokerId}!
          </h2>
          <p style={{ color: T.fgAlt, margin: 0, fontSize: 14 }}>
            Complete this quick setup to activate your CRM.
          </p>
        </div>

        {/* Step indicators */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: '2rem' }}>
          <Step n={1} label="Connect WhatsApp" active={step === 1} done={step > 1} />
          <div style={{ width: 1, height: 12, background: T.border, marginLeft: 15 }} />
          <Step n={2} label="Set up email notifications" active={step === 2} done={step > 2} />
          <div style={{ width: 1, height: 12, background: T.border, marginLeft: 15 }} />
          <Step n={3} label="All done!" active={step === 3} done={false} />
        </div>

        <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: '1.5rem' }}>
          {step === 1 && (
            <StepWhatsApp
              apiCall={apiCall}
              metaNumber={profile?.meta_whatsapp_number}
              initialPhone={profile?.whatsapp_from_phone}
              onComplete={() => setStep(2)}
            />
          )}

          {step === 2 && (
            <StepSMTP
              apiCall={apiCall}
              initialData={profile}
              onComplete={() => setStep(3)}
            />
          )}

          {step === 3 && (
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 56, marginBottom: 12 }}>🎉</div>
              <h3 style={{ color: T.fg, marginBottom: 8 }}>You're all set!</h3>
              <p style={{ color: T.fgAlt, fontSize: 14, marginBottom: 24, lineHeight: 1.5 }}>
                Your inbox is ready. Start sending messages and they'll be automatically tracked, transcribed, and summarised.
              </p>
              <Btn variant="green" onClick={markComplete}>
                Enter the app →
              </Btn>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

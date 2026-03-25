import React, { useState, useEffect } from 'react'

function Meetings({ token, apiCall, setError, setSuccess }) {
  const [meetings, setMeetings] = useState([])
  const [customers, setCustomers] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [formData, setFormData] = useState({
    customer_id: '',
    title: '',
    description: '',
    scheduled_at: '',
    duration_minutes: 30,
    notes: ''
  })

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      setLoading(true)
      const [meetingsRes, customersRes] = await Promise.all([
        apiCall('/api/broker/meetings'),
        apiCall('/api/broker/customers')
      ])

      const meetingsData = await meetingsRes.json()
      const customersData = await customersRes.json()

      setMeetings(Array.isArray(meetingsData) ? meetingsData : [])
      setCustomers(Array.isArray(customersData) ? customersData : [])
    } catch (err) {
      setError('Failed to load data: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()

    try {
      const method = editingId ? 'PUT' : 'POST'
      const endpoint = editingId
        ? `/api/broker/meetings/${editingId}`
        : '/api/broker/meetings'

      const response = await apiCall(endpoint, {
        method,
        body: JSON.stringify({
          ...formData,
          customer_id: parseInt(formData.customer_id),
          duration_minutes: parseInt(formData.duration_minutes),
          scheduled_at: new Date(formData.scheduled_at).toISOString()
        })
      })

      const data = await response.json()

      if (response.ok) {
        setSuccess(editingId ? 'Meeting updated!' : 'Meeting created!')
        loadData()
        resetForm()
      } else {
        setError(data.detail || 'Failed to save meeting')
      }
    } catch (err) {
      setError('Error: ' + err.message)
    }
  }

  const handleEdit = (meeting) => {
    setFormData({
      customer_id: meeting.customer_id,
      title: meeting.title,
      description: meeting.description || '',
      scheduled_at: meeting.scheduled_at.substring(0, 16),
      duration_minutes: meeting.duration_minutes,
      notes: meeting.notes || ''
    })
    setEditingId(meeting.id)
    setShowForm(true)
  }

  const resetForm = () => {
    setFormData({
      customer_id: '',
      title: '',
      description: '',
      scheduled_at: '',
      duration_minutes: 30,
      notes: ''
    })
    setEditingId(null)
    setShowForm(false)
  }

  const getCustomerName = (id) => {
    const customer = customers.find(c => c.id === id)
    return customer ? customer.name : 'Unknown'
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <h2>Meetings</h2>
        <button
          className="btn btn-primary"
          onClick={() => setShowForm(!showForm)}
        >
          {showForm ? '✕ Cancel' : '+ Schedule Meeting'}
        </button>
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <h3>{editingId ? 'Edit Meeting' : 'Schedule New Meeting'}</h3>
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label>Customer *</label>
              <select
                value={formData.customer_id}
                onChange={(e) => setFormData({ ...formData, customer_id: e.target.value })}
                required
              >
                <option value="">Select a customer</option>
                {customers.map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>Title *</label>
              <input
                value={formData.title}
                onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                required
              />
            </div>
            <div className="form-group">
              <label>Description</label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                rows="2"
              />
            </div>
            <div className="form-group">
              <label>Date & Time *</label>
              <input
                type="datetime-local"
                value={formData.scheduled_at}
                onChange={(e) => setFormData({ ...formData, scheduled_at: e.target.value })}
                required
              />
            </div>
            <div className="form-group">
              <label>Duration (minutes) *</label>
              <input
                type="number"
                value={formData.duration_minutes}
                onChange={(e) => setFormData({ ...formData, duration_minutes: e.target.value })}
                min="5"
                required
              />
            </div>
            <div className="form-group">
              <label>Notes</label>
              <textarea
                value={formData.notes}
                onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                rows="2"
              />
            </div>
            <div className="btn-group">
              <button type="submit" className="btn btn-primary">
                {editingId ? 'Update' : 'Schedule'} Meeting
              </button>
              <button type="button" className="btn btn-secondary" onClick={resetForm}>
                Reset
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div className="loading">Loading meetings...</div>
      ) : meetings.length === 0 ? (
        <div className="card">
          <p>No meetings scheduled. Create one to get started!</p>
        </div>
      ) : (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Date & Time</th>
                <th>Customer</th>
                <th>Title</th>
                <th>Duration</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {meetings.map((meeting) => (
                <tr key={meeting.id}>
                  <td>{new Date(meeting.scheduled_at).toLocaleString()}</td>
                  <td>{getCustomerName(meeting.customer_id)}</td>
                  <td>{meeting.title}</td>
                  <td>{meeting.duration_minutes} min</td>
                  <td>
                    <div className="btn-group">
                      <button
                        className="btn btn-secondary"
                        onClick={() => handleEdit(meeting)}
                        style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}
                      >
                        Edit
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default Meetings

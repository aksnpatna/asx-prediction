import React, { useState, useRef, useEffect } from 'react'

function VoiceRecorder({ token, apiCall, setError, setSuccess }) {
  const [isRecording, setIsRecording] = useState(false)
  const [recordingTime, setRecordingTime] = useState(0)
  const [audioBlob, setAudioBlob] = useState(null)
  const [transcript, setTranscript] = useState('')
  const [liveTranscript, setLiveTranscript] = useState('') // real-time partial transcript
  const [extractedData, setExtractedData] = useState('')
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [isExtracting, setIsExtracting] = useState(false)
  const [meetings, setMeetings] = useState([])
  const [selectedMeetingId, setSelectedMeetingId] = useState('')
  const [loadingMeetings, setLoadingMeetings] = useState(false)
  const [audioDevices, setAudioDevices] = useState([])
  const [selectedDeviceId, setSelectedDeviceId] = useState('')
  const [permissionStatus, setPermissionStatus] = useState('prompt') // prompt, granted, denied
  const [speechApiSupported] = useState(() => !!(
    window.SpeechRecognition || window.webkitSpeechRecognition
  ))
  
  const mediaRecorderRef = useRef(null)
  const audioContextRef = useRef(null)
  const recordingIntervalRef = useRef(null)
  const chunksRef = useRef([])
  const streamRef = useRef(null)
  const speechRecognitionRef = useRef(null)
  const finalTranscriptRef = useRef('')

  // Detect mobile platform
  const isMobile = () => {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
  }

  // Request microphone permission explicitly
  const requestMicPermission = async () => {
    try {
      console.log('🎙️ Requesting microphone permission...')
      setSuccess('⏳ Requesting microphone access...\nLook for permission prompt in browser')
      
      const startTime = Date.now()
      let tempStream = null
      
      try {
        console.log('📱 Calling getUserMedia to request permission...')
        tempStream = await navigator.mediaDevices.getUserMedia({ audio: true })
        console.log('✅ Got audio stream successfully')
      } catch (err) {
        console.error('getUserMedia failed:', err.name, err.message)
        if (err.name === 'NotAllowedError') {
          console.log('❌ User denied permission')
          setPermissionStatus('denied')
          setError('❌ You denied microphone access. Click "Try Again" to allow it.')
          return
        }
        throw err
      }
      
      // Permission was granted! Stop the temporary stream
      if (tempStream) {
        tempStream.getTracks().forEach(track => {
          console.log('Stopping temporary stream track:', track.kind)
          track.stop()
        })
        console.log('✅ Closed temporary audio stream')
      }
      
      // Explicitly set permission to granted
      console.log('Setting permission status to granted')
      setPermissionStatus('granted')
      setSuccess('✅ Permission granted! Scanning for microphones...')
      
      // Wait for system to fully register the devices
      await new Promise(resolve => setTimeout(resolve, 300))
      
      // Also update Permissions API if available
      if (navigator.permissions && navigator.permissions.query) {
        try {
          const permission = await navigator.permissions.query({ name: 'microphone' })
          console.log('Permissions API state:', permission.state)
        } catch (err) {
          console.warn('Permissions API check failed:', err.message)
        }
      }
      
      // Enumerate devices with retry logic
      console.log('📊 Starting device enumeration...')
      const found = await enumerateAudioDevices(0)
      
      const elapsed = Date.now() - startTime
      console.log(`✅ Permission request completed in ${elapsed}ms, devices found: ${found}`)
      
      if (!found) {
        setSuccess('⚠️ Permission granted but no microphones detected.\nTry Re-scan button or check device connections.')
      }
      
    } catch (err) {
      console.error('Permission request error:', err)
      if (err.name === 'NotAllowedError') {
        setPermissionStatus('denied')
        setError('❌ Microphone permission DENIED.\n\nFor Edge browser:\n1. Click settings ⋯ in top right\n2. Settings → Privacy → Site permissions\n3. Find this site and set Microphone to "Allow"\n4. Refresh and try again')
      } else if (err.name === 'NotFoundError') {
        setError('❌ No microphone device found.\n\nTroubleshoot:\n1. Check if USB headset is connected\n2. Right-click volume icon in taskbar\n3. Select "Sound settings"\n4. Verify microphone appears in recording devices')
      } else if (err.name === 'SecurityError') {
        setError('🔒 Security Error: HTTPS required for microphone.')
      } else {
        setError(`❌ Failed: ${err.message}`)
      }
    }
  }

  // Check and enumerate audio devices with retry logic
  const enumerateAudioDevices = async (retryCount = 0) => {
    try {
      console.log(`📊 Attempt ${retryCount + 1}: Enumerating audio devices...`)
      const devices = await navigator.mediaDevices.enumerateDevices()
      const audioInputs = devices.filter(device => device.kind === 'audioinput')
      
      console.log(`Found ${audioInputs.length} audio device(s):`, audioInputs.map(d => ({ label: d.label, id: d.deviceId })))
      
      if (audioInputs.length === 0 && retryCount < 3) {
        // Device list might not be populated immediately after permission grant
        console.warn(`⚠️ No devices found yet (attempt ${retryCount + 1}/3), retrying in 500ms...`)
        await new Promise(resolve => setTimeout(resolve, 500))
        return enumerateAudioDevices(retryCount + 1)
      }
      
      setAudioDevices(audioInputs)
      
      if (audioInputs.length > 0) {
        setSelectedDeviceId(audioInputs[0].deviceId)
        console.log(`✅ Success! Found ${audioInputs.length} audio device(s)`)
        setSuccess(`✅ Found ${audioInputs.length} microphone(s)! Select one above.`)
      } else {
        console.warn('⚠️ No audio input devices detected after 3 attempts')
      }
      return audioInputs.length > 0
    } catch (err) {
      console.error('Device enumeration error:', err)
      if (retryCount < 2) {
        console.log(`Retrying after error... (attempt ${retryCount + 1}/3)`)
        await new Promise(resolve => setTimeout(resolve, 500))
        return enumerateAudioDevices(retryCount + 1)
      }
      return false
    }
  }

  // Check microphone permission status
  const checkMicPermission = async () => {
    try {
      if (navigator.permissions && navigator.permissions.query) {
        const permission = await navigator.permissions.query({ name: 'microphone' })
        setPermissionStatus(permission.state)
        console.log('📱 Microphone permission:', permission.state)
        
        permission.addEventListener('change', () => {
          setPermissionStatus(permission.state)
        })
      }
    } catch (err) {
      console.warn('Permission check not supported:', err.message)
    }
  }

  useEffect(() => {
    loadMeetings()
    checkMicPermission()
    
    // Only enumerate devices if permission already granted
    // Otherwise user needs to click "Request Permission" first
    if (permissionStatus === 'granted') {
      enumerateAudioDevices()
    }
    
    // Listen for device changes
    if (navigator.mediaDevices) {
      navigator.mediaDevices.addEventListener('devicechange', enumerateAudioDevices)
    }
    
    return () => {
      clearInterval(recordingIntervalRef.current)
      if (navigator.mediaDevices) {
        navigator.mediaDevices.removeEventListener('devicechange', enumerateAudioDevices)
      }
    }
  }, [])
  
  // Re-enumerate devices when permission status changes
  useEffect(() => {
    if (permissionStatus === 'granted' && audioDevices.length === 0) {
      enumerateAudioDevices()
    }
  }, [permissionStatus])

  const loadMeetings = async () => {
    try {
      setLoadingMeetings(true)
      const response = await apiCall('/api/broker/meetings')
      const data = await response.json()
      setMeetings(Array.isArray(data) ? data : [])
    } catch (err) {
      console.error('Failed to load meetings:', err)
    } finally {
      setLoadingMeetings(false)
    }
  }

  const checkAudioDevices = async () => {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const audioInputs = devices.filter(device => device.kind === 'audioinput')
      console.log('🔊 Audio devices available:', audioInputs)
      
      if (audioInputs.length === 0) {
        setError('❌ No audio input devices found. Please connect a microphone and refresh the page.')
        return false
      }
      
      console.log(`✅ Found ${audioInputs.length} audio device(s):`)
      audioInputs.forEach((device, i) => {
        console.log(`  ${i + 1}. ${device.label} (${device.deviceId})`)
      })
      return true
    } catch (err) {
      console.error('Device enumeration error:', err)
      setError('Could not access audio devices: ' + err.message)
      return false
    }
  }

  const startRecording = async () => {
    try {
      let stream
      try {
        const audioConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        if (selectedDeviceId) audioConstraints.deviceId = { ideal: selectedDeviceId }
        stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })
      } catch (err) {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      }
      streamRef.current = stream

      // --- MediaRecorder for saving audio blob ---
      let mimeType = 'audio/webm'
      if (!MediaRecorder.isTypeSupported('audio/webm')) mimeType = MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : ''
      const mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      mediaRecorderRef.current = mediaRecorder
      chunksRef.current = []
      mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      mediaRecorder.onstop = () => {
        setAudioBlob(new Blob(chunksRef.current, { type: mimeType || 'audio/webm' }))
        stream.getTracks().forEach(t => t.stop())
        streamRef.current = null
      }
      mediaRecorder.start()

      // --- Web Speech API for instant browser-side transcription ---
      finalTranscriptRef.current = ''
      setLiveTranscript('')
      if (speechApiSupported) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
        const recognition = new SpeechRecognition()
        recognition.continuous = true
        recognition.interimResults = true
        recognition.lang = 'en-US'
        recognition.onresult = (event) => {
          let interim = ''
          for (let i = event.resultIndex; i < event.results.length; i++) {
            const t = event.results[i][0].transcript
            if (event.results[i].isFinal) finalTranscriptRef.current += t + ' '
            else interim += t
          }
          setLiveTranscript(finalTranscriptRef.current + interim)
        }
        recognition.onerror = (e) => console.warn('SpeechRecognition error:', e.error)
        recognition.start()
        speechRecognitionRef.current = recognition
      }

      setIsRecording(true)
      setRecordingTime(0)
      setTranscript('')
      setExtractedData('')
      setSuccess(speechApiSupported ? '🎤 Recording — transcript appears in real-time below' : '🎤 Recording started - speak clearly')
      recordingIntervalRef.current = setInterval(() => setRecordingTime(t => t + 1), 1000)
    } catch (err) {
      console.error('Recording error:', err)
      if (err.name === 'NotAllowedError') setError('❌ Microphone access denied.')
      else if (err.name === 'NotFoundError') setError('❌ No microphone found.')
      else setError('Failed to start recording: ' + err.message)
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
      clearInterval(recordingIntervalRef.current)

      // Stop Web Speech API
      if (speechRecognitionRef.current) {
        speechRecognitionRef.current.stop()
        speechRecognitionRef.current = null
      }

      // Small delay so MediaRecorder onstop fires and blob is ready
      setTimeout(() => {
        const browserTranscript = finalTranscriptRef.current.trim()
        if (browserTranscript) {
          // Instant path: browser already transcribed it, skip server entirely
          setTranscript(browserTranscript)
          setLiveTranscript('')
          setSuccess('✅ Transcription complete (instant). Auto-extracting data...')
          setTimeout(() => extractData(browserTranscript), 200)
        } else {
          // Fallback: send to server Whisper (non-Chrome browsers)
          setSuccess('⏳ Recording stopped. Sending to server for transcription...')
          transcribeAudio()
        }
      }, 400)
    }
  }

  const transcribeAudio = async () => {
    // This is only called when browser Web Speech API is unavailable (non-Chrome fallback)
    if (!audioBlob) {
      setError('No audio recorded')
      return
    }
    try {
      setIsTranscribing(true)
      setSuccess('⏳ Transcribing with server Whisper...')
      const formData = new FormData()
      formData.append('file', audioBlob, 'recording.webm')
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 120000)
      let response
      try {
        response = await fetch('/api/broker/transcribe', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` },
          body: formData,
          signal: controller.signal
        })
      } finally {
        clearTimeout(timeoutId)
      }
      const data = await response.json()
      if (response.ok) {
        setTranscript(data.transcript)
        setSuccess('✅ Transcription complete. Auto-extracting data...')
        setTimeout(() => extractData(data.transcript), 300)
      } else {
        setError(data.detail || 'Transcription failed')
      }
    } catch (err) {
      setError('Transcription failed: ' + err.message)
    } finally {
      setIsTranscribing(false)
    }
  }

  const extractData = async (transcriptText = null) => {
    const textToExtract = transcriptText || transcript
    if (!textToExtract) {
      setError('No transcript to extract from')
      return
    }

    try {
      setIsExtracting(true)
      setSuccess('⏳ Extracting meeting data with LLM...')
      
      const response = await apiCall('/api/broker/extract-data', {
        method: 'POST',
        body: JSON.stringify({
          transcript: textToExtract,
          meeting_id: selectedMeetingId ? parseInt(selectedMeetingId) : null
        })
      })

      const data = await response.json()

      if (response.ok) {
        const extracted = data.extracted_data
        const displayData = `Extracted Meeting Data:

CONTACT: ${extracted.customer_name || 'Not identified'}
DISCUSSION: ${extracted.discussion_summary || 'No summary'}
NEXT MEETING: ${extracted.next_meeting || 'Not scheduled'}

ACTION ITEMS:
${extracted.action_items.map((item, i) => `${i + 1}. ${item}`).join('\n')}

KEY POINTS:
${extracted.key_points.map((point, i) => `${i + 1}. ${point}`).join('\n')}

FOLLOW-UPS:
${extracted.follow_ups}`
        setExtractedData(displayData)
        console.log('📊 Extracted:', extracted)
        setSuccess('✨ Data extraction complete! Auto-saving to database...')
        
        // Auto-save extracted data
        setTimeout(() => {
          autoSaveExtractedData(extracted)
        }, 300)
      } else {
        setError(data.detail || 'Data extraction failed')
      }
    } catch (err) {
      setError('Data extraction failed: ' + err.message)
    } finally {
      setIsExtracting(false)
    }
  }

  const saveToMeeting = async () => {
    if (!extractedData) {
      setError('No extracted data to save')
      return
    }

    if (!selectedMeetingId) {
      setError('Please select a meeting to save notes to')
      return
    }

    try {
      const response = await apiCall(
        `/api/broker/save-meeting-notes?meeting_id=${selectedMeetingId}&notes=${encodeURIComponent(extractedData)}`,
        { method: 'POST' }
      )

      if (response.ok) {
        setSuccess('Meeting notes saved successfully!')
        // Reset form
        setAudioBlob(null)
        setTranscript('')
        setExtractedData('')
        setRecordingTime(0)
        loadMeetings()
      } else {
        const data = await response.json()
        setError(data.detail || 'Failed to save notes')
      }
    } catch (err) {
      setError('Failed to save: ' + err.message)
    }
  }

  const autoSaveExtractedData = async (extracted) => {
    try {
      let customerId = null
      let meetingId = parseInt(selectedMeetingId) || null

      // Step 1: Auto-create or find customer by name if identified
      if (extracted.customer_name) {
        try {
          console.log('👤 Creating/finding customer:', extracted.customer_name)
          const customerResponse = await apiCall('/api/broker/customers', {
            method: 'POST',
            body: JSON.stringify({
              name: extracted.customer_name,
              email: 'contact@customer.com',
              phone: '',
              company: ''
            })
          })
          
          if (customerResponse.ok) {
            const customerData = await customerResponse.json()
            customerId = customerData.id
            console.log('✅ Customer created/found:', customerId)
            setSuccess('👤 Customer auto-linked!')
          }
        } catch (err) {
          console.warn('Could not auto-create customer:', err.message)
        }
      }

      // Step 2: Create new meeting if none selected
      if (!meetingId) {
        try {
          console.log('📅 Creating new meeting')
          const meetingResponse = await apiCall('/api/broker/meetings', {
            method: 'POST',
            body: JSON.stringify({
              customer_id: customerId,
              title: extracted.discussion_summary ? extracted.discussion_summary.substring(0, 100) : 'Meeting from Voice Recording',
              description: extracted.discussion_summary || '',
              notes: extracted.follow_ups || ''
            })
          })
          
          if (meetingResponse.ok) {
            const meetingData = await meetingResponse.json()
            meetingId = meetingData.id
            console.log('✅ Meeting created:', meetingId)
            setSelectedMeetingId(meetingId.toString())
            setSuccess('📅 Meeting auto-created!')
          }
        } catch (err) {
          console.warn('Could not auto-create meeting:', err.message)
        }
      }

      // Step 3: Save the detailed meeting notes
      if (meetingId) {
        const detailedNotes = `Customer: ${extracted.customer_name || 'N/A'}\nDate: ${new Date().toLocaleString()}\nDiscussion: ${extracted.discussion_summary || 'N/A'}\nNext Meeting: ${extracted.next_meeting || 'TBD'}\n\nAction Items: ${extracted.action_items.join(', ')}\nKey Points: ${extracted.key_points.join(', ')}\nFollow-ups: ${extracted.follow_ups}`
        
        const notesResponse = await apiCall(
          `/api/broker/save-meeting-notes?meeting_id=${meetingId}&notes=${encodeURIComponent(detailedNotes)}`,
          { method: 'POST' }
        )
        
        if (notesResponse.ok) {
          console.log('✅ Meeting notes saved')
          setSuccess('🎉 Meeting data auto-saved successfully!')
          // Refresh meetings list
          setTimeout(() => {
            loadMeetings()
            // Reset form
            setAudioBlob(null)
            setTranscript('')
            setExtractedData('')
            setRecordingTime(0)
          }, 1000)
        }
      }
    } catch (err) {
      console.error('Auto-save error:', err)
      setError('Auto-save failed: ' + err.message)
    }
  }

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  return (
    <div>
      <h2>🎤 Voice Recorder</h2>

      <div className="card">
        <h3>Select Meeting (Optional)</h3>
        {loadingMeetings ? (
          <p>Loading meetings...</p>
        ) : (
          <select
            value={selectedMeetingId}
            onChange={(e) => setSelectedMeetingId(e.target.value)}
            style={{ width: '100%', padding: '0.75rem', fontSize: '1rem', marginBottom: '1rem' }}
          >
            <option value="">-- No specific meeting selected --</option>
            {meetings.map(meeting => (
              <option key={meeting.id} value={meeting.id}>
                {meeting.title} (ID: {meeting.id})
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="card">
        <h3>🎙️ Audio Device Setup</h3>
        
        <div style={{ backgroundColor: permissionStatus === 'granted' ? '#e8f5e9' : permissionStatus === 'denied' ? '#ffebee' : '#fff3e0', padding: '0.5rem 1rem', borderRadius: '4px', marginBottom: '1rem' }}>
          <p style={{ margin: '0.25rem 0', fontSize: '0.9rem' }}>
            📱 Permission Status: <strong>{permissionStatus === 'granted' ? '✅ Granted' : permissionStatus === 'denied' ? '❌ Denied' : '⏳ Prompt'}</strong>
          </p>
          {isMobile() && (
            <p style={{ margin: '0.25rem 0', fontSize: '0.9rem' }}>
              📲 Platform: <strong>Mobile (iOS/Android)</strong>
            </p>
          )}
        </div>

        {permissionStatus === 'prompt' ? (
          <div style={{ backgroundColor: '#fff3e0', padding: '1rem', borderRadius: '4px', marginBottom: '1rem', textAlign: 'center' }}>
            <p style={{ margin: '0.5rem 0', fontSize: '0.95rem', color: '#e65100' }}>
              ⏳ Microphone permission not yet requested
            </p>
            <p style={{ margin: '0.5rem 0', fontSize: '0.9rem', color: '#f57c00' }}>
              Click the button below to request microphone access
            </p>
            <button
              className="btn btn-primary"
              onClick={requestMicPermission}
              style={{ marginTop: '0.75rem', fontSize: '1rem', padding: '0.75rem 1.5rem', width: '100%' }}
            >
              🎤 Request Microphone Permission
            </button>
            <p style={{ margin: '0.75rem 0 0 0', fontSize: '0.8rem', color: '#666' }}>
              ℹ️ <strong>Edge browser:</strong> Permission prompt might be silent or appear in address bar<br/>
              ℹ️ You may see: "Allow this site to use your microphone?"<br/>
              ℹ️ If no prompt appears, check browser address bar or system tray
            </p>
          </div>
        ) : permissionStatus === 'denied' ? (
          <div style={{ backgroundColor: '#ffebee', padding: '1rem', borderRadius: '4px', marginBottom: '1rem' }}>
            <p style={{ margin: '0.5rem 0', fontSize: '0.95rem', color: '#c62828' }}>
              ❌ Microphone permission DENIED
            </p>
            <p style={{ margin: '0.5rem 0', fontSize: '0.85rem', color: '#d32f2f' }}>
              <strong>To enable microphone for this site:</strong>
              <strong style={{ display: 'block', marginTop: '0.5rem' }}>📍 Edge Browser:</strong>
              <ol style={{ marginTop: '0.25rem', paddingLeft: '1.5rem' }}>
                <li>Click <strong>⋯</strong> (Settings) in top-right corner</li>
                <li>Go to <strong>Settings</strong></li>
                <li>Click <strong>Privacy, search, and services</strong></li>
                <li>Scroll to <strong>Site permissions</strong></li>
                <li>Click <strong>Microphone</strong></li>
                <li>Find this website and select <strong>Allow</strong></li>
                <li>Refresh this page</li>
              </ol>
              <strong style={{ display: 'block', marginTop: '0.5rem' }}>📲 Mobile (iOS/Android):</strong>
              <ul style={{ marginTop: '0.25rem', paddingLeft: '1.5rem' }}>
                <li>Go to browser app settings</li>
                <li>Find microphone permissions</li>
                <li>Select this website as "Allow"</li>
              </ul>
            </p>
            <button
              className="btn btn-secondary"
              onClick={requestMicPermission}
              style={{ marginTop: '0.75rem', fontSize: '0.9rem', width: '100%' }}
            >
              🔄 Try Again
            </button>
          </div>
        ) : audioDevices.length > 0 ? (
          <div>
            <div style={{ backgroundColor: '#e8f5e9', padding: '0.75rem', borderRadius: '4px', marginBottom: '1rem' }}>
              <p style={{ margin: '0.5rem 0', fontSize: '0.9rem', color: '#2e7d32' }}>
                ✅ Found {audioDevices.length} microphone(s)
              </p>
            </div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 'bold' }}>
              Select Microphone:
            </label>
            <select
              value={selectedDeviceId}
              onChange={(e) => setSelectedDeviceId(e.target.value)}
              style={{ width: '100%', padding: '0.75rem', fontSize: '1rem', marginBottom: '1rem' }}
            >
              {audioDevices.map((device, idx) => (
                <option key={device.deviceId} value={device.deviceId}>
                  {device.label || `Microphone ${idx + 1}`}
                </option>
              ))}
            </select>
            <button
              className="btn btn-secondary"
              onClick={async () => {
                console.log('User clicked Re-scan Devices')
                setSuccess('🔄 Scanning for microphones...')
                await enumerateAudioDevices(0)
              }}
              style={{ fontSize: '0.9rem', width: '100%' }}
            >
              🔄 Re-scan Devices
            </button>
          </div>
        ) : permissionStatus === 'granted' ? (
          <div style={{ backgroundColor: '#fff3e0', padding: '1rem', borderRadius: '4px', marginBottom: '1rem' }}>
            <p style={{ margin: '0.5rem 0', fontSize: '0.9rem', color: '#e65100' }}>
              ⚠️ Permission granted but no microphones detected
            </p>
            <p style={{ margin: '0.5rem 0', fontSize: '0.85rem', color: '#f57c00' }}>
              Troubleshoot:
              <ul style={{ marginTop: '0.5rem', paddingLeft: '1.5rem' }}>
                <li><strong>Check USB headset:</strong> Is it plugged in?</li>
                <li><strong>Check device status:</strong> Right-click volume icon (taskbar) → Sound settings → Recording tab</li>
                <li><strong>Try again:</strong> Click the button below to re-scan devices</li>
                <li><strong>Unplug/replug:</strong> Disconnect headset for 5 seconds and reconnect</li>
              </ul>
            </p>
            <button
              className="btn btn-warning"
              onClick={async () => {
                console.log('User clicked Re-scan after permission grant')
                setSuccess('🔄 Re-scanning... (attempt with 3 retries)')
                const found = await enumerateAudioDevices(0)
                if (found) {
                  setSuccess('✅ Microphones found!')
                } else {
                  setError('❌ Still no devices found. Check troubleshooting steps above.')
                }
              }}
              style={{ fontSize: '1rem', padding: '0.75rem 1.5rem', width: '100%', marginBottom: '0.5rem', marginTop: '0.5rem' }}
            >
              🔄 Re-scan Devices (with retry)
            </button>
          </div>
        ) : (
          <div style={{ backgroundColor: '#ffebee', padding: '0.75rem', borderRadius: '4px', marginBottom: '1rem' }}>
            <p style={{ margin: '0.25rem 0', fontSize: '0.9rem', color: '#c62828' }}>
              ❌ No microphone detected
            </p>
            <p style={{ margin: '0.25rem 0', fontSize: '0.85rem', color: '#d32f2f' }}>
              Try:
              <ul style={{ marginTop: '0.5rem', paddingLeft: '1.5rem' }}>
                <li>Connecting a USB microphone or headset</li>
                <li>Checking that microphone is not in use by another app</li>
                <li>Restarting your computer</li>
                <li>On mobile: Check device settings for audio input</li>
              </ul>
            </p>
            <button
              className="btn btn-secondary"
              onClick={() => {
                console.log('User clicked Re-scan Devices in error state')
                enumerateAudioDevices(0)
                setSuccess('🔄 Re-scanned audio devices')
              }}
              style={{ marginTop: '0.5rem', fontSize: '0.9rem', width: '100%' }}
            >
              🔄 Re-scan Devices
            </button>
          </div>
        )}
      </div>

      <div className="card">
        <h3>Record Voice Note</h3>
        
        <div className={`voice-recorder ${isRecording ? 'recording' : ''}`}>
          {isRecording && <span className="recording-indicator"></span>}
          <div className="voice-duration">{formatTime(recordingTime)}</div>
          
          <div className="btn-group" style={{ justifyContent: 'center', marginBottom: '1rem' }}>
            {!isRecording ? (
              <button
                className="btn btn-primary"
                onClick={startRecording}
                style={{ fontSize: '1.1rem', padding: '1rem 2rem' }}
              >
                🎙️ Start Recording
              </button>
            ) : (
              <button
                className="btn btn-danger"
                onClick={stopRecording}
                style={{ fontSize: '1.1rem', padding: '1rem 2rem' }}
              >
                ⏹️ Stop Recording
              </button>
            )}
          </div>

          {audioBlob && !isRecording && (
            <p style={{ fontSize: '0.9rem', color: '#666', marginTop: '1rem' }}>
              ✓ Recording saved ({(audioBlob.size / 1024).toFixed(1)} KB)
            </p>
          )}

          {/* Live transcript while recording */}
          {isRecording && speechApiSupported && (
            <div style={{ marginTop: '1rem', backgroundColor: '#e3f2fd', padding: '0.75rem', borderRadius: '4px', minHeight: '3rem' }}>
              <p style={{ margin: 0, fontSize: '0.8rem', color: '#1565c0', fontWeight: 'bold', marginBottom: '0.25rem' }}>
                🎙️ Live transcript (browser recognition):
              </p>
              <p style={{ margin: 0, fontSize: '0.92rem', color: '#0d47a1', fontStyle: liveTranscript ? 'normal' : 'italic' }}>
                {liveTranscript || 'Listening...'}
              </p>
            </div>
          )}
          {isRecording && !speechApiSupported && (
            <p style={{ fontSize: '0.8rem', color: '#f57c00', marginTop: '0.5rem' }}>
              ⚠️ Live transcription not supported in this browser. Will use server Whisper after recording.
            </p>
          )}
        </div>
      </div>

      {audioBlob && (
        <div className="card" style={{ backgroundColor: '#f5f5f5' }}>
          <h3>⚙️ Processing Flow (Automatic)</h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
            <div style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>📝</div>
              <div style={{ fontSize: '0.85rem', color: '#666' }}>Record</div>
              <div style={{ fontSize: '0.75rem', color: '#20c997', fontWeight: 'bold' }}>✅ Done</div>
            </div>
            <div style={{ fontSize: '2rem', color: '#20c997' }}>→</div>
            <div style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>🔄</div>
              <div style={{ fontSize: '0.85rem', color: '#666' }}>Transcribe</div>
              <div style={{ fontSize: '0.75rem', color: transcript ? '#20c997' : '#f57c00', fontWeight: 'bold' }}>{isTranscribing ? '⏳ Running' : transcript ? '✅ Done' : '⏳ Pending'}</div>
            </div>
            <div style={{ fontSize: '2rem', color: transcript ? '#20c997' : '#ccc' }}>→</div>
            <div style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>🤖</div>
              <div style={{ fontSize: '0.85rem', color: '#666' }}>Extract</div>
              <div style={{ fontSize: '0.75rem', color: extractedData ? '#20c997' : '#f57c00', fontWeight: 'bold' }}>{isExtracting ? '⏳ Running' : extractedData ? '✅ Done' : '⏳ Pending'}</div>
            </div>
            <div style={{ fontSize: '2rem', color: extractedData ? '#20c997' : '#ccc' }}>→</div>
            <div style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>💾</div>
              <div style={{ fontSize: '0.85rem', color: '#666' }}>Save</div>
              <div style={{ fontSize: '0.75rem', color: '#666', fontWeight: 'bold' }}>Auto</div>
            </div>
          </div>
        </div>
      )}

      {transcript && !extractedData && (
        <div className="card">
          <h3>📝 Transcript</h3>
          <div style={{ backgroundColor: '#f8f9fa', padding: '1rem', borderRadius: '4px' }}>
            <p style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.9rem' }}>
              {transcript}
            </p>
          </div>
        </div>
      )}

      {extractedData && (
        <div className="card">
          <h3>✨ Meeting Summary (Auto-Extracted & Auto-Saved)</h3>
          <div style={{ backgroundColor: '#e8f5e9', padding: '1rem', borderRadius: '4px', border: '2px solid #4caf50' }}>
            <p style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.9rem' }}>
              {extractedData}
            </p>
          </div>
          <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#fff3cd', borderRadius: '4px', textAlign: 'center' }}>
            <p style={{ margin: '0.25rem 0', fontSize: '0.95rem', color: '#856404', fontWeight: 'bold' }}>
              🎉 Meeting data saved! Recording recorded at {new Date().toLocaleTimeString()}
            </p>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setAudioBlob(null)
                setTranscript('')
                setExtractedData('')
                setRecordingTime(0)
                loadMeetings()
                setSuccess('✅ Ready for next recording')
              }}
              style={{ marginTop: '0.75rem', fontSize: '0.9rem' }}
            >
              🔄 New Recording
            </button>
          </div>
        </div>
      )}

      <div className="card" style={{ background: '#e3f2fd', borderLeft: '4px solid #2196F3', marginTop: '2rem' }}>
        <h4>📝 Phase 3 Implementation Status:</h4>
        <ul style={{ marginLeft: '1.5rem' }}>
          <li>✅ Real-time voice recording with Web Audio API</li>
          <li>⏳ Whisper speech-to-text via Ollama (API ready, awaiting model)</li>
          <li>⏳ LLM-powered data extraction via Ollama (API ready)</li>
          <li>✅ Meeting note auto-save to database</li>
          <li>✅ Selected meeting binding for notes</li>
          <li>⏳ Transcript history (coming in Phase 4)</li>
        </ul>
      </div>
    </div>
  )
}

export default VoiceRecorder

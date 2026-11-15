import { useEffect, useRef, useState } from 'react'
import { api } from './api'

type Gsi = {
  accounts: {
    id: {
      initialize: (opts: { client_id: string; callback: (r: { credential: string }) => void }) => void
      renderButton: (el: HTMLElement, opts: Record<string, unknown>) => void
    }
  }
}

const SCRIPT = 'https://accounts.google.com/gsi/client'

function loadGsi(): Promise<Gsi> {
  const existing = (window as unknown as { google?: Gsi }).google
  if (existing) return Promise.resolve(existing)
  return new Promise((resolve, reject) => {
    const tag = document.createElement('script')
    tag.src = SCRIPT
    tag.async = true
    tag.onload = () => resolve((window as unknown as { google: Gsi }).google)
    tag.onerror = () => reject(new Error('Google sign-in could not load'))
    document.head.appendChild(tag)
  })
}

// Renders Google's own sign-in button; shows nothing when the server has no client id configured.
export default function GoogleButton({ onCredential }: { onCredential: (credential: string) => void }) {
  const slot = useRef<HTMLDivElement>(null)
  const callback = useRef(onCredential)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    callback.current = onCredential
  }, [onCredential])

  useEffect(() => {
    let live = true
    api<{ google_client_id: string }>('/auth/config')
      .then(async ({ google_client_id }) => {
        if (!google_client_id || !live) return
        const gsi = await loadGsi()
        if (!live || !slot.current) return
        gsi.accounts.id.initialize({ client_id: google_client_id, callback: (r) => callback.current(r.credential) })
        // Google's button takes a pixel width (200 to 400), so match the card instead of overflowing it.
        const width = Math.max(200, Math.min(400, slot.current.offsetWidth))
        gsi.accounts.id.renderButton(slot.current, { theme: 'filled_black', size: 'large', width, text: 'continue_with' })
      })
      .catch(() => {
        if (live) setFailed(true)
      })
    return () => {
      live = false
    }
  }, [])

  return (
    <div className="mt-4 flex flex-col items-center gap-2">
      <div ref={slot} className="flex w-full justify-center" />
      {failed && <p className="font-mono text-[11px] text-muted">Google sign-in is unavailable right now.</p>}
    </div>
  )
}

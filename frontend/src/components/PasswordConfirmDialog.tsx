import { useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { ApiError } from '../api'

interface PasswordConfirmDialogProps {
  title: string
  description: string
  confirmLabel: string
  pending: boolean
  error: unknown
  onCancel: () => void
  onConfirm: (password: string) => void
}

/**
 * The single password prompt every sensitive action uses.
 *
 * Revoking a device and changing a password are the only places a password is typed
 * while already signed in, and both answer the same way: a wrong password is a 400,
 * and too many wrong ones is a 429 carrying `Retry-After`. Showing that countdown is
 * what turns "too many attempts" into something the user can act on instead of a
 * dead button.
 *
 * The typed password lives only in this component's state, is never rendered, and
 * leaves through `onConfirm` alone -- so no part of it can reach the DOM or a log.
 */
export function PasswordConfirmDialog({
  title,
  description,
  confirmLabel,
  pending,
  error,
  onCancel,
  onConfirm,
}: PasswordConfirmDialogProps) {
  const [password, setPassword] = useState('')
  const [waitSeconds, setWaitSeconds] = useState(0)

  // A 429 is a wait rather than a failure: the server said how long for.
  useEffect(() => {
    if (error instanceof ApiError && error.status === 429 && error.retryAfter) {
      setWaitSeconds(error.retryAfter)
    }
  }, [error])

  useEffect(() => {
    if (waitSeconds <= 0) return
    const timer = window.setTimeout(() => setWaitSeconds((value) => value - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [waitSeconds])

  const message = error instanceof Error ? error.message : ''
  const blocked = pending || waitSeconds > 0 || password.length === 0

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <ShieldCheck size={20} />
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
        </header>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (blocked) return
            onConfirm(password)
          }}
        >
          <label>
            当前密码
            <input
              name="current_password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={pending}
              required
            />
          </label>
          {message && (
            <p className="inline-error" role="alert">
              {message}
              {waitSeconds > 0 ? `（${waitSeconds} 秒后可重试）` : ''}
            </p>
          )}
          <div className="modal-actions">
            <button className="button secondary" type="button" onClick={onCancel} disabled={pending}>
              取消
            </button>
            <button className="button primary" type="submit" disabled={blocked}>
              {pending ? '正在确认…' : confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

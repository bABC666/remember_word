import { useState } from 'react'
import { KeyRound, LogIn } from 'lucide-react'
import { useAuth } from '../useAuth'

/**
 * Sign-in screen.
 *
 * Public registration does not exist in this version: accounts are created by an
 * operator, so this form only signs in.
 */
export function LoginPage() {
  const { login, notice } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (pending) return
    setError('')
    setPending(true)
    try {
      await login(username.trim(), password)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '登录失败，请重试')
      setPassword('')
      setPending(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit} aria-label="登录拾词">
        <header>
          <span className="brand-mark">拾</span>
          <div>
            <h1>拾词</h1>
            <p>登录后继续你的学习记录。</p>
          </div>
        </header>

        <label>
          用户名
          <input
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={pending}
            required
          />
        </label>

        <label>
          密码
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={pending}
            required
          />
        </label>

        {notice && !error && (
          <p className="login-notice" role="status">
            {notice}
          </p>
        )}

        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}

        <button className="button primary login-submit" type="submit" disabled={pending}>
          {pending ? <KeyRound size={17} /> : <LogIn size={17} />}
          {pending ? '正在登录…' : '登录'}
        </button>

        <p className="login-hint">当前版本不开放注册，账号由管理员创建。</p>
      </form>
    </div>
  )
}

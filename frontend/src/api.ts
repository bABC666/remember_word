import { reportUnauthorized } from './session'

export class ApiError extends Error {
  status: number
  /** Seconds the server asked us to wait, taken from a 429's `Retry-After`. */
  retryAfter?: number

  constructor(message: string, status: number, retryAfter?: number) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

/** How long a single request may take before it is treated as a failure. */
const REQUEST_TIMEOUT_MS = 120_000

export async function api<T>(path: string, init?: RequestInit, options?: { timeoutMs?: number }): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')

  // Same-origin requests must carry the session cookie; the session itself lives
  // only in an HttpOnly cookie and is never read or stored by JavaScript.
  const controller = new AbortController()
  const timeoutMs = options?.timeoutMs ?? REQUEST_TIMEOUT_MS
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(path, { credentials: 'same-origin', ...init, headers, signal: controller.signal })
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ApiError('请求超时，请稍后刷新页面查看处理结果。', 0)
    }
    throw error
  } finally {
    clearTimeout(timer)
  }

  if (response.status === 401) {
    // Authentication is not retryable: tell the app to drop the session and
    // clear every cached private response.
    reportUnauthorized()
    let message = '登录状态已失效，请重新登录'
    try {
      const payload = await response.json() as { detail?: string }
      message = payload.detail ?? message
    } catch {
      // Keep the fallback message.
    }
    throw new ApiError(message, 401)
  }

  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const payload = await response.json() as { detail?: string; message?: string }
      message = payload.detail ?? payload.message ?? message
    } catch {
      // Keep the HTTP fallback message.
    }
    // A 429 always explains how long the caller must wait; dropping the header
    // would leave "too many attempts" with no way to act on it.
    const advertised = Number(response.headers.get('Retry-After') ?? '')
    const retryAfter = Number.isFinite(advertised) && advertised > 0 ? advertised : undefined
    throw new ApiError(message, response.status, retryAfter)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

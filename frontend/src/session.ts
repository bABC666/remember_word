/**
 * Client-side session wiring.
 *
 * `api.ts` reports authentication failures here instead of importing React, so
 * the request layer and the auth state stay decoupled. The auth provider
 * registers a handler on mount; anything that talks to the API therefore
 * triggers exactly one reaction to a 401: drop the session and clear caches.
 */

type UnauthorizedHandler = () => void

let handler: UnauthorizedHandler | null = null

export function onUnauthorized(next: UnauthorizedHandler | null): void {
  handler = next
}

export function reportUnauthorized(): void {
  handler?.()
}

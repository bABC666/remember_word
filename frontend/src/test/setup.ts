import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Unmount whatever a test rendered, so state cannot leak between tests. Without
// this a second render leaves two copies of the app in the document, which is
// both a confusing failure and a real hazard for the account-switching tests.
afterEach(() => {
  cleanup()
})

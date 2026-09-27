import { describe, expect, it } from 'vitest'
import { licenseLink } from './sourcesModel'

/**
 * A licence link is only offered for an identifier that is *one* known identifier.
 *
 * The database stores `license_id` as free text (the value a submitter declared);
 * there is no URL column and the licence text itself is only a fingerprint. So a
 * link here is a canonical deed page for an identifier this code knows exactly,
 * and anything else -- a composite like `CC-BY-SA-4.0+GFDL`, a typo, an internal
 * label, an empty column -- answers `null` and is shown as text instead. A guessed
 * URL would look exactly like a checked one.
 */
describe('licence links', () => {
  it('maps a known single identifier to its canonical deed', () => {
    expect(licenseLink('CC-BY-SA-4.0')).toBe('https://creativecommons.org/licenses/by-sa/4.0/')
    expect(licenseLink('CC-BY-NC-SA-4.0')).toBe('https://creativecommons.org/licenses/by-nc-sa/4.0/')
  })

  it('tolerates surrounding whitespace in the recorded value', () => {
    expect(licenseLink('  CC-BY-SA-4.0  ')).toBe('https://creativecommons.org/licenses/by-sa/4.0/')
  })

  it('refuses a composite identifier, which is not one licence', () => {
    expect(licenseLink('CC-BY-SA-4.0+GFDL')).toBeNull()
    expect(licenseLink('CC-BY-SA-4.0 OR GFDL-1.3-only')).toBeNull()
  })

  it('refuses an unknown, differently-cased or empty identifier', () => {
    expect(licenseLink('proprietary-internal')).toBeNull()
    expect(licenseLink('cc-by-sa-4.0')).toBeNull()
    expect(licenseLink('')).toBeNull()
    expect(licenseLink('   ')).toBeNull()
  })
})

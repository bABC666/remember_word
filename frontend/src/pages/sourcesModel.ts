/**
 * The types and the one static licence mapping the sources page needs.
 *
 * Everything here describes a **recorded declaration**, never a verified right.
 * `source_artifact` stores what a submitter declared (publisher, version, licence
 * identifier, use and display scope); the licence *text* itself is never read, only
 * its fingerprint, and the API's `authorization_review` block is the server saying
 * so in its own words.
 */

/** One source artifact as `GET /api/lexicons/{id}/sources` reports it. */
export interface SourceArtifactRow {
  source_artifact_id: number
  role: string
  name: string
  publisher: string
  version: string
  obtained_at_utc: string
  format: string
  license_id: string
  license_text_sha256: string
  file_sha256: string
  mapping_sha256: string
  byte_size: number
  use_scope: string
  display_scope: string
  /** `explicitly_unapproved` when the declaration itself says so, else `not_assessed`. */
  declared_approval_state: 'not_assessed' | 'explicitly_unapproved'
  runs: Array<{ run_id: string; confirmed_at: string; outcome: string }>
}

/** The server's own statement about what it did and did not verify. */
export interface AuthorizationReview {
  status: 'not_assessed' | 'pending_owner_approval'
  pending_sources: string[]
  message: string
}

export interface LexiconSources {
  lexicon: { id: number; name: string }
  sources: SourceArtifactRow[]
  authorization_review: AuthorizationReview
}

/** The fields of `GET /api/lexicons` this page reads. */
export interface LexiconRow {
  id: number
  name: string
  description: string
  is_system: boolean
  entry_count: number
}

/**
 * Canonical deed pages, for identifiers that are **one** known identifier.
 *
 * The database holds `license_id` as free text (the value a submitter declared), so
 * this table is deliberately short and exact: a link is offered only where the
 * recorded value is an identifier whose canonical page is not in question. Anything
 * else -- a composite such as `CC-BY-SA-4.0+GFDL`, which is not one licence, a
 * differently-cased string, an internal label, an empty column -- answers `null` and
 * is displayed as text. A guessed URL would look exactly like a checked one, which is
 * why the fallback is "show what was recorded", not "search for something plausible".
 */
const LICENSE_LINKS: Record<string, string> = {
  'CC-BY-SA-4.0': 'https://creativecommons.org/licenses/by-sa/4.0/',
  'CC-BY-NC-SA-4.0': 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
}

export function licenseLink(licenseId: string): string | null {
  return LICENSE_LINKS[licenseId.trim()] ?? null
}

/**
 * What the recorded declaration state is worth saying in a card header.
 *
 * `not_assessed` means nobody has judged the declaration either way -- it is not a
 * softer word for approved, so it reads as unassessed rather than as anything else.
 */
const APPROVAL_LABELS: Record<string, string> = {
  explicitly_unapproved: '声明写明未获批准',
  not_assessed: '尚未评估',
}

export function approvalLabel(state: string): string {
  return APPROVAL_LABELS[state] ?? state
}

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const styles = readFileSync('src/styles.css', 'utf-8')

const escapeRegex = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** The settings rules whose tracks have to be able to shrink. */
const SHRINKABLE = [
  '.settings-grid',
  '.form-grid',
  '.session-list',
  '.session-row',
  '.session-meta',
  '.data-section dl > div',
]

function blocksFor(selector: string): string[] {
  const pattern = new RegExp(escapeRegex(selector) + '\\s*\\{([^}]*)\\}', 'g')
  return [...styles.matchAll(pattern)].map((match) => match[1])
}

/** The body of every ``@media (max-width: 620px) { ... }`` block. */
function compactBlocks(): string {
  return [...styles.matchAll(/@media \(max-width: 620px\) \{([^@]*)\}/g)].map((match) => match[1]).join('\n')
}

/**
 * G8: the settings page was about 2.5 screens wide on a phone.
 *
 * One session field holds a long, unbreakable User-Agent string.  Its rule asked
 * for `overflow: hidden; text-overflow: ellipsis`, but as a flex item it kept the
 * default `min-width: auto`, so it never shrank: the string set a min-content
 * floor of ~800px that travelled up through every single-column grid on the page
 * and widened the document to 961px at a 390px viewport.
 *
 * jsdom cannot lay this out, so these tests pin the declarations whose absence
 * caused it; the real pixel consequences are measured by the browser acceptance.
 */
describe('settings page width contract (G8)', () => {
  it('lets the session user agent shrink so its ellipsis can apply', () => {
    const rule = blocksFor('.session-head strong')[0] ?? ''

    expect(rule).toContain('min-width: 0')
    expect(rule).toContain('white-space: nowrap')
    expect(rule).toContain('overflow: hidden')
    expect(rule).toContain('text-overflow: ellipsis')
  })

  it('gives every settings grid a shrinkable track', () => {
    expect(styles).toContain('.settings-grid { display: grid; grid-template-columns: minmax(0, 1fr);')
    expect(styles).toContain(
      '.form-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);',
    )
    expect(styles).toContain(
      '.session-list { list-style: none; display: grid; grid-template-columns: minmax(0, 1fr);',
    )
    expect(styles).toContain('.session-row { display: grid; grid-template-columns: minmax(0, 1fr);')
    expect(styles).toContain('.session-meta { grid-template-columns: minmax(0, 1fr); }')
    expect(styles).toContain('grid-template-columns: 110px minmax(0, 1fr);')
  })

  it('leaves no bare 1fr track in a settings rule', () => {
    // A bare `1fr` is `minmax(auto, 1fr)`: its min-content floor is exactly what
    // let one long value stretch the whole page.  `repeat(n, minmax(0, 1fr))` is
    // fine and is what `.session-meta` already used above 620px.
    for (const selector of SHRINKABLE) {
      const blocks = blocksFor(selector)
      expect(blocks.length, selector).toBeGreaterThan(0)
      for (const block of blocks) {
        expect(block, selector).not.toMatch(/grid-template-columns:\s*1fr\b/)
        expect(block, selector).not.toMatch(/grid-template-columns:\s*1fr\s+1fr\b/)
        expect(block, selector).not.toMatch(/grid-template-columns:\s*repeat\(\d+,\s*1fr\)/)
      }
    }
  })

  it('lifts the sticky save bar above the phone bottom bar', () => {
    // The save bar sticks to the viewport bottom, and below 620px the navigation
    // is a fixed 64px bar at that same edge: the button measured 788-830 with the
    // nav starting at 780, so a phone could not press 保存设置 at all.
    expect(styles).toContain('.settings-save { position: sticky; bottom: 0;')
    const base = styles.indexOf('.settings-save { position: sticky')
    const offset = styles.indexOf('.settings-save { bottom: calc(64px + var(--safe-area-bottom)); }')
    expect(base).toBeGreaterThan(-1)
    expect(offset).toBeGreaterThan(base)
    expect(compactBlocks()).toContain('.settings-save { bottom: calc(64px + var(--safe-area-bottom)); }')
  })
})

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

/**
 * The contract the walkthrough's P2 and P3 asked for, asserted against the stylesheet.
 *
 * jsdom lays nothing out and Chrome is not available here, so the two properties under
 * test are read from `styles.css` and checked as properties: that the four new controls
 * declare a 24x24 minimum, and that the new ink and the two decision badges reach the
 * 4.5:1 contrast floor. The contrast is *computed* rather than compared against a
 * literal, so changing `--green-soft` or a badge colour re-checks the requirement
 * instead of silently breaking it. The measured proof in a real browser is the
 * walkthrough re-run, not this file.
 */
const styles = readFileSync('src/styles.css', 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '')

/** Every `--name: value;` declaration in the file, first definition winning. */
function variables(): Record<string, string> {
  const found: Record<string, string> = {}
  for (const match of styles.matchAll(/--([a-z0-9-]+):\s*([^;{}]+);/gi)) {
    const [, name, value] = match
    if (!(name in found)) found[name] = value.trim()
  }
  return found
}

/** Resolve one level of `var(--x)`, which is how these rules name their colours. */
function resolve(value: string): string {
  const match = /var\(--([a-z0-9-]+)\)/i.exec(value)
  return match ? (variables()[match[1]] ?? value) : value
}

/** The bodies of every rule whose selector list contains exactly `selector`. */
function blocks(selector: string): string[] {
  const found: string[] = []
  for (const match of styles.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    const list = match[1].split(',').map((entry) => entry.trim().replace(/\s+/g, ' '))
    if (list.includes(selector)) found.push(match[2])
  }
  return found
}

/** The last declaration of one property across those rules, as CSS would apply it. */
function declared(selector: string, property: string): string {
  const pattern = new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`, 'i')
  const values = blocks(selector)
    .map((body) => pattern.exec(body)?.[1]?.trim())
    .filter((value): value is string => Boolean(value))
  return values.length ? values[values.length - 1] : ''
}

const toRgb = (value: string) => {
  const hex = resolve(value).trim()
  const match = /^#([0-9a-f]{6})$/i.exec(hex)
  if (!match) throw new Error(`not a plain hex colour: ${value} -> ${hex}`)
  return {
    r: parseInt(match[1].slice(0, 2), 16),
    g: parseInt(match[1].slice(2, 4), 16),
    b: parseInt(match[1].slice(4, 6), 16),
  }
}

const luminance = ({ r, g, b }: { r: number; g: number; b: number }) => {
  const channel = (value: number) => {
    const scaled = value / 255
    return scaled <= 0.03928 ? scaled / 12.92 : Math.pow((scaled + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

/** WCAG 2.x contrast ratio, rounded to two decimals. */
function contrast(foreground: string, background: string): number {
  const front = luminance(toRgb(foreground))
  const back = luminance(toRgb(background))
  return Math.round(((Math.max(front, back) + 0.05) / (Math.min(front, back) + 0.05)) * 100) / 100
}

/** The surfaces the new blocks put text on, as the stylesheet declares them. */
const SURFACES = {
  'card': '#ffffff',
  'adopted row': '#fbfbfc',
  'authorization note': '#f4f4f8',
  'neutral chip': '#eef0f4',
}

describe('the new controls are at least 24x24 (P2)', () => {
  const TARGETS = [
    '.source-row-link',      // 查看该固定修订, measured 294x20
    '.source-row-card',      // 来源详情, measured 48x20
    '.source-candidates > summary', // 未采用的其他来源（1）, measured 320x16
    '.source-meta a',        // the licence deed link, measured 82x16
  ]

  it.each(TARGETS)('%s declares a 24x24 minimum', (selector) => {
    expect(declared(selector, 'min-height'), selector).toBe('24px')
    expect(declared(selector, 'min-width'), selector).toBe('24px')
  })

  it('keeps the candidates summary able to paint its disclosure marker', () => {
    // A marker is painted only for a list-item, so the summary must not be turned into
    // a flex container to gain its height.
    expect(blocks('.source-candidates > summary').join(';')).not.toMatch(/display:\s*(flex|inline-flex|block)/)
  })
})

describe('the new secondary text reaches 4.5:1 (P3)', () => {
  it('has one ink that clears the floor on every surface it is used on', () => {
    const ink = variables()['source-secondary']
    expect(ink, '--source-secondary must be defined').toBeTruthy()
    for (const [name, surface] of Object.entries(SURFACES)) {
      expect(contrast(ink, surface), `${ink} on ${name} (${surface})`).toBeGreaterThanOrEqual(4.5)
    }
  })

  const NEW_SECONDARY = [
    '.lexicon-head small',
    '.source-meta dt',
    '.source-foot',
    '.source-state',
    '.source-row-meta',
    '.source-row-revision',
    '.source-candidates > summary',
    '.source-role',
    '.approval-badge',
  ]

  it.each(NEW_SECONDARY)('%s uses that ink rather than --muted', (selector) => {
    expect(resolve(declared(selector, 'color')), selector).toBe(variables()['source-secondary'])
  })

  it('gives the block label the ink too, over the detail rule that paints it muted', () => {
    // `.word-detail section > span` is an existing rule the block's bare span matches;
    // the block carries a class so this one can win.
    expect(resolve(declared('.entry-sources .source-section-label', 'color'))).toBe(variables()['source-secondary'])
    expect(declared('.source-section-label', 'color')).toBe('')
  })

  it('leaves --muted and the existing site-wide text alone', () => {
    expect(variables()['muted']).toBe('#788196')
    expect(blocks('.page-head p').join(';')).toContain('color: var(--muted)')
    expect(blocks('.empty-state').join(';')).toContain('color: var(--muted)')
    expect(blocks('.status').join(';')).toContain('color: #687184')
  })
})

describe('the two decision badges stay readable and distinguishable (P3)', () => {
  it('clears 4.5:1 for the adopted badge', () => {
    expect(contrast(declared('.source-decision', 'color'), declared('.source-decision', 'background')))
      .toBeGreaterThanOrEqual(4.5)
  })

  it('clears 4.5:1 for the unadopted badge', () => {
    expect(contrast(declared('.source-decision.unadopted', 'color'), declared('.source-decision.unadopted', 'background')))
      .toBeGreaterThanOrEqual(4.5)
  })

  it('keeps them told apart by more than colour', () => {
    // Different wording, different ink, different chip, and the rows themselves keep
    // solid against dashed borders -- so the distinction never rests on colour alone.
    expect(resolve(declared('.source-decision', 'color')))
      .not.toBe(resolve(declared('.source-decision.unadopted', 'color')))
    expect(resolve(declared('.source-decision', 'background')))
      .not.toBe(resolve(declared('.source-decision.unadopted', 'background')))
    expect(declared('.source-row', 'border')).toContain('solid')
    expect(blocks('.source-row.unadopted').join(';')).toContain('border-style: dashed')
  })
})

describe('the concise block’s 12px secondary text reaches 4.5:1 (F1)', () => {
  /**
   * A real-browser acceptance run measured these three at 3.71:1 on `--bg`: the same
   * `--muted` shortfall the source surfaces had, on the same 12px text. The ink is
   * therefore the same local one, and the floor is asserted here so a later edit cannot
   * quietly put `--muted` back.
   *
   * Selectors are written as the stylesheet writes them, because that is what the
   * helpers above match on.
   */
  const SECONDARY = [
    '.concise-meaning-list .concise-note', // the rewrite note
    '.concise-meaning-list .concise-locator', // the primary source position
    '.concise-meaning-list .concise-citations', // the additional source positions
  ]

  it('defines that ink on the concise block rather than on the page root', () => {
    expect(declared('.concise-meanings', '--source-secondary')).toBe('#5f6880')
  })

  it.each(SECONDARY)('%s uses it rather than --muted', (selector) => {
    expect(resolve(declared(selector, 'color')), selector).toBe(variables()['source-secondary'])
  })

  // Both surfaces the block puts text on: the study answer area sits on the page
  // background, the detail page inside a card.
  it.each(SECONDARY.flatMap((selector) => [
    [selector, 'bg', variables()['bg']] as const,
    [selector, 'card', '#ffffff'] as const,
  ]))('%s clears 4.5:1 on the %s', (selector, _surface, background) => {
    expect(
      contrast(declared(selector, 'color'), background),
      `${selector} on ${background}`,
    ).toBeGreaterThanOrEqual(4.5)
  })

  it('lets the individual citation inherit that ink rather than set its own', () => {
    // `.concise-citation` is the span around each position; a colour of its own here
    // would be a second place to keep above the floor.
    expect(declared('.concise-meaning-list .concise-citation', 'color')).toBe('')
  })

  it('leaves --muted and the site-wide text exactly as they were', () => {
    expect(variables()['muted']).toBe('#788196')
  })

  it('uses the concise block ink for the confirmation line', () => {
    expect(declared('.concise-confirmed-by', 'color')).toBe('var(--source-secondary)')
    expect(resolve(declared('.concise-confirmed-by', 'color'))).toBe(variables()['source-secondary'])
  })

  it.each([
    ['study page', variables()['bg']],
    ['detail card', '#ffffff'],
  ])('keeps the confirmation line at 4.5:1 on the %s', (_surface, background) => {
    expect(contrast(declared('.concise-confirmed-by', 'color'), background)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('a shown group’s heading and its values share a left edge (F2)', () => {
  /**
   * The detail page is left-aligned throughout, but each group's list was a
   * shrink-to-fit centred block: the heading sat flush left while the values floated
   * ~50px (phone) / ~260px (desktop) to its right, so the heading looked detached from
   * the group it labels. The list fills the container there instead.
   *
   * The study page is the one place that centres, and it keeps doing so -- this asserts
   * both halves, because a fix that left-aligned everywhere would trade one defect for
   * another.
   */
  it('has the detail page fill the container instead of centring', () => {
    expect(declared('.word-detail .concise-meaning-list', 'width')).toBe('auto')
    expect(declared('.word-detail .concise-meaning-list', 'margin-inline')).toBe('0')
  })

  it('keeps the study page centring, which is the only centring left', () => {
    expect(declared('.answer-area .concise-meaning-list', 'margin-inline')).toBe('auto')
    // The base rule still centres by default, so the detail override is what makes the
    // difference -- removing it would silently restore the misalignment.
    expect(declared('.concise-meaning-list', 'margin')).toContain('auto')
  })
})

import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { SourceAttribution } from './SourceAttribution'

it('offers usable attribution, license, package fingerprint and modification links', () => {
  render(<SourceAttribution value={{
    creators: 'Karl Bartel / Wiktionary contributors', modifications: '去除 HTML，去重并拼接',
    license_url: 'https://creativecommons.org/licenses/by-sa/4.0/',
    snapshot_url: 'https://example.org/package.zip', snapshot_sha256: 'a'.repeat(64),
    snapshot_label: '2026-06-23 ZIP',
    links: [{ label: 'DBnary 来源', url: 'https://kaiko.getalp.org/about-dbnary/' }],
  }} historyUrl="https://example.org/history" />)
  expect(screen.getByText(/Karl Bartel/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '许可证正文与条件' })).toHaveAttribute('href', 'https://creativecommons.org/licenses/by-sa/4.0/')
  expect(screen.getByRole('link', { name: /固定来源包/ })).toHaveAttribute('href', 'https://example.org/package.zip')
  expect(screen.getByText('a'.repeat(64))).toBeInTheDocument()
  expect(screen.getByText(/去除 HTML/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '页面历史与贡献者' })).toHaveAttribute('href', 'https://example.org/history')
})

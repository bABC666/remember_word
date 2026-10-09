import type { PublicAttribution } from '../types'

export function SourceAttribution({ value, historyUrl, compact = false }: {
  value?: PublicAttribution; historyUrl?: string; compact?: boolean
}) {
  if (!value) return null
  const content = <div className="source-attribution">
    <p>署名：{value.creators}</p>
    {value.copyright_notice && <p>{value.copyright_notice}</p>}
    <p><a href={value.license_url} target="_blank" rel="noreferrer noopener">许可证正文与条件</a>
      {value.source_url && <> · <a href={value.source_url} target="_blank" rel="noreferrer noopener">来源项目</a></>}
      {historyUrl && <> · <a href={historyUrl} target="_blank" rel="noreferrer noopener">页面历史与贡献者</a></>}
    </p>
    {value.snapshot_label && <p>固定版本：{value.snapshot_label}</p>}
    {value.snapshot_url && <p><a href={value.snapshot_url} target="_blank" rel="noreferrer noopener">固定来源包（以指纹核对）</a></p>}
    {value.snapshot_sha256 && <p>来源 SHA-256：<code>{value.snapshot_sha256}</code></p>}
    <p>我们做了什么：{value.modifications}</p>
    {value.links?.length ? <p className="attribution-links">{value.links.map((link) =>
      <a key={link.url} href={link.url} target="_blank" rel="noreferrer noopener">{link.label}</a>)}</p> : null}
    {value.disclaimer && <p>{value.disclaimer}</p>}
  </div>
  return compact ? <details className="attribution-details"><summary>署名、许可、固定版本与修改说明</summary>{content}</details> : content
}

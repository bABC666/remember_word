import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FileText, ShieldAlert } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import { sourceCardAnchor } from '../sourceAnchor'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { SourceAttribution } from '../components/SourceAttribution'
import {
  approvalLabel,
  licenseLink,
  type AuthorizationReview,
  type LexiconRow,
  type LexiconSources,
  type SourceArtifactRow,
} from './sourcesModel'

/**
 * The page-level statement, said before anything else and worded so it stays true
 * whatever the per-lexicon state is.
 *
 * The display design's own banner wording asserted "尚未获批" for every source, which
 * was accurate when all three sources were marked unapproved. It is not accurate as a
 * page constant now: `authorization_review` reports the state per lexicon, so the
 * constant carries only the part that never changes -- this page transcribes a
 * declaration and verifies nothing -- and each lexicon states its own status verbatim
 * below.
 *
 * Forbidden here, by design: any wording that presents a recorded declaration as a
 * grant ("已获授权", "符合 CC 要求", "官方授权"), and any wording that turns "this page
 * does not offer the source file" into a limit on what a licence lets a reader do. Not
 * offering a download is a fact about this page, not a restriction on anyone's rights.
 */
const DECLARATION_NOTICE =
  '本页只如实转述导入时记录的来源声明与许可标识，不核实许可有效性，也不代表授权已获确认。'
  + '释义可能经过抽取、清洗、去重和拼接；固定版本与具体修改说明见各来源。'

/** How each recorded declaration state is announced at the lexicon level. */
const REVIEW_LABELS: Record<string, string> = {
  pending_owner_approval: '有待批准的来源',
  not_assessed: '许可未评估',
}

export function SourcesPage() {
  const lexicons = useQuery({
    queryKey: ['lexicons'],
    queryFn: () => api<LexiconRow[]>('/api/lexicons'),
  })

  return (
    <div className="page sources-page">
      <header className="page-head">
        <h1>数据来源与许可</h1>
        <p>按词库列出公共导入时记录的来源、许可标识与适用范围声明。</p>
      </header>
      <p className="sources-notice">{DECLARATION_NOTICE}</p>

      {lexicons.isLoading && <LoadingState label="正在读取词库…" />}
      {lexicons.isError && <ErrorState error={lexicons.error} action={
        <button className="button secondary" onClick={() => void lexicons.refetch()}>重试</button>
      } />}
      {lexicons.data && lexicons.data.length === 0 && (
        <EmptyState
          title="还没有可查看的词库"
          description="加入一个公共词库，或创建自己的词库后，这里会列出它记录的来源。"
        />
      )}
      {lexicons.data && lexicons.data.length > 0 && (
        <div className="lexicon-sources">
          {lexicons.data.map((lexicon) => (
            <LexiconSourcesSection key={lexicon.id} lexicon={lexicon} />
          ))}
        </div>
      )}
    </div>
  )
}

/** One lexicon's recorded sources, read on their own so a slow one holds nothing up. */
function LexiconSourcesSection({ lexicon }: { lexicon: LexiconRow }) {
  const headingId = `lexicon-${lexicon.id}`
  const query = useQuery({
    queryKey: ['lexicon-sources', lexicon.id],
    queryFn: () => api<LexiconSources>(`/api/lexicons/${lexicon.id}/sources`),
  })

  return (
    <section className="lexicon-section" aria-labelledby={headingId}>
      <header className="lexicon-head">
        <div>
          <h2 id={headingId}>{lexicon.name}</h2>
          <small>{lexicon.is_system ? '公共词库' : '我的词库'} · {lexicon.entry_count} 个词条</small>
        </div>
      </header>

      {query.isLoading && <LoadingState label="正在读取来源…" />}
      {query.isError && <ErrorState error={query.error} />}
      {query.data && (
        <>
          <AuthorizationNotice review={query.data.authorization_review} />
          {query.data.sources.length === 0 ? (
            <EmptyState
              title="该词库还没有记录任何来源"
              description="它的内容不是通过公共导入写入的，或还没有发生真实导入。"
            />
          ) : (
            <div className="source-cards">
              {query.data.sources.map((row) => (
                <SourceCard key={row.source_artifact_id} row={row} lexiconId={lexicon.id} />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}

/**
 * The server's own review statement, quoted rather than summarised.
 *
 * It is rendered for a lexicon with no sources too: "nothing is recorded" must not
 * read as "nothing was found wanting".
 */
function AuthorizationNotice({ review }: { review: AuthorizationReview }) {
  const pending = review.status === 'pending_owner_approval'
  return (
    <div className={pending ? 'authorization-note pending' : 'authorization-note'}>
      {pending ? <ShieldAlert size={17} /> : <FileText size={17} />}
      <div>
        <strong>{REVIEW_LABELS[review.status] ?? review.status}</strong>
        <p>{review.message}</p>
        {review.pending_sources.length > 0 && (
          <small>声明写明未获批准的来源：{review.pending_sources.join('、')}</small>
        )}
      </div>
    </div>
  )
}

function SourceCard({ row, lexiconId }: { row: SourceArtifactRow; lexiconId: number }) {
  // The card's fragment is built by the shared helper, the same one the word detail's
  // link uses. It carries the lexicon as well as the artifact, because one artifact can
  // be a card in several lexicons and a bare artifact anchor would make all but the
  // first one unreachable.
  const anchor = sourceCardAnchor(lexiconId, row.source_artifact_id)
  const titleId = `${anchor}-title`
  const link = licenseLink(row.license_id)
  const card = useRef<HTMLElement>(null)
  const hash = useLocation().hash

  // A card only exists once this lexicon's sources have loaded, which is later than the
  // browser's own fragment scroll: arriving here from a word detail is a client-side
  // navigation, so nothing moved the reader to the card the link named. Chrome gets
  // this right on a fresh load and misses it on that path, so the card that owns the
  // anchor brings itself into view when the fragment names it.
  useEffect(() => {
    let fragment: string
    try {
      fragment = decodeURIComponent(hash.replace(/^#/, ''))
    } catch (error) {
      if (error instanceof URIError) return
      throw error
    }
    if (fragment !== anchor) return
    card.current?.scrollIntoView({ block: 'start' })
  }, [hash, anchor])

  return (
    <article className="source-card" id={anchor} ref={card} aria-labelledby={titleId}>
      <header>
        <h3 id={titleId}>{row.publisher || row.name}</h3>
        <span className="source-role">{row.role}</span>
        <span className={row.declared_approval_state === 'explicitly_unapproved'
          ? 'approval-badge unapproved' : 'approval-badge'}>{approvalLabel(row.declared_approval_state)}</span>
      </header>
      <dl className="source-meta">
        <div><dt>版本</dt><dd>{row.version || '未记录'}</dd></div>
        <div><dt>取得时间</dt><dd>{row.obtained_at_utc || '未记录'}</dd></div>
        <div>
          <dt>许可标识</dt>
          <dd className="license-value">
            {!row.license_id
              ? '未记录'
              : link
                // Only an identifier this code knows exactly gets a link; the anchor's
                // text stays the recorded value so the page never shows a different
                // identifier than the one the import recorded.
                ? <a href={link} target="_blank" rel="noreferrer noopener">{row.license_id}</a>
                : row.license_id}
          </dd>
        </div>
        <div>
          <dt>许可文本指纹</dt>
          <dd>{row.license_text_sha256 ? row.license_text_sha256.slice(0, 12) : '未记录'}</dd>
        </div>
        <div className="full"><dt>使用范围声明</dt><dd>{row.use_scope || '未记录'}</dd></div>
        <div className="full"><dt>展示范围声明</dt><dd>{row.display_scope || '未记录'}</dd></div>
      </dl>
      <SourceAttribution value={row.attribution} />
      <p className="source-foot">
        记录文件：{row.name}；导入记录：
        {row.runs.length > 0
          ? row.runs.map((run) => `${run.run_id}（${run.outcome}）`).join('、')
          : '未记录'}
      </p>
    </article>
  )
}

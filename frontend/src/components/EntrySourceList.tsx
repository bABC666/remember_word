import { Link } from 'react-router-dom'
import type { EntrySources, SourceEvidence } from '../types'

/**
 * Where each field of this word came from, as the import recorded it.
 *
 * Three rules shape this block, and all three are the display design's.
 *
 * **An adopted row and a recorded-but-unadopted row are not the same thing.** The
 * confirmation writes a row for every value it considered, so `candidates` holds rows a
 * human saw and did not take. Only a `selected_for_default` row may sit beside the
 * value that is displayed, which is why the two are separate lists -- and why the
 * candidates are folded away rather than listed beside the adopted row where a reader
 * could take one for the origin of the other.
 *
 * **A link is offered only when the server sent one.** `source_revision_url` is either
 * a complete `https://` URL or an empty string; the client never assembles it, and an
 * empty one is not a gap to be filled but a state to be stated. The two ways it can be
 * empty are said differently -- no revision was recorded, or the frozen mapping
 * declares no usable template -- because they are different facts about the record.
 *
 * **This is a transcript, not a verdict.** The block shows the licence *identifier* a
 * submitter declared and says in words that it is not a statement of authorization. A
 * licence id displayed on its own could read as a grant; the canonical link, where one
 * is known, lives on the sources page this block links to.
 */

/** `field_kind` as the import records it, named as a reader knows it. */
const FIELD_LABELS: Record<string, string> = {
  word: '单词',
  meaning: '释义',
  phonetic: '音标',
  part_of_speech: '词性',
}

const fieldLabel = (kind: string) => FIELD_LABELS[kind] ?? kind

export function EntrySourceList({ sources }: { sources: EntrySources }) {
  const fields = sources.fields
  const incomplete = sources.completeness.status === 'incomplete'
  const stateMessage = incomplete
    ? sources.completeness.message || '该词条的来源记录不完整。'
    : ''

  return (
    // A word with no source record keeps its block and says so. The design is explicit
    // that the block may not silently disappear: "nothing recorded" and "nothing wrong"
    // would then look alike.
    <section className="entry-sources" aria-label="来源记录">
      <span>来源记录</span>
      {stateMessage && <p className="source-state">{stateMessage}</p>}

      {fields.map((group) => {
        const label = fieldLabel(group.field_kind)
        return (
          <div className="field-source" key={group.field_kind}>
            <h3 className="field-source-name">{label}</h3>
            {group.selected.length > 0 ? (
              <ul className="source-rows" aria-label={`${label}：已采用的来源`}>
                {group.selected.map((row) => (
                  <EvidenceRow key={row.source_evidence_id} row={row} unadopted={false} />
                ))}
              </ul>
            ) : (
              <p className="source-state">该字段没有采用来源，只有未采用的候选。</p>
            )}
            {group.candidates.length > 0 && (
              <details className="source-candidates">
                <summary>未采用的其他来源（{group.candidates.length}）</summary>
                <ul className="source-rows unadopted" aria-label={`${label}：未采用的候选`}>
                  {group.candidates.map((row) => (
                    <EvidenceRow key={row.source_evidence_id} row={row} unadopted />
                  ))}
                </ul>
              </details>
            )}
          </div>
        )
      })}

      {fields.length > 0 && (
        <p className="source-foot">以上是导入时记录的来源声明，不代表授权已获确认。</p>
      )}
    </section>
  )
}

function EvidenceRow({ row, unadopted }: { row: SourceEvidence; unadopted: boolean }) {
  return (
    <li className={unadopted ? 'source-row unadopted' : 'source-row'}>
      <div className="source-row-head">
        <strong>{row.source.publisher || row.source.name}</strong>
        <span className="source-role">{row.source.role}</span>
        <span className={unadopted ? 'source-decision unadopted' : 'source-decision'}>
          {unadopted ? '未采用' : '已采用'}
        </span>
      </div>
      <p className="source-row-text">{row.raw_text}</p>
      <small className="source-row-meta">
        第 {row.row_locator} 行 · 版本 {row.source.version || '未记录'} · 许可标识 {row.source.license_id || '未记录'}
      </small>
      <small className="source-row-revision">
        {row.source_revision ? `固定修订 ${row.source_revision}` : '没有记录固定修订号'}
      </small>
      {row.source_revision_url ? (
        <a
          className="source-row-link"
          href={row.source_revision_url}
          target="_blank"
          rel="noreferrer noopener"
        >
          查看该固定修订
        </a>
      ) : (
        // No link, and the reason said out loud. What is never done here is guessing
        // one from the line number or from another row's revision.
        <small className="source-row-nolink">
          {row.source_revision ? '无法合成固定修订链接（映射未声明或不可用）' : '没有修订号，无法回查固定版本'}
        </small>
      )}
      {/* Every recorded row links to its own card on the sources page, including a
          candidate: a source we did not adopt is still a source we recorded, and the
          anchor is the artifact's own id. */}
      <Link className="source-row-card" to={`/sources#source-${row.source.source_artifact_id}`}>
        来源详情
      </Link>
    </li>
  )
}

"""Display only structurally recovered source values, explicitly unconfirmed."""
from app.models import EntryDictionaryExtraction


def display_extraction(row: EntryDictionaryExtraction | None) -> dict | None:
    if row is None:
        return None
    payload = row.payload
    senses = payload.get('senses', [])
    pronunciations = payload.get('pronunciations', [])
    audited = payload.get('audit', {}).get('version') == 'netem-source-audit-v1'
    result = {
        'entry_id': row.lexicon_entry_id,
        'parser_version': row.parser_version,
        'status': 'unconfirmed_source_extraction',
        'senses': [s for s in senses if s.get('language') == 'en' and (
            (s.get('status') == 'extracted' and s.get('pos_key'))
            or (audited and s.get('status') == 'source_verified'))],
        # Multiple source variants are kept; no canonical phonetic is guessed.
        'pronunciations': [p for p in pronunciations if p.get('language') == 'en' and (
            p.get('status') == 'extracted' or (audited and p.get('status') == 'source_verified'))],
        'pending_count': sum(s.get('status') == 'pending' for s in senses + pronunciations),
        'pending_reasons': sorted({s.get('reason', '') for s in senses + pronunciations
                                   if s.get('status') == 'pending'}),
        'pending_values': [s for s in senses + pronunciations if s.get('status') in {'pending', 'rejected'}],
        'originals': [{'raw_text': item['text'], 'source': item['source']}
                      for item in payload.get('originals', [])],
    }
    if audited:
        result.update(audit_status='automated_source_verified',
                      excluded_count=sum(s.get('status') == 'rejected' for s in senses + pronunciations))
    return result

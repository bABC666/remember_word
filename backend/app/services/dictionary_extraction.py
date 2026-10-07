"""Display only structurally recovered source values, explicitly unconfirmed."""
from app.models import EntryDictionaryExtraction


def display_extraction(row: EntryDictionaryExtraction | None) -> dict | None:
    if row is None:
        return None
    payload = row.payload
    senses = payload.get('senses', [])
    pronunciations = payload.get('pronunciations', [])
    return {
        'parser_version': row.parser_version,
        'status': 'unconfirmed_source_extraction',
        'senses': [s for s in senses if s.get('status') == 'extracted'
                   and s.get('language') == 'en' and s.get('pos_key')],
        # Multiple source variants are kept; no canonical phonetic is guessed.
        'pronunciations': [p for p in pronunciations if p.get('status') == 'extracted'
                           and p.get('language') == 'en'],
        'pending_count': sum(s.get('status') == 'pending' for s in senses + pronunciations),
        'pending_reasons': sorted({s.get('reason', '') for s in senses + pronunciations
                                   if s.get('status') == 'pending'}),
        'pending_values': [s for s in senses + pronunciations if s.get('status') == 'pending'],
        'originals': [{'raw_text': item['text'], 'source': item['source']}
                      for item in payload.get('originals', [])],
    }

"""Resumable fixed Wiktionary evidence acquisition, never writes a database."""
import argparse
import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
import netem_display_diagnosis as diagnosis
import netem_rich_import as rich


def fetch(database, out, limit):
    out = out.resolve()
    if not out.is_relative_to(rich.ROOT / 'test-artifacts'):
        raise ValueError('evidence directory must be in test-artifacts')
    out.mkdir(parents=True, exist_ok=True)
    rows = diagnosis.records(database)
    required = ['Easter','July','January','question','learn','or','and','yellow','you','February',
                'bank','lead','record','read','wind','bow','set','fine','double','lease']
    rows.sort(key=lambda r: (r['word'] not in required, rich.digest(r['word'])))
    if limit:
        rows = rows[:limit]
    batches = [rows[i:i+40] for i in range(0, len(rows), 40)]
    def get(batch):
        words = [r['word'] for r in batch]
        key = rich.digest(words)[:16]
        path = out / (key + '.json')
        meta = out / (key + '.meta.json')
        if path.exists() and meta.exists():
            record = json.loads(meta.read_bytes())
            if rich.sha(path.read_bytes()) != record['sha256']:
                raise ValueError('cached response fingerprint changed')
            return record
        url = 'https://en.wiktionary.org/w/api.php?' + urlencode({
            'action':'query','format':'json','formatversion':2,'prop':'revisions',
            'rvprop':'ids|timestamp|content','rvslots':'main','titles':'|'.join(words)})
        for attempt in range(8):
            try:
                req = urllib.request.Request(url, headers={'User-Agent':'NETEM-quality-audit/1.0 (local educational source verification)'})
                with urllib.request.urlopen(req, timeout=45) as response:
                    body = response.read()
                data = json.loads(body)
                if 'error' in data or 'query' not in data:
                    raise ValueError('invalid dictionary API response')
                record = {'url':url,'fetched_at':datetime.now(UTC).isoformat(),
                          'words':words,'file':path.name,'sha256':rich.sha(body)}
                path.write_bytes(body)
                meta.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                time.sleep(4)
                return record
            except Exception as error:
                if attempt == 7:
                    raise
                delay = max(30, int(error.headers.get('Retry-After','60'))) if isinstance(error, urllib.error.HTTPError) and error.code == 429 else 3*(attempt+1)
                print('Source backoff:', type(error).__name__, 'seconds:', delay, flush=True)
                time.sleep(delay)
    with ThreadPoolExecutor(max_workers=1) as pool:
        records = list(pool.map(get, batches))
    # The licence and contributor-attribution policy is evidence, too.
    for title in ['Wiktionary:Copyrights','Wiktionary:Text of the Creative Commons Attribution-ShareAlike 4.0 International License']:
        records.append(get([{'word':title}]))
    manifest = {'method':'Wiktionary revision API; exact title only; no redirect guessing',
                'entries':len(rows),'core_confirmation':False,'files':records}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'entries':len(rows),'batches':len(records)}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--limit',type=int)
    a = p.parse_args()
    print(json.dumps(fetch(a.database,a.out,a.limit)))

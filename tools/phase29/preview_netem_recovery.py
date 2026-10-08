"""Reproduce the retained-source recovery preview without writing its input DB."""
import argparse
import json
from pathlib import Path

import netem_source_audit as audit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    evidence=audit.ROOT/'test-artifacts/netem-source-audit-20261008'
    report=audit.preview(args.database,evidence/'records.json',
        [evidence/f'locked-review-{i}.json' for i in range(3)],args.out,args.limit,
        evidence/'locked-review-existing-77.json',evidence/'review-corrections-recovery.json',
        evidence/'recovered-candidates.json',
        [evidence/f'recovered-review-{i}.json' for i in range(2)],
        evidence/'recovered-originals',evidence/'fetched-originals.json',
        [evidence/'fetched-candidates.json',evidence/'extra-format-normalized-candidates.json'],
        [evidence/name for name in ['fetched-reviewed-0-599.json','fetched-reviewed-600-1199.json',
            'fetched-reviewed-1200-1799.json','fetched-reviewed-1800-2283.json','extra-reviewed-0-825.json']],
        [evidence/'normalized-gap-candidates.json',evidence/'annotation-candidates-v2.json'],
        [evidence/'normalized-gap-reviewed.json',evidence/'annotations-reviewed-v2.json'],
        evidence/'license-evidence')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

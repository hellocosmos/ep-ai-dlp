"""Guard the split boundaries used to make training improvement claims."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'judge/training'))
from policies import POLICIES
DATA=ROOT/'judge/training/data/v1'

def test_frozen_hashes_and_family_document_isolation():
    manifest=json.loads((DATA/'manifest.json').read_text());families={};documents={}
    for split,stats in manifest['splits'].items():
        raw=(DATA/f'{split}.jsonl').read_bytes();assert hashlib.sha256(raw).hexdigest()==stats['sha256']
        rows=[json.loads(x) for x in raw.splitlines()];assert len(rows)==stats['rows']
        unique=set()
        for row in rows:
            for registry,key in [(families,row['source_family']),(documents,row['document_id'])]:
                assert registry.setdefault(key,split)==split
            key=(row['text'],row['question'],json.dumps(row['choices'],sort_keys=True),row['gold'])
            assert key not in unique;unique.add(key)
        assert {r['language'] for r in rows}=={'ko','en'}
        assert {r['policy_language'] for r in rows}=={'ko','en'}
        assert {r['policy_id'] for r in rows}==set(POLICIES)
        for policy in POLICIES:
            for lang in ['ko','en']:
                assert {r['gold'] for r in rows if r['policy_id']==policy and r['language']==lang}=={'match','no_match','insufficient'}

def test_no_legacy_evaluation_text_in_new_training():
    legacy={json.loads(s)['text'] for s in (ROOT/'judge/evaluation/corpus.jsonl').read_text().splitlines()}
    train=[json.loads(s) for s in (DATA/'train.jsonl').read_text().splitlines()]
    assert not legacy.intersection(r['text'] for r in train)
    assert all(r['provenance']=='agent_authored_synthetic_family_v1' for r in train)

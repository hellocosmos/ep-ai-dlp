import json,statistics,hashlib
from pathlib import Path
files={'decider_base':'evidence/judge/finetune/base-dev.jsonl','decider_finetuned':'evidence/judge/finetune/candidate-dev.jsonl','laya_typed':'evidence/judge/candidates/laya-dev-356.jsonl','gliner_multi_decide':'evidence/judge/candidates/gliner-dev-356.jsonl'}
data=Path('judge/training/data/v1/dev.jsonl');expected={json.loads(s)['id'] for s in data.read_text().splitlines()}
report={'scope':'Previously used synthetic DEV set; candidate screening, not independent acceptance or production evidence','dev_sha256':hashlib.sha256(data.read_bytes()).hexdigest(),'models':{}}
def metrics(rs):
 return {'n':len(rs),'correct':sum(r['gold']==r['prediction'] for r in rs),'accuracy':sum(r['gold']==r['prediction'] for r in rs)/len(rs),'match_predicted_no_match':sum(r['gold']=='match' and r['prediction']=='no_match' for r in rs),'insufficient_predicted_no_match':sum(r['gold']=='insufficient' and r['prediction']=='no_match' for r in rs),'no_match_predicted_match':sum(r['gold']=='no_match' and r['prediction']=='match' for r in rs)}
for model,f in files.items():
 rows=[json.loads(s) for s in Path(f).read_text().splitlines()];assert len(rows)==len(expected) and {r['id'] for r in rows}==expected
 assert all(r['prediction'] in ('match','no_match','insufficient') for r in rows)
 result=metrics(rows)
 result['slices']={key:{v:metrics([r for r in rows if r[key]==v]) for v in sorted({r[key] for r in rows})} for key in ['language','policy_language','policy_id']}
 if 'latency_ms' in rows[0]:
  lat=sorted(r['latency_ms'] for r in rows);result['latency_ms']={'p50':statistics.median(lat),'p95':lat[int(.95*(len(lat)-1))],'scope':'sequential batch=1, one warmup excluded, different runtimes; not optimized like-for-like latency'}
 if model=='laya_typed': result['state_truncated_count']=sum(r['raw']['usage']['truncated'] for r in rows)
 report['models'][model]=result
Path('evidence/judge/candidates/comparison.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:{a:b for a,b in v.items() if a!='slices'} for k,v in report['models'].items()},indent=2))

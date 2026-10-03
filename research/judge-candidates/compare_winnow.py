"""Compare unchanged synthetic inputs; thresholds are fit on calibration only."""
import hashlib,json,statistics,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'judge/training'))
from calibration_metrics import decision,actions
from refine_calibration import fine_gates,validate
OUT=ROOT/'evidence/judge/winnow'
def read(p):
 rows=[json.loads(s) for s in p.read_text().splitlines()];validate(rows);return rows
def summary(rows):
 return {'n':len(rows),'correct':sum(r['prediction']==r['gold'] for r in rows),'accuracy':sum(r['prediction']==r['gold'] for r in rows)/len(rows),'sensitive_predicted_safe':sum(r['gold']=='match' and r['prediction']=='no_match' for r in rows),'ambiguous_predicted_safe':sum(r['gold']=='insufficient' and r['prediction']=='no_match' for r in rows)}
def selected_actions(rows, decisions):
 return {'n':len(rows),'allow':decisions.count('allow'),'block':decisions.count('block'),'review':decisions.count('review'),'sensitive_allowed':sum(r['gold']=='match' and a=='allow' for r,a in zip(rows,decisions)),'ambiguous_allowed':sum(r['gold']=='insufficient' and a=='allow' for r,a in zip(rows,decisions)),'safe_blocked':sum(r['gold']=='no_match' and a=='block' for r,a in zip(rows,decisions)),'ambiguous_blocked':sum(r['gold']=='insufficient' and a=='block' for r,a in zip(rows,decisions))}
base=read(ROOT/'evidence/judge/finetune/candidate-test.jsonl');base_idx={r['id']:r for r in base}
base_gates=json.loads((ROOT/'evidence/judge/routing-v2/profile.json').read_text())['thresholds']
report={'scope':'Retrospective reused synthetic test; not fresh independent acceptance. All thresholds use calibration only. Different runtimes and quantization.','decider':summary(base),'decider_gates':base_gates,'decider_actions':actions(base,base_gates),'models':{}}
for model in ['Winnow-E4B','Winnow-12B']:
 cal=read(OUT/f'{model}-calibration.jsonl');test=read(OUT/f'{model}-test.jsonl')
 expected_cal={json.loads(s)['id'] for s in (ROOT/'judge/training/data/v1/calibration.jsonl').read_text().splitlines()}
 assert len(cal)==len(expected_cal)==428 and {r['id'] for r in cal}==expected_cal
 assert len(test)==len(base_idx)==736 and {r['id'] for r in test}==set(base_idx)
 assert not {r['source_family'] for r in cal}&{r['source_family'] for r in test}
 gates=fine_gates(cal); lat=sorted(r['latency_ms'] for r in test)
 d=summary(test)|{'thresholds':gates,'calibration':summary(cal),'calibration_actions':actions(cal,gates),'actions':actions(test,gates),'language':{l:summary([r for r in test if r['language']==l]) for l in ['en','ko']},'policy':{p:summary([r for r in test if r['policy_id']==p]) for p in sorted({r['policy_id'] for r in test})},'latency_ms':{'p50':statistics.median(lat),'p95':lat[int((len(lat)-1)*.95)]}}
 d['error_overlap']={'decider_wrong_rescued':sum(base_idx[r['id']]['prediction']!=r['gold'] and r['prediction']==r['gold'] for r in test),'decider_correct_regressed':sum(base_idx[r['id']]['prediction']==r['gold'] and r['prediction']!=r['gold'] for r in test),'both_wrong':sum(base_idx[r['id']]['prediction']!=r['gold'] and r['prediction']!=r['gold'] for r in test)}
 escalated=[r for r in test if decision(base_idx[r['id']],base_gates)=='review']
 d['decider_review_subset']=summary(escalated)
 cascade=[decision(base_idx[r['id']],base_gates) if decision(base_idx[r['id']],base_gates)!='review' else decision(r,gates) for r in test]
 d['cascade']=selected_actions(test,cascade)
 d['source_metadata']=json.loads((ROOT/'research/judge-candidates'/f'{model}-metadata.json').read_text())
 d['result_sha256']={split:hashlib.sha256((OUT/f'{model}-{split}.jsonl').read_bytes()).hexdigest() for split in ['calibration','test']}
 report['models'][model]=d
(OUT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:{a:b for a,b in v.items() if a not in ['source_metadata','policy','result_sha256']} for k,v in report['models'].items()},indent=2))

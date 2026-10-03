"""Offline Q8/Q6/Q5/Q4 comparison; no threshold is chosen on test outcomes."""
import hashlib,json,statistics,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'judge/training'))
from calibration_metrics import decision,actions
from refine_calibration import fine_gates,validate
OUT=ROOT/'evidence/judge/winnow';QUANTS=['Q8_0','Q6_K','Q5_K_M','Q4_K_M']
def read(p):
 rows=[json.loads(s) for s in p.read_text().splitlines()];validate(rows);return rows
def metrics(rows):
 return {'n':len(rows),'correct':sum(r['prediction']==r['gold'] for r in rows),'accuracy':sum(r['prediction']==r['gold'] for r in rows)/len(rows),'sensitive_predicted_safe':sum(r['gold']=='match' and r['prediction']=='no_match' for r in rows),'ambiguous_predicted_safe':sum(r['gold']=='insufficient' and r['prediction']=='no_match' for r in rows)}
def summarize(rows):
 d=metrics(rows);lat=sorted(r['latency_ms'] for r in rows if not r.get('cached',False))
 d.update(language={l:metrics([r for r in rows if r['language']==l]) for l in ['en','ko']},policy={p:metrics([r for r in rows if r['policy_id']==p]) for p in sorted({r['policy_id'] for r in rows})},latency_ms={'n':len(lat),'p50':statistics.median(lat),'p95':lat[int((len(lat)-1)*.95)]})
 return d
def selected_actions(rows, decisions):
 return {'n':len(rows),'allow':decisions.count('allow'),'block':decisions.count('block'),'review':decisions.count('review'),'sensitive_allowed':sum(r['gold']=='match' and a=='allow' for r,a in zip(rows,decisions)),'ambiguous_allowed':sum(r['gold']=='insufficient' and a=='allow' for r,a in zip(rows,decisions)),'safe_blocked':sum(r['gold']=='no_match' and a=='block' for r,a in zip(rows,decisions)),'ambiguous_blocked':sum(r['gold']=='insufficient' and a=='block' for r,a in zip(rows,decisions))}
expected={s:{json.loads(l)['id'] for l in (ROOT/'judge/training/data/v1'/f'{s}.jsonl').read_text().splitlines()} for s in ['calibration','test']}
allrows={};report={'scope':'Previously consumed synthetic short-document DLP test. Candidate/quantization comparison, not fresh independent acceptance. Timings are loopback on a shared Mac, not remote production network.','execution':json.loads((OUT/'execution-plan.json').read_text()),'models':{}}
for name in ['decider-4b-GGUF-Q4','decider-4b-finetuned-MLX-Q4']+['Winnow-E4B-'+q for q in QUANTS]:
 data={s:read(OUT/f'{name}-{s}.jsonl') for s in ['calibration','test']}
 for split,rs in data.items():assert len(rs)==len(expected[split]) and {r['id'] for r in rs}==expected[split]
 assert not {r['source_family'] for r in data['calibration']}&{r['source_family'] for r in data['test']}
 gates=fine_gates(data['calibration']);test=data['test'];allrows[name]=test
 d=summarize(test)|{'thresholds':gates,'calibration':metrics(data['calibration']),'calibration_actions':actions(data['calibration'],gates),'actions':actions(test,gates)}
 d['fixed_threshold_diagnostics']={str(t):actions(test,{'allow':t,'block':t}) for t in [.5,.7,.8,.9,.95,.97,.99]}
 report['models'][name]=d
base_name='decider-4b-finetuned-MLX-Q4';base_idx={r['id']:r for r in allrows[base_name]};base_gates=report['models'][base_name]['thresholds']
q8_idx={r['id']:r for r in allrows['Winnow-E4B-Q8_0']}
for q in QUANTS:
 name='Winnow-E4B-'+q;rows=allrows[name];d=report['models'][name];gates=d['thresholds']
 d['versus_q8']={'changed_predictions':sum(r['prediction']!=q8_idx[r['id']]['prediction'] for r in rows),'accuracy_delta_pp':100*(d['accuracy']-report['models']['Winnow-E4B-Q8_0']['accuracy'])}
 d['versus_finetuned_decider']={'decider_wrong_rescued':sum(base_idx[r['id']]['prediction']!=r['gold'] and r['prediction']==r['gold'] for r in rows),'decider_correct_regressed':sum(base_idx[r['id']]['prediction']==r['gold'] and r['prediction']!=r['gold'] for r in rows),'both_wrong':sum(base_idx[r['id']]['prediction']!=r['gold'] and r['prediction']!=r['gold'] for r in rows)}
 escalated=[r for r in rows if decision(base_idx[r['id']],base_gates)=='review'];d['decider_review_subset']=metrics(escalated) if escalated else None
 cascade=[decision(base_idx[r['id']],base_gates) if decision(base_idx[r['id']],base_gates)!='review' else decision(r,gates) for r in rows]
 d['cascade']=selected_actions(rows,cascade)
 d['latency_probe']=json.loads((OUT/f'{name}-api-latency.json').read_text())
 multi=json.loads((OUT/f'{name}-multi-policy.json').read_text());d['multi_policy']={mode:{'n':len(s:=[r for r in multi if r['mode']==mode]),'success':sum(all(x==200 for x in r['statuses']) for r in s),'median_ms':statistics.median(r['ms'] for r in s)} for mode in ['six_sequential','six_shared_state']}
 d['weights']=json.loads((OUT/'quantized-manifest.json').read_text())['files'][q]
 d['runtime']=json.loads((OUT/f'{q}-runtime.json').read_text())
 d['gpu_allocated_bytes']=max(r['diagnostics']['backend_allocated_bytes'] for r in rows)
 d['input_token_range']=[min(r['usage']['input_tokens'] for r in rows),max(r['usage']['input_tokens'] for r in rows)]
 assert all(r['usage']['output_tokens']==0 and not r['diagnostics']['cache_hit'] and r['diagnostics']['prefix_reused_tokens']==0 for r in rows)
(OUT/'quantization-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:{a:v[a] for a in ['accuracy','language','latency_ms','actions']} for k,v in report['models'].items()},indent=2))

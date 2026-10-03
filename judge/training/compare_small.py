"""Frozen six-condition comparison; gates fit to calibration, never test."""
import json,statistics
from small_judges import ROOT,NAMES,read_data
from refine_calibration import fine_gates,validate
from calibration_metrics import actions
OUT=ROOT/'evidence/judge/small-comparison-bf16'
QUAL=ROOT/'evidence/judge/small-comparison'
expected={s:{r['id'] for r in rows} for s,rows in read_data().items()}
def read(path):
    rows=[json.loads(s) for s in path.read_text().splitlines()];validate(rows);return rows
def metric(rows):
    return {'n':len(rows),'correct':sum(r['gold']==r['prediction'] for r in rows),'accuracy':sum(r['gold']==r['prediction'] for r in rows)/len(rows),'sensitive_predicted_safe':sum(r['gold']=='match' and r['prediction']=='no_match' for r in rows),'ambiguous_predicted_safe':sum(r['gold']=='insufficient' and r['prediction']=='no_match' for r in rows)}
report={'scope':'Three publicly trained decision checkpoints, before/after local DLP LoRA. Six paired MLX BF16 conditions; same loopback queue/HTTP harness. Synthetic reused test, not new independent production acceptance.','models':{}}
for name in NAMES:
    directory=OUT/name;artifact=ROOT/'.local/finetune/small-judges-adapted-bf16'/name;conditions={};allrows={}
    assert json.loads((QUAL/name/'qualification-transformers.json').read_text())['passed']
    assert json.loads((directory/'gradient-smoke.json').read_text())['passed']
    for condition in ['original','adapted']:
        data={s:read(directory/f'{condition}-{s}.jsonl') for s in ['calibration','test']}
        for split,rows in data.items():
            assert len(rows)==len(expected[split]) and {r['id'] for r in rows}==expected[split]
            assert all(not r['cached'] and r['output_tokens']==0 and r['input_tokens']<=2048 for r in rows)
        assert not {r['source_family'] for r in data['calibration']}&{r['source_family'] for r in data['test']}
        gates=fine_gates(data['calibration']);rows=data['test'];allrows[condition]=rows;lat=sorted(r['latency_ms'] for r in rows)
        d=metric(rows)|{'language':{lang:metric([r for r in rows if r['language']==lang]) for lang in ['ko','en']},'policy':{p:metric([r for r in rows if r['policy_id']==p]) for p in sorted({r['policy_id'] for r in rows})},'thresholds':gates,'calibration_actions':actions(data['calibration'],gates),'actions':actions(rows,gates),'latency_ms':{'p50':statistics.median(lat),'p95':lat[int((len(lat)-1)*.95)]},'input_tokens':[min(r['input_tokens'] for r in rows),max(r['input_tokens'] for r in rows)],'gpu_active_bytes':max(r['gpu_active_bytes'] for r in rows),'load_probe':json.loads((directory/f'{condition}-latency.json').read_text()),'runtime':json.loads((directory/f'{condition}-runtime.json').read_text())}
        d['tags']={tag:metric([r for r in rows if tag in r['tags']]) for tag in sorted({t for r in rows for t in r['tags']})}
        conditions[condition]=d
    base={r['id']:r for r in allrows['original']}
    conditions['paired_change']={'accuracy_delta_pp':100*(conditions['adapted']['accuracy']-conditions['original']['accuracy']),'rescued':sum(base[r['id']]['prediction']!=r['gold'] and r['prediction']==r['gold'] for r in allrows['adapted']),'regressed':sum(base[r['id']]['prediction']==r['gold'] and r['prediction']!=r['gold'] for r in allrows['adapted'])}
    conditions['training']=json.loads((artifact/'completion.json').read_text());conditions['selection']=json.loads((artifact/'selection.json').read_text())
    conditions['development']={'original':metric(read(directory/'base-dev.jsonl')),'adapted':metric(read(directory/'candidate-dev.jsonl'))}
    conditions['source']=json.loads((ROOT/'.local/finetune/small-judges'/name/'source-manifest.json').read_text())
    report['models'][name]=conditions
(OUT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
for name,conditions in report['models'].items():
    for c in ['original','adapted']:
        d=conditions[c];print(json.dumps({'model':name,'condition':c,**{k:d[k] for k in ['accuracy','language','latency_ms','actions']}}))

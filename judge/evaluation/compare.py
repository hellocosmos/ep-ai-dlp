"""Run fixed held-out cases. Threshold selection only uses calibration rows."""
import argparse,json,time,resource,hashlib,platform
from pathlib import Path
import numpy as np
from aidlp_judge.backends import LocalJudge
from aidlp_judge.defaults import default_policies

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--models',nargs='+',default=['decider-2b','decider-4b','standardone-3b']);args=ap.parse_args()
 cases=[json.loads(s) for s in (HERE/'corpus.jsonl').read_text().splitlines()]
 policies={p.id:p for p in default_policies()}
 out=ROOT/'evidence/judge/benchmark';out.mkdir(parents=True,exist_ok=True)
 reports=[]
 for model in args.models:
  begin=time.perf_counter();j=LocalJudge(model);load_ms=(time.perf_counter()-begin)*1000
  rows=[]
  with (out/(model+'.jsonl')).open('w') as f:
   for case in cases:
    p=policies[case['policy_id']]
    try:
     result=j.evaluate(case['text'],p.question,{'match':p.match_description,'no_match':p.no_match_description,'insufficient':'The supplied content is insufficient or genuinely ambiguous for this distinction.'},use_cache=False)
     row={k:v for k,v in case.items() if k!='text'};row.update(result);row['correct']=row['choice']==row['gold']
    except Exception as exc:row={k:v for k,v in case.items() if k!='text'};row['error']=type(exc).__name__;row['correct']=False
    rows.append(row);f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
  thresholds={}
  for pid in policies:
   cal=[r for r in rows if r['split']=='calibration' and r['policy_id']==pid and 'scores' in r]
   if not cal:continue
   candidates=[]
   for t in [0.5,0.6,0.7,0.8,0.85,0.9,0.95,0.98,0.99]:
    accepted=[r for r in cal if r['choice']!='insufficient' and r['scores'][r['choice']]>=t]
    if accepted and all(r['correct'] for r in accepted):candidates.append((len(accepted),-t,t))
   thresholds[pid]=max(candidates)[2] if candidates else 0.999
  for r in rows:
   threshold=thresholds.get(r['policy_id'],0.999)
   r['effective']='review' if 'scores' not in r or r['choice']=='insufficient' or max(r['scores'].values())<threshold else ('block' if r['choice']=='match' else 'allow')
  test=[r for r in rows if r['split']=='test'];lat=[r['latency_ms'] for r in rows if 'latency_ms' in r]
  summaries={}
  for group,predicate in [('all',lambda r:True),('ko',lambda r:r['language']=='ko'),('en',lambda r:r['language']=='en')]:
   data=[r for r in test if predicate(r)]
   summaries[group]=dict(n=len(data),raw_correct=sum(r['correct'] for r in data),
    false_allow=sum(r['gold']=='match' and r['effective']=='allow' for r in data),
    false_block=sum(r['gold']=='no_match' and r['effective']=='block' for r in data),
    reviews=sum(r['effective']=='review' for r in data),safe_allow=sum(r['gold']=='no_match' and r['effective']=='allow' for r in data),
    sensitive_count=sum(r['gold']=='match' for r in data),safe_count=sum(r['gold']=='no_match' for r in data))
  report=dict(model=model,revision=j.revision,runtime=j.runtime,load_ms=round(load_ms,2),
    p50_ms=float(np.percentile(lat,50)) if lat else None,p95_ms=float(np.percentile(lat,95)) if lat else None,
    process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    thresholds=thresholds,held_out=summaries,errors=sum('error'in r for r in rows),
    cases=rows,limits=['Authored synthetic pilot, not production accuracy.','Thresholds calibrated on eight cases per class only.','RSS is process high-water mark across sequential model runs, not isolated model VRAM.'])
  (out/(model+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
  reports.append({k:v for k,v in report.items() if k!='cases'})
  (out/'summary.json').write_text(json.dumps(dict(corpus_sha256=hashlib.sha256((HERE/'corpus.jsonl').read_bytes()).hexdigest(),platform=platform.platform(),reports=reports),ensure_ascii=False,indent=2))
  print(json.dumps(reports[-1],ensure_ascii=False),flush=True)
  j.close()
if __name__=='__main__':main()

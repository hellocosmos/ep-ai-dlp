"""Decision-level smoke comparison of MLX and existing GGUF; not numerical parity."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'judge'))
from aidlp_judge.jev_client import JevClient

def main():
    rows=[json.loads(s) for s in (ROOT/'judge/training/data/v1/dev.jsonl').read_text().splitlines()]
    mlx={r['id']:r for r in map(json.loads,(ROOT/'evidence/judge/finetune/base-dev.jsonl').read_text().splitlines())}
    chosen=[]
    for i,policy in enumerate(sorted({r['policy_id'] for r in rows})):
        for gold in ['match','no_match','insufficient']:
            chosen.append(next(r for r in rows if r['policy_id']==policy and r['gold']==gold and r['language']==('ko' if i%2 else 'en')))
    client=JevClient('http://127.0.0.1:8311',(ROOT/'.local/jev-state/api.token').read_text().strip());result=[]
    try:
        for r in chosen:
            value=client.evaluate('decider-4b',r['text'],r['question'],r['choices'])
            result.append({'id':r['id'],'gold':r['gold'],'mlx':mlx[r['id']]['prediction'],'gguf':value['choice'],'same_choice':mlx[r['id']]['prediction']==value['choice']})
    finally:client.close()
    report={'scope':'decision smoke only, different quantizers and temperature; not tensor parity','rows':result,'agree':sum(r['same_choice'] for r in result),'total':len(result)}
    (ROOT/'evidence/judge/finetune/base-runtime-smoke.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()

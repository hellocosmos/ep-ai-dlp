"""Independent Transformers float32 reference for six saved development prompts."""
import argparse,gc,json,time
from pathlib import Path
import torch
from transformers import AutoModel,AutoModelForCausalLM
from safetensors.torch import load_file
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('model');a=p.parse_args();torch.set_num_threads(8)
directory=ROOT/'.local/finetune/small-judges'/a.model;out=ROOT/'evidence/judge/small-comparison'/a.model
reference=json.loads((out/'qualification-mlx-0.json').read_text())
loader=AutoModelForCausalLM if a.model=='decider-2b' else AutoModel
model=loader.from_pretrained(directory,dtype=torch.float32,attn_implementation='sdpa',trust_remote_code=False).eval()
if a.model=='decider-2b':
    from transformers import AutoTokenizer
    tok=AutoTokenizer.from_pretrained(directory,trust_remote_code=False)
    ids=[tok.encode(c,add_special_tokens=False)[0] for c in 'ABC']
    head=model.get_output_embeddings().weight[ids].detach();backbone=model.model
else:
    head=load_file(str(directory/'readout.safetensors'))['weight'][:3].float();backbone=model
rows=[]
with torch.inference_mode():
    for r in reference['records']:
        began=time.perf_counter();x=torch.tensor([r['ids']]);hidden=backbone(input_ids=x,attention_mask=torch.ones_like(x),use_cache=False).last_hidden_state[:,-1]
        probs=torch.softmax(hidden@head.T,dim=-1)[0].tolist()
        rows.append({'id':r['id'],'probabilities':probs,'ms':(time.perf_counter()-began)*1000,'max_delta':max(abs(x-y) for x,y in zip(probs,r['probabilities'])),'argmax_equal':max(range(3),key=lambda i:probs[i])==max(range(3),key=lambda i:r['probabilities'][i])})
result={'model':a.model,'reference':'Transformers CPU float32, publisher saved backbone and head','passed':all(r['argmax_equal'] and r['max_delta']<.03 for r in rows),'max_probability_delta':max(r['max_delta'] for r in rows),'records':rows}
(out/'qualification-transformers.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if not result['passed']:raise SystemExit(1)

import json,time
from pathlib import Path
import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as opt
from mlx.utils import tree_flatten
from mlx_decider import load_base,encode,option_weights,logits,attach_lora

def main():
    started=time.perf_counter();model,tok,_=load_base();weights=option_weights(model,tok);mx.eval(weights)
    ids=encode(tok,'한별 고객: 장비 임대료 30만원 미납.','Does this contain identifiable customer transaction records?',['Customer records are present.','Only general public information.','Insufficient information.'])
    x=mx.array([ids]);lengths=mx.array([len(ids)])
    model.eval();before=mx.softmax(logits(model,x,lengths,weights),axis=-1);mx.eval(before)
    cfg=attach_lora(model,layers=8,rank=8);params=sum(v.size for _,v in tree_flatten(model.trainable_parameters()))
    optimizer=opt.AdamW(learning_rate=1e-4)
    def loss(m,x,l,y):return nn.losses.cross_entropy(logits(m,x,l,weights),y,reduction='mean')
    grad=nn.value_and_grad(model,loss)
    rows=[]
    for step in range(3):
        st=time.perf_counter();value,g=grad(model,x,lengths,mx.array([0]));optimizer.update(model,g);mx.eval(model.parameters(),optimizer.state,value)
        rows.append({'step':step,'loss':float(value.item()),'seconds':time.perf_counter()-st,'peak_memory_gb':mx.get_peak_memory()/1e9})
        print(json.dumps(rows[-1]),flush=True)
    result={'before':before.tolist(),'tokens':len(ids),'trainable_parameters':params,'config':cfg,'steps':rows,'total_seconds':time.perf_counter()-started}
    Path('evidence/judge/finetune').mkdir(parents=True,exist_ok=True);Path('evidence/judge/finetune/runtime-smoke.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()

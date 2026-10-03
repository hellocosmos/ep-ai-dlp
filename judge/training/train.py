"""Reproducible option-slot QLoRA, development-only selection and frozen holdout."""
import argparse,hashlib,json,random,time
from pathlib import Path
import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten
from mlx_lm.tuner.trainer import grad_checkpoint
from mlx_decider import ROOT,load_base,encode,option_weights,logits,attach_lora

LABELS=['match','no_match','insufficient']
DATA=ROOT/'judge/training/data/v1'
OUT=ROOT/'.local/finetune/decider-4b-dlp-v1'
EVIDENCE=ROOT/'evidence/judge/finetune'

def read_data():
    manifest=json.loads((DATA/'manifest.json').read_text()); result={}
    for split,expected in manifest['splits'].items():
        payload=(DATA/f'{split}.jsonl').read_bytes()
        assert hashlib.sha256(payload).hexdigest()==expected['sha256'],f'{split} modified'
        result[split]=[json.loads(s) for s in payload.splitlines()]
    return result

def prepare(rows,tok,randomize=False,rng=None):
    prepared=[]
    for row in rows:
        order=LABELS.copy()
        if randomize:rng.shuffle(order)
        ids=encode(tok,row['text'],row['question'],[row['choices'][k] for k in order])
        if len(ids)>768:raise ValueError(f"{row['id']}: {len(ids)} tokens exceeds limit; no silent truncation")
        prepared.append((ids,order.index(row['gold']),row|{'choice_order':order}))
    return prepared

def batch(items,pad=0):
    lengths=[len(x[0]) for x in items];width=max(lengths)
    return mx.array([x[0]+[pad]*(width-len(x[0])) for x in items]),mx.array(lengths),mx.array([x[1] for x in items])

def score(model,weights,prepared,batch_size=4):
    model.eval();results=[]
    # Length sorting reduces padded work; IDs restore original order if necessary.
    ordered=sorted(prepared,key=lambda x:len(x[0]))
    start=time.perf_counter()
    for pos in range(0,len(ordered),batch_size):
        items=ordered[pos:pos+batch_size];x,lengths,y=batch(items)
        probs=mx.softmax(logits(model,x,lengths,weights),axis=-1);mx.eval(probs)
        for item,values in zip(items,probs.tolist()):
            row=item[2];order=row.get('choice_order',LABELS);pred=order[max(range(3),key=lambda k:values[k])]
            results.append({k:row[k] for k in ['id','source_family','policy_id','language','policy_language','gold','tags']}|{'prediction':pred,'probabilities':dict(zip(order,values))})
    return results,time.perf_counter()-start

def summarize(rows):
    correct=sum(r['gold']==r['prediction'] for r in rows)
    return {'rows':len(rows),'accuracy':correct/len(rows),'confusion':{g:{p:sum(r['gold']==g and r['prediction']==p for r in rows) for p in LABELS} for g in LABELS},'match_to_no_match':sum(r['gold']=='match' and r['prediction']=='no_match' for r in rows),'insufficient_to_no_match':sum(r['gold']=='insufficient' and r['prediction']=='no_match' for r in rows)}

def save_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def save_rows(path,rows):path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))

def train(args):
    OUT.mkdir(parents=True,exist_ok=True);EVIDENCE.mkdir(parents=True,exist_ok=True)
    random.seed(args.seed);mx.random.seed(args.seed);rng=random.Random(args.seed)
    data=read_data();model,tok,config=load_base();weights=option_weights(model,tok);mx.eval(weights)
    prepared={s:prepare(rows,tok) for s,rows in data.items()}
    save_json(EVIDENCE/'token-audit.json',{s:{'rows':len(rows),'max_tokens':max(len(x[0]) for x in rows),'mean_tokens':sum(len(x[0]) for x in rows)/len(rows)} for s,rows in prepared.items()})
    # Baseline only dev at this stage: final holdout is evaluated once after selection.
    baseline,seconds=score(model,weights,prepared['dev'],args.batch_size)
    save_rows(EVIDENCE/'base-dev.jsonl',baseline);print(json.dumps({'event':'baseline_dev','seconds':seconds,**summarize(baseline)}),flush=True)
    adapter_config=attach_lora(model,layers=args.layers,rank=args.rank,scale=16)
    grad_checkpoint(model.layers[0])
    mx.set_cache_limit(1024**3)
    adapter_config['base_model_name_or_path']=str(ROOT/'.local/finetune/decider-4b-bf16')
    save_json(OUT/'adapter_config.json',adapter_config)
    steps_per_epoch=(len(data['train'])+args.batch_size-1)//args.batch_size;total=steps_per_epoch*args.epochs
    schedule=optim.join_schedules([optim.linear_schedule(1e-6,args.learning_rate,30),optim.cosine_decay(args.learning_rate,max(total-30,1),end=1e-6)],[30])
    optimizer=optim.AdamW(learning_rate=schedule,weight_decay=0.01)
    def loss(m,x,lengths,y):
        values=logits(m,x,lengths,weights)
        return nn.losses.cross_entropy(values,y,label_smoothing=0.03,reduction='mean')
    grad=nn.value_and_grad(model,loss)
    best=-1.;step=0;started=time.perf_counter();history=[]
    trainable=sum(v.size for _,v in tree_flatten(model.trainable_parameters()))
    save_json(OUT/'run-config.json',vars(args)|{'trainable_parameters':trainable,'quantization':{'bits':4,'group_size':64},'dataset_manifest':json.loads((DATA/'manifest.json').read_text()),'base_manifest':json.loads((ROOT/'judge/training/base-manifest.json').read_text()),'selection':'highest development accuracy; calibration and test excluded','label_smoothing':0.03})
    for epoch in range(args.epochs):
        rows=data['train'].copy();rng.shuffle(rows);items=prepare(rows,tok,True,rng)
        model.train()
        for layer in model.layers[:-args.layers]:layer.eval()
        for offset in range(0,len(items),args.batch_size):
            x,lengths,y=batch(items[offset:offset+args.batch_size]);t=time.perf_counter()
            value,grads=grad(model,x,lengths,y);grads,norm=optim.clip_grad_norm(grads,1.0);optimizer.update(model,grads)
            mx.eval(model.parameters(),optimizer.state,value,norm);step+=1
            if step%25==0 or step==1:
                event={'event':'train','epoch':epoch+1,'step':step,'total_steps':total,'loss':float(value.item()),'gradient_norm':float(norm.item()),'step_seconds':time.perf_counter()-t,'elapsed_seconds':time.perf_counter()-started,'peak_memory_gb':mx.get_peak_memory()/1e9}
                history.append(event);print(json.dumps(event),flush=True)
                save_rows(EVIDENCE/'train-log.jsonl',history)
            if step%steps_per_epoch==0 or step==steps_per_epoch//2:
                result,sec=score(model,weights,prepared['dev'],args.batch_size);metrics=summarize(result)
                event={'event':'development','step':step,'seconds':sec,**metrics};history.append(event);print(json.dumps(event),flush=True)
                # Always save recoverable latest adapter + optimizer + RNG states separately.
                mx.save_safetensors(str(OUT/'latest.safetensors'),dict(tree_flatten(model.trainable_parameters())))
                mx.savez(str(OUT/'optimizer.npz'),**dict(tree_flatten(optimizer.state)))
                save_json(OUT/'progress.json',{'epoch':epoch+1,'step':step,'seed':args.seed,'resume_support':'optimizer and adapter captured; automatic mid-epoch resume not implemented'})
                if metrics['accuracy']>best:
                    best=metrics['accuracy'];mx.save_safetensors(str(OUT/'adapters.safetensors'),dict(tree_flatten(model.trainable_parameters())))
                    save_rows(EVIDENCE/'candidate-dev.jsonl',result);save_json(OUT/'selection.json',event)
                model.train()
                for layer in model.layers[:-args.layers]:layer.eval()
    save_rows(EVIDENCE/'train-log.jsonl',history)
    save_json(OUT/'completion.json',{'completed':True,'steps':step,'best_dev_accuracy':best,'elapsed_seconds':time.perf_counter()-started,'peak_memory_gb':mx.get_peak_memory()/1e9})
    print(json.dumps({'event':'complete','best_dev_accuracy':best,'artifact':str(OUT)}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--epochs',type=int,default=2);p.add_argument('--batch-size',type=int,default=4);p.add_argument('--learning-rate',type=float,default=2e-5);p.add_argument('--seed',type=int,default=20261003);p.add_argument('--layers',type=int,default=1);p.add_argument('--rank',type=int,default=16)
    train(p.parse_args())

"""Matched DLP adaptation of three small decision models; selection uses dev only."""
import argparse,json,random,time
from pathlib import Path
import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten
from mlx_lm.tuner.trainer import grad_checkpoint
from small_judges import ROOT,NAMES,LABELS,SmallJudge,read_data

def write_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def write_rows(path,rows):path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
def batch(items):
    lengths=[len(r[0]) for r in items];width=max(lengths)
    return mx.array([r[0]+[0]*(width-len(r[0])) for r in items]),mx.array(lengths),mx.array([r[1] for r in items])
def prepare(judge,rows,rng=None):
    result=[]
    for row in rows:
        order=LABELS.copy()
        if rng:rng.shuffle(order)
        ids=judge.encode(row['text'],row['question'],{k:row['choices'][k] for k in order})
        result.append((ids,order.index(row['gold']),row,order))
    return result
def evaluate(judge,items,batch_size=4):
    judge.model.eval();rows=[]
    for i in range(0,len(items),batch_size):
        group=items[i:i+batch_size];x,lengths,_=batch(group);probs=mx.softmax(judge.logits(x,lengths),axis=-1);mx.eval(probs)
        for item,values in zip(group,probs.tolist()):
            row=item[2];order=item[3];p=order[max(range(3),key=lambda k:values[k])]
            rows.append({k:row[k] for k in ['id','source_family','policy_id','language','policy_language','gold','tags']}|{'prediction':p,'probabilities':dict(zip(order,values))})
    return rows
def metrics(rows):
    return {'n':len(rows),'accuracy':sum(r['prediction']==r['gold'] for r in rows)/len(rows),'sensitive_predicted_safe':sum(r['gold']=='match' and r['prediction']=='no_match' for r in rows),'ambiguous_predicted_safe':sum(r['gold']=='insufficient' and r['prediction']=='no_match' for r in rows)}

def main(args):
    artifact=ROOT/('.local/finetune/small-judges-adapted-bf16' if args.bits==0 else '.local/finetune/small-judges-adapted')/args.model
    evidence=ROOT/('evidence/judge/small-comparison-bf16' if args.bits==0 else 'evidence/judge/small-comparison')/args.model;evidence.mkdir(parents=True,exist_ok=True)
    if not args.smoke and artifact.exists():raise RuntimeError('Refusing to overwrite training artifact')
    random.seed(args.seed);mx.random.seed(args.seed);rng=random.Random(args.seed)
    judge=SmallJudge(args.model,bits=args.bits);data=read_data()
    prepared={s:prepare(judge,rows) for s,rows in data.items()}
    audit={s:{'n':len(rows),'max_tokens':max(len(x[0]) for x in rows),'min_tokens':min(len(x[0]) for x in rows)} for s,rows in prepared.items()}
    if not args.smoke:
        artifact.mkdir(parents=True);write_json(evidence/'token-audit.json',audit)
        before=evaluate(judge,prepared['dev'],args.batch_size);write_rows(evidence/'base-dev.jsonl',before)
        print(json.dumps({'event':'base_dev',**metrics(before)}),flush=True)
    config=judge.attach_lora(args.rank);grad_checkpoint(judge.model.layers[0])
    config.update(model=args.model,bits=args.bits,source=json.loads((judge.directory/'source-manifest.json').read_text()))
    steps_per_epoch=(len(data['train'])+args.batch_size-1)//args.batch_size;total=steps_per_epoch*args.epochs
    schedule=optim.join_schedules([optim.linear_schedule(1e-6,args.learning_rate,30),optim.cosine_decay(args.learning_rate,max(total-30,1),end=1e-6)],[30])
    optimizer=optim.AdamW(learning_rate=schedule,weight_decay=.01)
    def loss(model,x,lengths,y):return nn.losses.cross_entropy(judge.logits(x,lengths),y,label_smoothing=.03,reduction='mean')
    gradient=nn.value_and_grad(judge.model,loss)
    trainable=sum(v.size for _,v in tree_flatten(judge.model.trainable_parameters()))
    if not args.smoke:
        write_json(artifact/'adapter_config.json',config)
        write_json(artifact/'run-config.json',vars(args)|{'trainable_parameters':trainable,'temperature':1.0,'precision':'publisher BF16 backbone, float readout' if args.bits==0 else 'MLX affine Q4 group64, float readout','dataset_manifest':json.loads((ROOT/'judge/training/data/v1/manifest.json').read_text()),'selection':'highest development accuracy; calibration/test excluded','label_smoothing':.03})
    began=time.perf_counter();step=0;best=-1.;history=[]
    for epoch in range(args.epochs):
        rows=data['train'].copy();rng.shuffle(rows);items=prepare(judge,rows,rng);judge.train_mode()
        for offset in range(0,len(items),args.batch_size):
            x,lengths,y=batch(items[offset:offset+args.batch_size]);t=time.perf_counter()
            value,grads=gradient(judge.model,x,lengths,y);grads,norm=optim.clip_grad_norm(grads,1.0);optimizer.update(judge.model,grads)
            mx.eval(judge.model.parameters(),optimizer.state,value,norm);step+=1
            if not mx.isfinite(value).item() or not mx.isfinite(norm).item():raise RuntimeError('Nonfinite training')
            if step==1 or step%25==0 or args.smoke:
                event={'event':'train','model':args.model,'step':step,'total':total,'loss':value.item(),'gradient_norm':norm.item(),'step_seconds':time.perf_counter()-t,'elapsed_seconds':time.perf_counter()-began,'peak_memory_gb':mx.get_peak_memory()/1e9};history.append(event);print(json.dumps(event),flush=True)
                write_rows(evidence/('smoke-log.jsonl' if args.smoke else 'train-log.jsonl'),history)
            if args.smoke and step==3:
                write_json(evidence/'gradient-smoke.json',{'passed':True,'steps':3,'trainable_parameters':trainable,'peak_memory_gb':mx.get_peak_memory()/1e9});return
            if not args.smoke and (step==steps_per_epoch//2 or step%steps_per_epoch==0):
                result=evaluate(judge,prepared['dev'],args.batch_size);event={'event':'development','step':step,**metrics(result)};history.append(event);print(json.dumps(event),flush=True)
                mx.save_safetensors(str(artifact/'latest.safetensors'),dict(tree_flatten(judge.model.trainable_parameters())))
                mx.savez(str(artifact/'optimizer.npz'),**dict(tree_flatten(optimizer.state)))
                write_json(artifact/'progress.json',{'step':step,'epoch':epoch+1,'automatic_resume':False})
                if event['accuracy']>best:
                    best=event['accuracy'];mx.save_safetensors(str(artifact/'adapters.safetensors'),dict(tree_flatten(judge.model.trainable_parameters())))
                    write_rows(evidence/'candidate-dev.jsonl',result);write_json(artifact/'selection.json',event)
                judge.train_mode()
    write_rows(evidence/'train-log.jsonl',history)
    completion={'completed':True,'steps':step,'best_dev_accuracy':best,'elapsed_seconds':time.perf_counter()-began,'peak_memory_gb':mx.get_peak_memory()/1e9}
    write_json(artifact/'completion.json',completion);print(json.dumps({'event':'complete',**completion}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('model',choices=NAMES);p.add_argument('--bits',type=int,choices=[0,4],default=0);p.add_argument('--epochs',type=int,default=2);p.add_argument('--batch-size',type=int,default=4);p.add_argument('--rank',type=int,default=16);p.add_argument('--learning-rate',type=float,default=2e-5);p.add_argument('--seed',type=int,default=20261003);p.add_argument('--smoke',action='store_true');main(p.parse_args())

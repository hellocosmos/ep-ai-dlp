"""Check publisher prompt equivalence and MLX single/batch readouts on dev only."""
import argparse,ast,importlib.util,json,time
from types import SimpleNamespace
import mlx.core as mx
from small_judges import ROOT,NAMES,SmallJudge,read_data,jeff_messages

p=argparse.ArgumentParser();p.add_argument('model',choices=NAMES);p.add_argument('--bits',type=int,choices=[0,4],default=0);a=p.parse_args()
j=SmallJudge(a.model,bits=a.bits);data=read_data()['dev']
rows=[next(r for r in data if r['policy_id']==pid and r['language']==('ko' if i%2 else 'en')) for i,pid in enumerate(sorted({r['policy_id'] for r in data}))]
if a.model=='decider-2b':
    source=j.directory/'decider/prompt.py';spec=importlib.util.spec_from_file_location('pinned_decider_prompt',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class KeepOrder:
        def shuffle(self,values):pass
    for r in rows:
        example=SimpleNamespace(context=r['text'],qs=[SimpleNamespace(text=r['question'],options=list(r['choices'].values()),gold=0)])
        expected=module.build(example,j.tok,rng=KeepOrder(),max_ctx_tokens=1024)['ids']
        assert expected==j.encode(r['text'],r['question'],r['choices'])
else:
    # Evaluate only the three inspected pure prompt functions, not the Torch model/imports.
    source=ROOT/'.local/jeff-research/src/jeff/model.py';tree=ast.parse(source.read_text())
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['describe','options','decision_messages']]
    safe=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*selected],type_ignores=[])
    ns={'json':json,'MAX_OPTIONS':255,'PROMPT_LAYOUTS':('state-first','live-last')};exec(compile(ast.fix_missing_locations(safe),str(source),'exec'),ns)
    for r in rows:
        expected=ns['decision_messages']({'state':r['text'],'question':{'type':'choice','instructions':r['question'],'criteria':r['choices']}},j.codes)
        native=[dict(m) for m in expected]
        if a.model=='jeff-gemma-e2b':native[1]['content']=native[1]['content'][-1]['text']
        kwargs={'enable_thinking':False} if a.model=='jeff-qwen-2b' else {}
        text=j.tok.apply_chat_template(native,tokenize=False,add_generation_prompt=True,**kwargs)
        assert j.tok.encode(text,add_special_tokens=False)==j.encode(r['text'],r['question'],r['choices'])
        expected[1]['content']=expected[1]['content'][-1]['text']
        assert expected==jeff_messages(r['text'],r['question'],r['choices'],j.codes)
records=[]
for r in rows:
    ids=j.encode(r['text'],r['question'],r['choices']);began=time.perf_counter();probs=j.predict_ids(ids)
    records.append({'id':r['id'],'ids':ids,'probabilities':probs,'ms':(time.perf_counter()-began)*1000})
lengths=[len(r['ids']) for r in records];width=max(lengths);x=mx.array([r['ids']+[0]*(width-len(r['ids'])) for r in records])
batch=mx.softmax(j.logits(x,mx.array(lengths)),axis=-1).tolist()
delta=max(abs(v-w) for r,b in zip(records,batch) for v,w in zip(r['probabilities'],b))
assert delta<.03,delta
out=ROOT/'evidence/judge/small-comparison'/a.model;out.mkdir(parents=True,exist_ok=True)
result={'model':a.model,'bits':a.bits,'publisher_prompt_parity':True,'batch_max_probability_delta':delta,'records':records,'peak_memory_gb':mx.get_peak_memory()/1e9}
(out/f'qualification-mlx-{a.bits}.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='records'}),flush=True)

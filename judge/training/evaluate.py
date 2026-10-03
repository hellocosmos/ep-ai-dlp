"""Evaluate frozen candidate and matching 4-bit MLX baseline; calibration-only gates."""
import json,sys,random
import mlx.core as mx
from calibration_metrics import actions,calibrate
from mlx_decider import ROOT,load_base,option_weights,attach_saved_adapter
from train import DATA,OUT,EVIDENCE,prepare,read_data,score,summarize,save_json,save_rows


def slices(rows):
    result={}
    for key in ['language','policy_language','policy_id']:
        result[key]={v:summarize([r for r in rows if r[key]==v]) for v in sorted({r[key] for r in rows})}
    for tag in ['embedded_instruction','counterfactual_policy','distractor_prefix']:
        subset=[r for r in rows if tag in r['tags']]
        if subset:result[tag]=summarize(subset)
    return result

def main():
    data=read_data();report={'evaluation_settings':{'batch_size':1,'max_tokens':768,'score_temperature':1.0,'runtime':'MLX/Metal/4bit-group64','calibration_selection':'minimum gate with zero observed wrong-class accepts'}};all_results={}
    sys.path.insert(0,str(ROOT/'judge'))
    from aidlp_judge.defaults import default_policies
    policies={p.id:p for p in default_policies()}
    legacy=[]
    for line in (ROOT/'judge/evaluation/corpus.jsonl').read_text().splitlines():
        row=json.loads(line)
        if row['split']!='test':continue
        p=policies[row['policy_id']]
        legacy.append(row|{'source_family':row['group'],'policy_language':'en','tags':['legacy_regression'],'question':p.question,'choices':{'match':p.match_description,'no_match':p.no_match_description,'insufficient':'The supplied content is insufficient or genuinely ambiguous for this distinction.'}})
    data['legacy']=legacy
    for name in ['base','candidate']:
        model,tok,_=load_base();weights=option_weights(model,tok);mx.eval(weights)
        if name=='candidate':
            attach_saved_adapter(model,OUT);mx.eval(model.parameters())
        report[name]={};all_results[name]={}
        for split in ['calibration','test','legacy']:
            rows,seconds=score(model,weights,prepare(data[split],tok),batch_size=1);all_results[name][split]=rows
            save_rows(EVIDENCE/f'{name}-{split}.jsonl',rows)
            report[name][split]=summarize(rows)|{'seconds':seconds,'slices':slices(rows)}
            print(json.dumps({'model':name,'split':split,'seconds':seconds,**summarize(rows)}),flush=True)
            if split=='calibration':
                thresholds=calibrate(rows);report[name]['thresholds']=thresholds
                save_json(EVIDENCE/f'{name}-calibration-gates.json',{'thresholds':thresholds,'selected_before_final_test_scoring':True})
        permuted,sec=score(model,weights,prepare(data['dev'],tok,True,random.Random(20261004)),batch_size=1)
        report[name]['permuted_dev']=summarize(permuted)
        save_rows(EVIDENCE/f'{name}-permuted-dev.jsonl',permuted)
        report[name]['test_actions']=actions(all_results[name]['test'],thresholds)
        report[name]['calibration_actions']=actions(all_results[name]['calibration'],thresholds)
        report[name]['legacy_actions_with_new_thresholds']=actions(all_results[name]['legacy'],thresholds)
        del model,weights;mx.clear_cache()
    save_json(EVIDENCE/'comparison.json',report)
    candidate=report['candidate'];base=report['base']
    ca=candidate['test_actions'];ba=base['test_actions'];cl=candidate['legacy_actions_with_new_thresholds']
    gates={
        'raw_accuracy_improves_two_points':candidate['test']['accuracy']>=base['test']['accuracy']+0.02,
        'no_sensitive_allow':ca['sensitive_allowed']==0,
        'no_ambiguous_allow':ca['ambiguous_allowed']==0,
        'no_added_safe_block':ca['safe_blocked']<=ba['safe_blocked'],
        'correct_automation_maintained':ca['safe_allowed']+ca['sensitive_blocked']>=ba['safe_allowed']+ba['sensitive_blocked'],
        'legacy_accuracy_maintained':candidate['legacy']['accuracy']>=base['legacy']['accuracy'],
        'legacy_no_sensitive_or_ambiguous_allow':cl['sensitive_allowed']==0 and cl['ambiguous_allowed']==0,
    }
    accepted=all(gates.values())
    report['acceptance_gates']=gates;save_json(EVIDENCE/'comparison.json',report)
    save_json(OUT/'calibration.json',{'scope':'six fixed bilingual policies, synthetic short documents only','thresholds':candidate['thresholds'],'calibration_manifest_sha256':json.loads((DATA/'manifest.json').read_text())['splits']['calibration']['sha256'],'heldout_actions':candidate['test_actions'],'status':'offline_candidate_pass' if accepted else 'needs_review','production_default_changed':False,'warning':'Empirical calibration on correlated synthetic families does not guarantee real-document error bounds.'})
    print(json.dumps({'event':'evaluation_complete','offline_candidate_pass':accepted,'production_default_changed':False}),flush=True)

if __name__=='__main__':main()

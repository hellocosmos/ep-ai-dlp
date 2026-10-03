import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'training'))
from calibration_metrics import calibrate,actions,decision

def row(gold,prediction,confidence):
    scores={k:(1-confidence)/2 for k in ['match','no_match','insufficient']};scores[prediction]=confidence
    return {'gold':gold,'prediction':prediction,'probabilities':scores}

def test_confident_errors_disable_automatic_action():
    data=[row('match','no_match',1.0),row('no_match','no_match',0.99),row('no_match','match',1.0)]
    thresholds=calibrate(data)
    assert thresholds['allow']>1 and thresholds['block']>1
    assert actions(data,thresholds)['review']==3

def test_ambiguous_is_counted_as_unsafe_allow():
    data=[row('insufficient','no_match',0.99)]
    assert actions(data,{'allow':0.5,'block':0.5})['ambiguous_allowed']==1
    assert calibrate(data)['allow']>0.99

def test_gates_do_not_turn_abstention_into_allow():
    r=row('insufficient','insufficient',0.9999)
    assert decision(r,{'allow':0.5,'block':0.5})=='review'

def test_lowest_observed_clean_gate_preserves_coverage():
    data=[row('no_match','no_match',0.95),row('match','no_match',0.79),row('match','match',0.98)]
    threshold=calibrate(data)
    assert threshold['allow']==0.8
    stats=actions(data,threshold)
    assert stats['safe_allowed']==1 and stats['sensitive_allowed']==0
    assert stats['sensitive_blocked']==1
    assert sum(stats[k] for k in ['allow','block','review'])==len(data)

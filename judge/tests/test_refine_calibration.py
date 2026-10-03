import math
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'training'))
from refine_calibration import fine_gates, family_folds, validate, load_profile
from calibration_metrics import actions


def row(i, gold='no_match', confidence=.98):
    return {'id': str(i), 'source_family': f'family-{i}', 'gold': gold,
            'prediction': 'no_match',
            'probabilities': {'no_match': confidence, 'match': (1-confidence)/2,
                              'insufficient': (1-confidence)/2}}


def test_fine_gate_recovers_clean_predictions_between_coarse_steps():
    rows = [row(i) for i in range(3)] + [row(3, 'match', .97081)]
    gates = fine_gates(rows)
    assert gates['allow'] == .9709
    assert gates['block'] > 1
    assert actions(rows, gates)['safe_allowed'] == 3
    assert actions(rows, gates)['sensitive_allowed'] == 0


def test_ties_and_perfectly_confident_errors_do_not_pass():
    for bad in [.98, 1.0]:
        rows = [row(i, confidence=bad) for i in range(3)] + [row(3, 'match', bad)]
        assert actions(rows, fine_gates(rows))['allow'] == 0


def test_repeated_variants_do_not_supply_independent_support():
    rows = [row(i) for i in range(10)]
    for r in rows:
        r['source_family'] = 'one-source'
    assert fine_gates(rows)['allow'] > 1


def test_folds_preserve_families_and_cover_every_row_once():
    rows = [row(i) for i in range(12)]
    rows += [dict(r, id=r['id']+'-translation') for r in rows]
    held_ids = []
    for fit, held in family_folds(rows):
        assert not {r['source_family'] for r in fit} & {r['source_family'] for r in held}
        held_ids.extend(r['id'] for r in held)
    assert sorted(held_ids) == sorted(r['id'] for r in rows)


@pytest.mark.parametrize('confidence', [math.nan, math.inf, -0.1, 1.1])
def test_invalid_scores_rejected(confidence):
    with pytest.raises(ValueError):
        validate([row(0, confidence=confidence)])


def test_profile_binds_actual_weights_and_policies(tmp_path):
    adapter, config, policies = b'adapter', b'config', b'policy'
    sha = lambda value: hashlib.sha256(value).hexdigest()
    revision = sha((sha(adapter)+sha(config)+'base-rev'+'MLX/Metal/4bit-group64/LoRA').encode())
    (tmp_path/'adapters.safetensors').write_bytes(adapter)
    (tmp_path/'adapter_config.json').write_bytes(config)
    (tmp_path/'policies.py').write_bytes(policies)
    (tmp_path/'artifact-manifest.json').write_text(json.dumps({'base_revision':'base-rev','model_revision':revision}))
    profile = {'schema':'aidlp-offline-routing-v1','model_id':'decider-4b-dlp-v1',
               'model_revision':revision,'policies_sha256':sha(policies),
               'status':'offline_rehearsal_only','thresholds':{'allow':.9709,'block':.9845}}
    path=tmp_path/'profile.json';path.write_text(json.dumps(profile))
    assert load_profile(path,tmp_path,tmp_path/'policies.py')['thresholds']==profile['thresholds']
    (tmp_path/'policies.py').write_bytes(b'changed policy')
    with pytest.raises(ValueError):load_profile(path,tmp_path,tmp_path/'policies.py')
    (tmp_path/'policies.py').write_bytes(policies)
    (tmp_path/'adapters.safetensors').write_bytes(b'changed adapter')
    with pytest.raises(ValueError):load_profile(path,tmp_path,tmp_path/'policies.py')

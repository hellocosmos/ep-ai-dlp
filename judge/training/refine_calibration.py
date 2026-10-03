"""Offline routing study. Never changes the sealed model or serving defaults."""
import hashlib
import json
import math
import random
from pathlib import Path

from calibration_metrics import actions, calibrate, decision

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'evidence/judge/finetune'
OUTPUT = ROOT / 'evidence/judge/routing-v2'
LABELS = {'match', 'no_match', 'insufficient'}


def validate(rows):
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('empty or duplicate rows')
    for row in rows:
        scores = row['probabilities']
        if set(scores) != LABELS or row['gold'] not in LABELS:
            raise ValueError('invalid labels')
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
            raise ValueError('invalid probabilities')
        if not math.isclose(sum(scores.values()), 1, abs_tol=1e-5):
            raise ValueError('probabilities must sum to one')
        if row['prediction'] != max(scores, key=scores.get):
            raise ValueError('prediction does not match scores')
        if not row['source_family']:
            raise ValueError('missing source family')


def fine_gates(rows, min_families=3):
    """Round above each highest observed error to 1e-4; require family support."""
    gates = {}
    for action, label in [('allow', 'no_match'), ('block', 'match')]:
        wrong = [r['probabilities'][label] for r in rows
                 if r['prediction'] == label and r['gold'] != label]
        threshold = max(0.5, (math.floor(max(wrong, default=0) * 10000) + 1) / 10000)
        accepted = [r for r in rows if r['prediction'] == label
                    and r['probabilities'][label] >= threshold]
        if len({r['source_family'] for r in accepted}) < min_families:
            threshold = 1.0001
        gates[action] = threshold
    return gates


def family_folds(rows, count=6):
    """Keep all translations and policy variants of each source together."""
    families = sorted({r['source_family'] for r in rows})
    if len(families) < count:
        raise ValueError('too few source families')
    random.Random(20261003).shuffle(families)
    assignment = {f: i % count for i, f in enumerate(families)}
    return [([r for r in rows if assignment[r['source_family']] != fold],
             [r for r in rows if assignment[r['source_family']] == fold])
            for fold in range(count)]


def cross_validate(rows, selector):
    results = []
    for fit, held in family_folds(rows):
        gates = selector(fit)
        results.append({'thresholds': gates, 'fit_families': sorted({r['source_family'] for r in fit}),
                        'held_families': sorted({r['source_family'] for r in held}),
                        'metrics': actions(held, gates)})
    aggregate = {k: sum(f['metrics'][k] for f in results) for k in results[0]['metrics']}
    return {'aggregate': aggregate, 'folds': results}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_profile(path, model_folder, policies_path):
    profile = json.loads(path.read_text())
    manifest = json.loads((model_folder / 'artifact-manifest.json').read_text())
    adapter = digest(model_folder / 'adapters.safetensors')
    config = digest(model_folder / 'adapter_config.json')
    revision = hashlib.sha256((adapter + config + manifest['base_revision']
                               + 'MLX/Metal/4bit-group64/LoRA').encode()).hexdigest()
    if (profile.get('schema') != 'aidlp-offline-routing-v1'
            or profile.get('model_id') != 'decider-4b-dlp-v1'
            or profile.get('model_revision') != revision
            or manifest.get('model_revision') != revision
            or profile.get('policies_sha256') != digest(policies_path)
            or profile.get('status') != 'offline_rehearsal_only'):
        raise ValueError('routing profile does not match model, policies or offline scope')
    gates = profile['thresholds']
    if set(gates) != {'allow', 'block'} or any(
            not isinstance(v, (int, float)) or isinstance(v, bool)
            or not math.isfinite(v) or not .5 <= v <= 1.0001 for v in gates.values()):
        raise ValueError('invalid routing thresholds')
    return profile


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cal_path = EVIDENCE / 'candidate-calibration.jsonl'
    test_path = EVIDENCE / 'candidate-test.jsonl'
    calibration = [json.loads(line) for line in cal_path.read_text().splitlines()]
    validate(calibration)
    gates = fine_gates(calibration)
    artifact = ROOT / '.local/finetune/decider-4b-dlp-v1/artifact-manifest.json'
    profile = {'schema': 'aidlp-offline-routing-v1', 'model_id': 'decider-4b-dlp-v1',
               'model_revision': json.loads(artifact.read_text())['model_revision'],
               'thresholds': gates, 'selection_source': 'calibration only',
               'selection_rule': 'highest observed wrong prediction + next 0.0001 step; minimum 3 accepted source families per action',
               'calibration_scores_sha256': digest(cal_path),
               'policies_sha256': digest(Path(__file__).with_name('policies.py')),
               'status': 'offline_rehearsal_only', 'production_default_changed': False}
    (OUTPUT / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    # This test was consumed previously. Replaying saved predictions is explicitly retrospective.
    test = [json.loads(line) for line in test_path.read_text().splitlines()]
    validate(test)
    if {r['source_family'] for r in calibration} & {r['source_family'] for r in test}:
        raise ValueError('calibration and test source families overlap')
    original = json.loads((EVIDENCE / 'candidate-calibration-gates.json').read_text())['thresholds']
    report = {'scope': 'synthetic short-document offline routing study; not new independent acceptance',
              'calibration_families': len({r['source_family'] for r in calibration}),
              'test_scores_sha256': digest(test_path), 'profile': profile,
              'family_cross_validation': {name: cross_validate(calibration, selector)
                                          for name, selector in [('coarse', calibrate), ('fine', fine_gates)]},
              'calibration': {'original': actions(calibration, original), 'fine': actions(calibration, gates)},
              'retrospective_test': {'original': actions(test, original), 'fine': actions(test, gates)},
              'sensitivity_only_not_selected_on_test': {str(t): actions(test, {'allow': t, 'block': t})
                                                       for t in [0.5, 0.9, 0.95, 0.97, 0.98, 0.99]}}
    (OUTPUT / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    replay = [{'id': r['id'], 'policy_id': r['policy_id'], 'gold': r['gold'],
               'prediction': r['prediction'], 'recommended_action': decision(r, gates),
               'enforcement_performed': False} for r in test]
    (OUTPUT / 'replay.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in replay))
    print(json.dumps({'thresholds': gates, 'retrospective': report['retrospective_test'],
                      'cross_validation': {k: v['aggregate'] for k, v in report['family_cross_validation'].items()}}, indent=2))


if __name__ == '__main__':
    main()

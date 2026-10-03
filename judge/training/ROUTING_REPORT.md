# Decider DLP routing refinement — 2026-10-03

The existing fine-tuned model remains the candidate. Its original 87.50% classification accuracy is unchanged. This follow-up improves how probabilities route decisions into offline allow/block/review recommendations.

## Method

The previous gate grid jumped from 0.97 directly to 0.99. Many correct predictions fall between those values. The new method places each action's threshold at the next 0.0001 step above its highest erroneous calibration prediction. It requires accepted examples from at least three source families; unsupported actions are disabled. This support rule is a heuristic, not a statistical guarantee. `insufficient` always routes to review.

From 428 calibration decisions (36 source families), the resulting gates are:

- Allow: **0.9709** on the winning `no_match` probability.
- Block: **0.9845** on the winning `match` probability.

These probabilities are model scores, not validated real-world correctness estimates. In particular, 0.9709 is not a claimed 97.09% reliability guarantee.

## Retrospective replay of the original 736 test decisions

| Routing outcome | Original 0.99 / 0.99 | Refined gates |
|---|---:|---:|
| Allow recommendations | 2 | 183 |
| Block recommendations | 15 | 85 |
| Total automatic recommendations | 17 (2.31%) | 268 (36.41%) |
| Correct automatic recommendations | 17 | 267 |
| Review | 719 (97.69%) | 468 (63.59%) |
| Sensitive documents recommended allow | 0 | 0 |
| Insufficient documents recommended allow | 0 | 0 |
| Safe documents recommended block | 0 | 1 |

No transfer or actual block was performed. These are saved-score replays. Gates use calibration rows only, but the test results had already been inspected in the earlier experiment; this is not a new independent acceptance test. The single safe-document block also means this does not satisfy the previous zero-added-false-block gate. The incumbent baseline previously had 294 correct automatic decisions, so the refined candidate's 267 still does not meet the original automation-maintenance gate.

## Validation across source families

Six deterministic folds keep each source family's translations, variations and cross-policy examples together. Each fold derives gates from the other 30 families and applies them to six held-out families. All 428 rows are scored once outside their gate-fitting fold.

| Outcome | Coarse method | Fine method |
|---|---:|---:|
| Automatic recommendations | 67 (15.65%) | 220 (51.40%) |
| Correct automatic recommendations | 60 | 213 |
| Review | 361 | 208 |
| Sensitive documents recommended allow | 0 | 0 |
| Insufficient documents recommended allow | 6 | 6 |
| Safe documents recommended block | 1 | 1 |

Fine thresholds recover coverage without adding aggregate observed errors in this comparison. However, the six insufficient-content allows demonstrate that zero errors on fitted calibration rows does not generalize to all unseen families. Fold results estimate this selection procedure within the same synthetic collection; they are not fresh real-document validation, and no production error bounds are claimed.

## Implemented output

- `refine_calibration.py`: score validation, calibration-only gate selection, family-separated validation, profiles and 736 replay recommendations.
- `predict.py --routing-profile`: real model inference plus an offline recommendation; rejects incompatible model/policy profiles and base-model use.
- `evidence/judge/routing-v2/profile.json`: gates bound to the existing model revision and policy hash.
- `evidence/judge/routing-v2/comparison.json`: full metrics and fold memberships. Fixed-threshold sensitivity tables are diagnostic only, not selected using test performance.

The original training report, sealed model artifacts and serving defaults are preserved. This profile is ready for offline rehearsal and subsequent independent document qualification. Broader automatic enforcement remains unqualified, especially for insufficient content and customer-record policies.

## Executed checks

- Main test suite: **48 passed, 1 skipped** (optional MLX adapter test module is unavailable in the main environment). Command: `PYTHONPATH=judge judge/.venv/bin/python -m pytest judge/tests -q`.
- Four real MLX CLI inferences exercised block, allow, explicit uncertainty review, and below-threshold review. All returned the bound revision and `enforcement_performed: false`; evidence is in `smoke-*.json`.
- These four probes are execution checks, not an accuracy benchmark. Two raw labels differed from their gold labels and were routed to review. The CLI uses canonical policy text, while dataset rows include introductory wording variants; scores need not exactly reproduce saved dataset-row scores.
- New tests cover score validation, tied/high-confidence errors, source-family separation and support, and rejection of changed model weights or policy definitions.

The next acceptance step is a separately authored or human-reviewed document set scored with the exact intended runtime and policy wording. The new report does not replace the original failed deployment gates with weaker success claims.

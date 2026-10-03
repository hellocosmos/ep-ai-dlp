# AI DLP bilingual fine-tuning report

Status: **needs_review**. Training completed; the incumbent production/default model was not changed.

## Measured final holdout comparison

Both columns use the same pinned Decider 4B BF16 source quantized to MLX 4-bit/group-64. The only intended model difference is the selected LoRA adapter. The test split was excluded from training, checkpoint selection and threshold selection.

| Metric | Base | Fine-tuned candidate |
|---|---:|---:|
| Raw accuracy | 82.34% | 87.50% |
| ko document accuracy | 79.73% | 85.68% |
| en document accuracy | 84.97% | 89.34% |
| sensitive allowed | 0 | 0 |
| ambiguous allowed | 0 | 0 |
| safe blocked | 5 | 0 |
| sensitive blocked | 205 | 15 |
| safe allowed | 89 | 2 |
| review | 435 | 719 |
| Legacy 36-case raw accuracy | 86.11% | 86.11% |
| Permuted-option development accuracy | 86.24% | 93.26% |

Automatic-action counts use thresholds selected separately for each model on the calibration split. Review includes explicit uncertainty and confidence below those thresholds. Raw match-to-no-match errors are separate from an actual allowed transfer.

## Policy slices

| Policy | Base accuracy | Candidate accuracy | Rows |
|---|---:|---:|---:|
| commercial_terms | 87.05% | 87.05% | 139 |
| customer_records | 68.12% | 76.09% | 138 |
| hr_records | 87.10% | 90.32% | 124 |
| personal_sensitive | 86.79% | 95.28% | 106 |
| proprietary_technical | 77.24% | 86.18% | 123 |
| strategic_finance | 90.57% | 93.40% | 106 |

## Acceptance gates

- PASS: raw_accuracy_improves_two_points
- PASS: no_sensitive_allow
- PASS: no_ambiguous_allow
- PASS: no_added_safe_block
- FAIL: correct_automation_maintained
- PASS: legacy_accuracy_maintained
- PASS: legacy_no_sensitive_or_ambiguous_allow

## Deployment judgment

The candidate routes 719/736 final cases (97.7%) to review under the predeclared fixed-grid gates. Classification improved, but automatic handling coverage fell sharply, so the candidate is not promoted. Zero observed unsafe allows here must not be presented as successful autonomous operation.

Calibration-only inspection explains part of this: correct scores cluster near 0.984 (match) and 0.974 (no-match), below the selected gates. The highest wrong calibration scores are 0.98442 and 0.97081; 80 and 110 correctly classified calibration rows respectively lie above those values. A finer or policy-specific calibration method merits a separate experiment. It was not selected against the final test, and the reported thresholds and held-out results were not changed. Fresh independent confirmation is required before claiming an improved operating point.

## Training and dataset

- Dataset: 4394 synthetic decisions; 234 authored source families.
- Split rows: train 2874, development 356, calibration 428, final test 736.
- LoRA: last 1 layer(s), rank 16, scale 16; 901,120 trainable parameters.
- Completed 1438 optimizer steps over 2 epochs, batch 4; seed 20261003.
- Training time including development scoring: 23.4 minutes; process peak MLX memory 8.75 GB.
- Selected step 719 by development accuracy 92.98%.
- Base: Mapika/decider-4b revision eb5fbdfc9448473ec25e399882912863afbdb70e.
- Adapter SHA-256: `2156a7879849f326a502e068b79ca50bd291ca4a875d3a6069c8ae5d3396fe54`.
- Candidate calibration gates: `{"allow": 0.99, "block": 0.99}`. Threshold > 1 disables that automatic action.

## Runtime verification

- Candidate API checks: 12/12 passed.
- Composed local DLP scenarios: 12/12 passed.
- These checks use an isolated local HTTP receiver and synthetic input, with a candidate override in the test client. They do not establish real provider or Windows acceptance.

## Limitations and deployment judgment

The dataset is agent-authored and has no independent human review. Variants within a source family are correlated. These results measure short, synthetic business excerpts and fixed policy wording, not representative enterprise documents, unseen policy generalization, OCR quality, long context, or multilingual coverage beyond Korean/English. Most identity/account examples use TEST markers. Redaction, authorization, approval, audit and actual delivery remain separate deterministic controls.

Score confidence is concentration over options, not a correctness guarantee. Empirical zero-error calibration does not establish zero risk. Any conversion to GGUF or deployment to a Linux/GPU runtime requires fresh inference checks and calibration. The candidate API is opt-in; the DLP policy defaults remain on the incumbent model.

## Local artifacts

- Training data and sealed split hashes: `judge/training/data/v1/`.
- Selected adapter and provenance: `.local/finetune/decider-4b-dlp-v1/`.
- Raw comparison, slices and score records: `evidence/judge/finetune/`.
- Reproduction and candidate service instructions: `judge/training/README.md`.

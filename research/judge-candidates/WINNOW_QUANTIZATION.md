# Winnow E4B quantization comparison — 2026-10-03

## Decision

Do not adopt Winnow E4B Q4 as a latency optimization on this Mac/Metal stack. Q4 reduced allocated GPU memory from 8.43 GB to 5.74 GB but lost 4.08 percentage points of accuracy without reducing measured latency. Q8 was the strongest Winnow variant in this run. Its 87.91% accuracy exceeded the fine-tuned Decider by only three correct decisions out of 736; this is not evidence of a robust general advantage. Keep the existing deployment unchanged.

The fine-tuned Decider remains a candidate, not a latency-qualified production replacement: median API time was 160 ms but p95 was 483 ms. For the near-zero-delay objective, first reduce repeated policy prefills and qualify the intended server hardware and concurrency. Do not automatically approve content because the semantic model is unavailable or slow.

## Conditions

- Apple M4 Max, 36 GiB unified memory; native Metal; one candidate server at a time on a shared workstation.
- Winnow: pinned source and runtime revisions in `execution-plan.json`; authenticated loopback API, 4,096-token context, one decision slot, selected classifier head, temperature 1.0, no inter-request prefix reuse.
- Four quantizations independently created from the SHA-256-verified BF16 source. No requantization. `Q4_K_M` and `Q5_K_M` are mixed quantizations; Q4 keeps large embeddings at Q6. Model files and hashes are in `quantized-manifest.json`.
- E4B is an effective-size designation; its stored weights contain roughly 8B parameters. It is not storage-equivalent to a conventional 4B model.
- Identical policy-conditioned inputs: 428 calibration decisions and 736 test decisions, six policy classes, 370 Korean and 366 English test documents. Family-disjoint calibration/test splits and exact expected IDs were verified.
- This synthetic test was previously consumed in model development. These are retrospective candidate comparisons, not fresh independent acceptance or real-customer accuracy.
- Timings include HTTP/API work after warmup, exclude endpoint-to-server networking and full DLP extraction/enforcement. No truncation was observed within the configured context; saved token ranges are in the JSON report.
- Each model used its own calibration-only action gates. No test-derived threshold tuning. Zero observed sensitive auto-allows on this finite corpus is not a safety guarantee.
- Decider baseline uses GGUF/Q4 and the fine-tuned Decider uses MLX/Q4. Runtime, tokenizer, and prompt-format differences remain; this compares working implementations, not isolated quantization kernels.

## Quality and API time

| Model | Accuracy | Korean | English | Median ms | p95 ms | Weights GB | GPU allocation GB |
|---|---:|---:|---:|---:|---:|---:|---:|
| decider-4b-GGUF-Q4 | 83.29% | 80.81% | 85.79% | 181 | 263 | — | Not sampled |
| decider-4b-finetuned-MLX-Q4 | 87.50% | 85.68% | 89.34% | 160 | 483 | — | Not sampled |
| Winnow-E4B-Q8_0 | 87.91% | 87.30% | 88.52% | 322 | 420 | 8.03 | 8.43 |
| Winnow-E4B-Q6_K | 87.09% | 87.30% | 86.89% | 337 | 440 | 6.22 | 6.62 |
| Winnow-E4B-Q5_K_M | 86.55% | 85.95% | 87.16% | 353 | 457 | 5.76 | 6.16 |
| Winnow-E4B-Q4_K_M | 83.83% | 83.24% | 84.43% | 329 | 433 | 5.34 | 5.74 |

GPU allocation is the runtime-reported backend allocation, not total process RSS or reserved system memory. Decimal GB is used. Small timing differences are not established as statistically significant; measurement order was Q8, Q6, Q5, Q4.

## Calibrated actions on the 736-case test

| Model | Allow gate | Block gate | Allow | Block | Review | Sensitive allowed | Ambiguous allowed | Safe blocked |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| decider-4b-GGUF-Q4 | 0.9060 | 0.6346 | 159 | 193 | 384 | 0 | 4 | 4 |
| decider-4b-finetuned-MLX-Q4 | 0.9709 | 0.9845 | 183 | 85 | 468 | 0 | 0 | 1 |
| Winnow-E4B-Q8_0 | 0.9647 | 0.5000 | 100 | 257 | 379 | 0 | 1 | 6 |
| Winnow-E4B-Q6_K | 0.9655 | 0.5000 | 97 | 254 | 385 | 0 | 0 | 6 |
| Winnow-E4B-Q5_K_M | 0.9307 | 0.5178 | 127 | 250 | 359 | 0 | 2 | 7 |
| Winnow-E4B-Q4_K_M | 0.9621 | 0.5000 | 137 | 236 | 363 | 0 | 2 | 10 |

Raw classification and automatic authorization are separate. For example, Q8 directly predicted safe for 25 sensitive cases, but its calibrated allow gate routed these away from automatic allowance. Q4 also blocked two ambiguous cases. The gates were evaluated offline and were not published to the running policy service.

## Length, concurrency, and six-policy cost

Each length group contains 20 successful requests. Four simultaneous callers were tested in five batches (20 requests). Each multi-policy mode contains 10 requests/documents. Values below are milliseconds. The same synthetic text templates are used across quantizations, with fresh identifiers causing minor token-count variation.

| Quantization | Short p95 | Medium p95 | Long p95 | Concurrent 4 p95 | Success | Six sequential median | Six shared-state median |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q8_0 | 267 | 403 | 1032 | 984 | 20/20 | 2775 | 1488 |
| Q6_K | 281 | 454 | 1142 | 1082 | 20/20 | 3022 | 1620 |
| Q5_K_M | 277 | 469 | 1174 | 1142 | 20/20 | 3164 | 1693 |
| Q4_K_M | 279 | 468 | 1067 | 1030 | 20/20 | 2890 | 1574 |

The original Decider API succeeded on only 5/20 simultaneous requests; 15 returned `judge_busy`. Its single-slot worker admits requests for only 100 ms. Its all-response latency must not be interpreted as successful concurrent throughput. Fine-tuned Decider concurrency was not remeasured. Winnow queues all tested requests, which increases waiting time.

Shared-state requests reduced repeated work, but still took 1.49–1.69 seconds for six policies in this single-slot configuration. This is a runtime capability probe, not implemented DLP integration. `first_request` in the latency files means first request of that probe after quality evaluation; it is not a cold model load.

## Complementarity and limits

Q8 corrected 54 cases missed by the fine-tuned Decider, but regressed 51 that Decider got right. An offline cascade that sends only Decider review cases to Q8 reduced review from 468 to 255, with one ambiguous allowance and six normal-content blocks. Cascade end-to-end latency was not measured and the cascade was not integrated; it is not recommended as a universal synchronous path.

No new training, endpoint integration, Windows deployment, remote GPU qualification, service defaults, or production policy changes were performed. Q8/Q4 selected-versus-full-head parity was not separately tested in this experiment; the pinned runtime build and its native unit check passed. All experimental servers were stopped after their measurements.

## Artifacts and reproduction

- `evidence/judge/winnow/quantization-comparison.json`: full metrics, gates, per-policy slices, cascade diagnostics, runtime and weight metadata.
- `evidence/judge/winnow/*-{calibration,test}.jsonl`: exact per-case decisions and timings.
- `research/judge-candidates/quantize_winnow.py`: BF16 verification and independent conversions.
- `research/judge-candidates/run_winnow_quant.py`: isolated authenticated server lifecycle and evaluation.
- `research/judge-candidates/compare_e4b_quant.py`: completeness checks and offline comparison.

Evaluation scripts refuse to overwrite saved quality outputs. Archive an existing evidence set deliberately before a new run; do not mix runs.

Sources: [Winnow E4B model](https://huggingface.co/EldanRing/Winnow-E4B), [Winnow inference source](https://github.com/EldanRing/winnow-inference). Local evidence above takes precedence over author benchmark claims for this task.

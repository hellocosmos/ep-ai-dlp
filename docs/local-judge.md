# Policy Judge MVP — development setup and on-premises deployment

Implemented and measured on 2026-10-03, Apple M4 Max, 36 GiB unified memory. This MVP adds a separate policy Judge API to the existing console. It does not deploy the Judge to Windows or connect to Microsoft/Google tenants.

## Deployment intent and verified scope

The intended product deployment places the judgment LLM on a company-hosted, on-premises API server. Endpoint agents enforce policy and use the internal decision service; model weights are not required on employee PCs. Inspection text is transmitted to the internal server, so the data boundary is the company environment rather than each device.

The instructions below reproduce the single-Mac development setup. “Local Judge” is the existing console label. Apple Silicon/Metal requirements apply to this development environment, not to employee endpoints. The standalone API supports a separately configured HTTPS inference host; physical GPU-server operation, server capacity and Windows semantic-enforcement integration remain unqualified. See the [target architecture](architecture.md#on-premises-semantic-judgment-target-architecture) and [internal API deployment guide](jev-api.md).

## Open and run the development environment

Open <http://127.0.0.1:3100/> with the existing administrator account, then select **Local Judge**. The experimental workspace retains its Korean scenario/policy content. The screen contains six scenario tests, a semantic policy editor, measured model comparison, and metadata audit. With the current configuration, uploaded files and typed text are processed on this Mac. A configured remote Jev endpoint receives the extracted text for inference. An allow or redact decision actually POSTs extracted/sanitized text to an authenticated loopback receiver. A block/review decision does not call it.

Existing managed control-plane and console setup: [getting-started.md](getting-started.md). The control plane remains on port 3901; the console uses port 3100. The decision model is a separate service. See [jev-api.md](jev-api.md) for the API contract and remote GPU deployment. Start these in separate terminals from the repository root:

```sh
scripts/start-jev.sh
# In another terminal:
scripts/start-judge.sh
```

To set up the isolated environment on another Apple Silicon Mac with Python 3.12 and build tools:

```sh
uv venv --python 3.12 judge/.venv
CMAKE_ARGS=-DGGML_METAL=on uv pip install --python judge/.venv/bin/python --no-binary llama-cpp-python -r judge/requirements-evaluation.txt
judge/.venv/bin/python research/judge-models/download.py
scripts/start-jev.sh
# In another terminal:
scripts/start-judge.sh
```

For the console, use the existing `npm run build -w @aidlp/console` then `npm run start:console` workflow. `requirements-lock.txt` captures the measured environment, including evaluation tooling. A Metal-capable llama.cpp build is required; the tested Python wheel path was a local source build. Do not launch multiple uvicorn workers: publication/dispatch serialization is in-process. Only the script's single worker is supported.

The first service start creates `.local/judge-state/` with directory mode 0700, bearer-token files and SQLite database mode 0600. The browser never receives either backend token: the Next server verifies the existing administrator session with the control plane and then proxies to DLP. DLP uses a different bearer token to call the standalone Jev API. Optionally set `AIDLP_JUDGE_TOKEN_FILE` for a different server-side token-file location. Do not print these tokens or place them in URLs. Stop the foreground service with Ctrl-C; stop the separate Jev service to terminate its owned inference worker. No launch-at-login or recurring automation was added.

## Current development architecture and enforcement

```text
Browser / controlled adapter
  -> authenticated Next proxy -> loopback policy service
  -> trusted ACL, label and tool authorization gate
  -> bounded extraction -> deterministic identifier / secret inspection
  -> applicable natural-language policy -> authenticated separate Jev API
  -> option-logit model worker (local Metal or remote GPU)
  -> deterministic block / review / allow / redact
  -> metadata audit -> authenticated loopback receiving adapter
```

Policy revisions contain model ID, semantic question, positive/negative criteria, scenario/destination scope, enforcement effect, threshold and uncertainty behavior. The UI edits existing policy questions/criteria, model, threshold, enabled state and effects. The strict API also supports adding policies and changing their scopes. The service stores immutable revisions through compare-and-swap publication; SQLite is **not** an external immutable audit system.

- Model inputs contain document text and the fixed policy question, not a precomputed desired verdict. ACL/label facts are evaluated separately.
- Direct identifiers on external routes and hard secrets cannot be allowed by a model or review approval.
- ACL-denied or AI-processing-prohibited documents skip file extraction and model inference.
- An unknown/low-score result becomes review (or block, if configured), never automatic allow.
- Review approval binds content/file digest, filename digest, trusted adapter facts, scenario and policy revision. It expires in 10 minutes and is consumed once. Reinspection is required before delivery. Changed content/revision, reuse, expired approval and hard blocks cannot use it.
- A publication during inference invalidates the decision. The final revision check, audit and delivery are serialized against publication within the single service process. A decision already dispatching completes under its previous revision before a new publication becomes effective.
- Parser deadline: 10 seconds in an owned subprocess. Inference deadline: 30 seconds per question in an owned subprocess; timeout kills that worker. Concurrent inference contention fails closed with `judge_busy`. There is no late allow after a timed-out call.
- Audit must succeed before sending. Receiver timeout is `delivery_outcome_unknown`, not proof of non-delivery. There is no automatic transmission retry.
- Audit stores decision metadata, model/version/scores, rule IDs, digests and receipt metadata. Source text, filenames and outgoing content are not persisted by the service. Browser form contents and short-lived parser/inference memory still hold the input during a request. In-memory score-cache entries contain digests/scores, not raw text.

## What the six scenarios prove

Acceptance uses actual XLSX/DOCX inputs, the real Decider 4B model, and a separate HTTP test receiver that inspects content in memory. The run records 12 passing paths, including one allowed example that requires human approval. These are local adapter acceptance results, not live SaaS acceptance.

| Scenario | Protected example | Allowed example | Evidence boundary |
|---|---|---|---|
| Customer XLSX to external AI | Review; 0 receiver calls | Anonymous aggregate delivered | Actual XLSX parsed; semantic model remains uncertain on protected customer records |
| M365 document grounding | Label gate blocks; no extraction/inference/receipt | Public DOCX delivered | Local label contract, no live Copilot policy installation |
| Drive document to personal AI | Deal terms blocked; 0 receiver calls | Public catalogue delivered | Local source/account fixture, not Google authentication |
| Server-side cloud summary | Phone/email/resident-ID patterns removed before receiver | Ordinary request delivered | Actual outgoing text checked in memory; supported identifiers only |
| RAG HR access | Denied before content processing; no model/receipt | HR-authorized example delivered | Local ACL fixture, not a live vector index/enterprise ACL integration |
| Agent external mail | Private deal terms blocked; 0 tool-adapter calls | Public invitation initially reviewed; approved once, replay denied | HTTP adapter represents execution; no real email sent |

“Review” is a protected non-delivery state but is not an automatic sensitive-content classification success. The customer case and public invitation case remain quality gaps. No model question or test fixture was changed to conceal them.

## Model comparison

All models use pinned public GGUF Q4_K_M weights and actual Metal inference. [Model provenance and prompt contract](../research/judge-models/README.md).

The fixed authored synthetic corpus has 60 cases: 24 calibration (8 per policy class), 36 held out (18 sensitive, 12 safe, 6 ambiguous). The classes are customer records, business deal terms, and employee records. The held-out set contains 21 Korean and 15 English cases. Class/source groups are separate; this is still a small authored pilot, not a statistically representative benchmark. The corpus SHA-256 was fixed before comparative inference. Thresholds were selected on calibration rows only; held-out rows were not used to tune them. M365's separate review question was not calibrated by this corpus.

| Model | Raw 3-way correct | Sensitive false allow | Safe false block | Review | Safe allow | p50 / p95 |
|---|---:|---:|---:|---:|---:|---:|
| Decider 2B | 23/36 | 0/18 | 0/12 | 18/36 | 10/12 | 60 / 69 ms |
| **Decider 4B** | **29/36** | **0/18** | **0/12** | **15/36** | **12/12** | **145 / 168 ms** |
| Standard One 3B | 21/36 | 0/18 | 0/12 | 24/36 | 5/12 | 126 / 146 ms |

Decider 4B is the default because it achieved the best raw classification and safe-case coverage in this corpus. Its selected thresholds are customer 0.90, commercial 0.50, HR 0.50. The M365 question keeps the conservative uncalibrated 0.85 review setting. Relative option scores are **not** correctness probabilities; zero observed false allow is not a production safety guarantee. Held-out performance is per relevant policy, whereas scenario execution composes several applicable policies, so the latter can abstain more often.

The p50/p95 values measure one uncached question on short synthetic documents, excluding model loading and extraction. Measured 4B model load was approximately 1.24 seconds. Sequential process peak RSS reached 3.23 GB; it is a process high-water mark, **not** isolated model memory or total Metal allocation.

The supplemental 4B length probe measured ~0.85s at 858 tokens, ~1.91s at 2,258 tokens and ~4.39s at 5,058 tokens, with the inserted private deal classified as a match. A 25,945-character input exceeded the 8,192-token context and was rejected without truncation. This repeated-text probe is latency evidence, not long-document accuracy validation.

## Files and format limitations

Accepted formats: UTF-8 TXT/MD/CSV/TSV/JSON, XLSX, DOCX, text PDF. Limit 4 MB decoded upload, 60,000 extracted characters, 8,192 total model-input tokens including policy. No OCR. XLSX formulas, embedded content, external links, image-containing files, encrypted documents and unsupported formats fail closed. ZIP expansion/ratio, spreadsheet row/cell, PDF page and parser time limits are enforced. PDFs with forms, annotations or active/embedded content are conservatively rejected.

Only extracted text is delivered by this MVP; original file bytes, full fidelity document forwarding, image redaction and transformed-file download are not implemented. The summary scenario reuses supported deterministic PII patterns; it does not claim comprehensive name, address, account-number or contextual de-identification. Supported file extraction is not a generic sandbox for hostile office documents.

## Reproduce verification

```sh
PYTHONPATH=judge judge/.venv/bin/python -m pytest judge/tests -q
PYTHONPATH=judge judge/.venv/bin/python judge/evaluation/compare.py
PYTHONPATH=judge judge/.venv/bin/python judge/evaluation/tokenizer_parity.py
PYTHONPATH=judge judge/.venv/bin/python judge/evaluation/acceptance.py
PYTHONPATH=judge judge/.venv/bin/python judge/evaluation/length_probe.py
judge/.venv/bin/python judge/evaluation/http_checks.py
judge/.venv/bin/python judge/evaluation/jev_http_checks.py
npm run typecheck -w @aidlp/console
npm run build -w @aidlp/console
```

`http_checks.py` requires running console/control-plane/Judge services and the local managed environment; it signs in normally and logs out its own test session. No credential is printed. `acceptance.py` requires the separate Jev API and uses a temporary store and receiver; it does not change running policies or contact external providers. Its synthetic fixtures are saved for browser upload testing. Unit tests explicitly use doubles; model/HTTP acceptance and benchmark evidence are separate.

Evidence under `evidence/judge/`: per-model JSONL/reports, `benchmark/summary.json`, `tokenizer-parity.json`, `acceptance.json`, `http-checks.json`, `length-probe.json`, and rendered UI captures. UI validation covered login, semantic policy publication, real model verdict, allowed delivery, approval then delivery, benchmark display and actual XLSX upload. The existing Windows control-plane policy was not edited.

## Live integration contract and next acceptance gate

Adapters must obtain actor, organization, destination/account, document identity/version, label and ACL from authenticated provider or application state. The current caller-selectable `variant` is explicitly lab-only and must never become a production authorization API. Bind canonical source identity/version and destination/tool arguments to each approval digest; validate account and delegation outside the model. Maintain the inspection-before-send ordering and receipt evidence.

| Real boundary | Required integration | Live acceptance before claiming protection |
|---|---|---|
| Desktop/browser upload | Existing interception or app upload hook -> bounded extraction -> Judge -> original-send gate | Verify original bytes never reach provider on deny, chunked/resumable/multipart uploads, background endpoints, negative-control app and user-facing failure |
| M365 native Copilot | Supported Microsoft tenant-native information protection/access/grounding controls | Confirm tenant licenses/configuration and prohibited source exclusion in actual Copilot; an endpoint proxy cannot independently enforce provider-internal grounding |
| Drive to personal AI | Verified Drive file identity/ACL/labels plus authenticated destination-account identity | Test managed vs personal account, public export, copied text and loss of source provenance |
| Server model API | Application pre-request hook and sanitized request construction | Inspect actual test-provider inbound payload and application behavior after redaction/failure |
| RAG retrieval | Identity-aware source filtering before retrieval and context assembly | Real unauthorized-user query returns no forbidden chunks; ACL/version changes invalidate cached access |
| Agent mail/tools | Delegated tool wrapper with canonical recipient/arguments and execution gate | Actual controlled mailbox receives allowed content only; deny yields zero execution; approval mutation/replay fails |

Priority: collect representative Korean company documents with adjudicated labels, measure each policy and their composition, reduce unnecessary reviews, then attach one real integration boundary. Do not infer organization-wide AI DLP coverage from this local pilot. Model/schema/config artifacts are local and unauthenticated against privileged filesystem tampering; production requires controlled deployment, per-organization authorization, durable external audit, operational quotas and appropriate data retention controls.

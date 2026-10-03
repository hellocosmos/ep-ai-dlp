# Standalone Jev-like decision API

The inference service is independently runnable on a Mac or GPU server. It has no DLP policies, approval store, receiving sink or tool-execution capability. This is the `aidlp-decision-v1` contract around Jev-like public decision models, **not** an official Jev vendor API or an assertion of vendor wire compatibility.

```text
Console :3100 -> DLP policy / enforcement :8310
                             |
                      authenticated HTTP(S)
                             |
                     Jev decision API :8311
                             |
                     model worker (Metal / CUDA build)
```

The DLP process imports `JevClient`; it does not create a model worker or load weights. It owns all allow/block/review/redact rules, policy revisions, source authorization, approval and dispatch. The separate service returns only bounded choice scores plus model/version/runtime/latency/token metadata.

## Run locally

Use the environment setup in [local-judge.md](local-judge.md), then in separate terminals:

```sh
scripts/start-jev.sh
scripts/start-judge.sh
```

The API creates a random bearer token at `.local/jev-state/api.token` with mode 0600. The DLP service reads that same token file on startup. The existing console remains on port 3100. The inference API binds to loopback 8311; use `AIDLP_JEV_PORT` to change its port.

## Run inference on an NVIDIA GPU server

Copy the source folders `judge/aidlp_judge/`, `research/judge-models/`, `judge/requirements-inference.txt` and `scripts/start-jev.sh` into the same directory structure on the GPU host. Do not copy the Mac's `.local/`, credentials, audit database, `.venv/` or model cache. A Python 3.12 environment, working NVIDIA driver/CUDA toolkit and C/C++ compiler are prerequisites.

```sh
uv venv --python 3.12 judge/.venv
CMAKE_ARGS=-DGGML_CUDA=on uv pip install --python judge/.venv/bin/python --no-binary llama-cpp-python -r judge/requirements-inference.txt
judge/.venv/bin/python research/judge-models/download.py
scripts/start-jev.sh
```

The build option follows the [official llama-cpp-python CUDA instructions](https://github.com/abetlen/llama-cpp-python#installation-configuration). The same runtime adapter uses GPU layer offload. Metal is verified on this Mac; **the CUDA build and physical remote GPU execution have not been tested in this task**. A Linux result reports `GPU-offload` rather than claiming a specific backend without evidence.

Place a TLS reverse proxy on the GPU host in front of loopback 8311. Restrict ingress to the DLP host and disable request-body logging. Do not expose the bare HTTP listener publicly. Provision the inference token securely into a mode-0600 file on the DLP host, then configure only the DLP service:

```sh
export AIDLP_JEV_URL=https://judge.internal.example
export AIDLP_JEV_TOKEN_FILE=/absolute/protected/path/jev-api.token
# Optional private CA bundle; normal certificate verification remains enabled.
export AIDLP_JEV_CA_FILE=/absolute/path/internal-ca.pem
scripts/start-judge.sh
```

`AIDLP_JEV_URL` is an origin, not an arbitrary request path. Remote plaintext HTTP, credentials in URLs, query strings and redirects are rejected. Local loopback HTTP is allowed. No proxy environment variables are inherited by the HTTP client. There is no silent fallback to a different endpoint/model. Changing to a remote URL sends extracted document text and policy questions to that configured inference host; use an organization-controlled trusted host.

`AIDLP_JEV_TOKEN_FILE` is also supported on the inference host to select its token file. These files contain the same shared API token but may have different absolute paths. Restart both sides after token rotation; never paste the token into chat or browser forms. The API can use `AIDLP_JEV_MODEL_ROOT` to load pre-provisioned model folders from another location. The downloader's default destination remains `.local/judge-models/`.

The DLP-only host can install `judge/requirements-policy.txt`; it does not require `llama-cpp-python`, NumPy, model weights or CUDA. The full development environment uses `requirements-evaluation.txt`.

## Contract

- `GET /health`: unauthenticated liveness and contract version; does not claim model readiness.
- `GET /v1/models`: bearer authentication; pinned model IDs/revisions, file availability, current worker status.
- `POST /v1/decide`: bearer authentication; no source authorization or delivery operations.

Request example:

```json
{
  "model_id": "decider-4b",
  "text": "Public retail catalogue: the same listed price applies to all customers.",
  "question": "Does the content disclose private customer-specific negotiated terms?",
  "choices": {
    "match": "Private negotiated terms are disclosed.",
    "no_match": "Only public general information is present.",
    "insufficient": "The content is insufficient to decide."
  }
}
```

Response fields: `choice`, `scores`, `model_id`, `model_revision`, `runtime`, `input_tokens`, `latency_ms`, `cached`. Scores are normalized over exactly the three supplied labels. No generated prose, hidden reasoning, policy action or execution authorization is returned. Each DLP response is schema-validated; wrong model identity, malformed scores and inconsistent argmax are errors, not allow decisions.

Limits: 512 KiB request, 60,000 text characters, 1,500 question characters, 700 characters per option, 8,192 input tokens including the policy prompt. Only the three pinned model IDs are accepted. Documents/files are parsed by DLP, not by this API. The model server keeps a single resident model and rejects contention (`judge_busy`). Run one API worker; multi-tenant scheduling/batching is a follow-up.

Failure behavior: 401 for authentication, 413 for size, 422 for malformed requests, 503 for model/inference errors. Validation responses never echo submitted content. The DLP call deadline is 35 seconds; model execution has a hard 30-second process deadline. Any unavailable/timed-out/malformed API response blocks delivery. No automatic retry. Inference completed after a disconnected client can only return a score and has no ability to send a document or execute a tool.

## Verified

The Mac runs two independent API processes. All 12 scenario paths were re-run through the authenticated decision API and an independent receiving adapter. API authentication, bounded input, no body echo, pinned model metadata and absence of policy/send routes were checked. An actual inference-service outage was also induced: DLP returned `block / jev_api_unavailable`, and the receiver count remained unchanged (`jev-outage.json`). The inference service was restored. Unit tests cover remote URL/TLS requirements, auth errors, redirects, timeouts, malformed scores and model mismatch. See `evidence/judge/jev-api-checks.json` and `evidence/judge/acceptance.json`.

For a physical GPU-server acceptance, additionally verify CUDA build/runtime, device memory, real endpoint certificate/auth, equivalent decisions on the frozen corpus, realistic concurrency and latency, and that raw request bodies are absent from proxy/application logs. This is a future deployment gate, not a completed GPU test.

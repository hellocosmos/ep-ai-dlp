# Validation record and known gaps

## Historical Windows pilot: 2026-10-02

These are measured results from the development lab. Raw evidence is deliberately
not distributed because it includes environment identifiers and operational
state. Use the included harnesses for independent reproduction. README images
are separately generated synthetic UI fixtures, not this acceptance evidence.

| Check | Measured result | Limit |
|---|---|---|
| Configured executable, then fresh PID | Safe request 200; synthetic sensitive request 403 | Configured paths only |
| Unrelated executable | Both safe and sensitive requests reached the lab origin | Deliberate negative control, outside protection scope |
| Ordinary Chrome after service restart | Safe request arrived; sensitive request did not increment origin count | Controlled HTTPS origin |
| Management | Signed policy revision synchronized; device state and block metadata persisted | Single-tenant pilot |
| Ordinary Chrome to ChatGPT | One harmless prompt received a normal answer | One logged-out conversation; not broad provider acceptance |
| Synthetic test-card attempt | Engine reported block; no visible answer | No provider-origin log; UI did not clearly explain the block |
| Earlier ancillary ChatGPT request | Credit-card detection before test-card entry | Possible false positive; raw body was not retained, cause unresolved |
| Historical local regression | 29 Rust unit tests, 39 controlled TLS cases, 11 control-plane tests passed | Historical counts; new public checks recorded below |

## Unverified or incomplete

- File upload inspection/extraction and broad provider compatibility.
- Edge acceptance for the transparent-service path, sustained performance, and large fleets.
- Reboot and forced-crash recovery, uninterrupted fail-closed capture, tamper resistance.
- Existing connections before activation, helper processes, pinning and ECH.
- macOS endpoint capture, document provenance, immutable audit storage.
- A clear end-user block explanation in the transparent browser path.

The older dedicated-profile browser pilot exercised five browsers against a
controlled HTTPS origin. This is not evidence that the later transparent-service
path supports all five browsers.

## Reproduce local checks

```bash
npm ci
npm run build
npm run typecheck
# Requires a dedicated PostgreSQL database, see getting-started.md:
npm run test:server

cargo test --locked --manifest-path native/Cargo.toml --lib
cargo build --locked --manifest-path native/Cargo.toml --bins
native/target/debug/acceptance --proxy native/target/debug/aidlp-native --output .local/acceptance-new

python3 -m venv .venv
.venv/bin/pip install pytest
PYTHONPATH=agent .venv/bin/python -m pytest -q agent/tests
```

Use a fresh output directory for acceptance. Local fixtures prove parser,
inspection and transport behavior; they do not prove Windows capture or a live
AI provider. Build prerequisites include Rust 1.95+ and Protocol Buffers (`protoc`).

## Public snapshot checks: 2026-10-03

Re-run against the curated public source on macOS:

- npm clean install, production build and TypeScript checks: passed.
- PostgreSQL-backed control-plane tests: 11 passed (isolated test schema).
- Native Rust unit tests: 29 passed; native binaries built successfully.
- Controlled TLS acceptance harness: 39 passed, 0 failed.
- Legacy Python tests: 123 passed.
- English console: four synthetic-fixture screenshots, no browser runtime errors.

These checks do not repeat the Windows live-provider acceptance. The public
snapshot changes documentation and console language; the historical Windows
service remains a separate lab installation. Vendored Hudsucker emits an
existing unused-import warning during Rust builds.

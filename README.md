# EP AI DLP — Endpoint AI Data Protection

**Explore what it takes to inspect sensitive data before it leaves a Windows application for an AI service.**

[![Status: Experimental](https://img.shields.io/badge/status-Experimental-F59E0B)](docs/validation.md)
[![Runtime: Rust](https://img.shields.io/badge/runtime-Rust-0F172A)](native/)
[![Console: Next.js](https://img.shields.io/badge/console-Next.js-2563EB)](apps/console/)
[![Original code: MIT](https://img.shields.io/badge/original_code-MIT-2563EB)](LICENSE)

[English](README.md) · [한국어](README.ko.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md)

> **Experimental research preview.** Application-scoped Windows protection, a native Rust inspection engine, and a management console. This is not a production DLP replacement or a claim of complete AI data-leak prevention. Third-party components retain their own licenses, including LGPL-covered Windows components.

EP AI DLP explores an endpoint enforcement boundary for external AI use:
select the applications and destinations, inspect supported requests locally,
enforce a signed policy, and report decisions without collecting protected text.

## Why I added a local Judge — inspired by Jev

> “Jev inspired me to apply small decision models to AI DLP. Regular expressions are useful for structured identifiers and known secret patterns, but I wanted to go further: recognize private deal terms, internal business context, and policy meaning. Sending sensitive content to a commercial AI API just to inspect it creates another data-exposure concern. So I built and fine-tuned a local decision layer. After trying it myself, my conclusion was: this is useful in the prototype and worth developing further.” — Jaemyung Kim

The implementation combines deterministic detection with local, policy-conditioned semantic decisions. Regex and hard security rules still protect identifiers and secrets; the model adds context-sensitive classification. The decision model returns choice scores, while a separate policy engine owns allow/block/review/redact, authorization, approval, audit and delivery. Local inference keeps inspection content off third-party commercial inference APIs. An explicitly configured remote inference host does receive the text and must remain within the organization's trusted boundary.

The repository now includes the **Local Judge** experimental console workspace, an independently runnable Jev-inspired decision API, a bilingual synthetic DLP training corpus, LoRA training, and measured comparisons. This is an independent implementation, not an official Jev API integration or an affiliation claim. The experimental scenario workspace retains Korean content; the existing English endpoint-management console is preserved.

| Public decision checkpoint | Original accuracy | After local DLP fine-tuning | Fine-tuned API median |
|---|---:|---:|---:|
| Decider 2B | 66.6% | 81.8% | 68 ms |
| Jeff Qwen3.5 2B | 82.7% | 89.9% | 87 ms |
| Jeff Gemma4 E2B | 83.8% | 91.2% | 121 ms |

Measured on the same Apple M4 Max, BF16, 736 reused synthetic test decisions from 54 source families. These numbers show prototype utility, not production DLP accuracy. Accuracy, calibrated routing errors and review coverage are reported separately. The new small-model adapters were not installed into Windows enforcement or selected as the running default.

- [Local Judge setup, controls and limits](docs/local-judge.md)
- [Standalone decision API](docs/jev-api.md)
- [Training corpus and pipeline](judge/training/README.md)
- [Full six-condition results and operating-point tradeoffs](research/judge-candidates/SMALL_MODEL_FINETUNING.md)
- [Published synthetic measurement snapshot](research/judge-candidates/results/small-bf16/)

## Console in action

Actual English console, populated with **synthetic UI fixtures**. Device names,
counts, and policy states illustrate the interface; they are not customer usage
or live enforcement evidence. [Screenshot provenance](docs/screenshot-provenance.md).

![Endpoint overview with reported policy state](docs/assets/console-overview.png)

<table>
<tr>
<td width="50%"><img src="docs/assets/console-policy.png" alt="Sensitive data policy"><br><strong>Per-rule block or monitor policy</strong></td>
<td width="50%"><img src="docs/assets/console-devices.png" alt="Device enrollment and reported state"><br><strong>Connectivity and applied policy are separate</strong></td>
</tr>
</table>

![Metadata-only inspection events in dark mode](docs/assets/console-events-dark.png)

## What is implemented

- **Native Rust runtime:** selected TCP capture, selective TLS inspection, bounded parsing, deterministic detection, and local audit.
- **Application-scoped Windows service:** configured executable paths cover new matching processes; no browser proxy flag or dedicated profile is needed in this path.
- **Eleven detection rules:** common credential patterns, private key markers, email, credit cards, Korean phone numbers and resident IDs.
- **Signed policy:** one-time enrollment, device-bound Ed25519 verification, per-rule block/monitor actions, and reported policy revisions.
- **Management console:** Next.js, NestJS and PostgreSQL; devices, rules, events and administrative audit history.
- **Metadata reporting:** destination host, rule identifiers, decision and size; protected request text is not included in the event schema.

The console uses one tenant and one shared rule policy. Domain and executable
selection remain agent configuration. Request inspection does not imply response
inspection, file extraction, contextual classification, or Agent IAM.

## Enforcement path

```text
Configured Windows application
  -> TCP capture / redirector
  -> Rust selective TLS inspection
  -> request parsing + DLP rules + metadata audit
  -> allow / monitor / block
  -> selected AI destination

Next.js console -> NestJS API -> PostgreSQL
                         |
               signed policy / metadata
                         |
                     Rust agent
```

[Architecture](docs/architecture.md) · [Security boundaries](SECURITY.md) · [Third-party components](THIRD_PARTY.md)

## Start the local console

Node.js 22.19+, npm, and Docker Compose are required. This starts a local
management environment; it does not install an endpoint agent.

```bash
git clone https://github.com/hellocosmos/ep-ai-dlp.git
cd ep-ai-dlp
npm ci
npm run bootstrap
docker compose --env-file .local/managed.env -f deploy/compose.yaml up -d
npm run build
```

Run `npm run dev:server` and `npm run start:console` in separate terminals.
Open `http://127.0.0.1:3100`. Bootstrap generates random credentials in the local,
ignored `.local/managed.env` file. There is no shared default password.

[Full setup](docs/getting-started.md) · [Windows lab installation](docs/windows-lab.md)

## Evidence, with boundaries

| Evidence | What it establishes |
|---|---|
| Controlled Windows HTTPS origin | Safe requests arrived; blocked synthetic requests did not increase origin receipts. |
| Ordinary Chrome after service restart | Configured application capture and allow/block behavior worked in the tested lab. |
| One real ChatGPT conversation | A harmless prompt received a normal answer. Broad provider compatibility is not established. |
| Earlier ancillary ChatGPT request | A possible credit-card false positive remains unresolved. |
| Local test harnesses | Reproducible inspection, transport, policy and control-plane checks; not production certification. |

**Known gaps:** live file uploads, broader providers/browsers, clear block UX in
the transparent path, sustained performance, reboot/crash qualification, and
tamper resistance. A service or redirector outage may release capture;
**uninterrupted fail-closed protection is not established**. macOS endpoint
capture is not implemented. Audit storage is not immutable.

[Detailed validation and reproduction](docs/validation.md)

## Repository map

| Path | Purpose |
|---|---|
| `native/` | Rust runtime, Windows service scripts and capture dependencies |
| `apps/console/` | English management UI |
| `services/control-plane/` | Enrollment, policy signing, reports and audit API |
| `packages/contracts/` | Shared schemas and signed-policy fixture |
| `agent/` | Earlier Python/PySide dedicated-browser experiment |
| `research/hudsucker-spike/` | TLS experiments, patches and vendored source |
| `proxy/` | Earlier .NET/Titanium comparison experiment |
| `docs/` | Setup, architecture, evidence and limitations |

The earlier browser and .NET experiments are preserved for learning. Their
capabilities are not interchangeable with the current transparent-service path.
Lab credentials, raw operational evidence, private strategy notes, and machine-specific
transfer helpers are excluded from this public snapshot.

## Why share this?

Building a blocking demo is only one part of endpoint security. Protocol
compatibility, ownership of the traffic path, false positives, trustworthy
policy state and failure behavior determine whether the result is useful.
This repository shares an implementation and its limits so those questions
can be examined and reproduced.

Designed and validated by **Jaemyung Kim**, cybersecurity product architect,
with implementation assisted by AI coding agents. Architecture, threat modeling,
acceptance criteria and evidence review are part of the work—not claims that
all code was written by hand.

Contributions with bounded, synthetic reproductions are welcome. See
[contributing](CONTRIBUTING.md) and [security reporting](SECURITY.md).

**Licensing:** original work is MIT; vendored software and Windows components
retain their respective licenses. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).

# Architecture

```text
Managed Windows application (configured executable path)
  -> Windows redirector / selected TCP capture
  -> Rust selective TLS engine
  -> bounded request parsing + deterministic DLP rules
  -> metadata audit + allow / monitor / block decision
  -> selected external HTTPS destination

Next.js console -> NestJS control plane -> PostgreSQL
                           |
               enrollment + signed policy
                           v
                     Rust agent
               policy verification + reports
```

Inspection runs on the endpoint. The control plane distributes an Ed25519-signed
policy envelope and receives bounded metadata events. Device enrollment is
one-time, device credentials are separate from administrator sessions, and the
agent pins its policy verification key. The policy contains per-rule actions;
target domains and executable paths remain agent-side configuration.

There are eleven deterministic rule identifiers: Anthropic/OpenAI/Google API
keys, AWS access keys, GitHub and Slack tokens, private key markers, email,
credit cards, Korean mobile numbers, and Korean resident IDs. Pattern detection
is not contextual classification or proof of a valid credential.

The current console is single-tenant and uses one common rule policy. It does
not implement enterprise RBAC, IdP integration, Agent IAM, arbitrary tool-call
authorization, or a fleet-wide domain policy editor.

Two older approaches are retained for comparison: a Python/PySide dedicated
browser pilot (`agent/`) and a Titanium .NET proxy experiment (`proxy/`). They
are separate execution paths, not components required by the native Rust engine.

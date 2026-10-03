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

In the current native enforcement path, TLS inspection and deterministic detection run on the endpoint. The control plane distributes an Ed25519-signed
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

## On-premises semantic judgment: target architecture

The intended deployment runs the judgment LLM as a centrally managed API service on a company-owned, on-premises server. Employee PCs run endpoint enforcement agents; they do not host model weights or require an inference GPU.

```text
Employee PC: endpoint interception + policy enforcement
  -> internal policy/decision integration
  -> authenticated HTTPS -> company on-premises LLM API server
  <- bounded classification scores + model metadata
  -> deterministic policy decision + approval/review when required
  -> endpoint releases or blocks the original external request
```

Inspection content is transmitted from the endpoint to the internal server. The privacy boundary is the company-controlled environment, not each PC. The inference API only returns scores; it does not authorize access, approve delivery or execute tools. Keep TLS verification, scoped network access, protected API credentials and request-body logging controls at this boundary; see [API deployment](jev-api.md).

**Implementation boundary:** the independent decision API and its authenticated HTTPS client are implemented. The console/policy/API scenario path was exercised on one Mac. The native Windows path above still uses deterministic rules; its integration with the experimental semantic models is not complete. Physical on-premises GPU-server operation, capacity, end-to-end latency and endpoint failure behavior need deployment qualification. The policy-service outage test does not establish Windows enforcement behavior during a judgment-server outage.

The Apple M4 Max benchmarks describe the development host only. Neither its hardware nor its measured API timings are employee-PC requirements or production server sizing results. “Local Judge” remains an experimental UI label, not the intended deployment topology.

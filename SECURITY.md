# Security and intended use

EP AI DLP is an experimental, application-scoped research implementation.
Use a disposable, explicitly authorized Windows lab before considering any deployment.

## Trust boundaries

- Only configured executable paths and selected destinations are in scope.
- The Windows service installer changes the local root certificate store, adds per-application UDP firewall rules, and installs a LocalSystem service. It previews changes unless `-Apply` is supplied.
- Local CA private keys use Windows DPAPI protection and restricted filesystem permissions. This does not resist a compromised administrator or kernel.
- Enrollment files contain credentials. Transfer them securely, use them once, and delete them afterwards. Never publish `.local/`, browser profiles, CA material, enrollment files, or raw lab evidence.
- In-scope inspection errors block the request. **A service/redirector outage may release capture. End-to-end fail-closed protection during outages is not established.**
- Events intentionally omit protected text. Hostnames, rule identifiers, timestamps, and device information are still sensitive organizational metadata.
- PostgreSQL and local audit records are mutable. No immutable external audit store is implemented.
- Rules can produce false positives and false negatives. Obfuscated, split, encrypted, image-based, or unsupported content is not claimed as reliably detected.
- Existing connections, helper executables, certificate pinning, ECH, tamper resistance, and provider changes require additional qualification.

## Reporting

For non-sensitive bugs, open a GitHub issue with synthetic reproduction data.
For security-sensitive findings, use the repository's private vulnerability
reporting facility when available. Do not put credentials, private keys, personal
information, real prompts, or customer documents into public issues.

# Native Rust runtime

Application-scoped Windows TLS inspection, deterministic DLP, signed policy
verification and metadata reporting. Python is not needed by this runtime.

- [Architecture](../docs/architecture.md)
- [Windows lab setup](../docs/windows-lab.md)
- [Validation and local harness](../docs/validation.md)
- [Component licenses](THIRD_PARTY.md)

Build: `cargo build --locked --bins` from this directory.
Test: `cargo test --locked --lib`.
Run the loopback fixture with a fresh output directory:
`target/debug/acceptance --proxy target/debug/aidlp-native --output acceptance-runs/new-run`.

The fixture does not establish Windows capture or live provider compatibility.

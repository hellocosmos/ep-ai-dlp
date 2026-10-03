# Third-party software and provenance

The root MIT license covers original EP AI DLP work, not every dependency.

| Component | Provenance / license |
|---|---|
| mitmproxy Rust core | Vendored from `mitmproxy/mitmproxy_rs`, revision `51fe2b7c5aa8439c162abb665db61c6669d146d6`; root package MIT. Upstream notices retained. |
| Windows redirector | Its own Cargo manifest declares **LGPL-3.0-or-later**. Local capture changes are retained as source and in `native/patches/bounded-transport.patch`. |
| WinDivert | Upstream driver/library components and version marker are retained under `native/third_party/mitmproxy_rs/mitmproxy-windows/mitmproxy_windows/`, including the upstream LGPLv3/GPLv2 dual-license notice. |
| Hudsucker | Vendored 0.25.0 source with MIT/Apache notices. Local TLS patches and provenance are retained in `research/hudsucker-spike/`. |
| TrapDefense signing boundary | Adapted at commit `b1867b7`; original MIT notice in `licenses/TrapDefense-MIT.txt`. Ported to TypeScript exact-byte signed JSON envelopes with a Rust verifier. |
| Console presentation | Color tokens and sidebar conventions adapted from the author's IT Manager project; AI DLP screens and integration implemented here. |
| Other dependencies | Resolved in npm, Cargo, and .NET lockfiles; their respective licenses apply. |

See [native notices](native/THIRD_PARTY.md). Vendored repositories are flattened
source snapshots, not Git submodules. No complete binary installer or
production distribution compliance assessment is provided by this preview.

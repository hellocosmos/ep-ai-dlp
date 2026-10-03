# Third-party provenance

- mitmproxy Rust repository: https://github.com/mitmproxy/mitmproxy_rs
- Pinned revision: `51fe2b7c5aa8439c162abb665db61c6669d146d6`.
- Root `mitmproxy` Rust package: MIT (retain `third_party/mitmproxy_rs/LICENSE`).
- Windows `windows-redirector` package: **LGPL-3.0-or-later**, as declared in its own Cargo.toml. The root MIT license does not override this package declaration.
- WinDivert and Rust wrapper: retain their distribution licenses, notices and the exact driver source/binary provenance. Driver is a separate component; do not describe the entire product as MIT or entirely proprietary.
- Existing Hudsucker 0.25.0 vendored CA implementation is referenced by path in the sibling research tree. Its existing license files and local patch provenance remain in that tree.
- `Cargo.lock` pins the new runtime dependency resolution. Python workspace bindings are not linked by the root mitmproxy dependency.

Source modifications to the pinned capture implementation, if any, must be listed here and retained as a patch with distribution materials. Commercial compliance review remains a release activity, not a claim made by the prototype.

## Local capture changes

`patches/bounded-transport.patch` records bounded 256-event/packet redirector queues, independent IPC read/write to avoid circular backpressure, 4096 cached directions with two unresolved packets per entry, a 256 TCP socket cap, 128 KiB extra send-buffer cap, drain acknowledgement including the extra buffer, full-close abort semantics, bounded 256-entry UDP caches with at most two pending datagrams, and host-side rejection of captured non-TCP packets before transport event creation. Malformed-packet logs omit raw payload bytes. The native stream adapter preserves graceful FIN on shutdown and aborts unfinished streams on drop. Windows integration evidence is recorded in the validation record; upstream debug messages are not a product audit trail.

The redirector limits network diversion to TCP. For the one exact protected PID, it holds a WinDivert SOCKET-layer DROP | RECV_ONLY handle for UDP Bind/Connect, then rejects preexisting selected UDP endpoints before applying capture configuration. UDP cannot rely on a stale userspace five-tuple owner cache. The exploratory UDP ownership module was rejected in review and is not part of the compiled runtime.

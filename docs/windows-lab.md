# Windows lab setup

This preview publishes source and the experimental installer scripts, not a
signed, supported Windows installer. Use an authorized disposable Windows 11 lab.

## Build prerequisites

The historical pilot used Rust's `x86_64-pc-windows-gnullvm` target with
LLVM-MinGW, Protocol Buffers (`protoc`), and the vendored WinDivert components.
Install these tools separately and configure the linker/compiler in your build
environment. MSVC build and installation are not claimed as accepted.

From the repository root, with that toolchain configured:

```powershell
cargo build --locked --manifest-path native/Cargo.toml --bins --target x86_64-pc-windows-gnullvm
cargo build --locked --manifest-path native/third_party/mitmproxy_rs/Cargo.toml -p windows-redirector --target x86_64-pc-windows-gnullvm
```

The installer expects `aidlp-native.exe`, `aidlp-service.exe`, and `libunwind.dll`
in `native/target/x86_64-pc-windows-gnullvm/debug/`. It expects
`windows-redirector.exe`, `WinDivert.dll`, and `WinDivert64.sys` in the equivalent
vendored mitmproxy workspace target directory. Stage the appropriate LLVM runtime
DLL and the vendored WinDivert files there after building. This is a manual lab
build; do not use a driver or DLL from an unverified download.

## Enroll and preview installation

Set up a reachable HTTPS management server and generate a one-time enrollment
file from the console. Transfer it securely to the Windows lab. In an elevated
PowerShell, adjust the paths to your installation:

```powershell
./native/windows-service/install.ps1 `
  -SourceRoot 'C:\ep-ai-dlp' `
  -EnrollmentPath 'C:\secure-transfer\enrollment.json' `
  -Applications @('C:\Program Files\Google\Chrome\Application\chrome.exe') `
  -Targets @('chatgpt.com:443')
```

The first call is a preview. Adding `-Apply` installs a root inspection CA,
per-application UDP blocking rules, and the automatic LocalSystem service.
The historical internal service name `FastpaceAiDlp` and Fastpace installation
paths are retained for consistency with the existing scripts.

Use the installed `status.ps1` for state inspection. `uninstall.ps1` also previews
its removal plan unless `-Apply` is supplied. It removes recorded system objects
but retains local data/evidence. Read the script before executing it.

Protection is application/destination scoped. A connected service is not proof
of whole-PC protection. See [measured evidence and gaps](validation.md).

fn main() {
    let target = std::env::var("TARGET").unwrap_or_default();
    // Match the usual 8 MiB development stack reserve on Windows. This is
    // virtual address reservation; pages commit on use and appear in RSS.
    // Debug async state-machine moves can exceed the GNU linker's 1 MiB default.
    if target.contains("windows") {
        let flag = if target.ends_with("msvc") {
            "/STACK:8388608"
        } else {
            "-Wl,--stack,8388608"
        };
        println!("cargo:rustc-link-arg-bins={flag}");
    }
}

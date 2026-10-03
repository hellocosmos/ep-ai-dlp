//! Local inspection trust. Public CA is exportable; the Windows private key is DPAPI protected.
use anyhow::{Result, Context, ensure};
use hudsucker::{certificate_authority::RcgenAuthority, rcgen::{self, BasicConstraints, CertificateParams, IsCa, Issuer, KeyPair}};
use std::{path::Path, io::Cursor};

fn generate(name: &str) -> Result<(KeyPair, String)> {
    let key = KeyPair::generate()?;
    let mut params = CertificateParams::default();
    params.is_ca = IsCa::Ca(BasicConstraints::Constrained(0));
    params.key_usages = vec![rcgen::KeyUsagePurpose::KeyCertSign, rcgen::KeyUsagePurpose::CrlSign];
    params.not_before = time::OffsetDateTime::now_utc() - time::Duration::days(1);
    params.not_after = time::OffsetDateTime::now_utc() + time::Duration::days(365);
    params.distinguished_name.push(rcgen::DnType::CommonName, name);
    let pem = params.self_signed(&key)?.pem();
    Ok((key, pem))
}

fn authority(key: KeyPair, pem: &str) -> Result<RcgenAuthority> {
    let (_, parsed) = x509_parser::pem::parse_x509_pem(pem.as_bytes()).map_err(|_| anyhow::anyhow!("invalid inspection CA"))?;
    let cert = parsed.parse_x509()?;
    ensure!(cert.validity().is_valid(), "inspection CA expired or not yet valid");
    ensure!(cert.public_key().subject_public_key.data.as_ref() == key.public_key_raw(), "inspection CA key mismatch");
    ensure!(cert.basic_constraints()?.is_some_and(|v| v.value.ca), "inspection certificate is not a CA");
    Ok(RcgenAuthority::new(Issuer::from_ca_cert_pem(pem, key)?, 64, rustls::crypto::ring::default_provider()))
}

pub fn load_or_ephemeral(directory: Option<&Path>) -> Result<(RcgenAuthority, String)> {
    let (key, pem) = if let Some(dir) = directory {
        let pem = std::fs::read_to_string(dir.join("ca.pem"))?;
        let bytes = unprotect(&std::fs::read(dir.join("key.dpapi"))?)?;
        let key = KeyPair::from_pem(std::str::from_utf8(&bytes).context("invalid protected CA key")?)?;
        (key, pem)
    } else { generate("AI DLP ephemeral test CA")? };
    Ok((authority(key, &pem)?, pem))
}

pub fn upstream_roots(file: Option<&Path>) -> Result<rustls::RootCertStore> {
    let mut roots = rustls::RootCertStore::empty();
    if let Some(file) = file {
        for cert in rustls_pemfile::certs(&mut Cursor::new(std::fs::read(file)?)) { roots.add(cert?)?; }
    } else {
        for cert in rustls_native_certs::load_native_certs().certs { let _ = roots.add(cert); }
    }
    ensure!(!roots.is_empty(), "empty upstream trust store");
    Ok(roots)
}

#[cfg(not(windows))]
pub fn initialize(_directory: &Path) -> Result<()> { anyhow::bail!("persistent inspection CA initialization requires Windows"); }
#[cfg(not(windows))]
fn unprotect(_bytes: &[u8]) -> Result<Vec<u8>> { anyhow::bail!("Windows DPAPI key cannot be loaded on this platform"); }

#[cfg(windows)]
pub fn initialize(directory: &Path) -> Result<()> {
    ensure!(directory.is_absolute(), "CA directory must be absolute");
    ensure!(!directory.exists(), "CA directory must be new");
    std::fs::create_dir(directory)?;
    // Protect before any key material is written. Machine DPAPI alone is not an ACL.
    let acl = std::process::Command::new("icacls.exe").arg(directory).args([
        "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
    ]).output()?;
    ensure!(acl.status.success(), "cannot protect CA directory permissions");
    let (key, pem) = generate(&format!("Fastpace AI DLP {}", uuid::Uuid::new_v4()))?;
    let sealed = protect(key.serialize_pem().as_bytes())?;
    std::fs::write(directory.join("key.dpapi"), sealed)?;
    std::fs::write(directory.join("ca.pem"), pem)?;
    let _ = load_or_ephemeral(Some(directory))?;
    println!("{{\"ca_initialized\":true,\"key_storage\":\"dpapi_machine_acl\"}}");
    Ok(())
}

#[cfg(windows)]
fn protect(bytes: &[u8]) -> Result<Vec<u8>> { dpapi(bytes, true) }
#[cfg(windows)]
fn unprotect(bytes: &[u8]) -> Result<Vec<u8>> { dpapi(bytes, false) }
#[cfg(windows)]
fn dpapi(bytes: &[u8], encrypt: bool) -> Result<Vec<u8>> {
    use windows::Win32::{Security::Cryptography::*, Foundation::{LocalFree,HLOCAL}};
    ensure!(bytes.len() < 65536, "invalid protected key size");
    let input = CRYPT_INTEGER_BLOB { cbData: bytes.len() as u32, pbData: bytes.as_ptr() as *mut u8 };
    let mut output = CRYPT_INTEGER_BLOB::default();
    unsafe {
        if encrypt { CryptProtectData(&input, windows::core::w!("Fastpace AI DLP inspection key"), None, None, None, CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN, &mut output)?; }
        else { CryptUnprotectData(&input, None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, &mut output)?; }
        let data = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        let _ = LocalFree(Some(HLOCAL(output.pbData as *mut _)));
        Ok(data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mismatched_ca_key_is_rejected() {
        let (key, pem) = generate("test").unwrap();
        assert!(authority(key, &pem).is_ok());
        assert!(authority(KeyPair::generate().unwrap(), &pem).is_err());
    }
    #[test]
    fn upstream_requires_a_nonempty_trust_store() {
        let dir = tempfile::tempdir().unwrap(); let file = dir.path().join("empty.pem");
        std::fs::write(&file, "").unwrap(); assert!(upstream_roots(Some(&file)).is_err());
    }
}

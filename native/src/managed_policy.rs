//! Exact-byte Ed25519 policy verification. No JavaScript/Rust canonicalization assumptions.
use crate::{detectors::RULE_IDS, inspection::Decision};
use anyhow::{bail, ensure, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Envelope {
    pub payload_base64: String,
    pub signature_base64: String,
    pub key_id: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuleAction {
    Block,
    Monitor,
}
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub schema_version: u8,
    pub tenant_id: Uuid,
    pub device_id: Uuid,
    pub revision: u64,
    pub issued_at: u64,
    pub expires_at: u64,
    pub rules: BTreeMap<String, RuleAction>,
}
pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_secs())
        .unwrap_or(0)
}
pub fn key_id(key: &[u8]) -> String {
    format!("{:x}", Sha256::digest(key))[..16].to_owned()
}
pub fn verify(
    envelope: &Envelope,
    public: &str,
    device: Uuid,
    tenant: Uuid,
    clock: u64,
) -> Result<Policy> {
    ensure!(envelope.payload_base64.len() <= 16384, "policy_too_large");
    let key: [u8; 32] = STANDARD
        .decode(public)?
        .try_into()
        .map_err(|_| anyhow::anyhow!("invalid_public_key"))?;
    ensure!(envelope.key_id == key_id(&key), "policy_key_mismatch");
    let bytes = STANDARD.decode(&envelope.payload_base64)?;
    let signature = Signature::from_slice(&STANDARD.decode(&envelope.signature_base64)?)?;
    VerifyingKey::from_bytes(&key)?
        .verify_strict(&bytes, &signature)
        .map_err(|_| anyhow::anyhow!("policy_signature_invalid"))?;
    let policy: Policy = serde_json::from_slice(&bytes)?;
    ensure!(
        policy.schema_version == 1 && policy.device_id == device && policy.tenant_id == tenant,
        "policy_binding_invalid"
    );
    ensure!(
        policy.revision > 0 && policy.revision <= 9_007_199_254_740_991,
        "policy_revision_invalid"
    );
    ensure!(
        policy.issued_at <= clock.saturating_add(60)
            && policy.expires_at > clock
            && policy.expires_at > policy.issued_at
            && policy.expires_at - policy.issued_at <= 7 * 86400,
        "policy_expired_or_invalid_time"
    );
    ensure!(
        policy.rules.len() == RULE_IDS.len()
            && RULE_IDS.iter().all(|id| policy.rules.contains_key(*id)),
        "policy_rules_invalid"
    );
    Ok(policy)
}
pub fn check_revision(new: &Policy, old: Option<&Policy>) -> Result<()> {
    if let Some(old) = old {
        ensure!(new.revision >= old.revision, "policy_revision_regressed");
        if new.revision == old.revision && new != old {
            bail!("policy_revision_conflict");
        }
    }
    Ok(())
}
/// Returns effective action; the original sensitive rule IDs remain in telemetry.
pub fn evaluate(
    mut decision: Decision,
    policy: Option<&Policy>,
    clock: u64,
) -> (Decision, &'static str, u64) {
    let Some(policy) =
        policy.filter(|p| p.expires_at > clock && p.issued_at <= clock.saturating_add(60))
    else {
        return (
            Decision {
                reason: "policy_unavailable",
                rules: vec![],
            },
            "block",
            0,
        );
    };
    let action = match decision.reason {
        "clean" => "allow",
        "sensitive_data" => {
            if decision
                .rules
                .iter()
                .any(|id| policy.rules.get(*id) != Some(&RuleAction::Monitor))
            {
                "block"
            } else {
                "monitor"
            }
        }
        _ => {
            decision = Decision::uninspectable();
            "block"
        }
    };
    (decision, action, policy.revision)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    #[test]
    fn verifies_node_generated_bytes() {
        let value: serde_json::Value = serde_json::from_str(include_str!(
            "../../packages/contracts/fixtures/node-signed-policy.json"
        ))
        .unwrap();
        let envelope: Envelope = serde_json::from_value(value["envelope"].clone()).unwrap();
        let expected: Policy = serde_json::from_value(value["payload"].clone()).unwrap();
        let actual = verify(
            &envelope,
            value["public_key_base64"].as_str().unwrap(),
            expected.device_id,
            expected.tenant_id,
            value["clock"].as_u64().unwrap(),
        )
        .unwrap();
        assert_eq!(actual, expected);
    }
    fn sample() -> Policy {
        Policy {
            schema_version: 1,
            tenant_id: Uuid::nil(),
            device_id: Uuid::nil(),
            revision: 1,
            issued_at: 100,
            expires_at: 200,
            rules: RULE_IDS
                .iter()
                .map(|id| (id.to_string(), RuleAction::Block))
                .collect(),
        }
    }
    fn signed(p: &Policy) -> (Envelope, String) {
        let key = SigningKey::from_bytes(&[7; 32]);
        let bytes = serde_json::to_vec(p).unwrap();
        (
            Envelope {
                payload_base64: STANDARD.encode(&bytes),
                signature_base64: STANDARD.encode(key.sign(&bytes).to_bytes()),
                key_id: key_id(key.verifying_key().as_bytes()),
            },
            STANDARD.encode(key.verifying_key().as_bytes()),
        )
    }
    #[test]
    fn binding_signature_time_and_complete_rules() {
        let policy = sample();
        let (mut e, key) = signed(&policy);
        assert_eq!(
            verify(&e, &key, Uuid::nil(), Uuid::nil(), 150).unwrap(),
            policy
        );
        assert!(verify(&e, &key, Uuid::new_v4(), Uuid::nil(), 150).is_err());
        assert!(verify(&e, &key, Uuid::nil(), Uuid::new_v4(), 150).is_err());
        assert!(verify(&e, &key, Uuid::nil(), Uuid::nil(), 200).is_err());
        e.payload_base64 = STANDARD.encode(b"{}");
        assert!(verify(&e, &key, Uuid::nil(), Uuid::nil(), 150).is_err());
        let mut p = sample();
        p.rules.remove("email");
        let (e, key) = signed(&p);
        assert!(verify(&e, &key, Uuid::nil(), Uuid::nil(), 150).is_err());
    }
    #[test]
    fn revision_and_action_precedence() {
        let mut p = sample();
        let old = p.clone();
        p.revision = 2;
        assert!(check_revision(&p, Some(&old)).is_ok());
        assert!(check_revision(&old, Some(&p)).is_err());
        p = old.clone();
        p.rules.insert("email".into(), RuleAction::Monitor);
        assert!(check_revision(&p, Some(&old)).is_err());
        let d = Decision {
            reason: "sensitive_data",
            rules: vec!["email"],
        };
        assert_eq!(evaluate(d.clone(), Some(&p), 150).1, "monitor");
        assert_eq!(
            evaluate(
                Decision {
                    reason: "sensitive_data",
                    rules: vec!["email", "private_key"]
                },
                Some(&p),
                150
            )
            .1,
            "block"
        );
        assert_eq!(
            evaluate(Decision::uninspectable(), Some(&p), 150).1,
            "block"
        );
        assert_eq!(
            evaluate(d.clone(), None, 150).0.reason,
            "policy_unavailable"
        );
        assert_eq!(evaluate(d, Some(&p), 200).1, "block");
    }
}

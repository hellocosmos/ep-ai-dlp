//! A normal HTTPS client: no capture configuration, PID enrollment or proxy.
use anyhow::{Result,ensure};
use serde::Deserialize;
use std::{path::PathBuf,time::Duration};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config { url:String, output:PathBuf, #[serde(default)] control_ca:Option<PathBuf> }
#[tokio::main]
async fn main()->Result<()> {
    let args=std::env::args().collect::<Vec<_>>();
    ensure!(args.len()==3&&args[1]=="--config","usage: transparent-probe --config FILE");
    let cfg:Config=serde_json::from_slice(&std::fs::read(&args[2])?)?;
    let mut builder=reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(20)).redirect(reqwest::redirect::Policy::none());
    let roots=rustls_native_certs::load_native_certs();ensure!(!roots.certs.is_empty(),"no system trust anchors");
    for root in roots.certs { builder=builder.add_root_certificate(reqwest::Certificate::from_der(root.as_ref())?); }
    if let Some(ca)=&cfg.control_ca {builder=builder.add_root_certificate(reqwest::Certificate::from_pem(&std::fs::read(ca)?)?);}
    let client=builder.build()?;let mut results=vec![];
    for (kind,prompt) in [("safe","Explain the water cycle."),("sensitive","Contact test.user@example.com for this synthetic test.")] {
        let response=client.post(format!("{}/submit/{kind}",cfg.url.trim_end_matches('/'))).json(&serde_json::json!({"prompt":prompt})).send().await;
        results.push(match response {Ok(r)=>serde_json::json!({"case":kind,"status":r.status().as_u16()}),Err(e)=>serde_json::json!({"case":kind,"error":e.to_string()})});
    }
    let output=serde_json::json!({"pid":std::process::id(),"control_ca":cfg.control_ca.is_some(),"proxy":false,"results":results});
    std::fs::write(&cfg.output,serde_json::to_vec_pretty(&output)?)?;println!("{output}");Ok(())
}

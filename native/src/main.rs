use anyhow::{Result, ensure};
#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 6 && args[1] == "enroll" && args[2] == "--config" && args[4] == "--output" {
        return aidlp_native::management::enroll(std::path::Path::new(&args[3]), std::path::Path::new(&args[5])).await;
    }
    if args.len() == 4 && args[1] == "init-ca" && args[2] == "--directory" {
        return aidlp_native::certificate::initialize(std::path::Path::new(&args[3]));
    }
    ensure!(args.len() == 3 && args[1] == "--config", "usage: aidlp-native --config FILE");
    let config = serde_json::from_slice(&std::fs::read(&args[2])?)?;
    let (send, receive) = tokio::sync::watch::channel(false);
    tokio::spawn(async move { let _ = tokio::signal::ctrl_c().await; let _ = send.send(true); });
    aidlp_native::runtime::run(config, receive).await
}

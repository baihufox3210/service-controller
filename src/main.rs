use std::io::IsTerminal;
use std::process::ExitCode;
use std::sync::Arc;

use service_controller::backend::{Backends, DockerAdapter, ProcessRunner, SystemdAdapter};
use service_controller::controller::Controller;
use service_controller::discord::{self, Data};
use service_controller::permissions::Policy;
use service_controller::registry::Registry;
use service_controller::settings::Settings;
use tracing::error;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info,serenity=warn")))
        // journald 已經記錄時間，也不需要終端機顏色
        .without_time()
        .with_ansi(std::io::stdout().is_terminal())
        .init();

    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            error!("{e}");
            ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<(), discord::Error> {
    let settings = Settings::from_env()?;

    let registry = Registry::load(settings.state_dir.join("services.toml"))?;
    let policy = Policy::load(
        settings.state_dir.join("permissions.toml"),
        settings.owners.clone(),
        settings.allowed_guilds.clone(),
    )?;

    let runner = Arc::new(ProcessRunner);
    let backends = Backends { systemd: SystemdAdapter::new(runner.clone()), docker: DockerAdapter::new(runner) };

    let data = Data { controller: Controller::new(registry, backends), policy };

    discord::run(settings, data).await
}

use std::sync::Arc;

use super::{BackendState, CONTROL_TIMEOUT, CommandResult, RunOptions, Runner, check_target, clamp_lines};
use crate::model::{Action, Backend};

/// 「開機自動啟動」在 Docker 中對應容器的 restart policy。
const ENABLED_RESTART_POLICY: &str = "unless-stopped";
const DISABLED_RESTART_POLICY: &str = "no";

/// 透過 docker CLI 操作主機上的容器。
pub struct DockerAdapter {
    runner: Arc<dyn Runner>,
}

impl DockerAdapter {
    pub fn new(runner: Arc<dyn Runner>) -> Self {
        Self { runner }
    }

    async fn docker(&self, args: &[&str], container: &str, options: RunOptions) -> CommandResult {
        if let Err(e) = check_target(Backend::Docker, container) {
            return e;
        }

        // "--" 確保容器名稱一定被解讀為參數而非選項
        let args = args.iter().copied().chain(["--", container]).map(str::to_owned).collect();
        self.runner.run("docker", args, options).await
    }

    pub async fn control(&self, action: Action, container: &str) -> CommandResult {
        let enable = format!("--restart={ENABLED_RESTART_POLICY}");
        let disable = format!("--restart={DISABLED_RESTART_POLICY}");

        let args: &[&str] = match action {
            Action::Start => &["start"],
            Action::Stop => &["stop"],
            Action::Restart => &["restart"],
            Action::Enable => &["update", &enable],
            Action::Disable => &["update", &disable],
        };

        self.docker(args, container, RunOptions { timeout: CONTROL_TIMEOUT, ..Default::default() }).await
    }

    pub async fn state(&self, container: &str) -> BackendState {
        let format = "--format={{.State.Status}} {{.HostConfig.RestartPolicy.Name}}";
        let result = self.docker(&["inspect", "--type=container", format], container, RunOptions::default()).await;

        if !result.success() {
            return BackendState {
                active: "not-found".to_owned(),
                enabled: "unknown".to_owned(),
                error: Some(result.error_message()),
            };
        }

        let output = result.stdout.trim();
        let (status, policy) = output.split_once(' ').unwrap_or((output, ""));
        let policy = if policy.is_empty() { DISABLED_RESTART_POLICY } else { policy };

        BackendState {
            active: if status.is_empty() { "unknown".to_owned() } else { status.to_owned() },
            enabled: if policy == DISABLED_RESTART_POLICY {
                "disabled".to_owned()
            } else {
                format!("enabled ({policy})")
            },
            error: None,
        }
    }

    pub async fn logs(&self, container: &str, lines: u32) -> CommandResult {
        let tail = format!("--tail={}", clamp_lines(lines));
        let options = RunOptions { merge_stderr: true, ..Default::default() };

        self.docker(&["logs", &tail, "--timestamps"], container, options).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::testing::{FakeRunner, fail, ok};

    #[tokio::test]
    async fn control_commands() {
        let runner = Arc::new(FakeRunner::default());
        let docker = DockerAdapter::new(runner.clone());

        for action in [Action::Start, Action::Stop, Action::Restart, Action::Enable, Action::Disable] {
            docker.control(action, "my-web").await;
        }

        let calls = runner.calls();
        assert_eq!(calls[0], ["docker", "start", "--", "my-web"]);
        assert_eq!(calls[3], ["docker", "update", "--restart=unless-stopped", "--", "my-web"]);
        assert_eq!(calls[4], ["docker", "update", "--restart=no", "--", "my-web"]);
    }

    #[tokio::test]
    async fn state_parsing() {
        for (result, active, enabled, has_error) in [
            (ok("running unless-stopped\n"), "running", "enabled (unless-stopped)", false),
            (ok("exited no\n"), "exited", "disabled", false),
            (fail(1, "", "No such container: ghost"), "not-found", "unknown", true),
        ] {
            let runner = Arc::new(FakeRunner::with_results([result]));
            let state = DockerAdapter::new(runner).state("my-web").await;
            assert_eq!(
                (state.active.as_str(), state.enabled.as_str(), state.error.is_some()),
                (active, enabled, has_error)
            );
        }
    }

    #[tokio::test]
    async fn logs_merge_stderr() {
        let runner = Arc::new(FakeRunner::default());
        DockerAdapter::new(runner.clone()).logs("my-web", 20).await;

        assert_eq!(runner.calls()[0], ["docker", "logs", "--tail=20", "--timestamps", "--", "my-web"]);
        assert!(runner.calls.lock().unwrap()[0].2);
    }

    #[tokio::test]
    async fn refuses_invalid_container() {
        let runner = Arc::new(FakeRunner::default());
        let docker = DockerAdapter::new(runner.clone());

        for name in ["-v", "--privileged", "a/b", "a b"] {
            assert!(!docker.control(Action::Start, name).await.success());
        }

        assert!(runner.calls().is_empty());
    }
}

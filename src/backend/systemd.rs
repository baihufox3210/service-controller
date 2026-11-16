use std::sync::Arc;

use super::{BackendState, CONTROL_TIMEOUT, CommandResult, RunOptions, Runner, check_target, clamp_lines};
use crate::model::{Action, Backend};

/// 透過 systemctl 操作主機的 systemd；權限由主機的 polkit 規則決定。
pub struct SystemdAdapter {
    runner: Arc<dyn Runner>,
}

impl SystemdAdapter {
    pub fn new(runner: Arc<dyn Runner>) -> Self {
        Self { runner }
    }

    async fn systemctl(&self, verb: &str, unit: &str, options: RunOptions) -> CommandResult {
        if let Err(e) = check_target(Backend::Systemd, unit) {
            return e;
        }

        // --no-ask-password：不等待互動式驗證；"--" 確保 unit 一定被解讀為參數而非選項
        let args = ["--no-ask-password", verb, "--", unit].map(str::to_owned).to_vec();
        self.runner.run("systemctl", args, options).await
    }

    pub async fn control(&self, action: Action, unit: &str) -> CommandResult {
        let options = RunOptions { timeout: CONTROL_TIMEOUT, ..Default::default() };
        self.systemctl(action.as_str(), unit, options).await
    }

    pub async fn state(&self, unit: &str) -> BackendState {
        let active = self.systemctl("is-active", unit, RunOptions::default()).await;
        let enabled = self.systemctl("is-enabled", unit, RunOptions::default()).await;

        // 服務未執行時 is-active 也會回傳非 0，但仍會輸出狀態；沒有輸出才代表查詢失敗
        let mut errors: Vec<String> = [&active, &enabled]
            .into_iter()
            .filter(|r| r.stdout.trim().is_empty())
            .map(CommandResult::error_message)
            .collect();
        errors.dedup();

        BackendState {
            active: non_empty_or_unknown(&active.stdout),
            enabled: non_empty_or_unknown(&enabled.stdout),
            error: (!errors.is_empty()).then(|| errors.join("\n")),
        }
    }

    pub async fn logs(&self, unit: &str, lines: u32) -> CommandResult {
        if let Err(e) = check_target(Backend::Systemd, unit) {
            return e;
        }

        let args = vec![
            format!("--unit={unit}"),
            "-n".to_owned(),
            clamp_lines(lines).to_string(),
            "--no-pager".to_owned(),
            "-o".to_owned(),
            "short-iso".to_owned(),
        ];
        self.runner.run("journalctl", args, RunOptions::default()).await
    }
}

fn non_empty_or_unknown(text: &str) -> String {
    match text.trim() {
        "" => "unknown".to_owned(),
        s => s.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::testing::{FakeRunner, fail, ok};

    fn adapter(runner: &Arc<FakeRunner>) -> SystemdAdapter {
        SystemdAdapter::new(runner.clone())
    }

    #[tokio::test]
    async fn control_uses_separator_and_no_password_prompt() {
        let runner = Arc::new(FakeRunner::default());

        for action in [Action::Start, Action::Stop, Action::Restart, Action::Enable, Action::Disable] {
            adapter(&runner).control(action, "nginx.service").await;
        }

        assert_eq!(runner.calls()[4], ["systemctl", "--no-ask-password", "disable", "--", "nginx.service"]);
        assert_eq!(runner.calls().len(), 5);
    }

    #[tokio::test]
    async fn state_distinguishes_stopped_from_query_error() {
        let runner = Arc::new(FakeRunner::with_results([fail(3, "inactive\n", ""), ok("disabled\n")]));
        let state = adapter(&runner).state("nginx.service").await;
        assert_eq!((state.active.as_str(), state.error), ("inactive", None));

        let denied = fail(1, "", "Failed to retrieve unit state: Access denied\n");
        let runner = Arc::new(FakeRunner::with_results([denied.clone(), denied]));
        let state = adapter(&runner).state("nginx.service").await;
        assert_eq!(state.active, "unknown");
        assert_eq!(state.error.as_deref(), Some("Failed to retrieve unit state: Access denied"));
    }

    #[tokio::test]
    async fn logs_clamp_lines() {
        let runner = Arc::new(FakeRunner::default());
        adapter(&runner).logs("nginx.service", 100_000).await;

        assert_eq!(
            runner.calls()[0],
            ["journalctl", "--unit=nginx.service", "-n", "500", "--no-pager", "-o", "short-iso"]
        );
    }

    #[tokio::test]
    async fn refuses_invalid_unit_without_running_anything() {
        let runner = Arc::new(FakeRunner::default());

        for unit in ["--help", "-H", "nginx", "a b.service"] {
            assert!(!adapter(&runner).control(Action::Start, unit).await.success());
        }

        assert!(runner.calls().is_empty());
    }
}

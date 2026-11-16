use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use super::{BackendState, CommandResult, RunOptions, Runner, SCRIPT_TIMEOUT, check_target, clamp_lines};
use crate::model::Backend;

/// 執行主機上以 scripts/register-script.sh 註冊的腳本。
///
/// 每個腳本都是一個 systemd oneshot unit（`sc-script-<名稱>.service`），由 root 建立並指定
/// 執行的使用者與指令；bot 只能依名稱啟動它，無法指定路徑或參數。
///
/// 成功執行的 oneshot unit 結束後會被 systemd 卸載，`systemctl show` 看不到上次的結果，
/// 所以輸出與執行結果都從 journal 讀取。
pub struct ScriptAdapter {
    runner: Arc<dyn Runner>,
}

pub fn unit_name(script: &str) -> String {
    format!("sc-script-{script}.service")
}

/// 腳本輸出顯示在 Discord 的最大行數
const OUTPUT_LINES: &str = "60";
const OUTPUT_RETRIES: u32 = 10;
const OUTPUT_RETRY_DELAY: Duration = Duration::from_millis(300);

impl ScriptAdapter {
    pub fn new(runner: Arc<dyn Runner>) -> Self {
        Self { runner }
    }

    async fn systemctl(&self, args: &[&str], options: RunOptions) -> CommandResult {
        let args = ["--no-ask-password"].iter().chain(args).map(|s| s.to_string()).collect();
        self.runner.run("systemctl", args, options).await
    }

    async fn journalctl(&self, args: &[&str]) -> CommandResult {
        let args = ["--no-pager"].iter().chain(args).map(|s| s.to_string()).collect();
        self.runner.run("journalctl", args, RunOptions::default()).await
    }

    /// 回傳 (LoadState, ActiveState)。
    async fn unit_state(&self, unit: &str) -> Result<(String, String), CommandResult> {
        let result =
            self.systemctl(&["show", "--property=LoadState,ActiveState", "--", unit], RunOptions::default()).await;

        if !result.success() {
            return Err(result);
        }

        let property = |key: &str| {
            result
                .stdout
                .lines()
                .find_map(|line| line.strip_prefix(key)?.strip_prefix('='))
                .unwrap_or_default()
                .to_owned()
        };

        Ok((property("LoadState"), property("ActiveState")))
    }

    /// 執行腳本並等待完成；回傳的 stdout 是這次執行的輸出。
    pub async fn run(&self, script: &str) -> CommandResult {
        if let Err(e) = check_target(Backend::Script, script) {
            return e;
        }

        let unit = unit_name(script);

        match self.unit_state(&unit).await {
            Ok((load, _)) if load == "loaded" => {}
            Ok(_) => return CommandResult::failed(not_registered(script)),
            Err(e) => return e,
        }

        let since = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs());

        // oneshot unit 的 start 會等到腳本結束才返回
        let options = RunOptions { timeout: SCRIPT_TIMEOUT, ..Default::default() };
        let started = self.systemctl(&["start", "--", &unit], options).await;

        CommandResult { stdout: self.output_since(&unit, since).await, ..started }
    }

    /// journald 是非同步寫入的，腳本剛結束時輸出可能還沒進到 journal，稍等後重試。
    async fn output_since(&self, unit: &str, since: u64) -> String {
        let filter = format!("_SYSTEMD_UNIT={unit}");
        let since = format!("--since=@{since}");

        for attempt in 1..=OUTPUT_RETRIES {
            let output = self.journalctl(&[&filter, &since, "-o", "cat", "-n", OUTPUT_LINES]).await.stdout;

            if !output.trim().is_empty() || attempt == OUTPUT_RETRIES {
                return output;
            }

            tokio::time::sleep(OUTPUT_RETRY_DELAY).await;
        }

        String::new()
    }

    /// 上次執行的結果；`active` 是 success、failed、running 或 never，`enabled` 是執行時間。
    pub async fn state(&self, script: &str) -> BackendState {
        let error = |message: String| BackendState {
            active: "unknown".to_owned(),
            enabled: "unknown".to_owned(),
            error: Some(message),
        };

        if let Err(e) = check_target(Backend::Script, script) {
            return error(e.error_message());
        }

        let unit = unit_name(script);

        match self.unit_state(&unit).await {
            Err(e) => return error(e.error_message()),
            Ok((load, _)) if load != "loaded" => {
                return BackendState { active: "not-found".to_owned(), ..error(not_registered(script)) };
            }
            Ok((_, active)) if active == "activating" => {
                return BackendState { active: "running".to_owned(), enabled: "執行中".to_owned(), error: None };
            }
            Ok(_) => {}
        }

        // systemd 會為每次啟動記錄一筆 JOB_RESULT（done、failed、timeout 等）
        let unit_filter = format!("UNIT={unit}");
        let result = self.journalctl(&[&unit_filter, "JOB_TYPE=start", "-n", "1", "-o", "verbose"]).await;

        if !result.success() {
            return error(result.error_message());
        }

        let Some(job_result) = result.stdout.lines().find_map(|l| l.trim().strip_prefix("JOB_RESULT=")) else {
            return BackendState { active: "never".to_owned(), enabled: "尚未執行".to_owned(), error: None };
        };

        BackendState {
            active: if job_result == "done" { "success".to_owned() } else { job_result.to_owned() },
            enabled: verbose_timestamp(&result.stdout),
            error: None,
        }
    }

    pub async fn logs(&self, script: &str, lines: u32) -> CommandResult {
        if let Err(e) = check_target(Backend::Script, script) {
            return e;
        }

        let unit = format!("--unit={}", unit_name(script));
        let lines = clamp_lines(lines).to_string();
        self.journalctl(&[&unit, "-n", &lines, "-o", "short-iso"]).await
    }
}

/// `-o verbose` 的第一行是時間，例如 `Mon 2026-11-16 05:55:56.866576 UTC [s=...]`；去掉微秒與游標。
fn verbose_timestamp(output: &str) -> String {
    let header = output.lines().next().unwrap_or_default();
    let header = header.split(" [").next().unwrap_or_default();

    header
        .split_whitespace()
        .map(|part| if part.contains(':') { part.split('.').next().unwrap_or(part) } else { part })
        .collect::<Vec<_>>()
        .join(" ")
}

fn not_registered(script: &str) -> String {
    format!("腳本 {script} 尚未在主機上註冊，請執行：sudo ./scripts/register-script.sh add {script} ...")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::testing::{FakeRunner, fail, ok};

    const LOADED: &str = "LoadState=loaded\nActiveState=inactive\n";

    #[tokio::test]
    async fn run_starts_unit_and_collects_output_since_start() {
        let runner = Arc::new(FakeRunner::with_results([ok(LOADED), ok(""), ok("Already up to date.\nDone\n")]));

        let result = ScriptAdapter::new(runner.clone()).run("blog-restart").await;

        assert!(result.success());
        assert_eq!(result.stdout, "Already up to date.\nDone\n");

        let calls = runner.calls();
        assert_eq!(calls[1], ["systemctl", "--no-ask-password", "start", "--", "sc-script-blog-restart.service"]);
        assert_eq!(calls[2][..3], ["journalctl", "--no-pager", "_SYSTEMD_UNIT=sc-script-blog-restart.service"]);
        assert!(calls[2][3].starts_with("--since=@"));
    }

    #[tokio::test]
    async fn failed_run_keeps_error_and_output() {
        let runner = Arc::new(FakeRunner::with_results([
            ok(LOADED),
            fail(
                1,
                "",
                "Job for sc-script-blog-restart.service failed because the control process exited with error code.",
            ),
            ok("[停止] npm run build\n"),
        ]));

        let result = ScriptAdapter::new(runner).run("blog-restart").await;

        assert!(!result.success());
        assert!(result.stderr.contains("exited with error code"));
        assert_eq!(result.stdout, "[停止] npm run build\n");
    }

    #[tokio::test]
    async fn retries_until_journal_has_output() {
        let runner = Arc::new(FakeRunner::with_results([ok(LOADED), ok(""), ok(""), ok(""), ok("Done\n")]));

        let result = ScriptAdapter::new(runner.clone()).run("blog-restart").await;

        assert_eq!(result.stdout, "Done\n");
        assert_eq!(runner.calls().len(), 5);
    }

    #[tokio::test]
    async fn unregistered_script_is_not_started() {
        let runner = Arc::new(FakeRunner::with_results([ok("LoadState=not-found\nActiveState=inactive\n")]));

        let result = ScriptAdapter::new(runner.clone()).run("ghost").await;

        assert!(!result.success() && result.stderr.contains("register-script.sh"));
        assert_eq!(runner.calls().len(), 1);
    }

    #[tokio::test]
    async fn invalid_name_runs_nothing() {
        let runner = Arc::new(FakeRunner::default());

        for name in ["../x", "-x", "x.sh", "x;id"] {
            assert!(!ScriptAdapter::new(runner.clone()).run(name).await.success());
        }

        assert!(runner.calls().is_empty());
    }

    #[tokio::test]
    async fn state_reads_last_job_result_from_journal() {
        let verbose = |result: &str| {
            ok(&format!(
                "Mon 2026-11-16 05:55:56.866576 UTC [s=6ad6;i=1b3]\n    JOB_TYPE=start\n    JOB_RESULT={result}\n"
            ))
        };

        for (journal, active, enabled) in [
            (verbose("done"), "success", "Mon 2026-11-16 05:55:56 UTC"),
            (verbose("failed"), "failed", "Mon 2026-11-16 05:55:56 UTC"),
            (ok("-- No entries --\n"), "never", "尚未執行"),
        ] {
            let runner = Arc::new(FakeRunner::with_results([ok(LOADED), journal]));
            let state = ScriptAdapter::new(runner).state("blog-restart").await;
            assert_eq!((state.active.as_str(), state.enabled.as_str(), state.error), (active, enabled, None));
        }

        let runner = Arc::new(FakeRunner::with_results([ok("LoadState=loaded\nActiveState=activating\n")]));
        assert_eq!(ScriptAdapter::new(runner).state("blog-restart").await.active, "running");

        let runner = Arc::new(FakeRunner::with_results([ok("LoadState=not-found\nActiveState=inactive\n")]));
        let state = ScriptAdapter::new(runner).state("ghost").await;
        assert_eq!(state.active, "not-found");
        assert!(state.error.unwrap().contains("尚未在主機上註冊"));
    }
}

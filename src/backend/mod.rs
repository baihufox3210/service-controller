//! 執行外部指令（systemctl、journalctl、docker）。
//!
//! 一律以參數陣列直接執行、不經過 shell；逾時會終止子程序。

mod docker;
mod systemd;

use std::future::Future;
use std::io::Read;
use std::pin::Pin;
use std::process::Stdio;
use std::time::Duration;

use tokio::process::Command;

pub use docker::DockerAdapter;
pub use systemd::SystemdAdapter;

use crate::model::{Action, Backend, ValidationError, validate_target};

pub const MAX_LOG_LINES: u32 = 500;
pub const QUERY_TIMEOUT: Duration = Duration::from_secs(15);
/// systemctl start / stop 會等待工作完成，給較長的時間
pub const CONTROL_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandResult {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
}

impl CommandResult {
    pub fn success(&self) -> bool {
        self.code == Some(0)
    }

    pub fn failed(message: impl Into<String>) -> Self {
        Self { code: None, stdout: String::new(), stderr: message.into(), timed_out: false }
    }

    /// 失敗時給使用者看的訊息。
    pub fn error_message(&self) -> String {
        if self.timed_out {
            return "操作逾時，可能仍在背景執行中，請稍後用 status 確認".to_owned();
        }

        [self.stderr.trim(), self.stdout.trim()].into_iter().find(|s| !s.is_empty()).unwrap_or("操作失敗").to_owned()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackendState {
    pub active: String,
    pub enabled: String,
    /// 無法查詢狀態時的錯誤訊息
    pub error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RunOptions {
    pub timeout: Duration,
    /// 把 stderr 併入 stdout（docker logs 兩者都是日誌內容）
    pub merge_stderr: bool,
}

impl Default for RunOptions {
    fn default() -> Self {
        Self { timeout: QUERY_TIMEOUT, merge_stderr: false }
    }
}

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

pub trait Runner: Send + Sync {
    fn run<'a>(&'a self, program: &'a str, args: Vec<String>, options: RunOptions) -> BoxFuture<'a, CommandResult>;
}

/// 實際執行子程序的 Runner。
pub struct ProcessRunner;

impl Runner for ProcessRunner {
    fn run<'a>(&'a self, program: &'a str, args: Vec<String>, options: RunOptions) -> BoxFuture<'a, CommandResult> {
        Box::pin(run_process(program, args, options))
    }
}

async fn run_process(program: &str, args: Vec<String>, options: RunOptions) -> CommandResult {
    let mut command = Command::new(program);
    command.args(&args).stdin(Stdio::null()).kill_on_drop(true);

    // 合併輸出時讓 stdout 與 stderr 共用同一條 pipe，保留原本的先後順序
    let merged_reader = if options.merge_stderr {
        let (reader, writer) = match std::io::pipe() {
            Ok(pipe) => pipe,
            Err(e) => return CommandResult::failed(e.to_string()),
        };
        let writer_clone = match writer.try_clone() {
            Ok(w) => w,
            Err(e) => return CommandResult::failed(e.to_string()),
        };
        command.stdout(Stdio::from(writer)).stderr(Stdio::from(writer_clone));
        Some(reader)
    } else {
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        None
    };

    let child = match command.spawn() {
        Ok(child) => child,
        Err(e) => return CommandResult::failed(format!("無法執行 {program}：{e}")),
    };

    // spawn 之後 command 仍持有 pipe 的寫入端，必須先釋放，否則讀取端永遠等不到 EOF
    drop(command);

    let output = async {
        match merged_reader {
            Some(mut reader) => {
                let read = tokio::task::spawn_blocking(move || {
                    let mut buf = Vec::new();
                    reader.read_to_end(&mut buf).map(|_| buf)
                });
                let status = child.wait_with_output().await?.status;
                let stdout = read.await.map_err(std::io::Error::other)??;
                Ok::<_, std::io::Error>((status, stdout, Vec::new()))
            }
            None => {
                let output = child.wait_with_output().await?;
                Ok((output.status, output.stdout, output.stderr))
            }
        }
    };

    match tokio::time::timeout(options.timeout, output).await {
        // 逾時時 future 被丟棄，kill_on_drop 會終止子程序
        Err(_) => CommandResult { code: None, stdout: String::new(), stderr: String::new(), timed_out: true },
        Ok(Err(e)) => CommandResult::failed(e.to_string()),
        Ok(Ok((status, stdout, stderr))) => CommandResult {
            code: status.code(),
            stdout: String::from_utf8_lossy(&stdout).into_owned(),
            stderr: String::from_utf8_lossy(&stderr).into_owned(),
            timed_out: false,
        },
    }
}

/// 執行前再次驗證 target，作為最後一道防線。
fn check_target(backend: Backend, target: &str) -> Result<(), CommandResult> {
    validate_target(backend, target).map_err(|e: ValidationError| CommandResult::failed(e.to_string()))
}

fn clamp_lines(lines: u32) -> u32 {
    lines.clamp(1, MAX_LOG_LINES)
}

/// 依後端分派操作。
pub struct Backends {
    pub systemd: SystemdAdapter,
    pub docker: DockerAdapter,
}

impl Backends {
    pub async fn control(&self, backend: Backend, action: Action, target: &str) -> CommandResult {
        match backend {
            Backend::Systemd => self.systemd.control(action, target).await,
            Backend::Docker => self.docker.control(action, target).await,
        }
    }

    pub async fn state(&self, backend: Backend, target: &str) -> BackendState {
        match backend {
            Backend::Systemd => self.systemd.state(target).await,
            Backend::Docker => self.docker.state(target).await,
        }
    }

    pub async fn logs(&self, backend: Backend, target: &str, lines: u32) -> CommandResult {
        match backend {
            Backend::Systemd => self.systemd.logs(target, lines).await,
            Backend::Docker => self.docker.logs(target, lines).await,
        }
    }
}

#[cfg(test)]
pub mod testing {
    use std::collections::VecDeque;
    use std::sync::Mutex;

    use super::*;

    /// 記錄收到的指令，並依序回傳預先設定的結果。
    #[derive(Default)]
    pub struct FakeRunner {
        pub calls: Mutex<Vec<(String, Vec<String>, bool)>>,
        pub results: Mutex<VecDeque<CommandResult>>,
    }

    impl FakeRunner {
        pub fn with_results(results: impl IntoIterator<Item = CommandResult>) -> Self {
            Self { results: Mutex::new(results.into_iter().collect()), ..Default::default() }
        }

        pub fn calls(&self) -> Vec<Vec<String>> {
            self.calls.lock().unwrap().iter().map(|(p, a, _)| [vec![p.clone()], a.clone()].concat()).collect()
        }
    }

    impl Runner for FakeRunner {
        fn run<'a>(&'a self, program: &'a str, args: Vec<String>, options: RunOptions) -> BoxFuture<'a, CommandResult> {
            self.calls.lock().unwrap().push((program.to_owned(), args, options.merge_stderr));
            let result = self.results.lock().unwrap().pop_front().unwrap_or_else(|| ok(""));
            Box::pin(async move { result })
        }
    }

    pub fn ok(stdout: &str) -> CommandResult {
        CommandResult { code: Some(0), stdout: stdout.to_owned(), stderr: String::new(), timed_out: false }
    }

    pub fn fail(code: i32, stdout: &str, stderr: &str) -> CommandResult {
        CommandResult { code: Some(code), stdout: stdout.to_owned(), stderr: stderr.to_owned(), timed_out: false }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[tokio::test]
    async fn runs_real_process() {
        let result =
            ProcessRunner.run("sh", args(&["-c", "echo out; echo err >&2; exit 3"]), RunOptions::default()).await;
        assert_eq!((result.code, result.stdout.as_str(), result.stderr.as_str()), (Some(3), "out\n", "err\n"));
    }

    #[tokio::test]
    async fn merges_stderr_in_order() {
        let options = RunOptions { merge_stderr: true, ..Default::default() };
        let result = ProcessRunner.run("sh", args(&["-c", "echo 1; echo 2 >&2; echo 3"]), options).await;
        assert_eq!(result.stdout, "1\n2\n3\n");
        assert!(result.success());
    }

    #[tokio::test]
    async fn times_out_and_kills() {
        let options = RunOptions { timeout: Duration::from_millis(200), ..Default::default() };
        let started = std::time::Instant::now();
        let result = ProcessRunner.run("sleep", args(&["10"]), options).await;

        assert!(result.timed_out && !result.success());
        assert!(started.elapsed() < Duration::from_secs(5));
        assert!(result.error_message().contains("逾時"));
    }

    #[tokio::test]
    async fn reports_missing_binary() {
        let result = ProcessRunner.run("/nonexistent/binary", vec![], RunOptions::default()).await;
        assert!(!result.success() && result.stderr.contains("/nonexistent/binary"));
    }
}

//! 對已註冊的服務執行操作，依後端分派並彙整每個後端的結果。

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use thiserror::Error;

use crate::backend::Backends;
use crate::model::{Action, Backend, ValidationError};
use crate::registry::Registry;

#[derive(Debug, Error)]
pub enum ControlError {
    #[error("找不到服務 {0}")]
    NotFound(String),
    #[error(transparent)]
    Invalid(#[from] ValidationError),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackendResult {
    pub backend: Backend,
    pub target: String,
    pub success: bool,
    /// 失敗時是錯誤訊息；日誌則是日誌內容
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackendStatus {
    pub backend: Backend,
    pub target: String,
    pub active: String,
    pub enabled: String,
    pub error: Option<String>,
}

pub struct Controller {
    pub registry: Registry,
    backends: Backends,
    // 同一個服務的操作依序執行，避免 start / stop 互相競爭
    locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
}

impl Controller {
    pub fn new(registry: Registry, backends: Backends) -> Self {
        Self { registry, backends, locks: Mutex::default() }
    }

    fn targets(&self, name: &str, scope: Option<Backend>) -> Result<Vec<(Backend, String)>, ControlError> {
        let service = self.registry.get(name).ok_or_else(|| ControlError::NotFound(name.to_owned()))?;
        Ok(service.select(scope)?)
    }

    pub async fn control(
        &self,
        name: &str,
        action: Action,
        scope: Option<Backend>,
    ) -> Result<Vec<BackendResult>, ControlError> {
        let service = self.registry.get(name).ok_or_else(|| ControlError::NotFound(name.to_owned()))?;
        let targets = service.plan(action, scope)?;

        let lock = self.locks.lock().unwrap().entry(name.to_owned()).or_default().clone();
        let _guard = lock.lock().await;

        let mut results = Vec::with_capacity(targets.len());

        for (backend, target) in targets {
            let result = self.backends.control(backend, action, &target).await;
            let message = match (backend, result.success()) {
                // 腳本不論成功與否都回傳這次執行的輸出
                (Backend::Script, true) => result.stdout.trim().to_owned(),
                (Backend::Script, false) => [result.error_message(), result.stdout.trim().to_owned()]
                    .into_iter()
                    .filter(|s| !s.is_empty())
                    .collect::<Vec<_>>()
                    .join("\n\n"),
                (_, true) => String::new(),
                (_, false) => result.error_message(),
            };
            results.push(BackendResult { backend, target, success: result.success(), message });
        }

        Ok(results)
    }

    pub async fn status(&self, name: &str, scope: Option<Backend>) -> Result<Vec<BackendStatus>, ControlError> {
        let mut statuses = Vec::new();

        for (backend, target) in self.targets(name, scope)? {
            let state = self.backends.state(backend, &target).await;
            statuses.push(BackendStatus {
                backend,
                target,
                active: state.active,
                enabled: state.enabled,
                error: state.error,
            });
        }

        Ok(statuses)
    }

    pub async fn logs(
        &self,
        name: &str,
        scope: Option<Backend>,
        lines: u32,
    ) -> Result<Vec<BackendResult>, ControlError> {
        let mut logs = Vec::new();

        for (backend, target) in self.targets(name, scope)? {
            let result = self.backends.logs(backend, &target, lines).await;
            let message = if result.success() { result.stdout.trim().to_owned() } else { result.error_message() };
            let message = if message.is_empty() { "(無輸出)".to_owned() } else { message };
            logs.push(BackendResult { backend, target, success: result.success(), message });
        }

        Ok(logs)
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::backend::testing::{FakeRunner, fail, ok};
    use crate::backend::{DockerAdapter, ScriptAdapter, SystemdAdapter};
    use crate::model::Service;

    pub fn controller(dir: &tempfile::TempDir, systemd: Arc<FakeRunner>, docker: Arc<FakeRunner>) -> Controller {
        controller_with_script(dir, systemd, docker, Arc::default())
    }

    fn controller_with_script(
        dir: &tempfile::TempDir,
        systemd: Arc<FakeRunner>,
        docker: Arc<FakeRunner>,
        script: Arc<FakeRunner>,
    ) -> Controller {
        let registry = Registry::load(dir.path().join("services.toml")).unwrap();
        registry
            .merge(vec![
                Service::new("web", [(Backend::Systemd, "web".into()), (Backend::Docker, "web".into())]).unwrap(),
                Service::new("db", [(Backend::Docker, "postgres".into())]).unwrap(),
                Service::new(
                    "blog",
                    [
                        (Backend::Systemd, "blog-api".into()),
                        (Backend::Docker, "my-dashboard".into()),
                        (Backend::Script, "blog-restart".into()),
                    ],
                )
                .unwrap(),
            ])
            .unwrap();

        let backends = Backends {
            systemd: SystemdAdapter::new(systemd),
            docker: DockerAdapter::new(docker),
            script: ScriptAdapter::new(script),
        };
        Controller::new(registry, backends)
    }

    #[tokio::test]
    async fn restart_runs_script_and_reports_output() {
        let dir = tempfile::tempdir().unwrap();
        let script = Arc::new(FakeRunner::with_results([
            ok("LoadState=loaded\nActiveState=inactive\n"),
            fail(1, "", "Job failed."),
            ok("[停止] npm run build\n"),
        ]));
        let systemd = Arc::new(FakeRunner::default());
        let docker = Arc::new(FakeRunner::default());
        let c = controller_with_script(&dir, systemd.clone(), docker.clone(), script.clone());

        let results = c.control("blog", Action::Restart, None).await.unwrap();

        assert_eq!(results.len(), 1);
        assert_eq!((results[0].backend, results[0].success), (Backend::Script, false));
        assert_eq!(results[0].message, "Job failed.\n\n[停止] npm run build");
        assert!(systemd.calls().is_empty() && docker.calls().is_empty());

        // stop 仍然直接操作 systemd 與 docker，不會執行腳本
        c.control("blog", Action::Stop, None).await.unwrap();
        assert_eq!(script.calls().len(), 3);
        assert_eq!(docker.calls().len(), 1);
    }

    #[tokio::test]
    async fn control_order_and_results() {
        let dir = tempfile::tempdir().unwrap();
        let systemd = Arc::new(FakeRunner::with_results([fail(5, "", "Unit web.service not found.")]));
        let docker = Arc::new(FakeRunner::default());
        let c = controller(&dir, systemd.clone(), docker.clone());

        let results = c.control("web", Action::Start, None).await.unwrap();
        assert_eq!(
            results.iter().map(|r| (r.backend, r.success)).collect::<Vec<_>>(),
            [(Backend::Systemd, false), (Backend::Docker, true)]
        );
        assert_eq!(results[0].message, "Unit web.service not found.");

        // stop 先停 docker 再停 systemd
        let results = c.control("web", Action::Stop, None).await.unwrap();
        assert_eq!(results.iter().map(|r| r.backend).collect::<Vec<_>>(), [Backend::Docker, Backend::Systemd]);

        c.control("web", Action::Restart, Some(Backend::Docker)).await.unwrap();
        assert_eq!(docker.calls().last().unwrap(), &["docker", "restart", "--", "web"]);
        assert_eq!(systemd.calls().len(), 2);
    }

    #[tokio::test]
    async fn errors() {
        let dir = tempfile::tempdir().unwrap();
        let c = controller(&dir, Arc::default(), Arc::default());

        assert!(matches!(c.control("ghost", Action::Start, None).await, Err(ControlError::NotFound(_))));
        assert!(matches!(c.control("db", Action::Start, Some(Backend::Systemd)).await, Err(ControlError::Invalid(_))));
    }

    #[tokio::test]
    async fn logs_collect_each_backend() {
        let dir = tempfile::tempdir().unwrap();
        let systemd = Arc::new(FakeRunner::with_results([ok("api started\n")]));
        let docker = Arc::new(FakeRunner::with_results([fail(1, "", "No such container")]));
        let c = controller(&dir, systemd, docker);

        let logs = c.logs("web", None, 20).await.unwrap();
        assert_eq!(
            logs.iter().map(|l| (l.success, l.message.as_str())).collect::<Vec<_>>(),
            [(true, "api started"), (false, "No such container")]
        );
    }
}

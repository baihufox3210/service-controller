//! 服務模型與輸入驗證。所有來自 Discord 的名稱都必須經過這裡的檢查才能使用。

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Backend {
    Systemd,
    Docker,
    /// 主機上以 scripts/register-script.sh 註冊的腳本（systemd oneshot unit）
    Script,
}

impl Backend {
    pub fn as_str(self) -> &'static str {
        match self {
            Backend::Systemd => "systemd",
            Backend::Docker => "docker",
            Backend::Script => "script",
        }
    }
}

impl fmt::Display for Backend {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Start,
    Stop,
    Restart,
    Enable,
    Disable,
}

impl Action {
    /// 與 systemctl 子指令相同。
    pub fn as_str(self) -> &'static str {
        match self {
            Action::Start => "start",
            Action::Stop => "stop",
            Action::Restart => "restart",
            Action::Enable => "enable",
            Action::Disable => "disable",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Action::Start => "啟動",
            Action::Stop => "關閉",
            Action::Restart => "重啟",
            Action::Enable => "設定開機自動啟動",
            Action::Disable => "取消開機自動啟動",
        }
    }

    /// 關閉類操作以相反順序執行（先停 docker，再停 systemd）。
    pub fn runs_in_reverse(self) -> bool {
        matches!(self, Action::Stop | Action::Disable)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ValidationError {
    #[error("無效的服務名稱：{0:?}（僅限 A-Z a-z 0-9 _ -，最多 32 字）")]
    InvalidName(String),
    #[error("無效的 {backend} 名稱：{target:?}")]
    InvalidTarget { backend: Backend, target: String },
    #[error("不支援的 systemd unit 類型：.{0}（僅限 service、socket、timer）")]
    UnsupportedUnitType(String),
    #[error("服務 {0} 至少需要設定 systemd、docker、script 其中之一")]
    NoTargets(String),
    #[error("服務 {service} 未設定 {backend}")]
    NotConfigured { service: String, backend: Backend },
    #[error("腳本只支援 restart")]
    ScriptActionUnsupported,
    #[error("服務 {0} 只設定了腳本，腳本只支援 restart")]
    OnlyScript(String),
}

const MAX_NAME_LEN: usize = 32;
const MAX_UNIT_LEN: usize = 256;
const MAX_CONTAINER_LEN: usize = 128;

/// 允許操作的 systemd unit 類型；未寫副檔名時預設為 .service。
const SYSTEMD_SUFFIXES: [&str; 3] = ["service", "socket", "timer"];
/// 其他 systemd unit 類型一律拒絕，不會被補上 .service。
const OTHER_SYSTEMD_SUFFIXES: [&str; 8] = ["target", "mount", "automount", "swap", "path", "slice", "scope", "device"];

fn is_valid_name(name: &str) -> bool {
    (1..=MAX_NAME_LEN).contains(&name.len())
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

pub fn validate_name(name: &str) -> Result<(), ValidationError> {
    if is_valid_name(name) { Ok(()) } else { Err(ValidationError::InvalidName(name.to_owned())) }
}

/// 腳本名稱只能引用主機上註冊過的名稱，格式與服務名稱相同，且必須以英數開頭。
fn is_valid_script(name: &str) -> bool {
    is_valid_name(name) && name.bytes().next().is_some_and(|b| b.is_ascii_alphanumeric())
}

/// 所有 target 都必須以英數字開頭，避免被命令列工具當成選項（例如 `--help`）。
fn is_valid_unit(unit: &str) -> bool {
    let Some((base, suffix)) = unit.rsplit_once('.') else { return false };

    unit.len() <= MAX_UNIT_LEN
        && SYSTEMD_SUFFIXES.contains(&suffix)
        && base.bytes().next().is_some_and(|b| b.is_ascii_alphanumeric())
        && base.bytes().all(|b| b.is_ascii_alphanumeric() || b":_.@-\\".contains(&b))
}

fn is_valid_container(name: &str) -> bool {
    (1..=MAX_CONTAINER_LEN).contains(&name.len())
        && name.bytes().next().is_some_and(|b| b.is_ascii_alphanumeric())
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}

/// 檢查已正規化的 target（給執行指令前的最後一道防線使用）。
pub fn validate_target(backend: Backend, target: &str) -> Result<(), ValidationError> {
    let valid = match backend {
        Backend::Systemd => is_valid_unit(target),
        Backend::Docker => is_valid_container(target),
        Backend::Script => is_valid_script(target),
    };

    if valid { Ok(()) } else { Err(ValidationError::InvalidTarget { backend, target: target.to_owned() }) }
}

/// systemd unit 沒有寫副檔名時補上 .service，並驗證格式。
pub fn normalize_target(backend: Backend, raw: &str) -> Result<String, ValidationError> {
    let invalid = || ValidationError::InvalidTarget { backend, target: raw.to_owned() };

    let target = match backend {
        Backend::Docker | Backend::Script => raw.to_owned(),
        Backend::Systemd => match raw.rsplit_once('.').map(|(_, suffix)| suffix) {
            Some(suffix) if OTHER_SYSTEMD_SUFFIXES.contains(&suffix) => {
                return Err(ValidationError::UnsupportedUnitType(suffix.to_owned()));
            }
            Some(suffix) if SYSTEMD_SUFFIXES.contains(&suffix) => raw.to_owned(),
            _ => format!("{raw}.service"),
        },
    };

    validate_target(backend, &target).map_err(|_| invalid())?;

    Ok(target)
}

/// 一個服務可以同時對應 systemd unit 與 Docker 容器。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Service {
    name: String,
    // BTreeMap 依 Backend 排序：systemd、docker、script
    targets: BTreeMap<Backend, String>,
}

impl Service {
    pub fn new(name: &str, targets: impl IntoIterator<Item = (Backend, String)>) -> Result<Self, ValidationError> {
        validate_name(name)?;

        let targets = targets
            .into_iter()
            .map(|(backend, raw)| normalize_target(backend, &raw).map(|target| (backend, target)))
            .collect::<Result<BTreeMap<_, _>, _>>()?;

        if targets.is_empty() {
            return Err(ValidationError::NoTargets(name.to_owned()));
        }

        Ok(Self { name: name.to_owned(), targets })
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn targets(&self) -> &BTreeMap<Backend, String> {
        &self.targets
    }

    /// `scope` 為 `None` 代表全部已設定的後端。
    pub fn select(&self, scope: Option<Backend>) -> Result<Vec<(Backend, String)>, ValidationError> {
        match scope {
            None => Ok(self.targets.iter().map(|(b, t)| (*b, t.clone())).collect()),
            Some(backend) => self
                .targets
                .get(&backend)
                .map(|target| vec![(backend, target.clone())])
                .ok_or_else(|| ValidationError::NotConfigured { service: self.name.clone(), backend }),
        }
    }

    /// 決定一個操作要對哪些後端執行。
    ///
    /// 有設定腳本時，`restart`（未指定後端）只執行腳本，由腳本負責完整的重啟流程；
    /// 其他操作不會動到腳本，腳本也不能用 restart 以外的操作執行。
    pub fn plan(&self, action: Action, scope: Option<Backend>) -> Result<Vec<(Backend, String)>, ValidationError> {
        let mut targets = self.select(scope)?;

        match scope {
            Some(Backend::Script) if action != Action::Restart => return Err(ValidationError::ScriptActionUnsupported),
            Some(_) => {}
            None => {
                let has_script = self.targets.contains_key(&Backend::Script);
                targets
                    .retain(|(backend, _)| (*backend == Backend::Script) == (has_script && action == Action::Restart));

                if targets.is_empty() {
                    return Err(ValidationError::OnlyScript(self.name.clone()));
                }
            }
        }

        if action.runs_in_reverse() {
            targets.reverse();
        }

        Ok(targets)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn systemd_suffix_defaults_to_service() {
        for (raw, expected) in [
            ("nginx", "nginx.service"),
            ("nginx.service", "nginx.service"),
            ("backup.timer", "backup.timer"),
            ("docker.socket", "docker.socket"),
            ("getty@tty1", "getty@tty1.service"),
            ("baihu-blog-api", "baihu-blog-api.service"),
        ] {
            assert_eq!(normalize_target(Backend::Systemd, raw).unwrap(), expected);
        }
    }

    #[test]
    fn rejects_dangerous_or_unsupported_targets() {
        for raw in ["-H", "--help", "a b", "nginx;rm", "$(id)", "../x", "", "x\n.service", "ｎｇｉｎｘ"] {
            assert!(normalize_target(Backend::Systemd, raw).is_err(), "{raw:?}");
        }

        for raw in ["multi-user.target", "home.mount"] {
            assert!(matches!(normalize_target(Backend::Systemd, raw), Err(ValidationError::UnsupportedUnitType(_))));
        }

        for raw in ["-v", "--privileged", "a/b", "a b", "", "x;y", &"a".repeat(129)] {
            assert!(normalize_target(Backend::Docker, raw).is_err(), "{raw:?}");
        }
    }

    #[test]
    fn invalid_target_error_shows_original_input() {
        let err = normalize_target(Backend::Systemd, "a b").unwrap_err();
        assert_eq!(err, ValidationError::InvalidTarget { backend: Backend::Systemd, target: "a b".into() });
    }

    #[test]
    fn script_names_are_strict() {
        assert_eq!(normalize_target(Backend::Script, "blog-restart").unwrap(), "blog-restart");

        for raw in ["-x", "_x", "../x", "/home/baihu/bin/x.sh", "x.sh", "x y", "x;id", "$(id)", "", &"a".repeat(33)] {
            assert!(normalize_target(Backend::Script, raw).is_err(), "{raw:?}");
        }
    }

    #[test]
    fn plan_runs_script_only_for_restart() {
        let all = [
            (Backend::Systemd, "api".to_owned()),
            (Backend::Docker, "web".to_owned()),
            (Backend::Script, "blog-restart".to_owned()),
        ];
        let service = Service::new("blog", all).unwrap();
        let backends = |plan: Vec<(Backend, String)>| plan.into_iter().map(|(b, _)| b).collect::<Vec<_>>();

        assert_eq!(backends(service.plan(Action::Restart, None).unwrap()), [Backend::Script]);
        assert_eq!(backends(service.plan(Action::Stop, None).unwrap()), [Backend::Docker, Backend::Systemd]);
        assert_eq!(backends(service.plan(Action::Start, None).unwrap()), [Backend::Systemd, Backend::Docker]);
        assert_eq!(backends(service.plan(Action::Restart, Some(Backend::Docker)).unwrap()), [Backend::Docker]);
        assert_eq!(backends(service.plan(Action::Restart, Some(Backend::Script)).unwrap()), [Backend::Script]);
        assert_eq!(service.plan(Action::Stop, Some(Backend::Script)), Err(ValidationError::ScriptActionUnsupported));

        let script_only = Service::new("deploy", [(Backend::Script, "deploy".to_owned())]).unwrap();
        assert_eq!(script_only.plan(Action::Stop, None), Err(ValidationError::OnlyScript("deploy".into())));

        let no_script = Service::new("db", [(Backend::Docker, "pg".to_owned())]).unwrap();
        assert_eq!(backends(no_script.plan(Action::Restart, None).unwrap()), [Backend::Docker]);
    }

    #[test]
    fn service_validation_and_ordering() {
        let service =
            Service::new("blog", [(Backend::Docker, "web".into()), (Backend::Systemd, "api".into())]).unwrap();

        assert_eq!(service.targets().keys().copied().collect::<Vec<_>>(), [Backend::Systemd, Backend::Docker]);
        assert_eq!(service.select(Some(Backend::Docker)).unwrap(), [(Backend::Docker, "web".to_owned())]);

        assert!(Service::new("bad name", [(Backend::Docker, "web".into())]).is_err());
        assert!(Service::new(&"x".repeat(33), [(Backend::Docker, "web".into())]).is_err());
        assert_eq!(Service::new("empty", []), Err(ValidationError::NoTargets("empty".into())));

        let only_docker = Service::new("db", [(Backend::Docker, "pg".into())]).unwrap();
        assert!(matches!(only_docker.select(Some(Backend::Systemd)), Err(ValidationError::NotConfigured { .. })));
    }
}

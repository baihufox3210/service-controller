//! 權限模型：擁有者 > 管理員 > 操作者 > 檢視者，預設拒絕。
//!
//! 擁有者來自設定檔；其餘授權由 `/access` 指令管理，存在狀態目錄的 permissions.toml。
//! 授權可以是全域，或只針對單一服務（最高到操作者）。

use std::collections::{BTreeMap, HashSet};
use std::io;
use std::path::PathBuf;
use std::sync::RwLock;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::store;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Permission {
    None,
    /// list / status / logs
    Viewer,
    /// start / stop / restart / enable / disable
    Operator,
    /// 管理服務設定、授予操作者與檢視者
    Admin,
    /// 由設定檔指定，可管理管理員
    Owner,
}

impl Permission {
    pub fn label(self) -> &'static str {
        match self {
            Permission::None => "無",
            Permission::Viewer => "檢視者",
            Permission::Operator => "操作者",
            Permission::Admin => "管理員",
            Permission::Owner => "擁有者",
        }
    }

    /// 管理此等級的授權所需的權限：管理員只能由擁有者新增或移除。
    pub fn required_to_manage(self) -> Permission {
        if self >= Permission::Admin { Permission::Owner } else { Permission::Admin }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Subject {
    User,
    Role,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    #[serde(rename = "type")]
    pub subject: Subject,
    pub id: u64,
    pub level: Permission,
    /// `None` 代表全域
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service: Option<String>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum GrantError {
    #[error("無法授予「{0}」")]
    NotGrantable(&'static str),
    #[error("單一服務最高只能授予操作者")]
    ServiceLevelTooHigh,
}

impl Grant {
    pub fn new(subject: Subject, id: u64, level: Permission, service: Option<String>) -> Result<Self, GrantError> {
        if !matches!(level, Permission::Viewer | Permission::Operator | Permission::Admin) {
            return Err(GrantError::NotGrantable(level.label()));
        }

        if service.is_some() && level > Permission::Operator {
            return Err(GrantError::ServiceLevelTooHigh);
        }

        Ok(Self { subject, id, level, service })
    }

    pub fn mention(&self) -> String {
        match self.subject {
            Subject::User => format!("<@{}>", self.id),
            Subject::Role => format!("<@&{}>", self.id),
        }
    }

    fn key(&self) -> GrantKey {
        (self.subject, self.id, self.service.clone())
    }
}

type GrantKey = (Subject, u64, Option<String>);

#[derive(Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GrantFile {
    #[serde(default)]
    grants: Vec<Grant>,
}

pub struct Policy {
    path: PathBuf,
    owners: RwLock<HashSet<u64>>,
    allowed_guilds: HashSet<u64>,
    grants: RwLock<BTreeMap<GrantKey, Grant>>,
}

impl Policy {
    pub fn load(path: PathBuf, owners: HashSet<u64>, allowed_guilds: HashSet<u64>) -> Result<Self, PolicyError> {
        let grants = match store::read_optional(&path)? {
            Some(text) => toml::from_str::<GrantFile>(&text)
                .map_err(|e| PolicyError::Invalid(e.message().to_owned()))?
                .grants
                .into_iter()
                // 檔案可能被手動修改過，重新驗證每一筆授權
                .map(|g| {
                    Grant::new(g.subject, g.id, g.level, g.service).map_err(|e| PolicyError::Invalid(e.to_string()))
                })
                .map(|g| g.map(|g| (g.key(), g)))
                .collect::<Result<_, _>>()?,
            None => BTreeMap::new(),
        };

        Ok(Self { path, owners: RwLock::new(owners), allowed_guilds, grants: RwLock::new(grants) })
    }

    pub fn owners(&self) -> Vec<u64> {
        let mut owners: Vec<_> = self.owners.read().unwrap().iter().copied().collect();
        owners.sort_unstable();
        owners
    }

    pub fn has_owners(&self) -> bool {
        !self.owners.read().unwrap().is_empty()
    }

    pub fn add_owners(&self, ids: impl IntoIterator<Item = u64>) {
        self.owners.write().unwrap().extend(ids);
    }

    pub fn guild_allowed(&self, guild: Option<u64>) -> bool {
        guild.is_some_and(|g| self.allowed_guilds.is_empty() || self.allowed_guilds.contains(&g))
    }

    /// `service` 有值時會一併套用該服務的單獨授權；取最高權限。
    pub fn level_for(&self, user: u64, roles: &[u64], guild: Option<u64>, service: Option<&str>) -> Permission {
        if !self.guild_allowed(guild) {
            return Permission::None;
        }

        if self.owners.read().unwrap().contains(&user) {
            return Permission::Owner;
        }

        self.grants
            .read()
            .unwrap()
            .values()
            .filter(|g| match g.subject {
                Subject::User => g.id == user,
                Subject::Role => roles.contains(&g.id),
            })
            .filter(|g| g.service.is_none() || g.service.as_deref() == service)
            .map(|g| g.level)
            .max()
            .unwrap_or(Permission::None)
    }

    pub fn get(&self, subject: Subject, id: u64, service: Option<&str>) -> Option<Grant> {
        self.grants.read().unwrap().get(&(subject, id, service.map(str::to_owned))).cloned()
    }

    /// `service` 為 `None` 時列出全部授權。
    pub fn list(&self, service: Option<&str>) -> Vec<Grant> {
        self.grants
            .read()
            .unwrap()
            .values()
            .filter(|g| service.is_none() || g.service.as_deref() == service)
            .cloned()
            .collect()
    }

    pub fn set(&self, grant: Grant) -> io::Result<()> {
        self.update(|grants| {
            grants.insert(grant.key(), grant);
        })
    }

    pub fn remove(&self, subject: Subject, id: u64, service: Option<&str>) -> io::Result<Option<Grant>> {
        let mut removed = None;
        self.update(|grants| removed = grants.remove(&(subject, id, service.map(str::to_owned))))?;
        Ok(removed)
    }

    /// 服務被移除時，一併刪除它的單獨授權。
    pub fn remove_services(&self, services: &[String]) -> io::Result<()> {
        self.update(|grants| grants.retain(|_, g| g.service.as_ref().is_none_or(|s| !services.contains(s))))
    }

    fn update(&self, change: impl FnOnce(&mut BTreeMap<GrantKey, Grant>)) -> io::Result<()> {
        let mut grants = self.grants.write().unwrap();
        let mut next = grants.clone();
        change(&mut next);

        if next != *grants {
            let file = GrantFile { grants: next.values().cloned().collect() };
            store::write_atomic(&self.path, &toml::to_string(&file).expect("grants always serialize"), true)?;
            *grants = next;
        }

        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum PolicyError {
    #[error("無法讀取權限設定：{0}")]
    Io(#[from] io::Error),
    #[error("權限設定格式錯誤：{0}")]
    Invalid(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    const GUILD: Option<u64> = Some(100);
    const OWNER: u64 = 1;
    const ADMIN: u64 = 2;
    const WEB_OPERATOR: u64 = 4;
    const VIEWER_ROLE: u64 = 30;

    fn policy(dir: &tempfile::TempDir) -> Policy {
        let policy = Policy::load(dir.path().join("permissions.toml"), [OWNER].into(), [100].into()).unwrap();
        policy.set(Grant::new(Subject::User, ADMIN, Permission::Admin, None).unwrap()).unwrap();
        policy.set(Grant::new(Subject::User, WEB_OPERATOR, Permission::Operator, Some("web".into())).unwrap()).unwrap();
        policy.set(Grant::new(Subject::Role, VIEWER_ROLE, Permission::Viewer, None).unwrap()).unwrap();
        policy
    }

    #[test]
    fn levels() {
        let dir = tempfile::tempdir().unwrap();
        let p = policy(&dir);

        assert_eq!(p.level_for(OWNER, &[], GUILD, None), Permission::Owner);
        assert_eq!(p.level_for(ADMIN, &[], GUILD, Some("web")), Permission::Admin);
        assert_eq!(p.level_for(WEB_OPERATOR, &[], GUILD, Some("web")), Permission::Operator);
        assert_eq!(p.level_for(WEB_OPERATOR, &[], GUILD, Some("db")), Permission::None);
        assert_eq!(p.level_for(WEB_OPERATOR, &[], GUILD, None), Permission::None); // 單一服務授權不變成全域
        assert_eq!(p.level_for(WEB_OPERATOR, &[VIEWER_ROLE], GUILD, Some("db")), Permission::Viewer);
        assert_eq!(p.level_for(9, &[], GUILD, None), Permission::None); // 預設拒絕
        assert_eq!(p.level_for(OWNER, &[], Some(200), None), Permission::None); // 其他伺服器
        assert_eq!(p.level_for(OWNER, &[], None, None), Permission::None); // 私訊
    }

    #[test]
    fn persists_and_removes() {
        let dir = tempfile::tempdir().unwrap();
        let p = policy(&dir);

        p.remove_services(&["web".into()]).unwrap();
        assert_eq!(p.remove(Subject::Role, VIEWER_ROLE, None).unwrap().unwrap().level, Permission::Viewer);

        let reloaded = Policy::load(dir.path().join("permissions.toml"), HashSet::new(), HashSet::new()).unwrap();
        assert_eq!(reloaded.list(None), vec![Grant::new(Subject::User, ADMIN, Permission::Admin, None).unwrap()]);
    }

    #[test]
    fn grant_rules() {
        assert_eq!(Grant::new(Subject::User, 1, Permission::Owner, None), Err(GrantError::NotGrantable("擁有者")));
        assert_eq!(
            Grant::new(Subject::User, 1, Permission::Admin, Some("web".into())),
            Err(GrantError::ServiceLevelTooHigh)
        );
        assert_eq!(Permission::Admin.required_to_manage(), Permission::Owner);
        assert_eq!(Permission::Operator.required_to_manage(), Permission::Admin);
    }

    #[test]
    fn rejects_tampered_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("permissions.toml");
        std::fs::write(&path, "[[grants]]\ntype = \"user\"\nid = 1\nlevel = \"owner\"\n").unwrap();

        assert!(Policy::load(path, HashSet::new(), HashSet::new()).is_err());
    }
}

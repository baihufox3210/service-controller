//! 已註冊服務的清單，儲存在狀態目錄的 services.toml。

use std::collections::BTreeMap;
use std::io;
use std::path::PathBuf;
use std::sync::RwLock;

use crate::config_text::{self, ConfigError};
use crate::model::Service;
use crate::store;

/// 套用設定後的變化，用來回報給使用者。
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Diff {
    pub added: Vec<String>,
    pub changed: Vec<String>,
    pub removed: Vec<String>,
}

impl Diff {
    pub fn is_empty(&self) -> bool {
        self.added.is_empty() && self.changed.is_empty() && self.removed.is_empty()
    }
}

pub struct Registry {
    path: PathBuf,
    services: RwLock<BTreeMap<String, Service>>,
}

impl Registry {
    pub fn load(path: PathBuf) -> Result<Self, LoadError> {
        let services = match store::read_optional(&path)? {
            Some(text) if !text.trim().is_empty() => {
                config_text::parse_services(&text)?.into_iter().map(|s| (s.name().to_owned(), s)).collect()
            }
            _ => BTreeMap::new(),
        };

        Ok(Self { path, services: RwLock::new(services) })
    }

    pub fn get(&self, name: &str) -> Option<Service> {
        self.services.read().unwrap().get(name).cloned()
    }

    pub fn all(&self) -> Vec<Service> {
        self.services.read().unwrap().values().cloned().collect()
    }

    pub fn render(&self) -> String {
        config_text::render_services(self.services.read().unwrap().values())
    }

    /// 新增或更新列出的服務，其他服務維持不變。
    pub fn merge(&self, incoming: Vec<Service>) -> io::Result<Diff> {
        self.update(|current| {
            let mut next = current.clone();
            next.extend(incoming.into_iter().map(|s| (s.name().to_owned(), s)));
            next
        })
    }

    /// 以新設定完整取代目前的清單。
    pub fn replace(&self, incoming: Vec<Service>) -> io::Result<Diff> {
        self.update(|_| incoming.into_iter().map(|s| (s.name().to_owned(), s)).collect())
    }

    pub fn remove(&self, names: &[String]) -> io::Result<Diff> {
        self.update(|current| {
            let mut next = current.clone();
            next.retain(|name, _| !names.contains(name));
            next
        })
    }

    fn update(&self, build: impl FnOnce(&BTreeMap<String, Service>) -> BTreeMap<String, Service>) -> io::Result<Diff> {
        let mut services = self.services.write().unwrap();
        let next = build(&services);
        let diff = diff(&services, &next);

        if !diff.is_empty() {
            // 先寫入檔案，成功後才更新記憶體中的資料
            store::write_atomic(&self.path, &config_text::render_services(next.values()), true)?;
            *services = next;
        }

        Ok(diff)
    }
}

fn diff(before: &BTreeMap<String, Service>, after: &BTreeMap<String, Service>) -> Diff {
    let mut diff = Diff::default();

    for (name, service) in after {
        match before.get(name) {
            None => diff.added.push(name.clone()),
            Some(old) if old != service => diff.changed.push(name.clone()),
            _ => {}
        }
    }

    diff.removed = before.keys().filter(|name| !after.contains_key(*name)).cloned().collect();
    diff
}

#[derive(Debug, thiserror::Error)]
pub enum LoadError {
    #[error("無法讀取服務清單：{0}")]
    Io(#[from] io::Error),
    #[error("服務清單格式錯誤：{0}")]
    Invalid(#[from] ConfigError),
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Backend;

    fn service(name: &str, container: &str) -> Service {
        Service::new(name, [(Backend::Docker, container.to_owned())]).unwrap()
    }

    #[test]
    fn merge_replace_remove_persist() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("services.toml");
        let registry = Registry::load(path.clone()).unwrap();

        let diff = registry.merge(vec![service("a", "a1"), service("b", "b1")]).unwrap();
        assert_eq!(diff.added, ["a", "b"]);

        let diff = registry.merge(vec![service("b", "b2"), service("c", "c1")]).unwrap();
        assert_eq!((diff.added, diff.changed, diff.removed.len()), (vec!["c".to_owned()], vec!["b".to_owned()], 0));

        assert!(registry.merge(vec![service("c", "c1")]).unwrap().is_empty());

        let diff = registry.replace(vec![service("a", "a1")]).unwrap();
        assert_eq!(diff.removed, ["b", "c"]);

        let diff = registry.remove(&["a".to_owned(), "ghost".to_owned()]).unwrap();
        assert_eq!(diff.removed, ["a"]);

        registry.merge(vec![service("z", "z1")]).unwrap();
        assert_eq!(Registry::load(path).unwrap().all(), vec![service("z", "z1")]);
    }

    #[test]
    fn invalid_state_file_fails_loudly() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("services.toml");
        std::fs::write(&path, "[x]\ndocker = \"--rm\"\n").unwrap();

        assert!(matches!(Registry::load(path), Err(LoadError::Invalid(_))));
    }
}

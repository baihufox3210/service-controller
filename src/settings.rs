//! 從環境變數讀取設定（由 systemd 的 EnvironmentFile 提供）。

use std::collections::HashSet;
use std::env;
use std::path::PathBuf;

use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum SettingsError {
    #[error("未設定 DISCORD_TOKEN")]
    MissingToken,
    #[error("{key} 包含無效的 ID：{value:?}")]
    InvalidId { key: &'static str, value: String },
    #[error("COMMAND_PREFIX 必須是 1 到 5 個非空白字元")]
    InvalidPrefix,
}

#[derive(Debug, Clone)]
pub struct Settings {
    pub token: String,
    pub owners: HashSet<u64>,
    pub allowed_guilds: HashSet<u64>,
    pub prefix: String,
    pub state_dir: PathBuf,
}

impl Settings {
    pub fn from_env() -> Result<Self, SettingsError> {
        Self::from_lookup(|key| env::var(key).ok())
    }

    pub fn from_lookup(get: impl Fn(&str) -> Option<String>) -> Result<Self, SettingsError> {
        let token = get("DISCORD_TOKEN").filter(|t| !t.trim().is_empty()).ok_or(SettingsError::MissingToken)?;

        let prefix = get("COMMAND_PREFIX").unwrap_or_else(|| "!".to_owned());
        if !(1..=5).contains(&prefix.chars().count()) || prefix.chars().any(char::is_whitespace) {
            return Err(SettingsError::InvalidPrefix);
        }

        Ok(Self {
            token: token.trim().to_owned(),
            owners: parse_ids("OWNER_USER_IDS", get("OWNER_USER_IDS"))?,
            allowed_guilds: parse_ids("ALLOWED_GUILD_IDS", get("ALLOWED_GUILD_IDS"))?,
            prefix,
            // systemd 的 StateDirectory= 會設定 STATE_DIRECTORY
            state_dir: get("STATE_DIRECTORY")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/var/lib/service-controller")),
        })
    }
}

fn parse_ids(key: &'static str, raw: Option<String>) -> Result<HashSet<u64>, SettingsError> {
    raw.unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.parse::<u64>().map_err(|_| SettingsError::InvalidId { key, value: s.to_owned() }))
        .collect()
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    fn load(pairs: &[(&str, &str)]) -> Result<Settings, SettingsError> {
        let map: HashMap<_, _> = pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        Settings::from_lookup(|key| map.get(key).cloned())
    }

    #[test]
    fn parses() {
        let s =
            load(&[("DISCORD_TOKEN", " abc "), ("OWNER_USER_IDS", "1, 2,,3"), ("STATE_DIRECTORY", "/tmp/x")]).unwrap();

        assert_eq!(s.token, "abc");
        assert_eq!(s.owners, [1, 2, 3].into());
        assert!(s.allowed_guilds.is_empty());
        assert_eq!(s.prefix, "!");
        assert_eq!(s.state_dir, PathBuf::from("/tmp/x"));
    }

    #[test]
    fn rejects_invalid() {
        assert_eq!(load(&[]).unwrap_err(), SettingsError::MissingToken);
        assert!(matches!(
            load(&[("DISCORD_TOKEN", "x"), ("OWNER_USER_IDS", "12a")]),
            Err(SettingsError::InvalidId { .. })
        ));
        assert!(matches!(
            load(&[("DISCORD_TOKEN", "x"), ("ALLOWED_GUILD_IDS", "-1")]),
            Err(SettingsError::InvalidId { .. })
        ));
        assert_eq!(
            load(&[("DISCORD_TOKEN", "x"), ("COMMAND_PREFIX", "a b")]).unwrap_err(),
            SettingsError::InvalidPrefix
        );
    }
}

//! 解析 Discord 訊息中的 TOML 設定區塊。
//!
//! 輸入來自使用者，因此：只接受一個程式碼區塊、限制大小與數量、TOML 欄位必須完全符合
//! （未知欄位直接拒絕），每個名稱都經過 [`crate::model`] 的白名單驗證。解析結果只會被
//! 當成資料使用，從不經過 shell。

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::model::{Backend, Service, ValidationError};

pub const MAX_CONFIG_BYTES: usize = 16 * 1024;
pub const MAX_SERVICES: usize = 100;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ConfigError {
    #[error("找不到設定區塊，請把 TOML 放在 ``` 程式碼區塊中")]
    MissingBlock,
    #[error("只能包含一個程式碼區塊")]
    MultipleBlocks,
    #[error("程式碼區塊沒有結束的 ```")]
    UnclosedBlock,
    #[error("程式碼區塊的語言標記只能是 toml 或留空，目前為 {0:?}")]
    UnsupportedLanguage(String),
    #[error("設定內容過大（上限 {MAX_CONFIG_BYTES} bytes）")]
    TooLarge,
    #[error("服務數量過多（上限 {MAX_SERVICES} 個）")]
    TooManyServices,
    #[error("設定中沒有任何服務")]
    Empty,
    #[error("TOML 格式錯誤：{0}")]
    Syntax(String),
    #[error(transparent)]
    Invalid(#[from] ValidationError),
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    #[serde(skip_serializing_if = "Option::is_none")]
    systemd: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    docker: Option<String>,
}

/// 從整則訊息中取出唯一的 ``` 程式碼區塊內容。
pub fn extract_code_block(message: &str) -> Result<&str, ConfigError> {
    let start = message.find("```").ok_or(ConfigError::MissingBlock)?;
    let rest = &message[start + 3..];
    let end = rest.find("```").ok_or(ConfigError::UnclosedBlock)?;

    if rest[end + 3..].contains("```") {
        return Err(ConfigError::MultipleBlocks);
    }

    let block = &rest[..end];

    // 第一行若不是空白，就是語言標記
    let (language, body) = block.split_once('\n').unwrap_or((block, ""));
    let language = language.trim();

    if !language.is_empty() && !language.eq_ignore_ascii_case("toml") {
        return Err(ConfigError::UnsupportedLanguage(language.chars().take(20).collect()));
    }

    Ok(body)
}

/// 解析並驗證設定，回傳依名稱排序的服務。
pub fn parse_services(text: &str) -> Result<Vec<Service>, ConfigError> {
    if text.len() > MAX_CONFIG_BYTES {
        return Err(ConfigError::TooLarge);
    }

    let entries: BTreeMap<String, Entry> =
        toml::from_str(text).map_err(|e| ConfigError::Syntax(e.message().to_owned()))?;

    if entries.is_empty() {
        return Err(ConfigError::Empty);
    }

    if entries.len() > MAX_SERVICES {
        return Err(ConfigError::TooManyServices);
    }

    entries
        .into_iter()
        .map(|(name, entry)| {
            let targets = [(Backend::Systemd, entry.systemd), (Backend::Docker, entry.docker)]
                .into_iter()
                .filter_map(|(backend, target)| target.map(|t| (backend, t)));

            Service::new(&name, targets).map_err(ConfigError::from)
        })
        .collect()
}

pub fn render_services<'a>(services: impl IntoIterator<Item = &'a Service>) -> String {
    let entries: BTreeMap<&str, Entry> = services
        .into_iter()
        .map(|service| {
            let targets = service.targets();
            let entry = Entry {
                systemd: targets.get(&Backend::Systemd).cloned(),
                docker: targets.get(&Backend::Docker).cloned(),
            };
            (service.name(), entry)
        })
        .collect();

    toml::to_string(&entries).expect("services always serialize")
}

#[cfg(test)]
mod tests {
    use super::*;

    const MESSAGE: &str = "!services set\n```toml\n[blog]\nsystemd = \"baihu-blog-api\"\ndocker = \"my-dashboard\"\n\n[mem0]\ndocker = \"mem0-dev-mem0-1\"\n```";

    #[test]
    fn parses_message_with_code_block() {
        let services = parse_services(extract_code_block(MESSAGE).unwrap()).unwrap();

        assert_eq!(services.len(), 2);
        assert_eq!(services[0].name(), "blog");
        assert_eq!(services[0].targets()[&Backend::Systemd], "baihu-blog-api.service");
        assert_eq!(services[1].targets().get(&Backend::Systemd), None);
    }

    #[test]
    fn render_round_trips() {
        let services = parse_services(extract_code_block(MESSAGE).unwrap()).unwrap();
        let rendered = render_services(&services);

        assert_eq!(parse_services(&rendered).unwrap(), services);
        assert!(rendered.contains("systemd = \"baihu-blog-api.service\""));
    }

    #[test]
    fn code_block_rules() {
        assert_eq!(extract_code_block("!services set"), Err(ConfigError::MissingBlock));
        assert_eq!(extract_code_block("```toml\n[a]"), Err(ConfigError::UnclosedBlock));
        assert_eq!(extract_code_block("```\n[a]\n```\n```\n[b]\n```"), Err(ConfigError::MultipleBlocks));
        assert!(matches!(extract_code_block("```sh\nrm -rf /\n```"), Err(ConfigError::UnsupportedLanguage(_))));
        assert_eq!(extract_code_block("```\n[a]\ndocker = \"x\"\n```").unwrap(), "[a]\ndocker = \"x\"\n");
    }

    #[test]
    fn rejects_malicious_or_malformed_config() {
        let cases = [
            "[blog]\nsystemd = \"x; rm -rf /\"",
            "[blog]\ndocker = \"--privileged\"",
            "[blog]\nsystemd = \"multi-user.target\"",
            "[blog]\ncommand = \"sh -c id\"", // 未知欄位
            "[blog]\nsystemd = 1",            // 型別錯誤
            "[\"bad name\"]\ndocker = \"x\"",
            "[\"<@&123>\"]\ndocker = \"x\"",         // mention 注入
            "[blog]",                                // 沒有任何後端
            "[blog]\n[blog.nested]\ndocker = \"x\"", // 巢狀表格
            "not toml",
            "",
        ];

        for case in cases {
            assert!(parse_services(case).is_err(), "should reject {case:?}");
        }
    }

    #[test]
    fn enforces_limits() {
        assert_eq!(parse_services(&"#".repeat(MAX_CONFIG_BYTES + 1)), Err(ConfigError::TooLarge));

        let many: String = (0..=MAX_SERVICES).map(|i| format!("[s{i}]\ndocker = \"c{i}\"\n")).collect();
        assert_eq!(parse_services(&many), Err(ConfigError::TooManyServices));
    }
}

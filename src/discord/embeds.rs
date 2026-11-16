//! Embed 輸出。以顏色條表示狀態：綠色成功、黃色部分失敗或已停止、紅色失敗、藍色資訊。

use poise::serenity_prelude::{CreateEmbed, CreateEmbedAuthor, CreateEmbedFooter};

use crate::controller::{BackendResult, BackendStatus};
use crate::model::{Backend, Service};

pub const SUCCESS: u32 = 0x2ECC71;
pub const WARNING: u32 = 0xF1C40F;
pub const FAILURE: u32 = 0xE74C3C;
pub const INFO: u32 = 0x5865F2;

// Discord 限制：embed 描述 4096 字、欄位 1024 字、單則訊息所有 embed 合計 6000 字
const DESCRIPTION_LIMIT: usize = 4096;
const FIELD_LIMIT: usize = 1024;
const MESSAGE_LIMIT: usize = 6000;

/// 包成程式碼區塊，避免內容提前關閉區塊；只保留最後 `limit` 個字元。
pub fn code_block(text: &str, limit: usize) -> String {
    let text = strip_ansi(text);
    let text = if text.trim().is_empty() { "(無輸出)".to_owned() } else { text.replace("```", "`\u{200b}``") };
    format!("```\n{}\n```", tail(&text, limit).0)
}

/// 移除終端機顏色等 ANSI 控制碼（腳本輸出常帶有這些字元）。
pub fn strip_ansi(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();

    while let Some(c) = chars.next() {
        if c != '\u{1b}' {
            out.push(c);
            continue;
        }

        // CSI：ESC [ 參數 最後一個字元落在 @..~
        if chars.peek() == Some(&'[') {
            chars.next();
            for c in chars.by_ref() {
                if ('@'..='~').contains(&c) {
                    break;
                }
            }
        } else {
            chars.next();
        }
    }

    out
}

/// 保留最後 `limit` 個字元（從完整的一行開始），回傳是否被截斷。
fn tail(text: &str, limit: usize) -> (&str, bool) {
    let count = text.chars().count();

    if count <= limit {
        return (text, false);
    }

    let start = text.char_indices().nth(count - limit).map_or(0, |(i, _)| i);
    let cut = &text[start..];

    match cut.find('\n') {
        Some(i) if i + 1 < cut.len() => (&cut[i + 1..], true),
        _ => (cut, true),
    }
}

fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

fn backend_label(backend: Backend, target: &str) -> String {
    format!("{backend} · {target}")
}

pub fn success(title: &str, description: &str) -> CreateEmbed {
    CreateEmbed::new().title(title).description(truncate(description, DESCRIPTION_LIMIT)).color(SUCCESS)
}

pub fn error(title: &str, message: &str) -> CreateEmbed {
    CreateEmbed::new().title(title).description(truncate(message, DESCRIPTION_LIMIT)).color(FAILURE)
}

pub fn info(title: &str) -> CreateEmbed {
    CreateEmbed::new().title(title).color(INFO)
}

pub fn control_result(service: &str, action_label: &str, results: &[BackendResult]) -> CreateEmbed {
    let succeeded = results.iter().filter(|r| r.success).count();

    let (title, color) = match succeeded {
        n if n == results.len() => (format!("{action_label}成功"), SUCCESS),
        0 => (format!("{action_label}失敗"), FAILURE),
        _ => (format!("部分{action_label}失敗"), WARNING),
    };

    let fields = results.iter().map(|r| {
        let value = match (r.success, r.message.is_empty()) {
            (true, true) => "成功".to_owned(),
            (true, false) => format!("成功\n{}", code_block(&r.message, 900)),
            (false, _) => format!("失敗\n{}", code_block(&r.message, 900)),
        };
        (backend_label(r.backend, &r.target), value, false)
    });

    CreateEmbed::new().title(title).description(format!("服務 **{service}**")).color(color).fields(fields)
}

#[derive(PartialEq)]
enum Health {
    Running,
    Transition,
    Stopped,
    Failed,
}

impl Health {
    fn of(status: &BackendStatus) -> Self {
        if status.error.is_some() {
            return Health::Failed;
        }

        if status.backend == Backend::Script {
            // 腳本的 active 是上次執行的結果
            return match status.active.as_str() {
                "success" => Health::Running,
                "running" => Health::Transition,
                "never" => Health::Stopped,
                _ => Health::Failed,
            };
        }

        match status.active.as_str() {
            "active" | "running" => Health::Running,
            "activating" | "deactivating" | "reloading" | "restarting" | "refreshing" | "removing" => {
                Health::Transition
            }
            "failed" | "dead" | "not-found" | "unknown" => Health::Failed,
            _ => Health::Stopped,
        }
    }

    fn label(&self) -> &'static str {
        match self {
            Health::Running => "運作中",
            Health::Transition => "轉換中",
            Health::Stopped => "已停止",
            Health::Failed => "異常",
        }
    }
}

pub fn status(service: &str, statuses: &[BackendStatus]) -> CreateEmbed {
    let healths: Vec<Health> = statuses.iter().map(Health::of).collect();

    // 顏色取最嚴重的狀態
    let color = if healths.contains(&Health::Failed) {
        FAILURE
    } else if healths.iter().all(|h| *h == Health::Running) {
        SUCCESS
    } else {
        WARNING
    };

    let fields = statuses.iter().zip(&healths).map(|(s, health)| {
        let value = match &s.error {
            Some(error) => format!("**無法查詢狀態**\n{}", code_block(error, 900)),
            None if s.backend == Backend::Script => {
                let label = match health {
                    Health::Running => "成功",
                    Health::Transition => "執行中",
                    Health::Stopped => "尚未執行",
                    Health::Failed => "失敗",
                };
                format!("上次執行：**{label}**（`{}`）\n時間：`{}`", s.active, s.enabled)
            }
            None => format!("執行狀態：**{}**（`{}`）\n開機啟動：`{}`", health.label(), s.active, s.enabled),
        };
        (backend_label(s.backend, &s.target), value, false)
    });

    CreateEmbed::new().title(format!("{service} 狀態")).color(color).fields(fields)
}

/// 每個後端一個 embed；內容過長時保留最新的部分，並回傳是否需要附上完整檔案。
pub fn logs(service: &str, logs: &[BackendResult], lines: u32) -> (Vec<CreateEmbed>, bool) {
    // 預留標題、作者、頁尾與程式碼區塊標記的空間
    let budget = (MESSAGE_LIMIT / logs.len().max(1)).min(DESCRIPTION_LIMIT) - 200;
    let mut truncated = false;

    let embeds = logs
        .iter()
        .map(|log| {
            let (_, cut) = tail(&log.message, budget);
            truncated |= cut;

            let footer = format!("最近 {lines} 行{}", if cut { "，已截斷，完整內容見附件" } else { "" });

            CreateEmbed::new()
                .title(format!("{service} 日誌"))
                .author(CreateEmbedAuthor::new(backend_label(log.backend, &log.target)))
                .description(code_block(&log.message, budget))
                .color(if log.success { INFO } else { FAILURE })
                .footer(CreateEmbedFooter::new(footer))
        })
        .collect();

    (embeds, truncated)
}

pub fn service_list(services: &[Service]) -> CreateEmbed {
    let description = services
        .iter()
        .map(|s| {
            let targets: Vec<String> = s.targets().iter().map(|(b, t)| format!("　{b}：`{t}`")).collect();
            format!("**{}**\n{}", s.name(), targets.join("\n"))
        })
        .collect::<Vec<_>>()
        .join("\n\n");

    CreateEmbed::new()
        .title("服務清單")
        .description(truncate(&description, DESCRIPTION_LIMIT))
        .color(INFO)
        .footer(CreateEmbedFooter::new(format!("共 {} 個服務", services.len())))
}

pub fn field_value(text: &str) -> String {
    truncate(text, FIELD_LIMIT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_ansi_codes() {
        assert_eq!(strip_ansi("\u{1b}[1m==> 建置\u{1b}[0m ok \u{1b}[31m[停止]\u{1b}[0m"), "==> 建置 ok [停止]");
        assert_eq!(code_block("\u{1b}[33mwarn\u{1b}[0m", 100), "```\nwarn\n```");
    }

    #[test]
    fn code_block_cannot_be_escaped() {
        assert_eq!(code_block("a```b", 100), "```\na`\u{200b}``b\n```");
    }

    #[test]
    fn tail_keeps_latest_complete_lines() {
        let text: String = (0..2000).map(|i| format!("line {i}\n")).collect();
        let (kept, cut) = tail(&text, 100);

        assert!(cut && kept.len() <= 100);
        assert!(kept.starts_with("line ") && kept.contains("line 1999"));
        assert_eq!(tail("short", 100), ("short", false));
    }

    #[test]
    fn tail_is_utf8_safe() {
        let (kept, cut) = tail("中文日誌內容", 3);
        assert_eq!((kept, cut), ("誌內容", true));
    }
}

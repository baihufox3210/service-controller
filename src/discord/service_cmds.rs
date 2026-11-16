//! `/service`：服務控制與查詢。

use poise::CreateReply;
use poise::serenity_prelude::{AutocompleteChoice, CreateAttachment, CreateAutocompleteResponse};

use super::{Context, Error, UserError, audit, embeds, ensure, level};
use crate::model::{Action, Backend, Service};
use crate::permissions::Permission;

#[derive(Debug, Clone, Copy, poise::ChoiceParameter)]
pub enum Scope {
    #[name = "全部"]
    All,
    #[name = "systemd"]
    Systemd,
    #[name = "Docker"]
    Docker,
}

impl Scope {
    fn backend(scope: Option<Scope>) -> Option<Backend> {
        match scope {
            Some(Scope::Systemd) => Some(Backend::Systemd),
            Some(Scope::Docker) => Some(Backend::Docker),
            Some(Scope::All) | None => None,
        }
    }
}

/// 回傳呼叫者有檢視權限（全域或單一服務）的服務。
pub async fn visible_services(ctx: Context<'_>) -> Vec<Service> {
    let mut visible = Vec::new();

    for service in ctx.data().controller.registry.all() {
        if level(ctx, Some(service.name())).await >= Permission::Viewer {
            visible.push(service);
        }
    }

    visible
}

/// 只顯示使用者看得到的服務，避免洩漏服務清單。
pub async fn autocomplete_service(ctx: Context<'_>, partial: &str) -> CreateAutocompleteResponse {
    let partial = partial.to_lowercase();

    let choices = visible_services(ctx)
        .await
        .into_iter()
        .filter(|s| {
            s.name().to_lowercase().contains(&partial)
                || s.targets().values().any(|t| t.to_lowercase().contains(&partial))
        })
        .take(25)
        .map(|s| {
            let targets: Vec<String> = s.targets().iter().map(|(b, t)| format!("{b}: {t}")).collect();
            let label: String = format!("{} ({})", s.name(), targets.join(", ")).chars().take(100).collect();
            AutocompleteChoice::new(label, s.name().to_owned())
        })
        .collect();

    CreateAutocompleteResponse::new().set_choices(choices)
}

/// 管理服務（systemd / Docker）
#[poise::command(
    slash_command,
    guild_only,
    subcommands("start", "stop", "restart", "enable", "disable", "status", "logs", "list"),
    subcommand_required
)]
pub async fn service(_: Context<'_>) -> Result<(), Error> {
    Ok(())
}

async fn control(ctx: Context<'_>, service: String, action: Action, scope: Option<Scope>) -> Result<(), Error> {
    ensure(ctx, Permission::Operator, Some(&service)).await?;
    ctx.defer().await?;

    let results =
        ctx.data().controller.control(&service, action, Scope::backend(scope)).await.map_err(UserError::msg)?;

    for r in &results {
        audit(ctx, action.as_str(), &service, r.success, &format!("{}:{} {}", r.backend, r.target, r.message));
    }

    ctx.send(CreateReply::default().embed(embeds::control_result(&service, action.label(), &results))).await?;
    Ok(())
}

/// 啟動服務
#[poise::command(slash_command, guild_only, user_cooldown = 5)]
pub async fn start(
    ctx: Context<'_>,
    #[description = "服務名稱"]
    #[autocomplete = "autocomplete_service"]
    service: String,
    #[description = "要操作的後端（預設：全部）"] backend: Option<Scope>,
) -> Result<(), Error> {
    control(ctx, service, Action::Start, backend).await
}

/// 關閉服務
#[poise::command(slash_command, guild_only, user_cooldown = 5)]
pub async fn stop(
    ctx: Context<'_>,
    #[description = "服務名稱"]
    #[autocomplete = "autocomplete_service"]
    service: String,
    #[description = "要操作的後端（預設：全部）"] backend: Option<Scope>,
) -> Result<(), Error> {
    control(ctx, service, Action::Stop, backend).await
}

/// 重啟服務
#[poise::command(slash_command, guild_only, user_cooldown = 5)]
pub async fn restart(
    ctx: Context<'_>,
    #[description = "服務名稱"]
    #[autocomplete = "autocomplete_service"]
    service: String,
    #[description = "要操作的後端（預設：全部）"] backend: Option<Scope>,
) -> Result<(), Error> {
    control(ctx, service, Action::Restart, backend).await
}

/// 設定開機自動啟動（Docker：restart=unless-stopped）
#[poise::command(slash_command, guild_only, user_cooldown = 5)]
pub async fn enable(
    ctx: Context<'_>,
    #[description = "服務名稱"]
    #[autocomplete = "autocomplete_service"]
    service: String,
    #[description = "要操作的後端（預設：全部）"] backend: Option<Scope>,
) -> Result<(), Error> {
    control(ctx, service, Action::Enable, backend).await
}

/// 取消開機自動啟動（Docker：restart=no）
#[poise::command(slash_command, guild_only, user_cooldown = 5)]
pub async fn disable(
    ctx: Context<'_>,
    #[description = "服務名稱"]
    #[autocomplete = "autocomplete_service"]
    service: String,
    #[description = "要操作的後端（預設：全部）"] backend: Option<Scope>,
) -> Result<(), Error> {
    control(ctx, service, Action::Disable, backend).await
}

/// 查詢服務狀態
#[poise::command(slash_command, guild_only)]
pub async fn status(
    ctx: Context<'_>,
    #[description = "服務名稱"]
    #[autocomplete = "autocomplete_service"]
    service: String,
    #[description = "要查詢的後端（預設：全部）"] backend: Option<Scope>,
) -> Result<(), Error> {
    ensure(ctx, Permission::Viewer, Some(&service)).await?;
    ctx.defer().await?;

    let statuses = ctx.data().controller.status(&service, Scope::backend(backend)).await.map_err(UserError::msg)?;
    ctx.send(CreateReply::default().embed(embeds::status(&service, &statuses))).await?;
    Ok(())
}

/// 查詢服務日誌（僅自己可見）
#[poise::command(slash_command, guild_only)]
pub async fn logs(
    ctx: Context<'_>,
    #[description = "服務名稱"]
    #[autocomplete = "autocomplete_service"]
    service: String,
    #[description = "要查詢的後端（預設：全部）"] backend: Option<Scope>,
    #[description = "每個後端讀取的行數（1-500，預設 50）"]
    #[min = 1]
    #[max = 500]
    lines: Option<u32>,
) -> Result<(), Error> {
    ensure(ctx, Permission::Viewer, Some(&service)).await?;
    // 日誌可能包含敏感資訊，只回覆給呼叫者
    ctx.defer_ephemeral().await?;

    let lines = lines.unwrap_or(50);
    let logs = ctx.data().controller.logs(&service, Scope::backend(backend), lines).await.map_err(UserError::msg)?;
    audit(ctx, "logs", &service, logs.iter().all(|l| l.success), "");

    let (log_embeds, truncated) = embeds::logs(&service, &logs, lines);
    let mut reply = CreateReply::default().ephemeral(true);

    for embed in log_embeds {
        reply = reply.embed(embed);
    }

    // 內容過長時，embed 只顯示最新的部分，並附上完整日誌檔
    if truncated {
        let content: String =
            logs.iter().map(|l| format!("===== {}: {} =====\n{}\n\n", l.backend, l.target, l.message)).collect();
        reply = reply.attachment(CreateAttachment::bytes(content, format!("{service}_logs.txt")));
    }

    ctx.send(reply).await?;
    Ok(())
}

/// 列出你可以存取的服務
#[poise::command(slash_command, guild_only)]
pub async fn list(ctx: Context<'_>) -> Result<(), Error> {
    if !ctx.data().policy.guild_allowed(ctx.guild_id().map(|g| g.get())) {
        return Err(UserError::GuildNotAllowed.into());
    }

    let services = visible_services(ctx).await;

    let reply = if services.is_empty() {
        CreateReply::default().embed(embeds::error("服務清單", "沒有你可以存取的服務。")).ephemeral(true)
    } else {
        CreateReply::default().embed(embeds::service_list(&services))
    };

    ctx.send(reply).await?;
    Ok(())
}

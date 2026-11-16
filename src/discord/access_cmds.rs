//! `/access`：權限管理。

use std::collections::BTreeMap;

use poise::CreateReply;
use poise::serenity_prelude as serenity;

use super::service_cmds::autocomplete_service;
use super::{Context, Error, UserError, audit, embeds, ensure, level};
use crate::permissions::{Grant, Permission, Subject};

#[derive(Debug, Clone, Copy, poise::ChoiceParameter)]
pub enum Level {
    #[name = "管理員（全域）"]
    Admin,
    #[name = "操作者"]
    Operator,
    #[name = "檢視者"]
    Viewer,
}

impl From<Level> for Permission {
    fn from(level: Level) -> Self {
        match level {
            Level::Admin => Permission::Admin,
            Level::Operator => Permission::Operator,
            Level::Viewer => Permission::Viewer,
        }
    }
}

fn subject(user: Option<serenity::User>, role: Option<serenity::Role>) -> Result<(Subject, u64, String), Error> {
    match (user, role) {
        (Some(user), None) => Ok((Subject::User, user.id.get(), format!("<@{}>", user.id))),
        (None, Some(role)) => Ok((Subject::Role, role.id.get(), format!("<@&{}>", role.id))),
        _ => Err(UserError::msg("請指定 user 或 role 其中之一。")),
    }
}

fn check_service(ctx: Context<'_>, service: Option<&str>) -> Result<(), Error> {
    match service {
        Some(name) if ctx.data().controller.registry.get(name).is_none() => {
            Err(UserError::msg(format!("找不到服務 {name}")))
        }
        _ => Ok(()),
    }
}

fn scope_label(service: Option<&str>) -> String {
    service.map_or("（全域）".to_owned(), |s| format!("（服務 **{s}**）"))
}

/// 權限管理
#[poise::command(slash_command, guild_only, subcommands("grant", "revoke", "list", "whoami"), subcommand_required)]
pub async fn access(_: Context<'_>) -> Result<(), Error> {
    Ok(())
}

/// 授予使用者或身分組權限（指定 service 則只對該服務有效）
#[poise::command(slash_command, guild_only)]
pub async fn grant(
    ctx: Context<'_>,
    #[description = "權限等級"] level: Level,
    #[description = "使用者"] user: Option<serenity::User>,
    #[description = "身分組"] role: Option<serenity::Role>,
    #[description = "只對此服務授權（留空為全域）"]
    #[autocomplete = "autocomplete_service"]
    service: Option<String>,
) -> Result<(), Error> {
    let actual = ensure(ctx, Permission::Admin, None).await?;
    let (subject, id, mention) = subject(user, role)?;
    check_service(ctx, service.as_deref())?;

    let new_level = Permission::from(level);
    let existing = ctx.data().policy.get(subject, id, service.as_deref());

    // 覆寫既有授權也需要足以管理該授權的權限（例如把管理員降級）
    let required =
        existing.iter().map(|g| g.level).chain([new_level]).map(Permission::required_to_manage).max().unwrap();
    if actual < required {
        return Err(UserError::Denied { required, actual }.into());
    }

    let grant = Grant::new(subject, id, new_level, service.clone()).map_err(UserError::msg)?;
    ctx.data().policy.set(grant)?;
    audit(ctx, "grant", &format!("{subject:?}:{id}"), true, &format!("level={new_level:?} service={service:?}"));

    let description = format!("{mention} → **{}**{}", new_level.label(), scope_label(service.as_deref()));
    ctx.send(CreateReply::default().embed(embeds::success("已授予權限", &description))).await?;
    Ok(())
}

/// 撤銷使用者或身分組的權限
#[poise::command(slash_command, guild_only)]
pub async fn revoke(
    ctx: Context<'_>,
    #[description = "使用者"] user: Option<serenity::User>,
    #[description = "身分組"] role: Option<serenity::Role>,
    #[description = "撤銷此服務的單獨授權（留空為全域授權）"]
    #[autocomplete = "autocomplete_service"]
    service: Option<String>,
) -> Result<(), Error> {
    let actual = ensure(ctx, Permission::Admin, None).await?;
    let (subject, id, mention) = subject(user, role)?;

    let Some(existing) = ctx.data().policy.get(subject, id, service.as_deref()) else {
        return Err(UserError::msg(format!("{mention} 沒有這項授權。")));
    };

    let required = existing.level.required_to_manage();
    if actual < required {
        return Err(UserError::Denied { required, actual }.into());
    }

    ctx.data().policy.remove(subject, id, service.as_deref())?;
    audit(
        ctx,
        "revoke",
        &format!("{subject:?}:{id}"),
        true,
        &format!("level={:?} service={service:?}", existing.level),
    );

    let description = format!("{mention} 的 **{}**{}", existing.level.label(), scope_label(service.as_deref()));
    ctx.send(CreateReply::default().embed(embeds::success("已撤銷權限", &description))).await?;
    Ok(())
}

/// 列出權限設定
#[poise::command(slash_command, guild_only)]
pub async fn list(
    ctx: Context<'_>,
    #[description = "只顯示此服務的單獨授權"]
    #[autocomplete = "autocomplete_service"]
    service: Option<String>,
) -> Result<(), Error> {
    ensure(ctx, Permission::Admin, None).await?;

    let policy = &ctx.data().policy;
    let title = service.as_ref().map_or("權限設定".to_owned(), |s| format!("權限設定 · {s}"));
    let mut embed = embeds::info(&title);

    if service.is_none() {
        let owners: Vec<String> = policy.owners().iter().map(|id| format!("<@{id}>")).collect();
        let owners = if owners.is_empty() { "（未設定）".to_owned() } else { owners.join("\n") };
        embed = embed.field("擁有者", embeds::field_value(&owners), false);
    }

    // 依範圍分組：全域在前，其次依服務名稱排序
    let mut groups: BTreeMap<Option<String>, Vec<Grant>> = BTreeMap::new();
    for grant in policy.list(service.as_deref()) {
        groups.entry(grant.service.clone()).or_default().push(grant);
    }

    if groups.is_empty() {
        embed = embed.description("目前沒有任何授權。");
    }

    for (scope, mut grants) in groups {
        grants.sort_by_key(|g| std::cmp::Reverse(g.level));
        let value: Vec<String> = grants.iter().map(|g| format!("{} — {}", g.mention(), g.level.label())).collect();
        let name = scope.map_or("全域".to_owned(), |s| format!("服務：{s}"));
        embed = embed.field(name, embeds::field_value(&value.join("\n")), false);
    }

    ctx.send(CreateReply::default().embed(embed).ephemeral(true)).await?;
    Ok(())
}

/// 查看自己的權限
#[poise::command(slash_command, guild_only)]
pub async fn whoami(
    ctx: Context<'_>,
    #[description = "查看對此服務的權限"]
    #[autocomplete = "autocomplete_service"]
    service: Option<String>,
) -> Result<(), Error> {
    let level = level(ctx, service.as_deref()).await;
    let scope = service.as_ref().map_or("全域".to_owned(), |s| format!("服務 **{s}**"));

    let embed =
        embeds::info("我的權限").field("範圍", scope, true).field("權限", format!("**{}**", level.label()), true);
    ctx.send(CreateReply::default().embed(embed).ephemeral(true)).await?;
    Ok(())
}

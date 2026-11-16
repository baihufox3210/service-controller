//! `!services`：在 Discord 中用 TOML 程式碼區塊設定要控制的服務（僅限管理員）。
//!
//! ```text
//! !services set
//! ```toml
//! [blog]
//! systemd = "baihu-blog-api"
//! docker = "my-dashboard"
//! ```
//! ```

use poise::CreateReply;

use super::{Context, Error, UserError, audit, embeds, ensure};
use crate::config_text::{self, MAX_CONFIG_BYTES, MAX_SERVICES};
use crate::model::validate_name;
use crate::permissions::Permission;
use crate::registry::Diff;

/// 指令本身之後的整則訊息內容（程式碼區塊在這裡面）。
fn message_content<'a>(ctx: Context<'a>) -> Result<&'a str, Error> {
    match ctx {
        poise::Context::Prefix(prefix) => Ok(&prefix.msg.content),
        poise::Context::Application(_) => Err(UserError::msg("此指令只能用前綴指令執行。")),
    }
}

fn usage(prefix: &str) -> String {
    format!(
        "**{p}services show**\n顯示目前的設定\n\n\
         **{p}services set** + 程式碼區塊\n新增或更新區塊中列出的服務，其他服務維持不變\n\n\
         **{p}services replace** + 程式碼區塊\n以區塊內容完整取代目前的設定（未列出的服務會被移除）\n\n\
         **{p}services remove** 名稱 [名稱 ...]\n移除指定的服務\n\n\
         設定格式：\n```toml\n[blog]\nsystemd = \"baihu-blog-api\"   # 可省略 .service\ndocker = \"my-dashboard\"\nscript = \"blog-restart\"     # 主機上註冊的腳本名稱\n```\n\
         每個服務至少要設定 systemd、docker、script 其中之一；最多 {MAX_SERVICES} 個服務、{MAX_CONFIG_BYTES} bytes。\n\
         有設定 script 時，/service restart 會改為執行腳本。腳本需先在主機上用 scripts/register-script.sh 註冊。",
        p = prefix
    )
}

fn diff_embed(title: &str, diff: &Diff, total: usize) -> poise::serenity_prelude::CreateEmbed {
    if diff.is_empty() {
        return embeds::info(title).description("設定沒有變化。");
    }

    let mut embed = embeds::success(title, &format!("目前共 {total} 個服務"));

    for (name, list) in [("新增", &diff.added), ("更新", &diff.changed), ("移除", &diff.removed)] {
        if !list.is_empty() {
            let value: Vec<String> = list.iter().map(|n| format!("`{n}`")).collect();
            embed = embed.field(name, embeds::field_value(&value.join("、")), false);
        }
    }

    embed
}

async fn reply(ctx: Context<'_>, embed: poise::serenity_prelude::CreateEmbed) -> Result<(), Error> {
    ctx.send(CreateReply::default().embed(embed).reply(true)).await?;
    Ok(())
}

/// 服務設定（前綴指令）
#[poise::command(prefix_command, guild_only, subcommands("show", "set", "replace", "remove"), discard_spare_arguments)]
pub async fn services(ctx: Context<'_>) -> Result<(), Error> {
    ensure(ctx, Permission::Admin, None).await?;
    reply(ctx, embeds::info("服務設定指令").description(usage(ctx.prefix()))).await
}

/// 顯示目前的設定
#[poise::command(prefix_command, guild_only, discard_spare_arguments)]
pub async fn show(ctx: Context<'_>) -> Result<(), Error> {
    ensure(ctx, Permission::Admin, None).await?;

    let rendered = ctx.data().controller.registry.render();
    let description = if rendered.trim().is_empty() {
        format!("目前沒有任何服務。用 `{}services set` 新增。", ctx.prefix())
    } else {
        format!("```toml\n{}\n```", rendered.trim_end().replace("```", "`\u{200b}``"))
    };

    reply(ctx, embeds::info("目前的服務設定").description(description)).await
}

async fn apply(ctx: Context<'_>, replace: bool) -> Result<(), Error> {
    ensure(ctx, Permission::Admin, None).await?;

    let block = config_text::extract_code_block(message_content(ctx)?).map_err(UserError::msg)?;
    let services = config_text::parse_services(block).map_err(UserError::msg)?;
    let registry = &ctx.data().controller.registry;

    let diff = if replace { registry.replace(services)? } else { registry.merge(services)? };

    // 被移除的服務，其單獨授權也一併刪除
    if !diff.removed.is_empty() {
        ctx.data().policy.remove_services(&diff.removed)?;
    }

    let detail = format!("added={:?} changed={:?} removed={:?}", diff.added, diff.changed, diff.removed);
    audit(ctx, if replace { "services.replace" } else { "services.set" }, "config", true, &detail);

    reply(ctx, diff_embed("服務設定已更新", &diff, registry.all().len())).await
}

/// 新增或更新程式碼區塊中列出的服務
#[poise::command(prefix_command, guild_only, user_cooldown = 3, discard_spare_arguments)]
pub async fn set(ctx: Context<'_>) -> Result<(), Error> {
    apply(ctx, false).await
}

/// 以程式碼區塊完整取代目前的設定
#[poise::command(prefix_command, guild_only, user_cooldown = 3, discard_spare_arguments)]
pub async fn replace(ctx: Context<'_>) -> Result<(), Error> {
    apply(ctx, true).await
}

/// 移除指定的服務
#[poise::command(prefix_command, guild_only, user_cooldown = 3)]
pub async fn remove(ctx: Context<'_>, #[rest] names: Option<String>) -> Result<(), Error> {
    ensure(ctx, Permission::Admin, None).await?;

    let names: Vec<String> = names.unwrap_or_default().split_whitespace().map(str::to_owned).collect();

    if names.is_empty() {
        return Err(UserError::msg(format!("請指定要移除的服務名稱，例如 `{}services remove blog`", ctx.prefix())));
    }

    for name in &names {
        validate_name(name).map_err(UserError::msg)?;
    }

    let diff = ctx.data().controller.registry.remove(&names)?;

    if diff.removed.is_empty() {
        return Err(UserError::msg("找不到指定的服務。"));
    }

    ctx.data().policy.remove_services(&diff.removed)?;
    audit(ctx, "services.remove", "config", true, &format!("removed={:?}", diff.removed));

    reply(ctx, diff_embed("服務設定已更新", &diff, ctx.data().controller.registry.all().len())).await
}

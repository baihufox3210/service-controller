//! Discord 介面：斜線指令（服務控制、權限）與前綴指令（服務設定）。

mod access_cmds;
mod config_cmds;
mod embeds;
mod service_cmds;

use poise::serenity_prelude as serenity;
use thiserror::Error;
use tracing::{error, info, warn};

use crate::controller::Controller;
use crate::permissions::{Permission, Policy};
use crate::settings::Settings;

pub struct Data {
    pub controller: Controller,
    pub policy: Policy,
}

pub type Error = Box<dyn std::error::Error + Send + Sync>;
pub type Context<'a> = poise::Context<'a, Data, Error>;

/// 給使用者看的錯誤；其他錯誤只記錄在日誌，對使用者顯示通用訊息。
#[derive(Debug, Error)]
pub enum UserError {
    #[error("此伺服器未被授權使用本機器人。")]
    GuildNotAllowed,
    #[error("需要「{}」權限，你目前的權限為「{}」。", required.label(), actual.label())]
    Denied { required: Permission, actual: Permission },
    #[error("{0}")]
    Message(String),
}

impl UserError {
    pub fn msg(message: impl ToString) -> Error {
        Box::new(UserError::Message(message.to_string()))
    }
}

async fn author_roles(ctx: Context<'_>) -> Vec<u64> {
    ctx.author_member().await.map(|m| m.roles.iter().map(|r| r.get()).collect()).unwrap_or_default()
}

/// 取得呼叫者的權限；`service` 有值時套用該服務的單獨授權。
pub async fn level(ctx: Context<'_>, service: Option<&str>) -> Permission {
    let roles = author_roles(ctx).await;
    ctx.data().policy.level_for(ctx.author().id.get(), &roles, ctx.guild_id().map(|g| g.get()), service)
}

/// 確認呼叫者至少有 `required` 權限，否則回傳 [`UserError`]。
pub async fn ensure(ctx: Context<'_>, required: Permission, service: Option<&str>) -> Result<Permission, Error> {
    if !ctx.data().policy.guild_allowed(ctx.guild_id().map(|g| g.get())) {
        return Err(UserError::GuildNotAllowed.into());
    }

    let actual = level(ctx, service).await;

    if actual < required {
        warn!(user = %ctx.author().id, command = %ctx.command().qualified_name, ?service, "permission denied");
        return Err(UserError::Denied { required, actual }.into());
    }

    Ok(actual)
}

/// 稽核日誌：誰在哪裡做了什麼。
pub fn audit(ctx: Context<'_>, action: &str, target: &str, success: bool, detail: &str) {
    info!(
        target: "audit",
        user = %ctx.author().name,
        user_id = %ctx.author().id,
        guild = ?ctx.guild_id().map(|g| g.get()),
        channel = %ctx.channel_id(),
        action,
        target,
        success,
        detail,
    );
}

async fn reply_error(ctx: Context<'_>, title: &str, message: &str) {
    let reply = poise::CreateReply::default().embed(embeds::error(title, message)).ephemeral(true).reply(true);

    if let Err(e) = ctx.send(reply).await {
        error!("failed to send error reply: {e}");
    }
}

async fn on_error(error: poise::FrameworkError<'_, Data, Error>) {
    match error {
        poise::FrameworkError::Command { error, ctx, .. } => match error.downcast_ref::<UserError>() {
            Some(user_error) => reply_error(ctx, "無法執行指令", &user_error.to_string()).await,
            None => {
                error!(command = %ctx.command().qualified_name, "command failed: {error}");
                reply_error(ctx, "無法執行指令", "執行指令時發生內部錯誤，請聯絡管理員查看日誌。").await;
            }
        },
        poise::FrameworkError::CooldownHit { remaining_cooldown, ctx, .. } => {
            let message = format!("指令冷卻中，請在 {:.1} 秒後再試。", remaining_cooldown.as_secs_f32());
            reply_error(ctx, "請稍後再試", &message).await;
        }
        poise::FrameworkError::GuildOnly { ctx, .. } => {
            reply_error(ctx, "無法執行指令", "此指令只能在伺服器中使用。").await
        }
        poise::FrameworkError::ArgumentParse { error, ctx, .. } => {
            reply_error(ctx, "參數錯誤", &error.to_string()).await;
        }
        other => {
            if let Err(e) = poise::builtins::on_error(other).await {
                error!("error while handling error: {e}");
            }
        }
    }
}

pub async fn run(settings: Settings, data: Data) -> Result<(), Error> {
    let options = poise::FrameworkOptions {
        commands: vec![service_cmds::service(), access_cmds::access(), config_cmds::services()],
        prefix_options: poise::PrefixFrameworkOptions {
            prefix: Some(settings.prefix.clone().into()),
            // 只處理新訊息；編輯過的訊息不會再次執行設定指令
            execute_untracked_edits: false,
            ignore_bots: true,
            ..Default::default()
        },
        // 機器人不會 @ 任何人
        allowed_mentions: Some(serenity::CreateAllowedMentions::new()),
        on_error: |error| Box::pin(on_error(error)),
        // 擁有者由 Policy 管理，不使用 poise 內建的擁有者機制
        initialize_owners: false,
        ..Default::default()
    };

    let framework = poise::Framework::builder()
        .options(options)
        .setup(|ctx, ready, framework| {
            Box::pin(async move {
                info!("logged in as {} ({})", ready.user.name, ready.user.id);

                if !data.policy.has_owners() {
                    // 未設定 OWNER_USER_IDS 時，以 Discord 應用程式的擁有者（或團隊成員）作為擁有者
                    let app = ctx.http.get_current_application_info().await?;
                    let owners: Vec<u64> = match app.team {
                        Some(team) => team.members.iter().map(|m| m.user.id.get()).collect(),
                        None => app.owner.iter().map(|u| u.id.get()).collect(),
                    };
                    warn!(?owners, "OWNER_USER_IDS is empty, using the application owners");
                    data.policy.add_owners(owners);
                }

                poise::builtins::register_globally(ctx, &framework.options().commands).await?;
                Ok(data)
            })
        })
        .build();

    // MESSAGE_CONTENT 讓 `!services` 前綴指令能讀取訊息內容
    let intents = serenity::GatewayIntents::GUILDS
        | serenity::GatewayIntents::GUILD_MESSAGES
        | serenity::GatewayIntents::MESSAGE_CONTENT;

    let mut client = serenity::ClientBuilder::new(&settings.token, intents).framework(framework).await?;

    match client.start().await {
        Err(serenity::Error::Gateway(serenity::GatewayError::DisallowedGatewayIntents)) => Err(UserError::msg(
            "Discord 拒絕了 Message Content Intent，請到 Developer Portal → Bot 開啟「MESSAGE CONTENT INTENT」",
        )),
        other => Ok(other?),
    }
}

#[cfg(test)]
mod tests {
    use poise::serenity_prelude as serenity;

    use super::*;

    fn commands() -> Vec<poise::Command<Data, Error>> {
        vec![service_cmds::service(), access_cmds::access(), config_cmds::services()]
    }

    #[test]
    fn slash_commands_are_valid_discord_definitions() {
        let definitions = poise::builtins::create_application_commands(&commands());
        let json = serenity::json::to_value(&definitions).unwrap();
        let commands = json.as_array().unwrap();

        // 只有 /service 與 /access 是斜線指令；!services 只能用前綴
        let names: Vec<&str> = commands.iter().map(|c| c["name"].as_str().unwrap()).collect();
        assert_eq!(names, ["service", "access"]);

        for command in commands {
            for sub in command["options"].as_array().unwrap() {
                let options = sub["options"].as_array().cloned().unwrap_or_default();
                let required: Vec<bool> = options.iter().map(|o| o["required"].as_bool().unwrap_or(false)).collect();

                // Discord 要求必填參數排在選填參數之前
                assert!(required.windows(2).all(|w| w[0] >= w[1]), "{}: {required:?}", sub["name"]);

                for item in std::iter::once(sub).chain(&options) {
                    let description = item["description"].as_str().unwrap();
                    assert!((1..=100).contains(&description.chars().count()), "{description}");
                }
            }
        }
    }

    #[test]
    fn config_commands_are_prefix_only() {
        let services = config_cmds::services();
        assert!(services.slash_action.is_none() && services.prefix_action.is_some());
        assert_eq!(services.subcommands.len(), 4);
    }
}

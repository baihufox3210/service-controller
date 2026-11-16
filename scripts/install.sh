#!/usr/bin/env bash
# 安裝或更新 service-controller。
#
#   sudo ./scripts/install.sh                 安裝或更新（已有設定時沿用）
#   sudo ./scripts/install.sh --reconfigure   重新輸入 Token、擁有者等設定
#   sudo ./scripts/install.sh --yes           所有確認都採用預設值（適合自動化）
#
# 設定也可以用環境變數預先提供：DISCORD_TOKEN、OWNER_USER_IDS、ALLOWED_GUILD_IDS、COMMAND_PREFIX
set -euo pipefail

readonly BOT_USER="svcbot"
readonly BIN="/usr/local/bin/service-controller"
readonly CONF_DIR="/etc/service-controller"
readonly ENV_FILE="$CONF_DIR/service-controller.env"
readonly UNIT_FILE="/etc/systemd/system/service-controller.service"
readonly POLKIT_RULE="/etc/polkit-1/rules.d/50-service-controller.rules"
readonly POLKIT_PKLA="/etc/polkit-1/localauthority/50-local.d/service-controller.pkla"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO

RECONFIGURE=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        --reconfigure) RECONFIGURE=1 ;;
        --yes | -y) ASSUME_YES=1 ;;
        -h | --help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知的參數：$arg" >&2; exit 2 ;;
    esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m[注意] %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m[錯誤] %s\033[0m\n' "$*" >&2; exit 1; }

# 預設為「是」的確認
confirm() {
    [[ $ASSUME_YES -eq 1 || ! -t 0 ]] && return 0
    local answer
    read -r -p "    $1 [Y/n] " answer
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

# 以觸發 sudo 的原使用者執行（編譯不需要 root）；rustup 安裝的 cargo 在 ~/.cargo/bin
as_builder() {
    local command="export PATH=\"\$HOME/.cargo/bin:\$PATH\"; $1"
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
        sudo -u "$SUDO_USER" -H bash -lc "$command"
    else
        bash -lc "$command"
    fi
}

# 從舊版部署的 .env 讀取預設值（只讀取指定的 key，不執行檔案內容）
legacy_value() {
    local file="$REPO/.env"
    [[ -f "$file" ]] || return 0
    grep -E "^$1=" "$file" | tail -n1 | cut -d= -f2- | tr -d '"'"'" || true
}

current_value() {
    [[ -f "$ENV_FILE" ]] || return 0
    grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true
}

ask() {
    local prompt="$1" default="$2" secret="${3:-0}" value
    # 非互動或 --yes 模式直接使用預設值（環境變數、既有設定或舊版 .env）
    if [[ ! -t 0 || $ASSUME_YES -eq 1 ]]; then
        printf '%s' "$default"
        return
    fi
    if [[ $secret -eq 1 ]]; then
        if [[ -n "$default" ]]; then prompt="$prompt（直接按 Enter 沿用目前的值）"; fi
        read -r -s -p "    $prompt：" value </dev/tty
        echo >&2
    else
        read -r -p "    $prompt${default:+ [$default]}：" value </dev/tty
    fi
    printf '%s' "${value:-$default}"
}

valid_ids() { [[ -z "$1" || "$1" =~ ^[0-9]+( *, *[0-9]+)*$ ]]; }

[[ $EUID -eq 0 ]] || exec sudo -- "$0" "$@"

step "檢查系統"
command -v systemctl >/dev/null || die "這個腳本需要 systemd"
[[ -f "$REPO/Cargo.toml" ]] || die "找不到 $REPO/Cargo.toml，請在專案目錄中執行"

if ! command -v cc >/dev/null; then
    warn "找不到 C 編譯器（編譯 TLS 相關套件需要）"
    if command -v apt-get >/dev/null && confirm "要用 apt 安裝 build-essential 嗎？"; then
        apt-get update -qq && apt-get install -y -qq build-essential
    else
        die "請先安裝 gcc 或 build-essential"
    fi
fi

if ! as_builder 'command -v cargo' >/dev/null; then
    warn "找不到 Rust 工具鏈"
    if confirm "要用 rustup 為 ${SUDO_USER:-root} 安裝 Rust 嗎？"; then
        as_builder "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal"
    else
        die "請先安裝 Rust：https://rustup.rs"
    fi
fi

step "編譯（release）"
as_builder "cd '$REPO' && cargo build --release --locked"

step "建立執行帳號 $BOT_USER"
if id "$BOT_USER" >/dev/null 2>&1; then
    info "帳號已存在"
else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$BOT_USER"
    info "已建立"
fi

for group in docker systemd-journal; do
    if getent group "$group" >/dev/null; then
        usermod -aG "$group" "$BOT_USER"
        info "已加入 $group 群組"
    else
        warn "找不到 $group 群組，$( [[ $group == docker ]] && echo "Docker 控制" || echo "systemd 日誌" )將無法使用"
    fi
done

step "安裝執行檔"
install -m 0755 "$REPO/target/release/service-controller" "$BIN"
info "$BIN"

step "設定檔 $ENV_FILE"
install -d -m 0750 -o root -g "$BOT_USER" "$CONF_DIR"

if [[ -f "$ENV_FILE" && $RECONFIGURE -eq 0 && -z "${DISCORD_TOKEN:-}" ]]; then
    info "沿用既有設定（要修改請加上 --reconfigure）"
else
    token="${DISCORD_TOKEN:-}"
    owners="${OWNER_USER_IDS:-}"
    guilds="${ALLOWED_GUILD_IDS:-}"
    prefix="${COMMAND_PREFIX:-}"

    [[ -n "$token" ]] || token="$(current_value DISCORD_TOKEN)"
    [[ -n "$token" ]] || token="$(legacy_value BOT_TOKEN)"
    [[ -n "$owners" ]] || owners="$(current_value OWNER_USER_IDS)"
    [[ -n "$owners" ]] || owners="$(legacy_value OWNER_USER_IDS)"
    [[ -n "$guilds" ]] || guilds="$(current_value ALLOWED_GUILD_IDS)"
    [[ -n "$guilds" ]] || guilds="$(legacy_value ALLOWED_GUILD_IDS)"
    [[ -n "$prefix" ]] || prefix="$(current_value COMMAND_PREFIX)"

    token="$(ask "Discord Bot Token" "$token" 1)"
    [[ -n "$token" ]] || die "必須提供 Discord Bot Token"

    while owners="$(ask "擁有者的 Discord 使用者 ID（逗號分隔，留空則使用應用程式擁有者）" "$owners")"; ! valid_ids "$owners"; do
        warn "只能輸入數字 ID，以逗號分隔"
        [[ -t 0 && $ASSUME_YES -eq 0 ]] || die "OWNER_USER_IDS 格式錯誤"
    done

    while guilds="$(ask "允許使用的伺服器 ID（逗號分隔，留空表示不限制）" "$guilds")"; ! valid_ids "$guilds"; do
        warn "只能輸入數字 ID，以逗號分隔"
        [[ -t 0 && $ASSUME_YES -eq 0 ]] || die "ALLOWED_GUILD_IDS 格式錯誤"
    done

    prefix="$(ask "前綴指令的符號" "${prefix:-!}")"
    [[ "$prefix" =~ ^[^[:space:]]{1,5}$ ]] || die "前綴必須是 1 到 5 個非空白字元"

    tmp="$(mktemp "$CONF_DIR/.env.XXXXXX")"
    {
        echo "# service-controller 設定（由 scripts/install.sh 產生）"
        echo "DISCORD_TOKEN=$token"
        echo "OWNER_USER_IDS=${owners// /}"
        echo "ALLOWED_GUILD_IDS=${guilds// /}"
        echo "COMMAND_PREFIX=$prefix"
    } >"$tmp"
    chown root:"$BOT_USER" "$tmp"
    chmod 0640 "$tmp"
    mv "$tmp" "$ENV_FILE"
    info "已寫入（權限 0640，只有 root 與 $BOT_USER 可讀）"
fi

step "設定 systemd 權限（polkit）"
polkit_version="$(pkaction --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+)?' | head -n1 || true)"

if [[ -z "$polkit_version" ]]; then
    warn "找不到 polkit，systemd 服務的控制將無法使用"
elif [[ "$polkit_version" == 0.105 || "$polkit_version" == 0.10[0-4] ]]; then
    # 舊版 polkit 不支援 JavaScript 規則，改用 .pkla
    install -d -m 0755 "$(dirname "$POLKIT_PKLA")"
    cat >"$POLKIT_PKLA" <<EOF
[service-controller]
Identity=unix-user:$BOT_USER
Action=org.freedesktop.systemd1.manage-units;org.freedesktop.systemd1.manage-unit-files;org.freedesktop.systemd1.reload-daemon
ResultAny=yes
ResultInactive=yes
ResultActive=yes
EOF
    info "polkit $polkit_version：已安裝 $POLKIT_PKLA"
else
    install -m 0644 "$REPO/deploy/polkit/50-service-controller.rules" "$POLKIT_RULE"
    info "polkit $polkit_version：已安裝 $POLKIT_RULE"
fi

step "清理舊版 Docker 部署"
if command -v docker >/dev/null && docker inspect service-controller >/dev/null 2>&1; then
    if confirm "發現舊版的 service-controller 容器，要停止並移除嗎？（兩個版本同時執行會重複回應）"; then
        docker rm -f service-controller >/dev/null
        info "已移除容器（資料 volume 保留，可用 docker volume ls 查看）"
    fi
else
    info "沒有舊版容器"
fi

if [[ -f /etc/apparmor.d/service-controller ]] && confirm "舊版的 AppArmor profile 已不再需要，要移除嗎？"; then
    apparmor_parser -R /etc/apparmor.d/service-controller 2>/dev/null || true
    rm -f /etc/apparmor.d/service-controller
    info "已移除"
fi

step "安裝並啟動 systemd 服務"
install -m 0644 "$REPO/deploy/service-controller.service" "$UNIT_FILE"
systemctl daemon-reload
systemctl enable service-controller >/dev/null
systemctl restart service-controller

sleep 3
if systemctl is-active --quiet service-controller; then
    info "service-controller 正在執行"
else
    warn "service-controller 沒有成功啟動，最近的日誌："
    journalctl -u service-controller -n 20 --no-pager || true
    exit 1
fi

step "完成"
cat <<EOF
    查看日誌：journalctl -u service-controller -f
    更新版本：git pull && sudo ./scripts/install.sh
    修改設定：sudo ./scripts/install.sh --reconfigure

    還沒做的話，請到 Discord Developer Portal → Bot 開啟「MESSAGE CONTENT INTENT」，
    之後在 Discord 用 !services 設定要控制的服務。
EOF

#!/usr/bin/env bash
# 從舊版（Python + Docker 部署）完整移植到新版。
#
#   curl -fsSL https://raw.githubusercontent.com/baihufox3210/service-controller/master/scripts/migrate.sh -o /tmp/migrate.sh
#   sudo bash /tmp/migrate.sh [舊版專案目錄]
#
# 會依序：
#   1. 讀取舊版 .env 的 Token、擁有者與伺服器設定
#   2. 匯出舊版的服務清單與權限，轉換成新版格式
#   3. 刪除舊版的容器、volume、映像檔、AppArmor profile 與程式碼
#   4. 下載新版並執行 scripts/install.sh
#   5. 匯入轉換後的服務清單與權限，重新啟動
#
# 匯出的資料與舊 .env 會備份在 /root/service-controller-migration-<時間>（只有 root 可讀）。
#
# 環境變數：BRANCH（預設 master）、REPO_URL
set -euo pipefail

readonly OLD_DIR="${1:-/data/projects/service-controller}"
readonly BRANCH="${BRANCH:-master}"
readonly REPO_URL="${REPO_URL:-https://github.com/baihufox3210/service-controller.git}"
readonly STATE_DIR="/var/lib/service-controller"
BACKUP_DIR="/root/service-controller-migration-$(date +%Y%m%d-%H%M%S)"
readonly BACKUP_DIR

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m[注意] %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m[錯誤] %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "請用 sudo 執行：sudo bash $0"
[[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]] || die "請以一般使用者透過 sudo 執行（新版會用該使用者編譯）"
command -v git >/dev/null || die "找不到 git"

# 刪除舊目錄前先離開它
cd /

install -d -m 0700 "$BACKUP_DIR"
trap 'warn "移植中斷。舊版設定與匯出的資料保留在 $BACKUP_DIR"' ERR

step "1. 讀取舊版設定"
env_value() {
    [[ -f "$OLD_DIR/.env" ]] || return 0
    grep -E "^$1=" "$OLD_DIR/.env" | tail -n1 | cut -d= -f2- | tr -d '"'"'" || true
}

if [[ -f "$OLD_DIR/.env" ]]; then
    install -m 0600 "$OLD_DIR/.env" "$BACKUP_DIR/old.env"
    DISCORD_TOKEN="$(env_value BOT_TOKEN)"
    OWNER_USER_IDS="$(env_value OWNER_USER_IDS)"
    ALLOWED_GUILD_IDS="$(env_value ALLOWED_GUILD_IDS)"
    info "已讀取 $OLD_DIR/.env"
else
    warn "找不到 $OLD_DIR/.env，安裝時需要手動輸入 Token"
fi

step "2. 匯出舊版的服務清單與權限"
volume=""
if command -v docker >/dev/null; then
    volume="$(docker volume ls -q | grep -E '(^|_)service-controller_data$' | head -n1 || true)"
fi

export_file() {
    local name="$1"
    # 優先從還在的容器複製，否則透過 volume 讀取
    if docker inspect service-controller >/dev/null 2>&1 &&
        docker cp "service-controller:/app/database/$name" "$BACKUP_DIR/$name" 2>/dev/null; then
        return 0
    fi
    if [[ -n "$volume" ]]; then
        local image="service-controller:latest"
        docker image inspect "$image" >/dev/null 2>&1 || image="busybox:1.37"
        docker run --rm -v "$volume:/data:ro" --entrypoint cat "$image" "/data/$name" >"$BACKUP_DIR/$name" 2>/dev/null && return 0
        rm -f "$BACKUP_DIR/$name"
    fi
    return 1
}

exported=0
if command -v docker >/dev/null; then
    for name in service.json permissions.json; do
        if export_file "$name"; then
            chmod 0600 "$BACKUP_DIR/$name"
            info "已匯出 $name"
            exported=1
        else
            info "沒有 $name"
        fi
    done
fi

if [[ $exported -eq 1 ]]; then
    command -v python3 >/dev/null || die "轉換資料需要 python3（sudo apt install python3）"

    # 舊版 JSON 與新版 TOML 的欄位相同，只是格式不同；新版啟動時會再完整驗證一次
    python3 - "$BACKUP_DIR" <<'PY'
import json, pathlib, sys

backup = pathlib.Path(sys.argv[1])

def quote(value):
    return json.dumps(str(value), ensure_ascii=False)

source = backup / "service.json"
if source.exists():
    services = json.loads(source.read_text() or "{}")
    lines = []
    for name, targets in sorted(services.items()):
        lines.append(f"[{quote(name)}]")
        for backend in ("systemd", "docker"):
            if targets.get(backend):
                lines.append(f"{backend} = {quote(targets[backend])}")
        lines.append("")
    (backup / "services.toml").write_text("\n".join(lines))
    print(f"    服務：{len(services)} 個")

source = backup / "permissions.json"
if source.exists():
    grants = json.loads(source.read_text() or "{}").get("grants", [])
    lines = []
    for grant in grants:
        lines += ["[[grants]]", f"type = {quote(grant['type'])}", f"id = {int(grant['id'])}", f"level = {quote(grant['level'])}"]
        if grant.get("service"):
            lines.append(f"service = {quote(grant['service'])}")
        lines.append("")
    (backup / "permissions.toml").write_text("\n".join(lines))
    print(f"    權限：{len(grants)} 筆")
PY
    chmod 0600 "$BACKUP_DIR"/*.toml 2>/dev/null || true
fi

step "3. 刪除舊版"
if command -v docker >/dev/null; then
    if docker rm -f service-controller >/dev/null 2>&1; then info "已移除容器"; fi
    if [[ -n "$volume" ]]; then
        docker volume rm "$volume" >/dev/null
        info "已移除 volume $volume"
    fi
    if docker rmi service-controller:latest >/dev/null 2>&1; then info "已移除舊映像檔"; fi
fi

if [[ -f /etc/apparmor.d/service-controller ]]; then
    apparmor_parser -R /etc/apparmor.d/service-controller 2>/dev/null || true
    rm -f /etc/apparmor.d/service-controller
    info "已移除 AppArmor profile"
fi

if [[ -d "$OLD_DIR" ]]; then
    rm -rf "$OLD_DIR"
    info "已刪除 $OLD_DIR"
fi

step "4. 下載並安裝新版（$BRANCH）"
install -d -o "$SUDO_USER" -g "$(id -gn "$SUDO_USER")" "$(dirname "$OLD_DIR")"
sudo -u "$SUDO_USER" git clone --branch "$BRANCH" "$REPO_URL" "$OLD_DIR"

export DISCORD_TOKEN OWNER_USER_IDS ALLOWED_GUILD_IDS
"$OLD_DIR/scripts/install.sh" --yes

step "5. 匯入服務清單與權限"
if [[ -f "$BACKUP_DIR/services.toml" || -f "$BACKUP_DIR/permissions.toml" ]]; then
    systemctl stop service-controller

    for name in services.toml permissions.toml; do
        if [[ -f "$BACKUP_DIR/$name" ]]; then
            install -m 0600 -o svcbot -g svcbot "$BACKUP_DIR/$name" "$STATE_DIR/$name"
            info "已匯入 $name"
        fi
    done

    systemctl start service-controller
    sleep 3

    if ! systemctl is-active --quiet service-controller; then
        warn "匯入的資料無法通過驗證，改用空白設定啟動。原始資料保留在 $BACKUP_DIR"
        journalctl -u service-controller -n 5 --no-pager || true
        systemctl stop service-controller
        rm -f "$STATE_DIR/services.toml" "$STATE_DIR/permissions.toml"
        systemctl start service-controller
    fi
else
    info "沒有需要匯入的資料"
fi

step "移植完成"
cat <<EOF
    新版目錄：$OLD_DIR
    備份位置：$BACKUP_DIR（含舊 Token，確認沒問題後可刪除：sudo rm -rf $BACKUP_DIR）

    還沒做的話，請到 Discord Developer Portal → Bot 開啟「MESSAGE CONTENT INTENT」。
    在 Discord 用 !services show 確認服務設定，/access list 確認權限。
EOF

#!/usr/bin/env bash
# 註冊讓 Discord bot 可以執行的腳本。
#
#   sudo ./scripts/register-script.sh add <名稱> --user <使用者> [--workdir <目錄>] [--env KEY=VALUE ...] -- <腳本路徑> [參數 ...]
#   sudo ./scripts/register-script.sh remove <名稱>
#   sudo ./scripts/register-script.sh list
#
# 例：
#   sudo ./scripts/register-script.sh add blog-restart --user baihu -- /home/baihu/bin/blog-restart.sh --restart
#
# 每個腳本會變成一個 systemd oneshot unit（sc-script-<名稱>.service），以指定的使用者執行。
# Discord 中只能用名稱引用（script = "<名稱>"），無法指定路徑或參數。
set -euo pipefail

readonly UNIT_DIR="/etc/systemd/system"
readonly PREFIX="sc-script-"

die() { printf '\033[31m[錯誤] %s\033[0m\n' "$*" >&2; exit 1; }
warn() { printf '\033[33m[注意] %s\033[0m\n' "$*" >&2; }
usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

valid_name() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$ ]]; }

unit_path() { printf '%s/%s%s.service' "$UNIT_DIR" "$PREFIX" "$1"; }

# 依 systemd 的規則加上引號：跳脫 \ 與 "，% 寫成 %%，$ 寫成 $$（避免被展開）
quote() {
    local s="$1"
    [[ "$s" != *$'\n'* ]] || die "參數不能包含換行：$s"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//%/%%}"
    s="${s//\$/\$\$}"
    printf '"%s"' "$s"
}

# 路徑上的每一層都只能由 root 或執行的使用者修改，否則其他人可以換掉腳本內容
check_owner_chain() {
    local path="$1" user="$2" uid owner mode
    uid="$(id -u "$user")"

    while :; do
        owner="$(stat -c %u "$path")"
        mode="$(stat -c %a "$path")"

        if [[ "$owner" != 0 && "$owner" != "$uid" ]]; then
            die "$path 的擁有者不是 root$([[ $uid != 0 ]] && echo " 或 $user")，其他使用者可以修改它"
        fi

        # 目錄的 sticky bit（例如 /tmp）也不安全，一律拒絕群組或其他人可寫入
        if (( 8#$mode & 8#022 )); then
            die "$path 可以被群組或其他使用者寫入（權限 $mode），請先執行：chmod go-w $path"
        fi

        [[ "$path" == / ]] && break
        path="$(dirname "$path")"
    done
}

cmd_add() {
    local name="${1:-}"
    shift || true
    valid_name "$name" || die "名稱只能用英文、數字、- 和 _，必須以英數開頭，最多 32 字"

    local user="" workdir=""
    local -a envs=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --user) user="${2:-}"; shift 2 ;;
            --workdir) workdir="${2:-}"; shift 2 ;;
            --env)
                [[ "${2:-}" =~ ^[A-Za-z_][A-Za-z0-9_]*=.*$ ]] || die "--env 格式應為 KEY=VALUE"
                envs+=("$2")
                shift 2
                ;;
            --) shift; break ;;
            *) die "未知的參數：$1（腳本路徑前要加 --）" ;;
        esac
    done

    [[ -n "$user" ]] || die "請用 --user 指定執行腳本的使用者"
    [[ "$user" != svcbot ]] || die "不能以 bot 自己的帳號 svcbot 執行腳本"
    id "$user" >/dev/null 2>&1 || die "找不到使用者 $user"
    [[ $# -ge 1 ]] || die "請在 -- 之後指定腳本路徑"

    local script
    script="$(realpath -e -- "$1")" || die "找不到腳本 $1"
    shift
    [[ -f "$script" ]] || die "$script 不是一般檔案"
    [[ -x "$script" ]] || die "$script 沒有執行權限，請先執行：chmod +x $script"
    check_owner_chain "$script" "$user"

    if [[ -z "$workdir" ]]; then
        workdir="$(getent passwd "$user" | cut -d: -f6)"
    fi
    workdir="$(realpath -e -- "$workdir")" || die "找不到工作目錄"
    [[ -d "$workdir" ]] || die "$workdir 不是目錄"
    [[ "$workdir" != *$'\n'* ]] || die "工作目錄不能包含換行"

    local exec_start
    exec_start="$(quote "$script")"
    for arg in "$@"; do
        exec_start+=" $(quote "$arg")"
    done

    local unit tmp
    unit="$(unit_path "$name")"
    tmp="$(mktemp "$UNIT_DIR/.${PREFIX}${name}.XXXXXX")"

    {
        echo "# 由 service-controller 的 scripts/register-script.sh 產生，請用該腳本修改"
        echo "[Unit]"
        echo "Description=service-controller script: $name"
        echo
        echo "[Service]"
        echo "Type=oneshot"
        echo "User=$user"
        echo "Group=$(id -gn "$user")"
        # WorkingDirectory= 不接受引號，只需要跳脫 %
        echo "WorkingDirectory=${workdir//%/%%}"
        echo "ExecStart=$exec_start"
        # Discord 的互動回覆最多只能等 15 分鐘
        echo "TimeoutStartSec=14min"
        echo "Environment=NO_COLOR=1 TERM=dumb"
        for env in "${envs[@]}"; do
            echo "Environment=$(quote "$env")"
        done
    } >"$tmp"

    chmod 0644 "$tmp"
    mv "$tmp" "$unit"
    systemctl daemon-reload

    echo "已註冊 $name → $unit"
    echo "  使用者：$user"
    echo "  指令：  $script $*"
    echo

    if grep -qw sudo "$script"; then
        warn "腳本裡有用到 sudo。腳本由 systemd 執行時無法輸入密碼，需要為 $user 設定免密碼的 sudoers 規則，"
        warn "並在腳本中改用 sudo -n，例如：$user ALL=(root) NOPASSWD: /usr/bin/systemctl restart <服務名稱>"
        echo
    fi

    cat <<EOF
在主機上手動測試：
  sudo systemctl start ${PREFIX}${name}.service && journalctl -u ${PREFIX}${name}.service -n 30 --no-pager

在 Discord 中引用（/service restart 會改為執行這個腳本）：
  !services set
  \`\`\`toml
  [服務名稱]
  script = "$name"
  \`\`\`
EOF
}

cmd_remove() {
    local name="${1:-}"
    valid_name "$name" || die "名稱格式錯誤"

    local unit
    unit="$(unit_path "$name")"
    [[ -f "$unit" ]] || die "沒有註冊名為 $name 的腳本"

    rm -f "$unit"
    systemctl daemon-reload
    systemctl reset-failed "${PREFIX}${name}.service" 2>/dev/null || true
    echo "已移除 $name。Discord 設定中引用它的服務請一併用 !services set 更新。"
}

cmd_list() {
    local found=0
    for unit in "$UNIT_DIR"/"$PREFIX"*.service; do
        [[ -e "$unit" ]] || continue
        found=1
        local name
        name="$(basename "$unit" .service)"
        printf '%s\n' "${name#"$PREFIX"}"
        grep -E '^(User|ExecStart)=' "$unit" | sed 's/^/    /'
    done
    [[ $found -eq 1 ]] || echo "尚未註冊任何腳本"
}

[[ $# -ge 1 ]] || usage 2
command="$1"
shift

case "$command" in
    -h | --help) usage 0 ;;
    list) cmd_list ;;
    add | remove)
        [[ $EUID -eq 0 ]] || exec sudo -- "$0" "$command" "$@"
        "cmd_$command" "$@"
        ;;
    *) die "未知的指令：$command（可用：add、remove、list）" ;;
esac

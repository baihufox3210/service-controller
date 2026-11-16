#!/usr/bin/env bash
# 重啟個人網站（blog）
#
#   blog-restart.sh                    更新程式碼並重新部署
#   blog-restart.sh --restart          只重啟，不更新程式碼
#   blog-restart.sh --post <文章 id>   指定用來驗證分享預覽的文章 id（沒指定會詢問）
#
# 以使用者 baihu 執行；只有重啟 API 時會用到 sudo（需要免密碼的 sudoers 規則）。
#
# 讓 Discord bot 執行（/service restart）：
#   echo 'baihu ALL=(root) NOPASSWD: /usr/bin/systemctl restart baihu-blog-api' | sudo tee /etc/sudoers.d/blog-restart
#   sudo chmod 0440 /etc/sudoers.d/blog-restart
#   sudo ./scripts/register-script.sh add blog-restart --user baihu -- /home/baihu/bin/blog-restart.sh --restart --post <文章 id>
set -uo pipefail

readonly PROJECT="/data/projects/my-dashboard"
readonly API_SERVICE="baihu-blog-api"
readonly CONTAINER="my-dashboard"
readonly API_URL="http://172.17.0.1:3001/api/server-status"
readonly SITE="https://baihu-tw.com"
readonly NGINX_CONF="/etc/nginx/conf.d/default.conf"

RESTART_ONLY=0
POST_ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --restart) RESTART_ONLY=1 ;;
        --post) POST_ID="${2:-}"; shift ;;
        -h | --help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知的參數：$1" >&2; exit 2 ;;
    esac
    shift
done

if [[ "$(id -un)" != "baihu" ]]; then
    echo "請用使用者 baihu 執行（目前是 $(id -un)）" >&2
    exit 1
fi

cd "$PROJECT" || { echo "找不到專案目錄 $PROJECT" >&2; exit 1; }

RESULTS=()
VERIFY=()

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok() { RESULTS+=("成功  $1"); }

summary() {
    printf '\n\033[1m==== 結果 ====\033[0m\n'
    printf '  %s\n' "${RESULTS[@]}"
    if [[ ${#VERIFY[@]} -gt 0 ]]; then
        printf '\n\033[1m==== 驗證 ====\033[0m\n'
        printf '%s\n' "${VERIFY[@]}"
    fi
}

# 失敗時停下來：記錄哪一步、哪個指令失敗，印出結果後結束
fail() {
    RESULTS+=("失敗  $1")
    printf '\n\033[31m[停止] %s\033[0m\n' "$1" >&2
    [[ -n "${2:-}" ]] && printf '%s\n' "$2" >&2
    summary
    exit 1
}

check_container() {
    if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
        fail "找不到容器 $CONTAINER" "目前的容器：
$(docker ps --format '  {{.Names}}  {{.Image}}  {{.Status}}')"
    fi
}

restart_api() {
    step "重啟 API（$API_SERVICE）"
    # -n：不詢問密碼，沒有 sudoers 規則時直接失敗（由 bot 執行時沒有終端機可以輸入密碼）
    sudo -n systemctl restart "$API_SERVICE" || fail "sudo -n systemctl restart $API_SERVICE（需要免密碼的 sudoers 規則）"
    sleep 5
    if ! systemctl is-active --quiet "$API_SERVICE"; then
        fail "API 沒有成功啟動" "$(journalctl -u "$API_SERVICE" -n 50 --no-pager)"
    fi
    ok "重啟 API"
}

update_nginx() {
    step "更新 nginx 設定並重新載入"
    check_container

    docker cp deploy/nginx-default.conf "$CONTAINER:$NGINX_CONF" || fail "docker cp deploy/nginx-default.conf"

    local test_output
    test_output="$(docker exec "$CONTAINER" nginx -t 2>&1)"
    echo "$test_output"

    if [[ "$test_output" != *"syntax is ok"* || "$test_output" != *"test is successful"* ]]; then
        fail "nginx -t 沒有通過，未執行 reload" "$test_output"
    fi

    docker exec "$CONTAINER" nginx -s reload || fail "nginx -s reload"
    ok "更新 nginx 設定並重新載入"
}

manual_deploy() {
    if [[ $RESTART_ONLY -eq 0 ]]; then
        step "更新程式碼"
        local pull_output
        if ! pull_output="$(git pull --ff-only 2>&1)"; then
            fail "git pull --ff-only（本機有修改或分支分岔，請手動處理）" "$pull_output"
        fi
        echo "$pull_output"
        ok "更新程式碼"

        step "安裝套件"
        npm install --no-audit --no-fund || fail "npm install --no-audit --no-fund"
        ok "安裝套件"

        step "建置前端"
        npm run build || fail "npm run build"
        ok "建置前端"
    fi

    restart_api
    update_nginx
}

# ---- 部署 ----

if [[ -f deploy/update.sh ]]; then
    step "執行 npm run deploy$([[ $RESTART_ONLY -eq 1 ]] && echo " -- --restart")"
    log="$(mktemp)"
    if [[ $RESTART_ONLY -eq 1 ]]; then npm run deploy -- --restart 2>&1 | tee "$log"; else npm run deploy 2>&1 | tee "$log"; fi
    status=${PIPESTATUS[0]}

    if [[ $status -eq 0 ]] && grep -v '^[[:space:]]*$' "$log" | tail -n1 | grep -q 'Done'; then
        ok "npm run deploy"
    else
        RESULTS+=("失敗  npm run deploy（結束代碼 $status），改用手動步驟")
        manual_deploy
    fi
    rm -f "$log"
else
    RESULTS+=("略過  沒有 deploy/update.sh，使用手動步驟")
    manual_deploy
fi

# ---- 驗證 ----

step "驗證"
check_container
failed=0

verify() {
    local name="$1" output="$2" passed="$3"
    VERIFY+=("$( [[ $passed -eq 1 ]] && echo "[通過]" || echo "[失敗]" ) $name" "$output" "")
    [[ $passed -eq 1 ]] || failed=1
}

output="$(systemctl status "$API_SERVICE" --no-pager | head -5)"
verify "服務狀態" "$output" "$([[ "$output" == *"active (running)"* ]] && echo 1 || echo 0)"

output="$(curl -s -o /dev/null -w '%{http_code}' "$API_URL")"
verify "API 回應（要 200）" "$output" "$([[ "$output" == 200 ]] && echo 1 || echo 0)"

output="$(docker exec "$CONTAINER" grep -n 'blog/post' "$NGINX_CONF")"
verify "nginx 設定是新的（要包含 |b|p）" "$output" "$([[ "$output" == *"|b|p"* ]] && echo 1 || echo 0)"

if [[ -z "$POST_ID" && -t 0 ]]; then
    read -r -p "輸入任一篇文章的 id（至少前 8 碼，直接 Enter 略過分享預覽檢查）：" POST_ID
fi

if [[ -n "$POST_ID" ]]; then
    output="$(curl -s "$SITE/b/${POST_ID:0:8}abc" | grep og:title)"
    verify "分享預覽 og:title（/b/${POST_ID:0:8}abc）" "${output:-（沒有輸出）}" "$([[ -n "$output" ]] && echo 1 || echo 0)"
else
    VERIFY+=("[略過] 分享預覽 og:title（沒有提供文章 id，可用 --post <id> 指定）" "")
fi

output="$(curl -s -o /dev/null -w '%{http_code}' "$SITE/")"
verify "首頁（要 200）" "$output" "$([[ "$output" == 200 ]] && echo 1 || echo 0)"

summary

if [[ $failed -eq 1 ]]; then
    cat <<'EOF'

有驗證項目失敗：
  - 對外預覽沒有 og:title，但 API 正常：多半是 nginx 還是舊設定，用 --restart 再跑一次
  - 出現 Cannot GET /b/...：API 還在跑舊程式，確認 git log 是最新的 commit 後再重跑
  - API 起不來：journalctl -u baihu-blog-api -n 50 --no-pager
EOF
    exit 1
fi

printf '\n全部完成\n'

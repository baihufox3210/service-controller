#!/usr/bin/env bash
# 移除 service-controller。
#
#   sudo ./scripts/uninstall.sh           移除服務、執行檔與 polkit 規則，保留設定與資料
#   sudo ./scripts/uninstall.sh --purge   連同設定、服務清單、權限資料與 svcbot 帳號一起刪除
set -euo pipefail

PURGE=0

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h | --help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知的參數：$arg" >&2; exit 2 ;;
    esac
done

[[ $EUID -eq 0 ]] || exec sudo -- "$0" "$@"

echo "==> 停止並移除 systemd 服務"
systemctl disable --now service-controller 2>/dev/null || true
rm -f /etc/systemd/system/service-controller.service
systemctl daemon-reload
systemctl reset-failed service-controller 2>/dev/null || true

echo "==> 移除執行檔與 polkit 規則"
rm -f /usr/local/bin/service-controller
rm -f /etc/polkit-1/rules.d/50-service-controller.rules
rm -f /etc/polkit-1/localauthority/50-local.d/service-controller.pkla

if [[ $PURGE -eq 1 ]]; then
    echo "==> 刪除設定、資料與 svcbot 帳號"
    rm -rf /etc/service-controller /var/lib/service-controller
    userdel svcbot 2>/dev/null || true
else
    echo "    保留 /etc/service-controller 與 /var/lib/service-controller（要一併刪除請加上 --purge）"
fi

echo "==> 完成"

# Service Controller

透過 Discord 斜線指令管理主機上的 **systemd 服務** 與 **Docker 容器**，內建角色權限控制、輸入驗證與稽核日誌。

## 功能

| 指令 | 說明 | 所需權限 |
| --- | --- | --- |
| `/service list` | 列出所有已註冊服務 | 檢視者 |
| `/service status <service>` | 查詢執行狀態與開機自動啟動狀態 | 檢視者 |
| `/service logs <service> [lines]` | 查詢日誌（1–500 行，僅自己可見） | 檢視者 |
| `/service start\|stop\|restart <service>` | 啟動 / 關閉 / 重啟 | 操作者 |
| `/service enable\|disable <service>` | 設定 / 取消開機自動啟動 | 操作者 |
| `/service add <name> <backend> <target>` | 註冊服務 | 管理員 |
| `/service remove <service>` | 移除已註冊的服務 | 管理員 |
| `/service whoami` | 查看自己的權限 | 任何人 |

支援的後端：

| 後端 | target | enable / disable 的意義 | 日誌來源 |
| --- | --- | --- | --- |
| `systemd` | unit 名稱，限 `.service` / `.socket` / `.timer`，例如 `nginx.service` | `systemctl enable/disable` | `journalctl --unit` |
| `docker` | 容器名稱，例如 `my-web` | restart policy 設為 `unless-stopped` / `no` | `docker logs` |

## 權限模型

權限分為三級，高等級包含低等級的所有權限：

- **檢視者 (VIEWER)**：`list`、`status`、`logs`
- **操作者 (OPERATOR)**：檢視者權限 + `start`、`stop`、`restart`、`enable`、`disable`
- **管理員 (ADMIN)**：操作者權限 + `add`、`remove`

權限透過環境變數，以 Discord **使用者 ID** 或 **身分組 ID** 授予（見 `.env.example`）。

- **預設拒絕**：沒有列在設定中的人沒有任何權限，連自動完成都看不到服務清單。
- **伺服器白名單**：設定 `ALLOWED_GUILD_IDS` 後，其他伺服器無法使用機器人。
- 所有指令僅能在伺服器中使用，不接受私訊。
- 控制類指令有冷卻時間（每人每 5 秒 1 次），避免誤觸連發。
- 每次操作與權限拒絕都會寫入 `system_controller.audit` 日誌（使用者、伺服器、頻道、動作、結果）。

> Discord 的「伺服器設定 → 整合」也可以再限制誰看得到指令，但那只是介面層級；真正的權限檢查一律由機器人執行。

## 安全設計

- 服務名稱與 target 皆以白名單正規表示式驗證，不允許以 `-` 開頭，並在指令中加上 `--`，防止參數注入。
- 只能操作透過 `/service add` 註冊過的服務；使用者無法直接指定任意 unit 或容器。
- 子程序以參數陣列執行（不經過 shell），逾時會被強制終止。
- 需要 root 的操作透過 `sudo -n` 執行，搭配 `deploy/sudoers.example` 只開放指定的指令；查詢類 systemctl 指令不經過 sudo。
- 機器人不會發出任何 @mention，輸出內容會跳脫程式碼區塊，日誌回覆僅呼叫者可見。
- 服務清單以原子寫入（暫存檔 + rename）儲存，檔案權限 `600`。
- 不需要任何 privileged gateway intent。

> ⚠️ 將帳號加入 `docker` 群組等同給予 root 權限。建議保留 `DOCKER_USE_SUDO=true`，並用 sudoers 限制只能操作指定容器。

## 安裝與執行

需求：Python 3.13+、[uv](https://docs.astral.sh/uv/)。

```bash
uv sync
cp .env.example .env    # 填入 BOT_TOKEN 與權限設定
uv run system-controller
```

### 部署建議

1. 建立專用帳號：`sudo useradd --system --create-home svcbot`
2. 將專案放在 `/opt/service-controller`，執行 `uv sync --no-dev`
3. 依 `deploy/sudoers.example` 建立 `/etc/sudoers.d/service-controller`（`sudo visudo -f ...`），只列出要開放的服務與容器
4. 若要讀取 systemd 日誌：`sudo usermod -aG systemd-journal svcbot`
5. 安裝 `deploy/service-controller.service` 為 systemd 服務：

```bash
sudo cp deploy/service-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now service-controller
```

## 服務清單格式

`database/service.json`（可用 `SERVICES_FILE` 更改位置）：

```json
{
    "nginx": { "backend": "systemd", "target": "nginx.service" },
    "web":   { "backend": "docker",  "target": "my-web" }
}
```

舊版格式 `{"nginx": "nginx.service"}` 仍可讀取，會視為 systemd 服務；不合法的項目會被略過並記錄警告。

## 開發

```bash
uv run pytest
```

架構：

```
src/system_controller/
├── adapters/      # 各後端的指令執行（systemd、docker）
├── cogs/          # Discord 斜線指令
├── core/          # 設定、權限、Bot
├── models/        # Service 資料模型與驗證
├── repositories/  # 服務清單持久化
└── services/      # 業務邏輯，依後端分派操作
```

新增後端時，實作 `adapters/base.py` 中的 `ServiceBackend` 介面，並在 `models/service.py` 的 `Backend` 與 `TARGET_PATTERNS` 加入對應項目。

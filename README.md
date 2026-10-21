# Service Controller

透過 Discord 斜線指令管理主機上的服務。每個服務可以**同時對應 systemd unit 與 Docker 容器**，操作時可指定 `systemd`、`docker` 或 `all`；權限可透過指令管理，並可針對單一服務授權。

## 指令

### `/service` — 服務控制

| 指令 | 說明 | 所需權限 |
| --- | --- | --- |
| `/service list` | 列出你有權限存取的服務 | — |
| `/service status <service> [backend]` | 查詢執行狀態與開機自動啟動狀態 | 檢視者 |
| `/service logs <service> [backend] [lines]` | 查詢日誌（每個後端 1–500 行，僅自己可見） | 檢視者 |
| `/service start\|stop\|restart <service> [backend]` | 啟動 / 關閉 / 重啟 | 操作者 |
| `/service enable\|disable <service> [backend]` | 設定 / 取消開機自動啟動 | 操作者 |
| `/service add <name> [unit] [container]` | 新增服務（至少填一個） | 管理員 |
| `/service set <service> <backend> <target>` | 為服務新增或更換 unit / 容器 | 管理員 |
| `/service unset <service> <backend>` | 移除服務的 unit 或容器設定 | 管理員 |
| `/service remove <service>` | 移除服務（連同其單一服務授權） | 管理員 |

`backend` 可選 `全部`（預設）、`systemd`、`Docker`。選「全部」時會依序操作所有已設定的後端：啟動類操作先 systemd 後 Docker，`stop` / `disable` 則相反。每個後端的結果會分別回報。

| 後端 | target | enable / disable 的意義 | 日誌來源 |
| --- | --- | --- | --- |
| `systemd` | unit 名稱，限 `.service` / `.socket` / `.timer`，例如 `nginx.service` | `systemctl enable/disable` | `journalctl --unit` |
| `docker` | 容器名稱，例如 `my-web` | restart policy 設為 `unless-stopped` / `no` | `docker logs` |

### `/access` — 權限管理

| 指令 | 說明 | 所需權限 |
| --- | --- | --- |
| `/access grant <使用者或身分組> <level> [service]` | 授予權限；指定 `service` 則只對該服務有效 | 管理員（授予管理員需擁有者） |
| `/access revoke <使用者或身分組> [service]` | 撤銷全域或單一服務授權 | 管理員（撤銷管理員需擁有者） |
| `/access list [service]` | 列出權限設定 | 管理員 |
| `/access whoami [service]` | 查看自己的權限 | — |

## 權限模型

| 等級 | 可做的事 | 來源 |
| --- | --- | --- |
| 擁有者 | 全部，且可新增 / 移除管理員 | `.env` 的 `OWNER_USER_IDS`（留空則為 Discord 應用程式擁有者），不能透過指令移除 |
| 管理員 | 管理服務、授予 / 撤銷操作者與檢視者 | `/access grant`（全域） |
| 操作者 | 控制服務 + 檢視 | `/access grant`（全域或單一服務） |
| 檢視者 | `status`、`logs` | `/access grant`（全域或單一服務） |

- 權限可授予**使用者**或**身分組**，有多個授權時取最高者。
- **單一服務授權**只對該服務有效，例如讓某個身分組只能重啟 `web`。單一服務最高只能授予操作者。
- 管理員無法新增、移除或降級其他管理員，避免管理員之間互相移除。
- **預設拒絕**：沒有授權的人什麼都不能做，連自動完成都看不到服務。
- **伺服器白名單**：設定 `ALLOWED_GUILD_IDS` 後，其他伺服器無法使用；指令不接受私訊。
- 控制類指令有冷卻時間（每人每 5 秒 1 次）。
- 所有操作、授權變更與權限拒絕都會寫入 `system_controller.audit` 日誌。

## 安全設計

- 服務名稱與 target 皆以白名單正規表示式驗證，不允許以 `-` 開頭，並在指令中加上 `--`，防止參數注入。
- 只能操作透過 `/service add` 註冊過的服務；一般使用者無法直接指定任意 unit 或容器。
- 子程序以參數陣列執行（不經過 shell），逾時會被強制終止。
- 需要 root 的操作透過 `sudo -n` 執行，搭配 `deploy/sudoers.example` 只開放指定的指令；查詢類 systemctl 指令不經過 sudo。
- 機器人不會發出任何 @mention，輸出內容會跳脫程式碼區塊，日誌回覆僅呼叫者可見。
- 資料檔以原子寫入（暫存檔 + rename）儲存，檔案權限 `600`。
- 不需要任何 privileged gateway intent。

> ⚠️ 將帳號加入 `docker` 群組等同給予 root 權限。建議保留 `DOCKER_USE_SUDO=true`，並用 sudoers 限制只能操作指定容器。

## 安裝與執行

需求：Python 3.13+、[uv](https://docs.astral.sh/uv/)。

```bash
uv sync
cp .env.example .env    # 填入 BOT_TOKEN（以及建議填 OWNER_USER_IDS、ALLOWED_GUILD_IDS）
uv run system-controller
```

啟動後由擁有者在 Discord 中設定權限，例如：

```
/access grant @維運組 admin
/access grant @網站組 operator service:web
/service add name:web unit:web.service container:web
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

## 資料格式

`database/service.json`（`SERVICES_FILE`）：

```json
{
    "web": { "systemd": "web.service", "docker": "web" },
    "db":  { "docker": "postgres" }
}
```

舊版格式 `{"nginx": "nginx.service"}` 仍可讀取，會視為只有 systemd 的服務。

`database/permissions.json`（`PERMISSIONS_FILE`，由 `/access` 指令維護）：

```json
{
    "grants": [
        { "type": "role", "id": 123, "level": "admin", "service": null },
        { "type": "user", "id": 456, "level": "operator", "service": "web" }
    ]
}
```

不合法的項目會被略過並記錄警告。

## 開發

```bash
uv run pytest
```

架構：

```
src/system_controller/
├── adapters/      # 各後端的指令執行（systemd、docker），共用 control / state / logs 介面
├── cogs/          # Discord 斜線指令（/service、/access）
├── core/          # 設定、權限檢查、Bot、共用互動工具
├── models/        # Service、Permission 資料模型與驗證
├── repositories/  # 服務清單與權限的 JSON 持久化
└── services/      # 業務邏輯，依後端分派操作
```

新增後端時，實作 `adapters/base.py` 中的 `ServiceBackend` 介面，並在 `models/service.py` 的 `Backend` 與 `TARGET_PATTERNS` 加入對應項目。

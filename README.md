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
| `systemd` | unit 名稱，例如 `nginx`（自動補上 `.service`）；也可明確指定 `.socket` / `.timer`，其他類型（如 `.target`、`.mount`）會被拒絕 | `systemctl enable/disable` | `journalctl --unit` |
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

## 部署（Docker）

機器人本身跑在容器裡，透過掛載進來的 socket 操作**主機**的 Docker 與 systemd：

| 用途 | 掛載 | 權限來源 |
| --- | --- | --- |
| Docker 後端 | `/var/run/docker.sock` | 主機 `docker` 群組（`DOCKER_GID`） |
| systemd 控制 / 狀態 | `/run/dbus/system_bus_socket`、`/run/systemd/system` | 主機的 polkit 規則 |
| systemd 日誌 | `/var/log/journal`、`/etc/machine-id`（唯讀） | 主機 `systemd-journal` 群組（`JOURNAL_GID`） |
| 服務與權限資料 | named volume `data` → `/app/database` | — |

### 1. 設定 `.env`

```bash
cp .env.example .env
```

填入 `BOT_TOKEN`、建議填 `OWNER_USER_IDS` 與 `ALLOWED_GUILD_IDS`，以及主機的群組 ID：

```bash
getent group docker | cut -d: -f3           # → DOCKER_GID
getent group systemd-journal | cut -d: -f3  # → JOURNAL_GID
```

### 2. 設定 systemd 權限（只用 Docker 後端可略過）

容器以 UID `10001` 執行。在主機建立同 UID 的使用者，並安裝 polkit 規則，允許它操作主機上的 systemd unit（實際可操作的服務由 `/service add` 註冊的服務決定）：

```bash
sudo useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin svcbot
sudo cp deploy/polkit/50-service-controller.rules /etc/polkit-1/rules.d/
```

- 規則檔需要使用 JavaScript 規則的 polkit（polkit 0.106 以上，Debian 12+、Ubuntu 23.04+ 等）。
- ⚠️ 此規則等同讓 `svcbot` 擁有主機 root 權限，請妥善保護 `BOT_TOKEN`。
- 日誌需要主機啟用持久化 journal（`/var/log/journal` 存在）。

**使用 AppArmor 的主機（Ubuntu、Debian 等）** 還需要載入專用 profile。Docker 預設的 `docker-default` profile 會擋掉容器的 D-Bus 通訊，查詢狀態會出現 `Access denied`：

```bash
sudo cp deploy/apparmor/service-controller /etc/apparmor.d/service-controller
sudo apparmor_parser -r -W /etc/apparmor.d/service-controller
```

`compose.yaml` 預設使用這個 profile（`APPARMOR_PROFILE`）。主機沒有 AppArmor 時 Docker 會忽略此設定。

### 3. 啟動

```bash
docker compose up -d --build
docker compose logs -f
```

啟動後由擁有者在 Discord 中設定權限，例如：

```
/access grant @維運組 admin
/access grant @網站組 operator service:web
/service add name:web unit:web container:web
```

資料存在 named volume `data`，更新映像檔不會遺失；備份可用：

```bash
docker compose cp service-controller:/app/database ./backup
```

## 安全設計

- 服務名稱與 target 皆以白名單正規表示式驗證，不允許以 `-` 開頭，並在指令中加上 `--`，防止參數注入。
- 只能操作透過 `/service add` 註冊過的服務；一般使用者無法直接指定任意 unit 或容器。
- 子程序以參數陣列執行（不經過 shell），逾時會被強制終止。
- 容器以非 root（UID 10001）執行，根目錄唯讀、移除所有 capabilities、啟用 `no-new-privileges`，並限制記憶體與行程數。
- systemd 操作透過主機 polkit 授權給專用使用者 `svcbot`，不使用 sudo。
- 機器人不會發出任何 @mention，輸出內容會跳脫程式碼區塊，日誌回覆僅呼叫者可見。
- 資料檔以原子寫入（暫存檔 + rename）儲存，檔案權限 `600`。
- 不需要任何 privileged gateway intent。

> ⚠️ **Docker socket 等同主機 root 權限**：能存取它就能建立特權容器。機器人只會執行白名單內的指令，但如果容器本身被攻破，主機也會受影響。請妥善保護 `BOT_TOKEN`，並以 `ALLOWED_GUILD_IDS` 與權限設定縮小可操作的範圍。只需要 systemd 後端時，可以從 `compose.yaml` 移除 docker.sock 掛載與 `DOCKER_GID`。

## 資料格式

`/app/database/service.json`：

```json
{
    "web": { "systemd": "web.service", "docker": "web" },
    "db":  { "docker": "postgres" }
}
```

`/app/database/permissions.json`（由 `/access` 指令維護）：

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
uv sync
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

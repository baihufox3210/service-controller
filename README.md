# Service Controller

用 Discord 控制主機上的 **systemd 服務**、**Docker 容器** 與 **重啟腳本**。以 Rust 撰寫，編譯成單一執行檔，直接以 systemd 服務的形式跑在主機上。

- 一個服務可以同時對應 systemd unit、Docker 容器與腳本，操作時可選 `全部`、`systemd`、`Docker` 或 `腳本`
- 有設定腳本的服務，`/service restart` 會改為執行腳本（例如 `git pull`、建置、重啟、檢查），並把輸出顯示在 Discord
- 要控制哪些服務，直接在 Discord 用 `!services` 加上 TOML 程式碼區塊設定
- 權限分級（擁有者／管理員／操作者／檢視者），可針對單一服務授權，預設拒絕
- 安裝、更新、移除都由腳本完成

## 安裝

需求：使用 systemd 的 Linux 主機。腳本會視需要安裝 Rust 工具鏈與 C 編譯器。

**1. 建立 Discord Bot**

1. 到 [Discord Developer Portal](https://discord.com/developers/applications) 建立 Application，在 **Bot** 頁面取得 Token。
2. 同一頁開啟 **MESSAGE CONTENT INTENT**（`!services` 指令需要讀取訊息內容）。建議關閉 **Public Bot**。
3. 在 **OAuth2 → URL Generator** 勾選 `bot` 與 `applications.commands`，用產生的連結把 bot 邀進伺服器。

**2. 執行安裝腳本**

```bash
git clone https://github.com/baihufox3210/service-controller.git
cd service-controller
sudo ./scripts/install.sh
```

腳本會詢問：

| 項目 | 說明 |
| --- | --- |
| Discord Bot Token | 必填 |
| 擁有者的使用者 ID | 開啟 Discord 開發者模式後，右鍵自己的頭像 →「複製使用者 ID」。多人以逗號分隔；留空則使用 Application 的擁有者 |
| 允許使用的伺服器 ID | 右鍵伺服器圖示 →「複製伺服器 ID」。留空表示不限制 |
| 前綴指令的符號 | 預設 `!` |

腳本會依序完成：

1. 編譯 release 版本，安裝到 `/usr/local/bin/service-controller`
2. 建立專用帳號 `svcbot`，加入 `docker` 與 `systemd-journal` 群組
3. 寫入設定檔 `/etc/service-controller/service-controller.env`（權限 0640）
4. 安裝 polkit 規則，讓 `svcbot` 可以操作 systemd（舊版 polkit 會自動改用 `.pkla`）
5. 偵測並移除舊版的 Docker 部署
6. 安裝並啟動 systemd 服務 `service-controller`

### 從舊版（Python + Docker）移植

`scripts/migrate.sh` 會讀取舊版 `.env`、把舊的服務清單與權限轉換成新版格式、刪除舊版的容器／volume／映像檔／AppArmor profile 與程式碼，接著下載新版並安裝、匯入資料：

```bash
curl -fsSL https://raw.githubusercontent.com/baihufox3210/service-controller/master/scripts/migrate.sh -o /tmp/migrate.sh
sudo bash /tmp/migrate.sh /data/projects/service-controller
```

舊設定與匯出的資料會備份在 `/root/service-controller-migration-<時間>`，確認沒問題後可以刪除。

## 維護

| 要做的事 | 指令 |
| --- | --- |
| 更新到最新版 | `git pull && sudo ./scripts/install.sh` |
| 修改 Token、擁有者等設定 | `sudo ./scripts/install.sh --reconfigure` |
| 查看日誌（含稽核紀錄） | `journalctl -u service-controller -f` |
| 重啟 bot | `sudo systemctl restart service-controller` |
| 移除（保留設定與資料） | `sudo ./scripts/uninstall.sh` |
| 完全移除 | `sudo ./scripts/uninstall.sh --purge` |

## 註冊重啟腳本

需要多個步驟的重啟流程（更新程式碼、建置、重啟、檢查）可以寫成腳本，讓 `/service restart` 執行。腳本必須先由 root 在主機上註冊，Discord 裡只能用名稱引用，無法指定路徑或參數：

```bash
sudo ./scripts/register-script.sh add blog-restart --user baihu -- /home/baihu/bin/blog-restart.sh --restart
sudo ./scripts/register-script.sh list
sudo ./scripts/register-script.sh remove blog-restart
```

| 選項 | 說明 |
| --- | --- |
| `--user` | 執行腳本的使用者（必填，不能是 `svcbot`） |
| `--workdir` | 工作目錄，預設為該使用者的家目錄 |
| `--env KEY=VALUE` | 額外的環境變數，可重複，例如 `--env PATH=/home/baihu/.nvm/versions/node/v22/bin:/usr/bin:/bin` |
| `--` 之後 | 腳本的完整路徑與固定參數 |

- 每個腳本會變成 systemd oneshot unit `sc-script-<名稱>.service`，最長執行 14 分鐘（Discord 回覆的時間上限）。
- 腳本檔案與它的每一層上層目錄，都只能由 root 或執行的使用者修改，否則會拒絕註冊，避免被其他人換掉內容。
- 腳本由 systemd 執行，沒有終端機可以輸入密碼。需要 `sudo` 的話，要設定免密碼的 sudoers 規則並改用 `sudo -n`。
- 註冊後可以先在主機上測試：`sudo systemctl start sc-script-blog-restart.service && journalctl -u sc-script-blog-restart.service -n 30`

`examples/blog-restart.sh` 是個人網站的重啟腳本範例，檔頭有完整的註冊步驟。

## 設定要控制的服務（`!services`）

在 Discord 傳送指令，下面接著 TOML 程式碼區塊：

````
!services set
```toml
[blog]
systemd = "baihu-blog-api"     # 可省略 .service
docker = "my-dashboard"
script = "blog-restart"        # 主機上註冊的腳本名稱

[mem0]
docker = "mem0-dev-mem0-1"
```
````

| 指令 | 說明 |
| --- | --- |
| `!services` | 顯示說明 |
| `!services show` | 以 TOML 顯示目前的設定（可直接複製修改） |
| `!services set` + 程式碼區塊 | 新增或更新區塊中列出的服務，其他服務不變 |
| `!services replace` + 程式碼區塊 | 以區塊內容完整取代設定，沒列出的服務會被移除 |
| `!services remove 名稱 [名稱 ...]` | 移除服務 |

- 每個服務至少要設定 `systemd`、`docker`、`script` 其中之一，只接受這三個欄位。
- `script` 只能填註冊時的名稱，不能填路徑；名稱不存在時執行會失敗並提示註冊方式。
- systemd 沒寫副檔名時自動補上 `.service`；可明確指定 `.socket`、`.timer`，其他類型（如 `.target`、`.mount`）會被拒絕。
- 這些指令只有管理員以上可以使用，回覆會列出新增、更新、移除了哪些服務。
- 每次修改前，舊設定會備份成 `/var/lib/service-controller/services.toml.bak`。

## 斜線指令

### `/service` — 服務控制

| 指令 | 說明 | 所需權限 |
| --- | --- | --- |
| `/service list` | 列出你有權限存取的服務 | — |
| `/service status <service> [backend]` | 執行狀態與開機啟動狀態；腳本則顯示上次執行的結果與時間 | 檢視者 |
| `/service logs <service> [backend] [lines]` | 日誌（每個後端 1–500 行，僅自己可見） | 檢視者 |
| `/service start\|stop\|restart <service> [backend]` | 啟動／關閉／重啟 | 操作者 |
| `/service enable\|disable <service> [backend]` | 設定／取消開機自動啟動 | 操作者 |

- `backend` 預設為全部。啟動類操作先 systemd 後 Docker，`stop`、`disable` 則相反。
- 有設定腳本時，`restart`（未指定後端）只執行腳本，由腳本負責完整流程；其他操作照常操作 systemd 與 Docker。指定 `backend:systemd` 或 `Docker` 時仍會直接重啟該後端。腳本只支援 `restart`。
- Docker 的 enable／disable 是把 restart policy 設為 `unless-stopped`／`no`。
- 控制指令每人每 5 秒限用一次，最長等待 120 秒（腳本 14 分鐘）；逾時會提示用 `status` 確認。

### `/access` — 權限管理

| 指令 | 說明 | 所需權限 |
| --- | --- | --- |
| `/access grant <level> [user\|role] [service]` | 授予權限；指定 `service` 則只對該服務有效 | 管理員（授予管理員需擁有者） |
| `/access revoke [user\|role] [service]` | 撤銷全域或單一服務授權 | 管理員（撤銷管理員需擁有者） |
| `/access list [service]` | 列出權限設定 | 管理員 |
| `/access whoami [service]` | 查看自己的權限 | — |

| 等級 | 可做的事 |
| --- | --- |
| 擁有者 | 全部，且可管理管理員。來自設定檔，不能用指令移除 |
| 管理員 | 設定服務（`!services`）、管理操作者與檢視者 |
| 操作者 | 控制服務 + 檢視 |
| 檢視者 | `status`、`logs` |

單一服務授權最高只能是操作者；有多個授權時取最高者。

## 安全設計

- **輸入驗證**：`!services` 只接受一個 `toml` 程式碼區塊，限制 16 KB、100 個服務；未知欄位、型別錯誤一律拒絕。服務名稱只允許英數、`_`、`-`；unit 與容器名稱必須以英數開頭，不能含空白或 shell 符號。
- **不經過 shell**：所有外部指令都以參數陣列執行，並加上 `--` 防止名稱被當成選項；執行前會再驗證一次。
- **只能操作已設定的服務**：一般使用者無法指定任意 unit 或容器。
- **腳本由 root 註冊**：Discord 只能引用主機上已註冊的腳本名稱，執行的檔案、參數與使用者都由 root 決定；腳本檔案的路徑必須只有 root 或執行的使用者能修改。
- **最小權限**：bot 以 `svcbot` 執行，沒有任何 capability，並啟用 systemd 沙箱（唯讀檔案系統、`NoNewPrivileges` 等）。systemd 的操作由 polkit 授權，不使用 sudo。
- **其他**：編輯過的訊息不會重新執行指令；bot 不會 @ 任何人；輸出會跳脫程式碼區塊；日誌只有呼叫者看得到；狀態檔以原子寫入、權限 0600；所有操作都記錄在稽核日誌。

> ⚠️ `docker` 群組與 polkit 規則讓 `svcbot` 實質上擁有主機的 root 權限。請保護好 Bot Token 與設定檔，管理員權限只給信任的人。

## 檔案位置

| 路徑 | 內容 |
| --- | --- |
| `/usr/local/bin/service-controller` | 執行檔 |
| `/etc/service-controller/service-controller.env` | Token、擁有者、伺服器、前綴 |
| `/var/lib/service-controller/services.toml` | 服務設定（由 `!services` 維護） |
| `/var/lib/service-controller/permissions.toml` | 權限設定（由 `/access` 維護） |
| `/etc/systemd/system/service-controller.service` | systemd unit |
| `/etc/polkit-1/rules.d/50-service-controller.rules` | polkit 規則 |
| `/etc/systemd/system/sc-script-<名稱>.service` | 註冊的腳本（由 `scripts/register-script.sh` 產生） |

## 開發

```bash
cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt
```

| 模組 | 內容 |
| --- | --- |
| `model.rs` | 服務模型與名稱驗證 |
| `config_text.rs` | 解析 Discord 中的 TOML 設定 |
| `registry.rs`、`permissions.rs`、`store.rs` | 服務清單、權限與狀態檔 |
| `backend/` | 執行 systemctl、journalctl、docker 與註冊的腳本 |
| `controller.rs` | 依後端分派操作並彙整結果 |
| `discord/` | 斜線指令、前綴指令與 Embed |

# HKG 低速動態轉向扭力實驗分支

分支：`TonyBinheWu/sunnypilot:ev6-low-speed-torque`

功能已擴充為 Hyundai／Kia／Genesis（HKG）CAN-FD 車型共用的可選開關。保留原分支名稱與本文件路徑，既有安裝網址繼續有效。

基底為本儲存庫的 `master`，提交 `ca717be308d052389ac54beaa9eed1923b704e07`，包含已合併的 sunnylink 日文、繁體中文與韓文介面翻譯。

## 安裝

在 comma 裝置的「Custom Software／自訂軟體」安裝畫面輸入：

```text
install.sunnypilot.ai/fork/TonyBinheWu/ev6-low-speed-torque
```

完整網址：<https://install.sunnypilot.ai/fork/TonyBinheWu/ev6-low-speed-torque>

`/fork/TonyBinheWu/` 指定此帳號的 `sunnypilot` 儲存庫。只輸入 `install.sunnypilot.ai/ev6-low-speed-torque` 不會指向這個 fork。

這是原始碼分支。安裝器會下載程式與子模組，首次啟動沿用 `launch_chffrplus.sh` → `openpilot/system/manager/build.py` 的 SCons 編譯流程；分支沒有 `prebuilt` 標記。首次安裝須保留穩定供電、Wi-Fi 與下載模型及編譯的時間。AGNOS 版本依本基底的 `launch_env.sh` 為 19.7，更新流程沿用基底。

目前裝有其他版本時，先在停車狀態透過原本的解除安裝流程回到自訂軟體畫面。不要在行駛時安裝或切換分支。

## 適用車型與設定

在裝置的 **Settings → Steering → HKG 低速轉向扭力（實驗功能）** 開啟。英文名稱為 **HKG Low-Speed Steering Torque (Experimental)**。

- 預設關閉，包括從舊 EV6 版本更新的裝置；更新後需手動開啟。
- 僅在非行車狀態且已辨識到相容車型時才能調整。首次使用若尚未辨識車型，先啟動車輛完成辨識，再回到非行車狀態設定。
- 設定在下次啟動行車系統時生效。停車但仍在行車模式時不會即時切換；關閉開關後，下次行車恢復原上限。
- sunnylink 的 Steering 設定定義也加入同一開關、非行車限制與相容性判斷。

條件為：已收錄的 HKG CAN-FD 平台、扭力控制、`hyundaiCanfd` Panda 模式，且沒有 `ALT_LIMITS`、`ALT_LIMITS_2` 或 `dashcamOnly` 限制。UI 與車輛初始化共用同一相容性函式。手動寫入設定也不能在不相容車型上啟用。

**傳統 CAN 與角度控制車型不支援此功能。** 傳統 CAN 車型有 170、255、270、384 等不同上限，不能直接套用此曲線。這不會新增未收錄的年式或 EPS 韌體支援，也不保證停車或髮夾彎可以自動完成。

目前符合架構條件的 19 個平台如下；這是程式相容性範圍，不代表每一款車都已完成實車驗證。

| 品牌 | 平台識別碼 |
| --- | --- |
| Hyundai | `HYUNDAI_IONIQ_5`、`HYUNDAI_IONIQ_6`、`HYUNDAI_KONA_EV_2ND_GEN`、`HYUNDAI_SANTA_CRUZ_1ST_GEN`、`HYUNDAI_STARIA_4TH_GEN`、`HYUNDAI_TUCSON_4TH_GEN` |
| Kia | `KIA_CARNIVAL_4TH_GEN`、`KIA_EV6`、`KIA_K8_HEV_1ST_GEN`、`KIA_NIRO_EV_2ND_GEN`、`KIA_NIRO_HEV_2ND_GEN`、`KIA_SORENTO_4TH_GEN`、`KIA_SORENTO_HEV_4TH_GEN`、`KIA_SPORTAGE_5TH_GEN` |
| Genesis | `GENESIS_G80_2ND_GEN_FL`、`GENESIS_GV60_EV_1ST_GEN`、`GENESIS_GV70_1ST_GEN`、`GENESIS_GV70_ELECTRIFIED_1ST_GEN`、`GENESIS_GV80` |

開關啟用時的曲線：

| 車速 | 轉向 CAN 指令絕對值上限 |
| --- | ---: |
| ≤ 46.8 km/h（13 m/s） | 310 |
| 46.8～61.2 km/h（13～17 m/s） | 310 線性降至 270 |
| ≥ 61.2 km/h（17 m/s） | 270 |

310 相較原 270 增加約 14.8%。這是 CAN 指令數值，不是 Nm，也不能推論實際轉向扭矩或可完成彎道增加相同比例。

保留扭力上升／下降速率 2／3、即時變化限制 112、駕駛扭力 allowance／multiplier 250／2，以及既有約 85° 的 EPS 故障避免邏輯。速度取自 `vEgoRaw`，不依模型版本或飽和狀態切換。開關關閉時，CAN-FD 上限保持原本的 270。

## 整合方式

將 [opendbc PR #3720](https://github.com/commaai/opendbc/pull/3720) 的候選實作移植到主分支原本釘選的 sunnypilot/opendbc 提交 `f95f996f5917dcbbf2e32fe51b606a24cf836af6`，保留 sunnypilot 的 `CarParamsSP`、`CarControlSP`、MADS 與其他既有擴充。

自訂 opendbc 分支為 `TonyBinheWu/opendbc:sunnypilot-ev6-low-speed-torque`。主儲存庫的 `.gitmodules` 指向此 fork，`opendbc_repo` 以固定提交釘選。更新 opendbc 分支本身不會自動改變本分支；必須另行更新子模組提交。

`HkgLowSpeedTorque` 是預設 `false` 的持久化布林設定。`card.py` 透過既有 `initialize_params` → `get_car` → opendbc `setup_interfaces` 流程讀取；在建立 CarController 前，同步設定 `CarParams.flags` 與 Panda 的 `safetyParam`。不在控制迴圈中讀取開關，也不會出現僅更新 Python 上限卻漏掉 Panda 參數的切換方式。

MADS、Chestnut 模型載入與既有裝置編譯流程沿用本分支基底。此功能不需要合併回 `master`；日後合入上游更新時，必須保留開關與 opendbc 的固定提交，並重新驗證 HKG 控制器及 safety 測試。

CarController 計算速度上限，Panda 另由 CAN 輪速取樣獨立計算與檢查。Panda 使用既有輪速取樣最小值，因此加速跨過邊界時，可能短暫保留先前較低的輪速樣本；當取樣最小值達 17 m/s 時，上限為 270。

本基底的 Panda `SConscript` 直接包含已釘選 opendbc 的 safety 原始碼。裝置編譯後，`pandad` 會依韌體簽章比對更新 Panda；不需要手動繞過 Panda 或改刷不相符的韌體。

## 確認與還原

安裝完成後，在軟體資訊頁確認分支為 `ev6-low-speed-torque`。有 SSH 時可在裝置檢查：

```bash
cd /data/openpilot
git branch --show-current
git remote get-url origin
git submodule status opendbc_repo
```

預期 origin 為 `https://github.com/TonyBinheWu/sunnypilot.git`，opendbc 提交與 GitHub 本分支的子模組一致。

若只要還原轉向上限，在 Steering 頁關閉開關，於下次行車生效。若要移除此實驗分支，可透過自訂軟體安裝：

```text
install.sunnypilot.ai/fork/TonyBinheWu/master
```

## 驗證範圍

本次 HKG 開關整合驗證結果（2026-09-07）：

- 此分支 opendbc 的 `python -m unittest discover -q`：11,132 項測試，1,377 項略過，整體通過。
- 變更涉及的 Python 程式與測試：Ruff、ty 檢查通過；`git diff --check` 通過。
- 編譯本版原生 Params 函式庫，確認新設定的布林型別、預設關閉、行車切換時持久化，以及設定讀取到 CarParams／Panda 旗標的切換流程。
- Steering 頁新文字有繁體中文翻譯；sunnylink YAML 與生成 JSON 一致。UI Python 語法檢查通過，未完成裝置畫面操作驗證。
- 以本分支 opendbc safety 與原有 Panda 提交進行 ARM Cortex-M7 編譯：成功產生 `panda_h7.bin.signed`（開發用簽章）。此編譯用於驗證，分支交付原始碼，裝置會自行編譯。
- 官方安裝器以 `AGNOSSetup` 請求回傳 ARM64 安裝程式，已核對其中的 GitHub 儲存庫與分支字串。

驗證涵蓋全部 79 個 HKG 平台的開關相容性與重設、19 個 CAN-FD 平台經 `get_car` 初始化後實際產生的 CAN 指令，以及多 Panda 參數、未知平台、角度控制與其他品牌的隔離。速度插值與正規化回饋、駕駛介入、速率與高角度故障避免測試涵蓋 EV6、IONIQ 5 與 GV60。Panda 測試包含汽油／油電／純電、雷達／攝影機 SCC、多種 CAN-FD 配置、輪速量化、旗標重設，以及 MADS 在 ACC 未啟用時仍遵守同一扭力上限。

310 是待驗證的實驗候選值。軟體測試與韌體編譯不代表已驗證各 HKG 車型 EPS 的物理極限、橫向加速度、jerk 或轉彎效果。此環境沒有連接實車、comma 或 Chestnut 裝置，無法完成裝置上的整套 sunnypilot 編譯、開機、刷寫與行車驗證。安裝成功後仍需在受控場地、可隨時接管的條件下比對修改前後的紀錄，不能把它視為已驗證的道路版本。

# HKG 低速動態轉向扭力實驗分支

分支：`TonyBinheWu/sunnypilot:hkg-enhanced`

本版採兩道門檻：第一道維持相容 Hyundai／Kia／Genesis（HKG）CAN-FD 車型的開關資格；第二道只有辨識為 `KIA_EV6` 才套用 EV6 低速扭力曲線。其他車型即使開啟開關，也保留各自原本的扭力上限。

目前分支由 `deafa7dff672a89cc7e4b0fabdd71bec695fa3b9` 衍生；本文件描述 `hkg-enhanced` 的兩道門檻，未修改其他分支。

## 安裝

在 comma 裝置的「Custom Software／自訂軟體」安裝畫面輸入：

```text
install.sunnypilot.ai/fork/TonyBinheWu/hkg-enhanced
```

完整網址：<https://install.sunnypilot.ai/fork/TonyBinheWu/hkg-enhanced>

`/fork/TonyBinheWu/` 指定此帳號的 `sunnypilot` 儲存庫。只輸入 `install.sunnypilot.ai/hkg-enhanced` 不會指向這個 fork。

這是原始碼分支。安裝器會下載程式與子模組，首次啟動沿用 `launch_chffrplus.sh` → `openpilot/system/manager/build.py` 的 SCons 編譯流程；分支沒有 `prebuilt` 標記。首次安裝須保留穩定供電、Wi-Fi 與下載模型及編譯的時間。AGNOS 版本依本基底的 `launch_env.sh` 為 19.7，更新流程沿用基底。

目前裝有其他版本時，先在停車狀態透過原本的解除安裝流程回到自訂軟體畫面。不要在行駛時安裝或切換分支。

## 適用車型與設定

在裝置的 **Settings → Steering → HKG 低速轉向扭力（實驗功能）** 開啟。英文名稱為 **HKG Low-Speed Steering Torque (Experimental)**。

- 預設關閉。更新保留既有開關設定，但每次行車初始化都重新判斷車型；不能把前一輛車的 EV6 曲線帶到其他車型。
- 僅在非行車狀態且已辨識到相容車型時才能調整。首次使用若尚未辨識車型，先啟動車輛完成辨識，再回到非行車狀態設定。
- 設定在下次啟動行車系統時生效。停車但仍在行車模式時不會即時切換；關閉開關後，下次行車恢復原上限。
- sunnylink 的 Steering 設定定義也加入同一開關、非行車限制與相容性判斷。

第一道條件為：已收錄的 HKG CAN-FD 平台、扭力控制、`hyundaiCanfd` Panda 模式，且沒有 `ALT_LIMITS`、`ALT_LIMITS_2` 或 `dashcamOnly` 限制。UI 與車輛初始化共用 `supports_low_speed_torque`。

第二道由 `supports_ev6_torque_profile` 確认 `carFingerprint == CAR.KIA_EV6`。只有兩道條件都成立且開關開啟時，才設定車輛動態扭力旗標及 Panda 的 `CANFD_DYNAMIC_TORQUE`（1024）。其他 HKG 車型仍可調整開關，但不會套用 EV6 曲線；CAN-FD 車型維持原本的 270。手動寫入開關也無法略過車型門檻。

| 狀態 | 開關資格 | 310→270 曲線 |
| --- | --- | --- |
| 相容 EV6，開關開啟 | 可調整 | 套用 |
| 相容 EV6，開關關閉 | 可調整 | 不套用，維持 270 |
| 其他相容 HKG CAN-FD，開關開啟 | 可調整 | 不套用，維持 270 |
| 未辨識或不相容車型 | 不可調整 | 不套用 |

車主車型為 **2022 Kia EV6 Air LR**，但現有 `KIA_EV6` 識別碼以平台為單位，不能精確區分年式或 Air LR 配備。310 是沿用的實驗候選值，**不是已查證的 Air LR 原廠最大允許扭力**。約 ±480° 的方向盤機械行程不會加入扭力曲線，也沒有改動 85° 的故障避免邏輯。

**傳統 CAN 與角度控制車型不支援此功能。** 傳統 CAN 車型有 170、255、270、384 等不同上限，不能直接套用此曲線。這不會新增未收錄的年式或 EPS 韌體支援，也不保證停車或髮夾彎可以自動完成。

第一道符合開關資格的 19 個平台如下；只有其中的 `KIA_EV6` 通過第二道曲線門檻。這不代表每一款車都已完成實車驗證。

| 品牌 | 平台識別碼 |
| --- | --- |
| Hyundai | `HYUNDAI_IONIQ_5`、`HYUNDAI_IONIQ_6`、`HYUNDAI_KONA_EV_2ND_GEN`、`HYUNDAI_SANTA_CRUZ_1ST_GEN`、`HYUNDAI_STARIA_4TH_GEN`、`HYUNDAI_TUCSON_4TH_GEN` |
| Kia | `KIA_CARNIVAL_4TH_GEN`、`KIA_EV6`、`KIA_K8_HEV_1ST_GEN`、`KIA_NIRO_EV_2ND_GEN`、`KIA_NIRO_HEV_2ND_GEN`、`KIA_SORENTO_4TH_GEN`、`KIA_SORENTO_HEV_4TH_GEN`、`KIA_SPORTAGE_5TH_GEN` |
| Genesis | `GENESIS_G80_2ND_GEN_FL`、`GENESIS_GV60_EV_1ST_GEN`、`GENESIS_GV70_1ST_GEN`、`GENESIS_GV70_ELECTRIFIED_1ST_GEN`、`GENESIS_GV80` |

EV6 通過兩道門檻且開關啟用時的曲線：

| 車速 | 轉向 CAN 指令絕對值上限 |
| --- | ---: |
| ≤ 46.8 km/h（13 m/s） | 310 |
| 46.8～61.2 km/h（13～17 m/s） | 310 線性降至 270 |
| ≥ 61.2 km/h（17 m/s） | 270 |

310 相較原 270 增加約 14.8%。這是 CAN 指令數值，不是 Nm，也不能推論實際轉向扭矩或可完成彎道增加相同比例。

保留扭力上升／下降速率 2／3、即時變化限制 112、駕駛扭力 allowance／multiplier 250／2，以及既有約 85° 的 EPS 故障避免邏輯。速度取自 `vEgoRaw`，不依模型版本或飽和狀態切換。開關關閉時，CAN-FD 上限保持原本的 270。

## 整合方式

將 [opendbc PR #3720](https://github.com/commaai/opendbc/pull/3720) 的候選實作移植到主分支原本釘選的 sunnypilot/opendbc 提交 `f95f996f5917dcbbf2e32fe51b606a24cf836af6`，保留 sunnypilot 的 `CarParamsSP`、`CarControlSP`、MADS 與其他既有擴充。

自訂 opendbc 分支為 `TonyBinheWu/opendbc:sunnypilot-hkg-enhanced`。主儲存庫的 `.gitmodules` 指向此 fork，`opendbc_repo` 以固定提交釘選。更新 opendbc 分支本身不會自動改變本分支；必須另行更新子模組提交。

`HkgLowSpeedTorque` 是預設 `false` 的持久化布林設定。`card.py` 透過既有 `initialize_params` → `get_car` → opendbc `setup_interfaces` 流程讀取；在建立 CarController 前，先清除舊動態扭力旗標，再依兩道門檻同步設定 `CarParams.flags` 與 Panda 的 `safetyParam`。不在控制迴圈中讀取開關，也不會出現僅更新 Python 上限卻漏掉 Panda 參數的切換方式。

MADS、Chestnut 模型載入與既有裝置編譯流程沿用本分支基底。此功能不需要合併回 `master`；日後合入上游更新時，必須保留開關與 opendbc 的固定提交，並重新驗證 HKG 控制器及 safety 測試。

CarController 的參數層也檢查 EV6 身分，非 EV6 即使殘留動態旗標也不會提高上限；控制器僅使用參數層實際建立的曲線。Panda 依初始化傳入的旗標決定是否使用動態限制，不自行識別車型或配備；輪速取樣與上限檢查維持原邏輯。Panda 使用既有輪速取樣最小值，因此加速跨過邊界時，可能短暫保留先前較低的輪速樣本；當取樣最小值達 17 m/s 時，上限為 270。

本基底的 Panda `SConscript` 直接包含已釘選 opendbc 的 safety 原始碼。裝置編譯後，`pandad` 會依韌體簽章比對更新 Panda；不需要手動繞過 Panda 或改刷不相符的韌體。

## 確認與還原

安裝完成後，在軟體資訊頁確認分支為 `hkg-enhanced`。有 SSH 時可在裝置檢查：

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

以下為先前 HKG 開關整合的歷史驗證紀錄；本次兩道門檻測試結果另列於文末，不能把歷史測試數當成本次重跑結果：

- 此分支 opendbc 的 `python -m unittest discover -q`：11,132 項測試，1,377 項略過，整體通過。
- 變更涉及的 Python 程式與測試：Ruff、ty 檢查通過；`git diff --check` 通過。
- 編譯本版原生 Params 函式庫，確認新設定的布林型別、預設關閉、行車切換時持久化，以及設定讀取到 CarParams／Panda 旗標的切換流程。
- Steering 頁新文字有繁體中文翻譯；sunnylink YAML 與生成 JSON 一致。UI Python 語法檢查通過，未完成裝置畫面操作驗證。
- 以本分支 opendbc safety 與原有 Panda 提交進行 ARM Cortex-M7 編譯：成功產生 `panda_h7.bin.signed`（開發用簽章）。此編譯用於驗證，分支交付原始碼，裝置會自行編譯。
- 官方安裝器以 `AGNOSSetup` 請求回傳 ARM64 安裝程式，已核對其中的 GitHub 儲存庫與分支字串。

驗證涵蓋全部 79 個 HKG 平台的開關相容性與重設、19 個 CAN-FD 平台經 `get_car` 初始化後實際產生的 CAN 指令，以及多 Panda 參數、未知平台、角度控制與其他品牌的隔離。速度插值與正規化回饋、駕駛介入、速率與高角度故障避免測試涵蓋 EV6、IONIQ 5 與 GV60。Panda 測試包含汽油／油電／純電、雷達／攝影機 SCC、多種 CAN-FD 配置、輪速量化、旗標重設，以及 MADS 在 ACC 未啟用時仍遵守同一扭力上限。

310 是待驗證的實驗候選值。軟體測試與韌體編譯不代表已驗證各 HKG 車型 EPS 的物理極限、橫向加速度、jerk 或轉彎效果。此環境沒有連接實車、comma 或 Chestnut 裝置，無法完成裝置上的整套 sunnypilot 編譯、開機、刷寫與行車驗證。安裝成功後仍需在受控場地、可隨時接管的條件下比對修改前後的紀錄，不能把它視為已驗證的道路版本。


## 本次兩道門檻驗證

- 針對性測試：21 項通過，434 個子測試通過。包含相容 HKG 開關資格、EV6 專屬曲線、非 EV6 開關開啟時保留原值、舊旗標重設，以及實際 CAN 指令與高角度故障避免。
- CAN-FD／Panda 與 HKG 回歸：3,357 項通過、548 項跳過，另有 3,899 個子測試通過。Panda C 原始碼未變動，本次沒有重編或刷寫韌體；變更的是車型初始化授權動態扭力旗標的條件。
- Python 靜態檢查、差異空白檢查、Steering 語法、sunnylink YAML→JSON 一致性，以及新增繁體中文字串的編譯與載入驗證通過。整份 PO 的嚴格 `msgfmt --check` 仍有基底既存的複數形式不一致，本次沒有修改該既存條目。
- 尚未完成裝置設定頁操作或實車驗證；310 仍是實驗候選上限。

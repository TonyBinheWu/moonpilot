# EV6 低速動態轉向扭力實驗分支

分支：`TonyBinheWu/sunnypilot:ev6-low-speed-torque`

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

只對被辨識為 `CAR.KIA_EV6` 的車輛啟用；其他 Hyundai/Kia 車型維持原設定。這不會新增其他 EV6 年式或 EPS 韌體的支援，也不保證停車或髮夾彎可以自動完成。

| 車速 | 轉向 CAN 指令絕對值上限 |
| --- | ---: |
| ≤ 46.8 km/h（13 m/s） | 310 |
| 46.8～61.2 km/h（13～17 m/s） | 310 線性降至 270 |
| ≥ 61.2 km/h（17 m/s） | 270 |

310 相較原 270 增加約 14.8%。這是 CAN 指令數值，不是 Nm，也不能推論實際轉向扭矩或可完成彎道增加相同比例。

保留扭力上升／下降速率 2／3、即時變化限制 112、駕駛扭力 allowance／multiplier 250／2，以及既有約 85° 的 EPS 故障避免邏輯。速度取自 `vEgoRaw`，不依模型版本或飽和狀態切換；沒有另外新增開關。

## 整合方式

將 [opendbc PR #3720](https://github.com/commaai/opendbc/pull/3720) 的候選實作移植到主分支原本釘選的 sunnypilot/opendbc 提交 `f95f996f5917dcbbf2e32fe51b606a24cf836af6`，保留 sunnypilot 的 `CarParamsSP`、`CarControlSP`、MADS 與其他既有擴充。

自訂 opendbc 分支為 `TonyBinheWu/opendbc:sunnypilot-ev6-low-speed-torque`。主儲存庫的 `.gitmodules` 指向此 fork，`opendbc_repo` 以固定提交釘選。更新 opendbc 分支本身不會自動改變本分支；必須另行更新子模組提交。

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

若要回到此 fork 的原設定，可透過自訂軟體安裝：

```text
install.sunnypilot.ai/fork/TonyBinheWu/master
```

## 驗證範圍

本次整合驗證結果（2026-09-06）：

- 此分支 opendbc 的 `python -m unittest discover -q`：9,762 項測試，1,287 項略過，整體通過。
- 變更涉及的 Python 程式與測試：Ruff、ty 檢查通過；`git diff --check` 通過。
- 以本分支 opendbc safety 與原有 Panda 提交進行 ARM Cortex-M7 編譯：成功產生 `panda_h7.bin.signed`（開發用簽章）。此編譯用於驗證，分支交付原始碼，裝置會自行編譯。
- 官方安裝器以 `AGNOSSetup` 請求回傳 ARM64 安裝程式，已核對其中的 GitHub 儲存庫與分支字串。

驗證涵蓋控制器實際產生的 CAN 指令、速度插值與正規化回饋、駕駛介入、速率與高角度故障避免；Panda 測試涵蓋多種 CAN-FD 配置、輪速量化、旗標重設，以及 MADS 在 ACC 未啟用時仍遵守同一扭力上限。

310 是待驗證的實驗候選值。軟體測試與韌體編譯不代表已驗證 EV6 EPS 的物理極限、橫向加速度、jerk 或轉彎效果。此環境沒有連接實車與 comma 裝置，無法完成裝置上的整套 sunnypilot 編譯、開機、刷寫與行車驗證。安裝成功後仍需在受控場地、可隨時接管的條件下比對修改前後的紀錄，不能把它視為已驗證的道路版本。

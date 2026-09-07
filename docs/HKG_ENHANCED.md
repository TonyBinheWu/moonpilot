# HKG Enhanced：Mapbox 導航與模型方向燈實驗分支

基底：`TonyBinheWu/sunnypilot` 的 `deafa7dff672a89cc7e4b0fabdd71bec695fa3b9`。
本次審查分支：`hkg-ui-osm-review`，基於 `hkg-enhanced` 的 `b623e3a`。新增 OSM 路線及原生 UI 網頁預覽，修正混合語系字形；移除未驗證的 EV6 310 曲線並保留 CAN-FD 270 上限。Chestnut 載入與小模型 fallback 延續基底。

## 本版完成範圍

| 項目 | 狀態 |
| --- | --- |
| Mapbox／OSRM 路線規劃、地址搜尋與座標目的地 | 已實作；OSRM 座標路線不需要 Mapbox 權杖，地址搜尋仍使用自有 Mapbox 公開權杖 |
| 開發網頁預覽 | 已實作真實設定 UI 的本機預覽，可點選、捲動、貼上及重新載入；不模擬行車 |
| 逐向提示、剩餘距離、估計時間、取消與重新規劃 | 已實作，畫面為文字導航橫幅；尚無地圖圖磚與路線圖 |
| 導航轉彎意圖送入大小模型 | 已接入兩個模型執行流程的既有 `turnLeft`／`turnRight` desire 輸入；預設關閉，每個轉彎需駕駛輕推方向盤確認 |
| HKG 模型方向燈開關 | 已實作於 Steering 與 sunnylink；預設關閉；限制 CAN-FD LKA／HDA2 架構 |
| taco2 的路線特徵條件式駕駛模型 | **大模型導航仍未完成**；已補齊可選 `nav_features` 編譯／執行器輸入，並完成 taco2 原始模型的離線推論實驗。本基底權重仍沒有導航輸入，行車時尚未接入導航特徵 |
| Chestnut Full-FOV 感知／Shadow Mode | **未完成且不可啟用**；未提供相容的全視野感知權重與輸出規格。已附模型輸入稽核工具，沒有假造 `fullFovPerception` 結果 |

這是第一階段原始碼實驗版，不是可依目的地自行完成全程行駛的版本。

## 開發與預覽

本次先以獨立開發分支交付。請依 [網頁預覽說明](../tools/ui_preview/README.md) 在電腦啟動 UI；OSM 功能與模型輸入限制見 [OSM_NAVIGATION.md](OSM_NAVIGATION.md)。未完成 firmware/AGNOS、HIL 或實車驗證，不作為已驗證的裝置發布版本。

此版新增設定頁與導航橫幅接在 comma 3／3X 使用的 tici UI。**comma four 的 mici UI 尚未移植本次導航頁與橫幅**；共用模型和車輛層的變更不代表已有 four 的完整操作介面。

## 操作

1. 在非行車模式進入 **Settings → Navigation**，開啟「Navigation」。
2. 選擇 Mapbox 或 OSRM 路線；Mapbox 路線及地址搜尋需要自己的 `pk.` 公開權杖。權杖使用 `DONT_LOG`，不加入 sunnylink 備份。
3. 輸入地址，核對搜尋結果後按「選取」；或輸入 **緯度、經度**，例如 `25.0330, 121.5654`，核對後選取。
4. 車輛啟動且取得可靠 GPS 後，背景工作程序規劃路線，行車畫面顯示逐向指示。
5. 「取消導航」會清除目的地。取消後最遲於下一次 1 Hz 導航更新撤回導航意圖。

地址搜尋採用 Geocoding v6，不包含商家／景點 POI 搜尋。因選定的目的地會保留在裝置上，請求使用 `permanent=true`，帳號須具備永久地理編碼資格；使用者直接輸入座標則不會呼叫地理編碼。路線使用 Directions v5 的 `mapbox/driving`，繁體中文語言碼為 `zh-TW`。沒有即時交通 ETA、語音播報或地圖圖磚功能。

金鑰太長時，可從電腦透過 SSH 連入 comma，在終端機貼上，不必使用裝置鍵盤逐字輸入：

```bash
cd /data/openpilot
python3 -c 'from getpass import getpass; from openpilot.common.params import Params; token = getpass("貼上 Mapbox 金鑰後按 Enter：").strip(); assert token.startswith("pk.") and not any(c.isspace() for c in token), "需要完整的 pk. 公開權杖"; Params().put("MapboxToken", token)'
```

這個命令不含金鑰本身，互動輸入不會回顯。回到 Navigation 開啟 Mapbox 導航即可；不要把私人金鑰寫入程式、GitHub 或 sunnylink 設定檔。

導航成立的定位條件包括：定位有效、資料年齡不超過 2.5 秒、水平誤差不超過 20 公尺、座標及速度為有限值。路段比對限制向前搜尋範圍並檢查行進方向；偏離超過 35 公尺、交叉路段比對歧義或定位中斷會停止提供有效轉彎意圖。連續三次無法比對後重新規劃。網路等待在背景執行，失敗退避，舊目的地的結果不會覆蓋新目的地。

## 導航轉彎提示模型

「導航轉彎提示模型（需駕駛確認）」預設關閉。啟用前須關閉 `Mads`、`LaneTurnDesire` 與 `BlinkerPauseLateralControl`，避免系統控制的方向燈回傳後又觸發另一套轉彎邏輯，或讓方向燈暫停橫向控制。

只處理一般 `turn + left/right`：速度必須大於 0.3 m/s、低於 8 m/s（28.8 km/h），距離須在 `min(30, max(8, vEgo × 3))` 公尺內，橫向控制已啟用，模型校準與車輛資料有效。駕駛必須在該轉彎提示出現後，向同方向重新輕推方向盤；持續握有扭力、導航自行換路線、舊轉彎完成都不算新的確認。

煞車、油門、取消鍵、反向操作、警示燈、目標側盲點、變換車道中、資料過期或控制退出時不再輸出導航意圖。同一路線的同一個轉彎只接受一次，意圖最多維持八秒。模型 fallback 會撤回本次意圖，不能自行對小模型重新發出一次。

模型把 desire 的上升沿視為提示，並依自己的輸出決定動作。**撤回 desire 不是保證立即停止轉彎的指令**；必要時須由駕駛接管。地圖資料不提供路權、紅綠燈或交叉來車的安全確認，也不判斷這個轉彎是否真的可完成。沒有加入匝道、自動變換車道、迴轉或導航直接控制曲率與加減速。

## HKG 模型方向燈

已回報啟用後的 CAN 故障，調查期間請保持方向燈實驗功能關閉。原功能位於 **Settings → Steering → HKG 模型方向燈（實驗功能）**，非行車模式設定，下次行車初始化生效。sunnylink 使用相同車型能力判斷。

`taco2` 在 EV6 的平台設定直接加入 `ENABLE_BLINKERS`。本分支採用現代名稱 `CANFD_ENABLE_BLINKERS`，在建立 CarController 前同步設定車輛旗標與 Panda 的 `CANFD_ENABLE_BLINKERS` safetyParam（2048）。條件為已收錄的 HKG CAN-FD 平台、辨識為 `CANFD_LKA_STEER_MSG`、非 dashcamOnly，且使用 `hyundaiCanfd` safety 模式；不是對所有 HKG 年式與配備無條件啟用。

具體移植：

- 使用現有 `SPAS1`（0x165）與 `SPAS2`（0x16A），ECAN bus 由車輛配置決定，支援多 Panda bus offset。
- 行車初始化停用 SPAS ECU 的一般 CAN 通訊，使用 0x7B1 tester-present 維持；修正此訊息原先依賴 longitudinal 啟用的條件。
- 修正 Hyundai `deinit` 的參數傳遞，讓恢復通訊的 helper 能傳入正確指令；既有行車程序不會因此自動呼叫新的結束 hook，程序退出仍依 ECU timeout 機制恢復。實車須驗證退出與駕駛操作的行為。
- 只對已開始的變換車道或已確認的導航轉彎輸出燈號；資料有效、車輛 CAN 正常、橫向控制啟用才允許。駕駛反向操作、警示燈及目標側盲點會取消系統燈號請求。
- Panda 只在專用旗標及 LKA 架構成立時接受這三種訊息；SPAS 除 CRC／counter／方向燈欄位外必須為零。方向燈只允許 0／3／4（取消／左／右），未啟用控制時只允許取消；0x7B1 僅允許 tester-present，不能發出其他診斷或停車動作。

方向燈功能不更改轉向扭力、加速度與煞車的既有檢查；先前低速扭力曲線的停用與安全邊界另見 [EV6_LOW_SPEED_TORQUE.md](EV6_LOW_SPEED_TORQUE.md)。

## Full-FOV 與導航模型的缺口

已直接讀取本基底的大小 ONNX 模型；完整輸入名稱、尺寸及 SHA-256 在 [HKG_MODEL_INPUT_AUDIT.json](HKG_MODEL_INPUT_AUDIT.json)。兩者的 `img`／`big_img` 都是 `[1, 12, 128, 256]` 的打包影像張量，並有 desire、traffic convention、action delay 與 recurrent features；沒有導航特徵輸入。

`nav_features` 移植的程式變更、原始 taco2 編碼器與駕駛模型的實際推論結果，以及仍需訓練的部分，見 [HKG_NAV_FEATURES.md](HKG_NAV_FEATURES.md)。新增輸入支援不會讓現有權重自動學會導航。

現有相機轉換仍沿用固定的 512 × 256 模型影像平面。張量大小、來源相機解析度和保留的光學 FOV 是不同問題；把相機影像放大、取消裁切或直接更改模型輸入形狀，都不能建立相容的感知模型。

後續 Full-FOV Shadow Mode 需要能處理完整廣角影像的模型、相應前處理／鏡頭校正及可驗證的輸出規格，才能加入獨立的 `fullFovPerception` 發布、UI／log 與負載／失敗隔離。現階段沒有新增未執行推論的感知程序，也沒有把影像直接接入控制。

## 驗證紀錄

- 導航、方向燈意圖、Cap'n Proto 序列化與導航特徵：30 項通過，包含 8 種實際 CPU JIT 輸入傳遞組合。另已完成 taco2 原始 ONNX 的離線 CPU 推論；結果與限制見 [HKG_NAV_FEATURES.md](HKG_NAV_FEATURES.md)。
- HKG 控制器、原有低速扭力與 CAN-FD safety：3,341 項通過、548 項跳過；另有 3,748 個子測試通過。跳過項目沿用測試套件的平台條件，不表示通過。
- 用本次 opendbc 原始碼及基底 Panda 提交編譯 Cortex-M7 韌體，成功產生開發簽章的 `panda_h7.bin.signed`。此檔用於編譯驗證，不隨分支提供預編譯安裝。
- 新增程式的靜態檢查、sunnylink 設定編譯一致性與差異空白檢查通過。
- 參數、資料新鮮度、取消及重規劃使用模擬資料測試；沒有用使用者權杖完成 Mapbox 線上請求，沒有 process replay、實際 GPU 推論迴圈、裝置畫面操作、整機編譯／開機或車輛測試。

以上只證明列出的程式測試與韌體編譯結果，不證明道路安全性、EPS 行為或模型能完成轉彎。

## 參考

- [sunnypilot PR #1397：Mapbox helpers](https://github.com/sunnypilot/sunnypilot/pull/1397)
- [sunnypilot PR #1412：導航事件](https://github.com/sunnypilot/sunnypilot/pull/1412)
- [taco2 的 EV6 旗標與 ECU 初始化](https://github.com/commaai/openpilot/blob/a8c957e9d98cb3c334ab60c09e731c262605ae56/selfdrive/car/hyundai/interface.py)
- [taco2 的 SPAS 發送路徑](https://github.com/commaai/openpilot/blob/a8c957e9d98cb3c334ab60c09e731c262605ae56/selfdrive/car/hyundai/carcontroller.py)
- [Mapbox Directions API](https://docs.mapbox.com/api/navigation/directions/)
- [Mapbox Geocoding API 與永久儲存](https://docs.mapbox.com/api/search/geocoding/)

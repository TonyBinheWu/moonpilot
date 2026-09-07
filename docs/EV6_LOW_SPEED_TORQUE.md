# EV6 低速轉向與安全邊界

此開發變更移除先前未經 EV6 驗證的 310 動態上限，HKG CAN-FD 控制器與 Panda 均保留 270；沒有加入 HKG 350 設定。舊的 `HkgLowSpeedTorque` 參數與旗標保留相容性，但無法增加轉向權限。裝置 UI 顯示此功能暫不可用，sunnylink 不再提供啟用能力。

這是供審查的軟體變更，並非已完成整車驗證的版本。既有 fork 的其他功能也不能因本次測試通過，就視為全面符合 comma 的安全要求。

## 與官方 Rivian 的差異

查核來源：[Rivian safety model](https://github.com/commaai/opendbc/blob/master/opendbc/safety/modes/rivian.h)、[Hyundai CAN-FD safety model](https://github.com/commaai/opendbc/blob/master/opendbc/safety/modes/hyundai_canfd.h)。

| 項目 | 官方 Hyundai CAN-FD | 官方 Rivian |
|---|---:|---:|
| 轉向指令上限 | 270 | 低速 350，高速 250 |
| 速度相關上限 | 固定 | 9–17 m/s 之間由 350 線性降至 250 |
| 每次指令上升／下降限制 | 2／3 | 3／5 |
| 即時扭力變化限制 | 112 | 125 |
| 駕駛扭力 allowance／multiplier | 250／2 | 100／2 |
| 轉向請求容錯 | 至少 89 個有效請求，最多 2 個無請求，最短 810 ms 間隔 | 不採用同一套 HKG 容錯設定 |

Rivian 的 350 是車系專用 CAN 指令數值，不是無上限設定，也不能據此推定 EV6 可接受的助力、橫向加速度、jerk 或駕駛接管負荷。Rivian 還以兩個 ECU 的速度來源檢查速度不一致；其 EPS、車身反應與 HKG 的驗證資料不同。

`KIA_EV6` 指紋不區分 2022 年式 Air LR 與其他配備，不能把平台辨識當成該配備最高參數已經查證。現有約 85° 的請求切換邏輯是 EPS 故障避免措施，並非方向盤的機械最大角度。

## 低速停頓如何判斷

提高上限無法直接解決所有轉向停頓。至少要把下列資料按同一個單調時鐘對齊：

- 模型要求的曲率、實際曲率與轉向角；模型是否已經減少轉彎要求。
- `carControl` 的 `latActive`、要求扭力、控制器輸出及限幅。
- `carState` 的駕駛扭力、煞車、油門、方向燈、CAN 狀態與 EPS 故障。
- 完整 CAN 傳送／接收、轉向請求位元、Panda `safetyTxBlocked` 變化。

qlog 會降採樣，不能用零星 CAN 訊息定位約 20 ms 的請求切換，也不能單憑擋送計數判定是哪個訊息或哪一道檢查。要確定原因，需同一個 segment 的完整 `rlog.zst`；行車紀錄與座標應留在私人分析環境。

## 已執行與仍待執行的驗證

已執行全車系 safety 單元測試：8,929 通過、3,189 跳過、17,791 個 subtest 通過。之後修正方向燈遮罩的 MISRA 整數型別問題，再執行 HKG/CAN-FD 測試：3,357 通過、548 跳過、3,900 個 subtest 通過。`opendbc/safety/tests/misra/test_misra.sh` 通過。跳過數包括基底測試類別與不適用的車系條件，並不表示未執行的驗證也已通過。

尚未執行完整 firmware/AGNOS 建置、硬體迴路（HIL）、實車接管負荷、橫向偏移／jerk 或低速 L 型路口驗證。網頁 UI 預覽只驗證操作與排版。軟體測試通過，不代表提高上限後的車輛反應安全。

依據 [comma 安全要求](https://docs.comma.ai/concepts/safety/) 與 [Panda safety model](https://github.com/commaai/panda#safety-model)，後續控制開發保留駕駛接管、訊息檢查及完整安全測試，不透過改大測試期望值、關閉限制或忽略擋送來達成更大轉向。

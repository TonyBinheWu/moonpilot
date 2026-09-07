# 驗證紀錄與尚待實測項目

日期：2026-09-07。

## 已完成

在 x86_64 CPU 測試環境、Python 3.12、PyTorch 2.13.0+cpu、TorchVision 0.28.0+cpu、ONNX 1.22.0、ONNX Runtime 1.29.0 執行：

```bash
ORT_DISABLE_TELEMETRY=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false python -m pytest tools/distill_lab/tests -q
```

加入 GUI 後的完整結果：**54 passed，4 warnings，18.65 秒，程序 exit code 0**。GUI 使用 Streamlit 1.63.0。另完成 Python 語法檢查、bootstrap／GUI shell 語法檢查、TOML 設定檢查，以及原有 Notebook schema 與 code cell 語法檢查。

初次測試完成斷言後，Microsoft 遙測連線被自動審查攔截；只呼叫 `disable_telemetry_events()` 仍未解決。依 [ONNX Runtime 1.29 官方版本說明](https://github.com/microsoft/onnxruntime/releases/tag/v1.29.0)，改在初始化前設定 `ORT_DISABLE_TELEMETRY=1`，最終重跑正常結束。工具、測試及上游推論入口都加入此設定；沒有放行該遙測連線。若既有 Notebook kernel 已先載入 ONNX Runtime，必須重啟 kernel 才能在初始化前生效。

測試包括：

- 依行程分組，拒絕用單行程宣稱獨立驗證。
- 丟幀、雙鏡頭錯位、30Hz 資料、NaN、錯誤四元數、標籤維度與時間錯誤。
- 原始資料副本 checksum、拒絕覆蓋、SSH 引數檢查。
- 真實學生模型架構的 supervised／RL 兩種 PyTorch→ONNX 匯出與 8 步循環狀態比對。
- 匯出結果交給現有 sunnypilot Parser，驗證 plan、車道等張量維度與有限數值。
- 評估與 checkpoint 不一致時拒絕匯出。
- 車端候選遭修改、必要程式版本不同、onroad 狀態時拒絕操作。
- 原始 big model 與所有 chunk 的回復，保留一般小模型。
- Notebook 訓練控制面板可建立，且建立介面本身不啟動訓練。
- 3X 與 four 攝影機參數差異。

上述匯出測試使用**隨機初始化的測試權重**，只驗證工程相容性；沒有把測試權重作為已訓練模型交付。

## GUI 與背景工作

新增 24 項測試，包括：

- Streamlit AppTest 驗證七個空資料頁面可開啟，單純瀏覽不啟動工作。
- 環境檢查、公開資料下載、訓練、評估、匯出及車端編譯按鈕送出正確參數。
- 真實本地背景 inventory 工作完成後，重建工作管理物件仍能讀取結果與紀錄。
- 真實本地子程序的停止、非零退出碼保存，以及子程序仍存活時保留資源鎖。
- 衝突工作拒絕啟動；車端操作拒絕取消；不接受未知操作、路徑跳脫、SSH 選項注入或錯誤確認文字。
- 裝置識別不符、未確認 offroad 時，在任何遠端寫入前停止。

初版程序追蹤在測試容器的 PID namespace 與 `/proc` 不一致時失敗，已改為核心維護的檔案鎖，並讓執行鎖傳遞至子程序。修正後上述測試與完整測試正常結束。

AppTest 不是實體瀏覽器的視覺截圖測試。GUI 測試的實驗資料與裝置報告是隔離測試 fixture，沒有作為實際訓練或車端成果交付。未執行真實 SSH、Spark GPU 訓練或車輛部署。

## 真實公開標籤檢查

實際讀取 commaai/comma1M 的：

- 資料集 commit：`4c949b27fbf0e4207bdd548de4cb7822d7aa63cd`。
- segment：`2333e255f1de1fe83ea5975b24a3cc67`。
- 確認包含 `fcamera.hevc`、`ecamera.hevc`、`frame_info.safetensors`、`localizer.safetensors`。
- 下載並檢查真實 frame_info 與 localizer；未下載整個資料集。
- 1200 個影格；`frame_states` 為 1200×43。
- 片段時間跨度約 59.9526 秒；最大標籤速度約 22.9567 m/s。
- 本工具時間、雙鏡頭同步、標籤維度、四元數及有限數值檢查通過。

## 尚未驗證

| 項目 | 現況與下一個實際驗證 |
|---|---|
| Tony 的 Spark 環境 | 尚未連線；執行 bootstrap、doctor 與短訓練 |
| GPU HEVC 解碼 | 套件存在且官方支援 Spark；本回合未在 GB10 實測 |
| 完整教師蒸餾訓練 | 程式已交付；本回合沒有 GPU 訓練結果 |
| Tony 的 3X rlog | 尚未提供；確認其實際 schema、GNSS、sensor、校正與時間 |
| 3X GNSS 定位品質 | 轉接已實作，但尚無真實 3X 資料驗證；不能宣稱所有版本相容 |
| 世界模型與 RL | 已提供完整執行入口；NVFP4 kernels 與模擬效能尚須在 Spark 驗證 |
| Chestnut 編譯 | 匯出已通過 CPU parity；AMD tinygrad 算子、數值與整體延遲尚須車端測試 |
| 車端啟用與回復 | 檔案切換／回復有隔離測試；未實際操作你的裝置 |
| 駕駛品質 | 沒有公共道路或封閉場地驗證，沒有可宣稱安全的實測權重 |

ONNX legacy exporter 目前產生兩類棄用警告（兩種匯出測試合計四則），沒有測試失敗。它是為靜態模型相容性選用的路徑；未來換 PyTorch 版本應重跑匯出比對，不要忽略 exporter 行為變更。

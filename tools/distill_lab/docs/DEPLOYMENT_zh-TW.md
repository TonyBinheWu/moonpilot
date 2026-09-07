# 車端部署與回復：Comma 3X＋Chestnut

本流程針對 **sunnypilot 預設 modeld＋Chestnut** 的模型介面。匯出的模型仍是研究候選，數值比對或編譯成功不代表能在公共道路駕駛。首次載入與測試安排在封閉場地，駕駛必須能隨時接管。這不是解除扭力、角度、駕駛監控或 CAN safety 限制的工具。

## 1. 在 Spark 確認候選檔

必須先完成 `evaluate` 和 `export`。`manifest.json` 包含 ONNX、parity fixture、評估報告的 hash，以及本版 sunnypilot 必要程式檔的 hash。任何一個檔案變更都需要重新驗證。

研究模型不會自動出現在 sunnypilot 線上模型選單，也不會因建立 GitHub 分支而自動成為 `install.sunnypilot.ai/分支` 安裝來源。這裡採用在既有安裝上暫存候選模型、車端編譯及備份切換的方式。

### 先決定保留哪一組行為

| 做法 | 可以做到 | 需要接受的代價 |
|---|---|---|
| 保留目前 BMRLNAP／既有模型 | 保持目前熟悉的行為 | 不會使用這次訓練成果 |
| 封閉場地測試 supervised 候選 | 評估個人資料蒸餾結果 | 模型完整行為可能退步，需重新測試所有相關場景 |
| 封閉場地測試 RL 候選 | 評估模擬器微調後的 action head | 多一層世界模型偏差與單片段過擬合風險 |

## 2. 複製至裝置的暫存區

在 Spark 執行：

```bash
ssh comma 'mkdir -p /data/distill-lab/incoming'
rsync --archive --partial --protect-args work/releases/ev6-r001/ comma:/data/distill-lab/incoming/ev6-r001/
rsync --archive --protect-args car.py comma:/data/distill-lab/car.py
```

這一步不改動正在使用的模型。`car.py` 要在車端既有的 sunnypilot Python 環境執行，不能拿 Spark 的虛擬環境複製過去。

## 3. 車端暫存與版本核對

下列命令在裝置的 SSH 終端機執行：

```bash
cd /data/openpilot
python3 /data/distill-lab/car.py stage --bundle /data/distill-lab/incoming/ev6-r001 --release ev6-r001
```

從 `compile` 開始，工具需要 `/data/params/d/IsOnroad` 明確為 `0`，也就是停車／offroad。讀不到狀態時不會把它當成已停車。

車端不一定已更新到本實驗室的基準。`target_contract.json` 以必要程式檔的 checksum 檢查模型解析、編譯、攝影機轉換、循環狀態與 chunk 格式。若不符，**先記錄裝置實際 commit，針對該版本整合並重跑測試**；不要刪除這些檢查來通過。

```bash
git rev-parse HEAD
```

本工具分支沒有要求你直接切換整台裝置的系統分支，也沒有替你處理 AGNOS 版本升級。上述兩項應依實際裝置版本另行核對。

## 4. 在 Chestnut 編譯

確認 Chestnut 供電與連線穩定、目前在 offroad。3X 原始來源參數通常是 1928×1208；實際值以你的 rlog 與前面匯入報告為準。

```bash
python3 /data/distill-lab/car.py compile --bundle /data/distill-lab/candidates/ev6-r001 --camera 1928x1208
```

工具呼叫既有 sunnypilot `compile_modeld.py`，使用該基準的 Chestnut AMD 設定。它包含影像 warp、循環特徵 queue 與模型；結果寫到候選區的 `compiled.pkl`，不覆蓋現用模型。完整建置紀錄在 `compile.log`。

目前提供的是 Chestnut 路徑。沒有 Chestnut 的 Qualcomm QCOM 編譯部署尚未納入這份工具，不能將 AMD 產物直接用於 QCOM。

## 5. 比對數值與完整推論時間

```bash
python3 /data/distill-lab/car.py benchmark --bundle /data/distill-lab/candidates/ev6-r001 --runs 100 --max-p95-ms 45
```

這一步會：

1. 以真正的 AMD ONNX 執行結果對照 Spark 匯出的 parity reference。
2. 執行編譯後的完整 warp＋循環 queue＋神經網路路徑。
3. 記錄 p50、p95、NaN／Inf 與 compiled 檔案 hash。

`45ms` 是小於 20Hz 的 50ms 週期的初始工程門檻，不是官方保證值；可設得更嚴格。這個量測使用合成 NV12 畫面，**不涵蓋完整車端程序競爭、長時間溫度、真實攝影機延遲，也不測駕駛品質**。還必須在停車錄影、重播與封閉場地測試中量測實際延遲和掉幀。

測試未過會停止；不要只提高門檻到超過 50ms 來「解決」延遲。

## 6. 備份與封閉場地啟用

先在 sunnypilot 模型選單選回「預設」模型並完成重啟，停止模型下載。若仍選用其他模型，modeld_v2 可能完全不讀取這次安裝的預設 modeld 檔案。工具會檢查相關參數，有已選模型時拒絕啟用。

確認已安排封閉場地測試、已閱讀同權重的軌跡評估，且已取得本候選的車端 benchmark：

```bash
python3 /data/distill-lab/car.py activate --bundle /data/distill-lab/candidates/ev6-r001 --closed-course
```

工具會把原本 big model 的 ONNX、compiled pickle、chunkmanifest 與所有 chunk 完整保存到 `/data/distill-lab/backups/<時間戳>`，記錄校驗碼，再安裝候選。一般小模型保留作既有系統的 fallback。安裝中的可捕捉例外會嘗試回復原模型；突然斷電仍需按下一節手動回復。

工具不會替你重開機。記錄它回傳的備份路徑後，再依裝置正常方式重新啟動。**不要在行車中替換模型。**

重啟後：

```bash
python3 /data/distill-lab/car.py verify-active
```

它檢查模型檔案是否被更新器或模型管理器取代，並列出 `ChestnutActive`／`ChestnutModelError`。這些還不是已載入正確模型的完整證據：仍需核對 `modeld` 啟動紀錄、確認沒有改走 `modeld_tinygrad` 或小模型 fallback，檢查真實輸出和延遲。不要只看 UI 的模型名稱判定成功。

## 7. 封閉場地的比較項目

先做靜態／不接管控制的檢視與已錄資料重播，再做低速、明確可接管的封閉場地測試。對比同一路線的原模型與候選模型，記錄：

- 軌跡偏差、突然的轉向／加速度變化、接管原因。
- 延遲、掉幀、NaN／Inf、模型重新載入或 fallback。
- 直線、彎道、停止／起步、遮擋、不同光線等場景。
- 長時間運轉後的 Chestnut 供電、溫度與效能變化。

上游訓練資料目前將 desire 輸入設為零，不能據此宣稱已訓練好指令換道行為。模擬器無異常也不能取代真實場景驗證。若目的只是改善 EV6 轉向飽和，先分清模型要求、控制器輸出與車輛實際能力，不能把這個候選當作解除 EPS 限制的方法。

## 8. 回復原模型

在停車／offroad 下，使用啟用時回傳的確切路徑：

```bash
python3 /data/distill-lab/car.py restore --backup /data/distill-lab/backups/實際時間戳
```

工具先核對備份 hash，移除候選 big model 的檔案與 chunk，再還原所有原檔。手動重新啟動後，確認預設模型可用；如要回到原先選用的 BMRLNAP，另從 sunnypilot 模型選單選回原模型。

實驗期間每次更新 sunnypilot 或切換模型後，都要重新檢查候選是否仍是現用檔案。不要把自動更新後的狀態當成已維持同一模型。訓練專案的版本與車端部署版本分開記錄。

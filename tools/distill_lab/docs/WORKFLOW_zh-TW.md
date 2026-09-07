# 從行車資料到駕駛模型

所有命令在 Spark 的 `tools/distill_lab` 目錄執行，除非另有標示。

```bash
LAB_PY=upstream/.venv/bin/python
"$LAB_PY" lab.py doctor
```

## 1. 收集適合目標的資料

需要原始 `fcamera.hevc`、`ecamera.hevc` 與同一分鐘的完整 `rlog.zst`／`rlog.bz2`／`rlog`。影片不能重編碼、裁切、插幀或單獨改時間；保留裝置、軟體版本、校正、時間戳、GNSS／姿態與車速。

要學自己的駕駛風格，應區分人工駕駛與系統已控制車輛的片段。程式會記錄 `carControl.latActive/longActive` 的占比，預設排除控制占比超過 5% 或未知的個人資料。這個 5% 是初始資料篩選設定，不是品質保證。遇到接管、鏡頭移動、惡劣能見度或時間跳躍，保留原始資料並另外檢查，不要默默混入一般訓練組。

先用數個完整片段確認流程，再增加不同日期、路線、天候、白天與夜晚的人工駕駛資料。幾分鐘可以驗證程式是否執行，不能證明模型能普遍駕駛。

### 從裝置取得

在 Spark 的 SSH config 設定 `comma`，使用你自己的裝置 SSH 金鑰；工具不保存私鑰或密碼。

```bash
ssh comma 'ls /data/media/0/realdata'
"$LAB_PY" lab.py fetch-device --host comma --route '實際行程名稱'
"$LAB_PY" lab.py inventory
```

`--route` 取目錄名稱移除最後的 `--片段序號`。工具只複製指定行程的影像與 rlog，原裝置檔案保留。斷線後可重跑 rsync 續傳。

若你已手動複製到 Spark：

```bash
"$LAB_PY" lab.py prepare --source /實際路徑/行程--1 --route '實際行程名稱'
```

### 先使用公開資料

```bash
"$LAB_PY" lab.py fetch-public --segments 2333e255f1de1fe83ea5975b24a3cc67
```

這是上游 README 使用的 segment ID。工具會先確認該資料版本是否有 `fcamera.hevc`、`frame_info.safetensors`、`localizer.safetensors`，缺少任何一項都會停止，不會虛構定位或影格時間。可一次指定多個 ID。`--revision` 可指定資料集 commit；不指定時也會解析並記錄當下的實際 commit。下載的是你指定的片段，沒有預設下載整個資料集。

公開匿名片段沒有原始完整行程 ID，故只能按公開 segment 分組，報告會標示 `route_isolation_verified=false`，不能宣稱已排除同一原行程的資料洩漏。

## 2. 匯入、定位與資料檢查

```bash
"$LAB_PY" lab.py prepare-route --route '實際行程名稱' --method raw-gnss
```

`raw-gnss` 沿用上游 UBlox GNSS＋deviceMotion＋車速融合；新增接受 3X 的真實裝置識別，不會將它改成 `mici`。每個片段保存原檔 SHA-256、sensor、資料來源與品質報告。

該模式仍要求外接 GPS 資料流、原始 UBlox 測量與現行 schema 的有效姿態／校正欄位。缺少 UBlox 不能以放寬 `deviceType` 解決。

若實際紀錄保有有效 `liveLocationKalman` 的 ECEF 位置、姿態、速度與加速度，可以**明確選擇**另一個標籤來源：

```bash
"$LAB_PY" lab.py prepare-route --route '另一個尚未匯入的行程' --method logged-ecef
```

這使用線上濾波器已記錄的狀態，不等同上游離線 GNSS 高品質標籤，會保留 `online_filter_research_only` 註記。工具不會在 raw-gnss 失敗時偷偷改用它。

匯入後若定位失敗，原始副本與 `source.json` 仍在 `work/data/<ID>`。修正問題後，不要再 copy 同一片段，而是：

```bash
"$LAB_PY" lab.py localize --segment 實際32位ID --method logged-ecef
```

預設檢查：

- rlog 影格 index 與原始 HEVC 影格數一致。
- 來源 sensor、原始解析度與相機內參一致。
- 雙鏡頭每幀時間差不超過 25ms，時間嚴格遞增，沒有明顯丟幀。
- 20Hz 來源幀率符合上游資料載入器；其他幀率不自動猜測轉換。
- 校正穩定；位置、速度、四元數、外參皆有限且符合維度。
- 訓練的完整歷史與未來標籤皆存在，不外插未來軌跡。

本版使用原始 SOF／EOF 中點作為影格時間，這是可追溯的時間定義，不是已測得的曝光中心。真實紀錄需要再以車速、轉向／姿態變化與影像對齊，量測是否有固定偏移。滾動快門也不會因中點時間而消失。

`work/reports/prepare-route.json` 列出每段成功或失敗原因。開啟 Notebook 的資料預覽，檢查雙鏡頭、速度及後續訓練裁切畫面。

## 3. 切分資料

正式實驗至少兩趟獨立行程：

```bash
"$LAB_PY" lab.py split
```

同一行程的所有分鐘會進同一組，約 80% 行程訓練、20% 驗證。`work/dataset.json` 保存確切 ID、分組、檔案 hash 與排除原因。訓練前會重新核對檔案 hash。

只有一個公開片段、目的是確認硬體可以跑：

```bash
"$LAB_PY" lab.py split --smoke
```

這會標記 `smoke_only=true`，沒有驗證組，因此不能完成車端候選匯出流程。做完 smoke 後，加入多趟資料重新 split，另建正式實驗名稱。

如果目標本來就是蒸餾系統的駕駛行為，可加 `--allow-assisted`；報告仍保留人工／系統控制來源。不要把這種結果解讀為「學會 Tony 的駕駛風格」。

## 4. 下載教師模型並開始訓練

```bash
"$LAB_PY" lab.py fetch-teacher
"$LAB_PY" lab.py train --name ev6-smoke --steps 10 --batch-size 1 --validate-every 5
```

上面是環境測試的初始步數，**不是已足夠訓練的建議量**。完成後查看每步秒數、GPU 記憶體與 loss 是否有限，才能估算後續預算。Spark 的 128GB 為統一記憶體，不等於全部都可作模型顯存，也不代表任何 batch size 都能成功。

建立有驗證組的資料後：

```bash
"$LAB_PY" lab.py train --name ev6-001 --steps 1000 --batch-size 1 --validate-every 100
```

`1000` 是可調整的實驗設定，並無已驗證最佳步數。使用驗證曲線、場景分組及模擬結果決定是否增加資料或繼續訓練。

若中斷後續訓：

```bash
"$LAB_PY" lab.py train --name ev6-001 --steps 2000 --batch-size 1 --validate-every 100 --resume
```

`--steps` 是累積總步數。續訓核對資料、教師、batch size 與 learning rate，恢復模型、optimizer 與亂數狀態。`resume.pt` 是你自己產生的可信 checkpoint；不要拿陌生來源的 pickle checkpoint 取代它。

## 5. 視覺化

Notebook：

- 依片段選擇原始雙鏡頭與速度。
- 按「開始訓練」「重新整理曲線」「停止訓練」。
- loss、每步秒數、GPU 配置記憶體峰值。
- 模型實際看到的窄角／廣角裁切影像。
- 同一驗證樣本的實際軌跡、教師模型預測、學生模型預測。

另開 Spark 終端機啟動 TensorBoard：

```bash
upstream/.venv/bin/tensorboard --logdir work/runs --host 127.0.0.1 --port 6006
```

在已設定 SSH port forwarding 的電腦開啟 `http://127.0.0.1:6006`。本地曲線與圖片不需要 W&B 帳號。

## 6. 驗證與模型選擇

```bash
"$LAB_PY" lab.py evaluate --name ev6-001 --checkpoint best.pt
```

報告比較學生與教師相對於同一組標籤的：橫向軌跡 MAE、3D 軌跡 ADE、橫向加速度 MAE、縱向加速度 MAE。誤差有實際單位，且報告綁定 checkpoint 與 dataset hash。

`best.pt` 依驗證軌跡 ADE 選取；`last.pt` 是最後 checkpoint。這是 open-loop 評估，模型沒有控制下一幀，也未測試從偏離軌跡中恢復。驗證集同時用於模型選擇，不能當成未見過的最終測試集。另保留日期與路線不同的最終場景供獨立檢查。

## 7. 世界模型模擬與可選微調

```bash
"$LAB_PY" lab.py worldmodel-download
"$LAB_PY" lab.py worldmodel-server
```

這個伺服器在前景執行。另開終端機，在有圖形桌面的 Spark／遠端桌面執行：

```bash
"$LAB_PY" lab.py simulate --name ev6-001 --segment 實際32位ID
```

上游 viewer 使用 Raylib 原生視窗；純 SSH 不會自動出現圖形畫面。它與 Jupyter 的訓練圖表是不同介面。

做 DAgger 風格的 on-policy 微調：

```bash
"$LAB_PY" lab.py rl-train --name ev6-001 --segment 實際訓練組32位ID --steps 10
"$LAB_PY" lab.py simulate --name ev6-001 --segment 實際驗證組32位ID --on-policy work/runs/ev6-001/on_policy.pt
"$LAB_PY" lab.py evaluate --name ev6-001 --checkpoint best.pt --on-policy on_policy.pt
```

不要拿驗證片段做 RL 訓練。上游 RL 每次以指定單一片段運作，可能嚴重過擬合；本流程不把完成 10 步當成泛化或安全證明。4B 世界模型在 Spark 的 NVFP4 kernels、記憶體與速度尚須實測，若失敗，保留已可用的 supervised 實驗，再依完整 traceback 排查，不要任意降版本。

## 8. 匯出與交給車端

只用 supervised：

```bash
"$LAB_PY" lab.py evaluate --name ev6-001 --checkpoint best.pt
"$LAB_PY" lab.py export --name ev6-001 --release ev6-r001 --checkpoint best.pt
```

採用已重新評估的 RL action head：

```bash
"$LAB_PY" lab.py export --name ev6-001 --release ev6-r001-rl --checkpoint best.pt --on-policy on_policy.pt
```

輸出至 `work/releases/<release>`：ONNX、輸出維度、模型來源、校驗碼、同權重評估、真實驗證樣本的 parity fixture。匯出器比較訓練模式最後一步、單步封裝、ONNX Runtime 以及 8 步循環狀態，發現數值差異超出設定容差就停止。

接著依 [車端部署與回復](DEPLOYMENT_zh-TW.md) 操作。Spark 匯出 ONNX，Chestnut 在自己的 AMD 後端編譯。**不要把 Spark 的 CUDA 執行檔複製成車端模型。**

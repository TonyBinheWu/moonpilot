# taco2 nav_features 移植實驗

目前完成 **可選的 64 維輸入支援與離線驗證**。Chestnut 現有大模型仍未具備路線條件式導航能力；沒有更換或修改大小模型權重，也沒有啟用新的行車導航特徵來源。

## taco2 實際資料流

參考提交固定為 `commaai/openpilot:a8c957e9d98cb3c334ab60c09e731c262605ae56`。

1. `navd/map_renderer` 依位置、航向、路線與指定地圖樣式輸出 256 × 256 影像。
2. `navmodel.onnx` 接收 `[1, 1, 256, 256]`，輸出 228 個數值：132 個 plan mean/std、32 個 desire prediction、最後 64 個 features。
3. `navModel.features` 經 `modeld` 傳入原始 `supercombo.onnx` 的 `nav_features`，形狀 `[1, 64]`，型別 float16。

這 64 個值是編碼器學出的表示，不是經緯度、轉彎角度或文字。不能把 Mapbox 路線座標補到 64 個數字就視為相容輸入。

## 這次實作

- 現行共用大小模型的編譯器會依模型 metadata 選擇性加入 `nav_features`，完成 CPU 打包、影像位移、GPU 輸入拆包與模型 dtype 轉換。沒有該輸入的模型保留原本的打包格式。
- stock 與 legacy 兩個執行器都能從明確傳入的 `inputs['nav_features']` 寫入模型緩衝區。要求 `[1, 64]` 浮點、有限值且落在 float16 範圍；資料缺少時歸零，錯誤資料清除後拋出例外。對不支援的模型明確傳入特徵會被拒絕。
- 支援能力只代表輸入可傳遞；不推定不同模型的 64 維表示具有相同語意。**目前行車主迴圈沒有提供這個輸入**，沒有把未配對的編碼器特徵送入控制。
- 新增離線工具，使用 SHA-256 固定的 taco2 編碼器做實際 ONNX Runtime CPU 推論；檢查目標模型的輸入、資料流與數值反應。工具不發布訊息、不修改模型、不連接車輛。

## 實際離線結果

完整原始報告：[HKG_NAV_FEATURES_PROBE.json](HKG_NAV_FEATURES_PROBE.json)。

| 模型 | 導航輸入 | 本次結果 |
| --- | --- | --- |
| taco2 `navmodel.onnx` | 地圖影像 | 四種人工測試影像均產生有限的 64 維特徵；不同影像產生不同特徵 |
| taco2 `supercombo.onnx` | `[1, 64]` float16 | 已實際推論；固定其他輸入，只改導航特徵，模型輸出隨之改變 |
| 本分支小模型 | 無 | 回報 `unsupported_no_nav_features`，沒有執行導航輸入推論 |
| 本分支 Chestnut 大模型 | 無 | 回報 `unsupported_no_nav_features`，沒有執行導航輸入推論 |

人工測試影像是空白、直線、左折線、右折線；不是 taco2 的完整地圖渲染，也不是道路資料。駕駛模型的相機與歷史輸入固定為零，只檢查數值是否受到導航特徵影響。**這不能證明模型會依左右路線轉彎或可安全上路**。即使空白影像也會產生非零特徵，不能拿空白地圖的編碼當成「未導航」的通用替代值。

## 為何不能直接接到目前大模型

taco2 的 recurrent features 是 `[1, 99, 128]`，本分支大模型是 `[1, 32, 32, 512]`；兩者的時間歷史、特徵結構、模型輸入與輸出解析不同。不能將 taco2 的導航權重直接複製到 Chestnut 模型，或用 64 維導航特徵覆蓋相機的 recurrent features。

在 ONNX 宣告一個沒被運算節點使用的 `nav_features` 也不會影響結果。稽核工具會列出輸入到輸出的依賴，測試包含這種「有欄位但沒接上運算」的情況。

完成大模型導航仍需要：

1. 可用於訓練／微調的大模型圖與 checkpoint，以及具有路線、定位、相機、車輛動作對齊的資料。
2. 與導航編碼器配對訓練的融合層或 adapter，保留現有視覺特徵並加入導航條件；訓練也須涵蓋無導航、改道與資料失效。
3. 匹配訓練規格的地圖渲染與即時編碼器，包含路線身分、時間戳、取消與失效處理。
4. 離線路線反事實比較、replay、Shadow Mode，以及裝置延遲／fallback 驗證，才評估行車接入。

目前儲存庫提供的是推論模型，這次沒有取得上述訓練資料或已配對的導航大模型權重。

## 重現離線實驗

另外下載原始 taco2 的兩個 LFS 模型到本機測試目錄。不要將它們覆蓋到裝置的現行駕駛模型。

| 檔案 | SHA-256 |
| --- | --- |
| `navmodel.onnx` | `f851f19b0a9e2299639f18856e4879ee863918227b7cef6a07eb6b94a273a9aa` |
| `supercombo.onnx` | `f794d65ddfb3600e0b3f7d7e894d1dd278b88569de863aae41246eec43f035cf` |

在儲存庫根目錄、已安裝 `numpy`、`onnx`、`onnxruntime` 的 Python 環境執行：

```bash
python -m openpilot.sunnypilot.navd.tools.probe_taco2 \
  --encoder /path/to/taco2/navmodel.onnx \
  --driving /path/to/taco2/supercombo.onnx \
  --driving openpilot/selfdrive/modeld/models/driving_supercombo.onnx \
  --driving openpilot/selfdrive/modeld/models/big_driving_supercombo.onnx
```

導航測試共 30 項通過。測試涵蓋現行及 legacy 編譯器的小／大 recurrent layout、有／無導航輸入，共 8 種 CPU JIT 組合；另檢查 JIT capture 後更新、缺失資料歸零、影像與其他輸入偏移，以及 ONNX 未使用的假輸入。tinygrad 測試需初始化已固定的 `tinygrad_repo`，並安裝 `clang`；CI 會執行同一套測試。尚未做真實 GPU 或裝置行車推論測試。

## 原始程式

- [taco2 模型規格與特徵位置](https://github.com/commaai/openpilot/blob/a8c957e9d98cb3c334ab60c09e731c262605ae56/selfdrive/modeld/models/nav.h)
- [taco2 編碼器推論與 features 發布](https://github.com/commaai/openpilot/blob/a8c957e9d98cb3c334ab60c09e731c262605ae56/selfdrive/modeld/models/nav.cc)
- [taco2 modeld 輸入](https://github.com/commaai/openpilot/blob/a8c957e9d98cb3c334ab60c09e731c262605ae56/selfdrive/modeld/modeld.cc)
- [taco2 地圖渲染](https://github.com/commaai/openpilot/blob/a8c957e9d98cb3c334ab60c09e731c262605ae56/selfdrive/navd/map_renderer.cc)

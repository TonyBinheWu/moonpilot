# Chestnut 上游版本對齊

## 固定基準

本次對齊的是下列快照，不會自動追蹤移動中的 master：

- openpilot：`b97a2d82018efe4a65cc961ea962c30df88d47b0`
- tinygrad：`f6fc4e3f2c3db5fae1e19cbfbc3ad9fc579a12ae`
- teleoprtc：`1aa8fc433bef1519a95c0700c96258c3be6dfb34`（原本已相同，未變更）

參考來源：

- https://github.com/commaai/openpilot/tree/b97a2d82018efe4a65cc961ea962c30df88d47b0
- https://github.com/tinygrad/tinygrad/tree/f6fc4e3f2c3db5fae1e19cbfbc3ad9fc579a12ae
- https://github.com/commaai/openpilot/pull/38684

## 變更範圍

`tinygrad_repo` 直接引用上游來源與固定 SHA，不把第三方程式複製進主專案。
stock `modeld/SConscript` 使用上游的 `TC_MIN_GLOBALS=32`，並在各建置平台
包含 `1928x1208` 與 `1344x760` 兩組相機 JIT。分塊大小估算也比照上游
計入兩組相機；這可能增加編譯時間與磁碟需求，不是把 C3X 相機改成 C4 尺寸。
除了保留原有的 `SKIP_TINYGRAD_COMPILE` 外，該建置檔與上述 openpilot 快照一致。

沿用現有的完整 NV12 輸入、AMD warp 與模型執行流程。不新增 C3X 縮圖路徑，
不改動車輛控制、Panda、opendbc、扭力限制、OSM 或 Visuals。
不提高或隱藏 Driving Model Lagging 門檻。

## 產物相容性與驗證限制

更新 tinygrad 原始碼，不等於既有模型產物已重新編譯。
SCons 的 stock 模型重建不會自動重新產生模型選擇器下載的所有 `.pkl`。
本次未更改選擇器目錄版本、下載來源、模型 hash 或使用者選取狀態。

模型選擇器的 CTM／CTM v2 必須另外核對其 tinygrad 版本、編譯參數、
裝置配置、相機 JIT 與模型 hash；需要同版本重新產生或取得相容的產物。
不能以修改 hash、跳過完整性檢查或刪除整個模型目錄代替相容性驗證。

本次的測試僅驗證原始碼層級的建置設定，不包含實際 SCons 建置、
QCOM／AMD 編譯、模型 pickle 載入、model replay 或 C3X／C4 實機掉幀測試。
因此本次對齊不是「Driving Model Lagging 已修復」的證明。

## 更新與回退

僅在停車、關閉車輛接管的測試環境執行；先保存本地修改與模型產物備份。

```bash
# 在已切換至本次更新的 checkout 內執行。
git submodule sync -- tinygrad_repo teleoprtc_repo
git submodule update --init --recursive --checkout -- tinygrad_repo teleoprtc_repo
git ls-tree HEAD tinygrad_repo teleoprtc_repo
git submodule status -- tinygrad_repo teleoprtc_repo

# 使用 modeld 實際使用的 Python 環境，確認 import 位置。
python -c 'import sys,tinygrad,teleoprtc; print(sys.executable); print(tinygrad.__file__); print(teleoprtc.__file__)'

# 執行不依賴 SCons／GPU 的原始碼設定測試。
python openpilot/selfdrive/modeld/tests/test_chestnut_build_config.py
```

以專案正常建置流程重新編譯 stock 模型，確認 `SKIP_TINYGRAD_COMPILE`
未啟用，並核對建置記錄中的 `TC_MIN_GLOBALS=32` 與兩組相機尺寸。
選擇器產物仍依上一節另行驗證；不要假設 stock 重建已涵蓋它們。
離線確認載入、輸出有效性與回退正常，再比較完整迴圈延遲與掉幀率。

回退時應一起還原主專案版本、submodule SHA 與該版本相容的模型產物，
不能僅還原 tinygrad 而繼續使用另一版編譯的 `.pkl`。

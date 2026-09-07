# Browser preview of the real settings UI

在開發電腦的瀏覽器操作與裝置相同的 Python/raylib 設定頁。支援點選、拖曳、捲動、鍵盤文字及貼上；修改 UI Python 或 `.po` 翻譯後自動重新啟動 UI，瀏覽器會重新連線。

本工具目前預覽**設定頁**，包括導航、轉向、顯示與語系。不是行車模擬器：不執行相機、駕駛模型、控制器、Panda、導航工作程序、更新程式或上傳程序。Wi-Fi 使用記憶體內的示範服務；需要背景程序的下載與更新不會完成。不能據此判定轉向、路線正確性、硬體效能或整車安全。

## Docker Desktop / Docker Compose

在這份 repository 的根目錄執行。電腦需安裝 Git LFS 及 Docker Compose；首次建置需要下載依賴。Apple Silicon 使用 amd64 模擬執行，速度會較慢。

```sh
git submodule update --init opendbc_repo msgq_repo tinygrad_repo
git lfs pull --include='openpilot/**/assets/**'
docker compose -f tools/ui_preview/compose.yaml up --build
```

開啟 <http://localhost:8080>。左側選單可捲動，找到 **Navigation** 測試導航設定。先打開裝置 UI 的文字對話框，再把文字貼入網頁下方輸入欄並按「送入欄位」；使用裝置畫面內的箭頭確認。不要將正式金鑰放入原始碼或截圖。

UI 與翻譯資料夾以唯讀方式掛載；編輯電腦上的原始碼即可觸發預覽重載。修改其他 Python、原生程式、schema 或依賴後，重新執行上述 Compose 指令建置。停止後再次啟動會重設預覽參數；UI 自動重載會保留同次預覽中的設定。

## 已有 Linux Python 開發環境

需要 Python 3.12、g++、make 及 Mesa EGL/OpenGL 系統函式庫。使用獨立虛擬環境，避免取代專案的行車開發依賴：

```sh
python3.12 -m venv /tmp/sunnypilot-ui-venv
. /tmp/sunnypilot-ui-venv/bin/activate
pip install -r tools/ui_preview/requirements.txt
pip install -e ./opendbc_repo -e ./msgq_repo -e ./tinygrad_repo
python tools/ui_preview/build.py
python tools/ui_preview/preview.py --watch
```

無須 X server、車輛或 GPU。預覽使用 headless raylib 和軟體渲染。原生設定頁及 HTTP 輸入傳輸已在 Linux Python 3.12 環境驗證；Docker 封裝仍需在使用者的 Docker 環境確認。

## 隔離與驗證

每次啟動產生獨立 Params 目錄及 messaging namespace，車輛資料只用於顯示 EV6 相容項目，`safetyModel` 設為 `noOutput`。工具拒絕在 `/AGNOS` 或 `/TICI` 裝置執行。預設 HTTP 僅監聽 loopback，Compose 也只發佈到電腦的 127.0.0.1；不要把此開發伺服器公開到網際網路。HTTP 輸入需符合本次工作階段 token、Host 及 Origin；不記錄輸入文字。

取得單張原生畫面：

```sh
python tools/ui_preview/preview.py --screenshot /tmp/settings.png
```

驗證字型覆蓋、中英文切換、貼上及瀏覽器輸入：

```sh
python -m unittest tools.ui_preview.test_preview
```

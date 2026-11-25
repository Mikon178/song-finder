# 聽歌尋曲小工具 (Song Finder)

一個免 API Key 即可開箱使用的網頁小工具，支援從**音檔 / 麥克風錄音**或**一小段歌詞**找出對應歌曲，每次列出 **1 個最符合的選項** 與 **4 個可能的選項**，並附上各音樂平台連結與 30 秒試聽功能。

## 功能特色

- **雙模式辨識**：
  - **音檔辨識**：支援拖放/上傳音檔（`.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`, `.webm` 等）或直接使用麥克風錄音，整合 Shazam 音訊指紋引擎（內含多時間點自動掃描）。
  - **歌詞搜尋**：整合 LRCLIB 歌詞資料庫、iTunes 與 Deezer 曲庫，支援中、英、日、韓等多語系歌詞片段比對與命中歌詞摘要顯示。
- **1 + 4 結果呈現**：
  - **1 個最符合的選項**：顯示信心度、命中原因/歌詞摘要、專輯封面、30 秒試聽播放器與連結。
  - **4 個可能的選項**：列出其餘最接近的 4 首候選歌曲，同樣附上連結與試聽。
- **平台連結**：每首歌皆附上 **YouTube**、**YouTube Music**、**Spotify**、**Apple Music**（以及可用的 **Shazam / Deezer**）連結。
- **選填增強引擎**：可在網頁右上角設定中選填 `Gemini API Key`（適合哼唱、清唱翻唱、模糊或翻譯歌詞）或 `AudD API Token`。

---

## 部署成線上公開網頁版（HTTPS 網址，手機與電腦皆可用）

專案已內建 `Dockerfile`、`render.yaml` 與 `Procfile`，可免費部署到雲端取得公開網址（HTTPS 環境下手機與電腦都能直接使用麥克風錄音）：

### 方式 A：Hugging Face Spaces（免安裝 Git/Docker，瀏覽器上傳即可）
1. 前往 [Hugging Face Spaces](https://huggingface.co/new-space) 建立新 Space，SDK 選擇 **Docker**（Blank）。
2. 在 Space 的 **Files → Add file → Upload files** 上傳專案資料夾中的檔案：`Dockerfile`、`.dockerignore`、`requirements.txt`、`app.py`、`recognizer.py` 與 `static/` 資料夾。
3. 等待建置完成後，即可取得公開網址（`https://<username>-<space>.hf.space`）。

### 方式 B：Render（支援一鍵 Docker Blueprint）
1. 將專案資料夾推上 GitHub 倉庫。
2. 前往 [Render](https://render.com) → **New + → Blueprint**（或 **Web Service**），選擇該倉庫。
3. Render 會自動讀取 `render.yaml` 與 `Dockerfile` 完成建置並提供公開 `https://*.onrender.com` 網址。

### 方式 C：Railway / Zeabur
- 直接匯入專案（或連結 GitHub），平台會自動偵測 `Dockerfile` 或 `Procfile` 並綁定 `$PORT` 啟動服務。

---

## 本機啟動方式

1. 雙擊執行 `run.bat`，或在終端機於專案目錄執行：
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765
   ```
2. 瀏覽器開啟 `http://127.0.0.1:8765`。

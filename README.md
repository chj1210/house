# 台北市購屋評估分析平台 (Taipei Real Estate Intelligence Platform)

針對台北房市特性（高單價、車位灌水稀釋單價、地上權混淆、多房仲重複刊登、隱晦事故屋黑話），實作完成之 **591 即時擷取 × 多源風險排除 × 實價登錄 AVM 對標** 系統。

---

## 📂 專案結構

- [run_pipeline.py](file:///d:/05_private/項下/AI_proj/001/run_pipeline.py)：核心 CLI 驗證與批次評估管線（一鍵輸出 S/A 級清單、JSON 評估報告與 LINE/Telegram 推播電報）。
- [app.py](file:///d:/05_private/項下/AI_proj/001/app.py)：FastAPI 後端伺服器，提供 REST API 與互動式戰情室介面。
- [static/index.html](file:///d:/05_private/項下/AI_proj/001/static/index.html)：Web 視覺化購屋雷達看板（含 Leaflet 台北市地圖、一鍵體檢報告 Modal、單筆 591 物件即時試算器）。
- `src/` 核心引擎模組：
  - [models.py](file:///d:/05_private/項下/AI_proj/001/src/models.py)：資料模型（591 刊登、實價登錄、風險檢核、對標明細與評估報告）。
  - [scraper_591.py](file:///d:/05_private/項下/AI_proj/001/src/scraper_591.py)：591 台北市房屋擷取與 `.woff/.ttf` 動態加密數字字型破解器（`FontDeobfuscator591`）。
  - [lvr_sync.py](file:///d:/05_private/項下/AI_proj/001/src/lvr_sync.py)：內政部實價登錄 Open Data（`A_lvr_land_A.csv`）解析器（自動換算坪數、拆算車位、剔除親友特殊交易）。
  - [deduplicator.py](file:///d:/05_private/項下/AI_proj/001/src/deduplicator.py)：多房仲重複刊登歸戶引擎（社區/路段 + 坪數誤差 ±0.5 坪 + 樓層 + 屋齡，自動找出全網最低開價管道）。
  - [risk_filter.py](file:///d:/05_private/項下/AI_proj/001/src/risk_filter.py)：四道硬性排他風控過濾器（法拍屋、凶宅庫與黑話 NLP、北市地上權/工業宅/頂加灌水、北市建管處海砂屋/輻射屋列管）。
  - [avm_engine.py](file:///d:/05_private/項下/AI_proj/001/src/avm_engine.py)：車位拆算與三層同心圓實價對標演算法（Tier 1 同社區大樓 → Tier 2 半徑 300m 同質同屋齡 → Tier 3 行政區基準）。
  - [notifier.py](file:///d:/05_private/項下/AI_proj/001/src/notifier.py)：LINE / Telegram Bot 秒殺物件結構化推播產生器。
- [tests/test_platform.py](file:///d:/05_private/項下/AI_proj/001/tests/test_platform.py)：自動化單元與 API 整合測試。

---

## 🚀 快速執行方式

### 1. 執行核心 Python 資料擷取與 AVM 對標腳本
```powershell
python run_pipeline.py
```
執行後將自動產生：
- [output/evaluation_report.json](file:///d:/05_private/項下/AI_proj/001/output/evaluation_report.json)：完整歸戶與實價對標結構化報告
- [output/s_tier_alerts.txt](file:///d:/05_private/項下/AI_proj/001/output/s_tier_alerts.txt)：S 級與降價突破型物件之 LINE / Telegram 秒殺推播通知卡片

### 2. 啟動 Web 視覺化購屋雷達戰情室
```powershell
python app.py
```
啟動後以瀏覽器開啟 `http://127.0.0.1:8000` 即可使用互動式儀表板與「單筆 591 物件即時試算器」。

### 3. 執行自動化測試
```powershell
python -m unittest discover -s tests -v
```

POWER AI V05 Secure — Render 替換部署版

用途
====
把目前最順手的 V05 版本部署到你原本的 Render 網站：
https://power-ai-cloud-test.onrender.com/

部署後：
- 打開網址先出現通關密語畫面
- 密語正確才進入 Power AI
- 原本 V05 的 MLCC / Power IC 替代料 / Circuit AI Review 保留
- 不需要 BAT / PowerShell / 本機 Python
- 通關密語不顯示在網址、檔名或頁面標題

你要做的事情
============
1. 解壓縮這個 ZIP。
2. 打開之前連接 Render 的 GitHub Repository。
3. 把 Repository 根目錄舊檔案刪除或覆蓋。
4. 上傳以下 5 個檔案：
   - app.py
   - app.html
   - requirements.txt
   - render.yaml
   - README_請先看.txt
5. Commit changes。
6. Render 若有 Auto Deploy，會自動重新部署。
7. 等 Deploy 顯示成功後，重新開：
   https://power-ai-cloud-test.onrender.com/

如果 Render 沒自動部署：
Render Dashboard → power-ai-cloud-test → Manual Deploy → Deploy latest commit

安全提醒
========
這是「簡易通關密語」，不是正式帳號/SSO安全機制。
熟悉前端程式的人仍有可能從網頁原始碼繞過。
目前用於 PoC / 小範圍測試是合理的。

如果未來要正式給整個部門使用，建議改公司 SSO / AD / 內網權限。

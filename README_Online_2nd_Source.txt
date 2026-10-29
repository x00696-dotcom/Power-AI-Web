POWER AI V13 Secure — Online 2nd Source Finder

這版的 2nd Source 已完全移除本地 IC database。
網站每次搜尋都由 Render backend 即時查詢線上元件資料源。

建議設定（最佳）：
1. Nexar / Octopart：找 Similar Parts + technical specs
   Render Environment 新增：
   NEXAR_CLIENT_ID
   NEXAR_CLIENT_SECRET

2. Mouser：補 Datasheet / Lifecycle / Stock / Price，也可單獨使用
   Render Environment 新增：
   MOUSER_SEARCH_API_KEY

至少設定其中一種即可。兩種都有時效果最好。

Render 最簡單更新方式：
1. 解壓 ZIP。
2. 把 app.py / app.html / requirements.txt / render.yaml 上傳並覆蓋 GitHub repository 根目錄舊檔。
3. Render Dashboard → power-ai-cloud-test → Environment。
4. 加入上面的 API credential（不要寫進 HTML / GitHub）。
5. Save Changes，Render 重新 Deploy。
6. 開網站，Power IC 替代料上方會顯示 Provider = Ready。

安全/工程原則：
- API credential 只存在 Render Environment，不送到瀏覽器。
- Online ranking 不是 AVL qualification。
- 沒有 datasheet-level pinout evidence 時，不宣稱 P2P / Drop-in。
- 同廠牌替代料會特別標示 Same Mfr；真正 2nd source 應優先看不同 Manufacturer。

申請入口：
- Nexar API: https://nexar.com/api
- Mouser Search API: https://www.mouser.com/en/api-search/


EXP SOP
- Website header includes an EXP export SOP download button.
- PDF file: PowerAI_EXP_Export_SOP.pdf
- SOP covers OrCAD Capture: Tools > Export Properties > Entire Design > Parts and Pins > Instance Properties.


V15.1: Provider status now validates the Mouser Search API key. "Invalid unique identifier" means the Search API key is invalid/wrong type.

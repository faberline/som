# M7 — CNL 需求入口與語意一致性

> Historical model-roadmap document. On 2026-10-13 the human replaced this plan with
> [the Rust roadmap](RUST_ROADMAP.md). Old milestone records were archived and deleted.
> Read [the current handoff](RUST_HANDOFF.md). Preserve the results below; do not resume old M3 training.
> New compiler implementation and acceptance remain pending. The default remains legacy M2.

## 決策與交接

主線在 2026-10-03 收到使用者授權的旁支交接。
來源對話 ID：`01a0fd74-60f9-7f90-afa5-0615f26d052f`。
使用者已選定把 CNL 工作放在 M7。
已登記 [M7](https://github.com/faberline/som/milestone/6)。
工作分為 [#16 契約](https://github.com/faberline/som/issues/16)、
[#17 解析器](https://github.com/faberline/som/issues/17) 與
[#18 驗收](https://github.com/faberline/som/issues/18)。
旁支只做評估，沒有修改程式、模型、設定或 GitHub。

CNL 是受控自然語言。它使用固定句型及明確的解讀規則。
一般英文不能直接當作 CNL 契約。

M2 繼續進行。M7 不取消 M2，也不授權新的完整訓練。
M6「檔案產物與按需讀取」只是旁支建議，仍未確認。
使用者曾質疑 64 KiB 的意義，但沒有確認新的上限。
M2 的 65,536 UTF-8 bytes 上限因此保持有效。

## 為何提出 M7

討論起於 M2 產物超過大小上限。
這個上限計算全部格式化後的檔案內容。
它包含程式、測試、migration、設定與說明。
它不是模型大小，也不是 prompt 或 DSL 長度。

旁支另提出把產物安放到 `/tmp/som-<run-id>/`。
主代理可取得檔案清單、雜湊、入口及驗收摘要。
檢查器讀取全部檔案，主代理按需要讀檔案。
這個交付方式尚未採用。
若採用，須建立新評測輪次及明確的可見性規則。
不得回填舊結果。
完整產物仍須另外封存，`/tmp` 只作工作區。

使用者接著提出「旗艦模型與本地語意編譯器」的概念。
核心仍是已核准的 code block 庫。
旗艦指定業務行為。本地模型選 block、順序及引用。
驗證器檢查完整性及契約。Assembler 展開已有程式。
解析器、模型及驗證器都不能新增未指定的業務步驟。

本輪的 Rust 是 SOM agent 的正式執行語言。
Python 是產出服務的目標語言。
跨 Rust、Swift 或 TypeScript 的產出另列後續工作。

## 第一階段架構

`旗艦 CNL → 規則解析器 → 型別化 SOM prompt → 模型 block plan → 驗證器 → assembler`

規則解析器只轉換明寫的需求。它不選 block。
同一份 CNL 與 JSON 契約應產生相同的模型輸入。
先驗證這一點，再決定能否沿用權重及 decoder。
新入口失敗時明確拒絕，不以 planner 補答案。

第二階段才評估讓模型直接讀取多種 CNL 表達。
那個階段須重新封存語言變體、資料分組及泛化測試。
第一階段通過不能作為模型直接解析 CNL 的證據。

## 支援範圍

完整表達現有能力，不增加新的 App：

- 資源、欄位、型別、必填、空值、預設值與唯一性。
- 資料庫、登入、儲存與既有 workflow。
- 具名操作、輸入、步驟結果與型別化常數。
- 分支、分支匯出、交易範圍與補償。
- 權限、結果與錯誤契約。

只允許契約明列的省略及預設規則。
必要資訊缺失、矛盾或引用不明時，解析器須拒絕。
「它」「相關資料」及「適當處理」不能替代具名引用。
禁止原始程式、SQL、block ID、任意運算式及任意迴圈。
沿用現有支援範圍及安全限制。
產物大小及按需讀取屬於另一個決策。

## 三項工作

1. 定義版本化 CNL 契約、句型、解讀規則、正反案例及拒絕規則。
2. 完成 Python 參考解析器及 Rust 正式解析器。
   兩者都產生既有的型別化 prompt。
3. 完成語意等價、相容性、診斷及輸入成本驗收。

先封存 grammar、需求及檢查器，再開始實作。
先完成本機測試，再評估付費代理測量或新的完整訓練。

## 驗收

- 同一需求的欄位、順序、引用、分支、權限、交易及補償全部一致。
- Python 與 Rust 的有效解析結果一致。
  無效輸入的拒絕類別一致。
- 錯誤引用、作用範圍、矛盾及缺漏確實失敗。
- 診斷包含行、列、出錯句子、原因及允許形式。
  不默默選定一種解讀。
- 同一契約的模型輸入一致。
  固定權重與 decoder 後，下游鏈路及產物相容。
- 使用既有服務及流程案例覆蓋語意。
  保留嚴格 Python 品質及獨立行為檢查。
- 封存 CNL、解析摘要、prompt、模型輸入及產物。
  記錄 grammar、契約、來源、工具及模型的雜湊。
- 先固定相同的產物交付方式，再比較 CNL 與 JSON。
  計入 grammar 文件、讀取、診斷及重試。
  缺少用量保持空白，不能當作零。

尚未證明 CNL 更省 token、更能組合或能縮小產物。
報告須分開列出這些問題。
規則解析器是否已足夠，也須以實測回答。

既有直接寫碼數據永久保留。
相同 App 的需求及驗收契約不變時，重用原 direct 基線。
只有新增 App 才收集新的直接寫碼基線。

## M2 與後續階段

M2 的 issue #4–#6 仍由原有關卡決定是否關閉。
M7 的登記不改變 M2 的結果。
`current`、原始失敗、封存資料及未提交變更全部保留。
M7 不啟動真實雲端、付費代理工作或預設切換。

設計參考由旁支提供，尚未作為 SOM 的驗收證據：

- [Attempto ACE troubleshooting](https://attempto.ifi.uzh.ch/site/docs/ace_troubleshooting.html)
- [Attempto APE](https://github.com/Attempto/APE)
- [Controlled Natural Languages survey](https://aclanthology.org/J14-1005/)

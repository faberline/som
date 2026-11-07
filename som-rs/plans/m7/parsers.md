> Historical M7 plan from 2026-10-03. The fixed Rust compiler replaced this model-based plan.
> The model inputs, weights, Python parser and training tasks below are not current requirements.
> Read [the current architecture](../../../docs/architecture.md) and [the compiler roadmap](../../RUST_ROADMAP.md).

## 目的

實作 Python 參考 CNL 解析器及 Rust 正式解析器。
先完成並封存 M7 的 CNL 契約，再開始這項工作。

## 路徑

旗艦 CNL → 規則解析器 → 型別化 SOM prompt → 模型 block plan → 驗證器 → assembler。

解析器只輸出公開 prompt。它不選 block，也不新增業務步驟。
正式生成保持 Rust。新入口失敗時，不使用 planner 補答案。

## 完成條件

- [ ] Python 與 Rust 的有效解析結果逐欄一致。
- [ ] 無效輸入得到相同拒絕類別。
- [ ] 診斷包含行、列、出錯句子、原因及允許形式。
- [ ] 型別化常數保留型別、值及浮點數正負零。
- [ ] 順序、引用、分支、交易、權限及補償全部保留。
- [ ] 同一 CNL／JSON 契約產生相同模型輸入。
- [ ] 固定權重與 decoder 後，現有下游鏈路及產物相容。
- [ ] 新入口失敗不改用另一解讀或舊解碼路徑。
- [ ] parser、grammar、契約、工具及來源雜湊都有證據。

## 邊界

先評估保留既有模型輸入與權重。
若改讓模型直接讀取 CNL，另封存語言變體、資料分組及泛化測試。
本 issue 通過不能作為模型直接解析 CNL 的證據。
不啟動付費代理、真實雲端或預設切換。

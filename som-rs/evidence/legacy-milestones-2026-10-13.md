# Legacy milestone archive — 2026-10-13

These are the six milestone records captured before deletion.
The new plan is [RUST_ROADMAP.md](../RUST_ROADMAP.md).
Old issue bodies and comments remain on GitHub. Their complete pre-reset API snapshot remains local.
The evidence index records its SHA-256 hash.

Git baseline: `8c2ab0b7d4aa83eac8f4a87c2dabfc1b05bbe848`.

## Milestones

### Old GitHub milestone 1: M1 — SOM 基線與模型決策範圍

State at capture: `closed`. Created: `2026-10-01T12:43:36Z`. Closed: `2026-10-02T05:57:26Z`.

保存可追溯的模型、block、契約與評測基線。對照規則式 planner，明確列出模型負責的決策。先封存未見組合與資料流測試，再開始本輪訓練。完成條件：基線可重現，決策分工清楚，測試及評測方法已封存。此 milestone 不設定到期日。

### Old GitHub milestone 2: M2 — 語意契約與可組合 DSL

State at capture: `closed`. Created: `2026-10-01T12:43:37Z`. Closed: `2026-10-04T20:00:00Z`.

讓旗艦能明確表達資料流、執行順序及交易需求。模型產生符合契約的 block 組合。驗證器接受合法替代組合，拒絕錯誤依賴、權限及交易。完成條件：Python 參考路徑與 Rust 測試模型路徑可驗證相同契約；模型輸入保留所有影響選擇的需求。

### Old GitHub milestone 3: M3 — 無重疊資料與組合模型

State at capture: `open`. Created: `2026-10-01T12:43:37Z`. Closed: `None`.

固定兩層、寬度64、四heads、vocab1024、context4096、seed17的小模型組合訓練。資料2400訓練／300開發／300新正式，完整輸入、去名結構及引用圖不得重疊。2700份訓練及開發答案先通過完整品質及獨立行為。最早原始開發完整序列300/300 checkpoint才進正式驗收。正式Rust Metal約束路徑須300/300；同checkpoint原始對照另列。完整品質、功能、byte parity、拒絕、legacy100+214與M2保護全過，才能安全切換M3開發預設。共享累計訓練／開發評估604800秒；維持12檔案、64block及輸入／輸出各2048token。大小、時間與記憶體只觀測。2026-10-11：資料與2700份答案通過；訓練中斷，完整checkpoint74000開發3/300；正式驗收未跑。拆為19張子票（2張已完成、17張待完成），原#7已完成，#8與#9保持開啟。不放寬門檻、不拼接輪次、不執行付費代理或真實雲端。 2026-10-12：來源提交a657f49已推送main。交接由#21、#22、#23繼續；M3未通過。

### Old GitHub milestone 4: M4 — 完整功能與 Python 品質驗收

State at capture: `open`. Created: `2026-10-01T12:43:38Z`. Closed: `None`.

候選模型與 block 通過舊回歸、新組合、完整 App 行為、Rust／Python 一致性及嚴格 Python 品質檢查。完成條件：適用檢查全部通過，每項都有來源雜湊及執行證據。效能與資源只報告觀測數值。

### Old GitHub milestone 5: M5 — Token 對照與候選採用決策

State at capture: `open`. Created: `2026-10-01T12:43:39Z`. Closed: `None`.

使用 Codex gpt-6.1-sol 與 Claude Code claude-opus-5-5 的可重用直接寫碼基線，比較同契約的 SOM 候選。每個代理、每套 App 及整體須至少節省 30% 總 token，功能與品質全部通過。候選驗收、Claude 補測、真實雲端與預設切換分開追蹤。

### Old GitHub milestone 6: M7 — CNL 需求入口與語意一致性

State at capture: `open`. Created: `2026-10-02T17:42:01Z`. Closed: `None`.

新增受控自然語言需求入口。先由規則解析器還原既有型別化 SOM prompt，再使用現有模型、驗證器及 assembler。規則解析器不選 block，也不新增業務步驟。完成條件：CNL／JSON 語意等價、Python／Rust 解析一致、拒絕及診斷可驗證、模型輸入與下游相容。先完成本機證據，再評估付費對照或直接讀取 CNL 的模型訓練。M2 繼續；M6、產物上限與按需讀取仍另行決定。

## Issue mapping

Counts below use individual issue records. Old milestone counters were inconsistent.

| Old issue | Original state | Action | New work |
|---|---|---|---|
| [#1](https://github.com/faberline/som/issues/1) 保存基線並釐清 SOM 模型實際負責的決策 | closed | Preserved closed | Historical evidence |
| [#2](https://github.com/faberline/som/issues/2) 建立規則式 planner 與 SOM 模型的共同對照 | closed | Preserved closed | Historical evidence |
| [#3](https://github.com/faberline/som/issues/3) 封存模型決策難度與未見組合的驗收矩陣 | closed | Preserved closed | Historical evidence |
| [#4](https://github.com/faberline/som/issues/4) 定義資料流、順序與交易範圍的 SOM 語意契約 | closed | Preserved closed | Historical evidence |
| [#5](https://github.com/faberline/som/issues/5) 補齊可組合 block 契約與通用 DSL 驗證器 | closed | Preserved closed | Historical evidence |
| [#6](https://github.com/faberline/som/issues/6) 檢查模型輸入資訊完整性並完成 Rust 組合解碼 | closed | Preserved closed | Historical evidence |
| [#7](https://github.com/faberline/som/issues/7) 建立無重疊的需求與 DSL 訓練資料 | closed | Preserved closed | Historical evidence |
| [#8](https://github.com/faberline/som/issues/8) 訓練並選用可重現的監督式組合候選模型 | open | Superseded (`not_planned`) | [#40](https://github.com/faberline/som/issues/40) |
| [#9](https://github.com/faberline/som/issues/9) 加入受約束解碼並驗證失敗處理 | open | Superseded (`not_planned`) | [#40](https://github.com/faberline/som/issues/40), [#42](https://github.com/faberline/som/issues/42) |
| [#10](https://github.com/faberline/som/issues/10) 驗收 Rust／Python 一致性、相容性與拒絕分類 | open | Superseded (`not_planned`) | [#42](https://github.com/faberline/som/issues/42), [#43](https://github.com/faberline/som/issues/43) |
| [#11](https://github.com/faberline/som/issues/11) 完成十套 App、十四套服務與未見組合的獨立功能驗收 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#12](https://github.com/faberline/som/issues/12) 完成產出 Python 的嚴格型別與品質驗收 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#13](https://github.com/faberline/som/issues/13) 封存可重用的 Codex／Claude 直接寫碼基線 | open | Superseded (`not_planned`) | Retired; no replacement gate |
| [#14](https://github.com/faberline/som/issues/14) 完成 SOM 候選的完整可見性與逐 App token 對照 | open | Superseded (`not_planned`) | Retired; no replacement gate |
| [#15](https://github.com/faberline/som/issues/15) 整理候選採用證據、Claude 補測與後續切換 | open | Superseded (`not_planned`) | [#44](https://github.com/faberline/som/issues/44) |
| [#16](https://github.com/faberline/som/issues/16) 定義 M7 CNL 契約、句型與拒絕規則 | open | Superseded (`not_planned`) | [#38](https://github.com/faberline/som/issues/38) |
| [#17](https://github.com/faberline/som/issues/17) 完成 M7 Python 與 Rust CNL 解析器 | open | Superseded (`not_planned`) | [#39](https://github.com/faberline/som/issues/39) |
| [#18](https://github.com/faberline/som/issues/18) 完成 M7 語意等價、診斷與輸入成本驗收 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#19](https://github.com/faberline/som/issues/19) M3：封存 r10 資料分組與零重疊稽核 | closed | Preserved closed | Historical evidence |
| [#20](https://github.com/faberline/som/issues/20) M3：封存 2,700 份訓練與開發答案的品質及行為驗證 | closed | Preserved closed | Historical evidence |
| [#21](https://github.com/faberline/som/issues/21) M3：封存訓練中斷並結清共用時間帳本 | closed | Preserved closed | Historical evidence |
| [#22](https://github.com/faberline/som/issues/22) M3：恢復並核對 M2 本機服務及回復入口 | closed | Preserved closed | Historical evidence |
| [#23](https://github.com/faberline/som/issues/23) M3：分析 74,000 步仍只有 3／300 的完整序列錯誤 | closed | Preserved closed | Historical evidence |
| [#24](https://github.com/faberline/som/issues/24) M3：在剩餘預算內訓練並選出最早原始 300／300 checkpoint | open | Superseded (`not_planned`) | [#40](https://github.com/faberline/som/issues/40) |
| [#25](https://github.com/faberline/som/issues/25) M3：封存選定模型的完整 manifest 與可重現載入資料 | open | Superseded (`not_planned`) | [#41](https://github.com/faberline/som/issues/41), [#44](https://github.com/faberline/som/issues/44) |
| [#26](https://github.com/faberline/som/issues/26) M3：完成逐 token 約束、合法替代與 Python／Rust 拒絕對照 | open | Superseded (`not_planned`) | [#40](https://github.com/faberline/som/issues/40), [#43](https://github.com/faberline/som/issues/43) |
| [#27](https://github.com/faberline/som/issues/27) M3：驗證選定 checkpoint 的 Rust Metal、HTTP 與 CLI 完整路徑 | open | Superseded (`not_planned`) | [#42](https://github.com/faberline/som/issues/42), [#43](https://github.com/faberline/som/issues/43) |
| [#28](https://github.com/faberline/som/issues/28) M3：封存未見 300 題的實際 Rust Metal 約束生成 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#29](https://github.com/faberline/som/issues/29) M3：驗收正式產物第 1–100 題的品質與獨立行為 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#30](https://github.com/faberline/som/issues/30) M3：驗收正式產物第 101–200 題的品質與獨立行為 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#31](https://github.com/faberline/som/issues/31) M3：驗收正式產物第 201–300 題的品質與獨立行為 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#32](https://github.com/faberline/som/issues/32) M3：完成同 checkpoint 的 300 題原始無約束對照 | open | Superseded (`not_planned`) | Retired; no replacement gate |
| [#33](https://github.com/faberline/som/issues/33) M3：核對實際模型 DSL 的最終字節一致性與拒絕分類 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#34](https://github.com/faberline/som/issues/34) M3：重跑舊 100＋214 案例與封存 legacy 輸出 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#35](https://github.com/faberline/som/issues/35) M3：重跑 M2 的 114／12／12 與 218 份保護矩陣 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43) |
| [#36](https://github.com/faberline/som/issues/36) M3：完成全範圍稽核、證據封存與收尾報告 | open | Superseded (`not_planned`) | [#43](https://github.com/faberline/som/issues/43), [#44](https://github.com/faberline/som/issues/44) |
| [#37](https://github.com/faberline/som/issues/37) M3：安全切換開發預設並核對 GitHub 收尾 | open | Superseded (`not_planned`) | [#44](https://github.com/faberline/som/issues/44) |

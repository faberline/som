"""Append verified local progress without closing unmet milestone gates."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "som-rs/evals/m2-flow-2026-10-02/r1"


def api(path, *, payload=None):
    command = ["gh", "api", "repos/faberline/som/" + path]
    if payload is not None:
        command += ["--method", "PATCH", "--input", str(payload)]
    return json.loads(subprocess.check_output(command, text=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((EVAL / "snapshot-002/result.json").read_text())
    assert receipt["m1_passed"] and not receipt["m2_passed"]
    assert receipt["formal_artifact_budget"]["accepted"] == 0
    assert receipt["formal_artifact_budget"]["cases"] == 114
    details = {
        4: "新增 som.prompt.v7 與 som.dsl.v3 的機器可讀 schema。語意契約分開定義名稱綁定、前一步結果與程式符號。12 題開發 smoke 與 114 題正式案例已封存。開發與正式案例的實際模型輸入及去名稱結構均無重疊。權限、交易、分支作用範圍與補償規則已有 Python/Rust 檢查；完整服務行為仍待驗收。",
        5: "新增 block 的型別化參數、符號、能力與交易契約。原 130 個 block 物件保持相同。Python/Rust 的 127 個有效計畫與 35 個拒絕案例結果相同，包含合法替代計畫。新增 run journal、revision、lease 與版本化物件補償 helper 仍屬原型。每個 block 的完整行為正反證據與故障恢復矩陣尚未完成。",
        6: "新增 flow_plan 結構 token 編碼與 Rust 解碼路徑。162 個語意／解碼案例與 20 個純值案例通過一致性檢查。錯選、漏選及錯誤引用直接拒絕。沒有補答案或退回舊路徑。新 flow 測試權重、Metal 完整解碼與 v7 manifest／HTTP 正式接入尚未完成。舊模型在 Metal 的 314 題重播不算新 flow 模型驗收。",
    }
    common = """

## M2 實作紀錄 — 2026-10-02

M1 的證據已通過。Issue #1–#3 與 M1 已關閉。

{details}

### 已核對的本機證據

- 最新 Rust 執行檔的 314 題舊輸出與 M1 封存程式逐位元組相同。
- 127 份新草稿的 Python/Rust 最終內容相同；重新格式化不改變內容。
- 三份草稿通過固定 Ruff、strict mypy、Pylint 與 isort 契約。這不代表完整正式矩陣的品質與功能通過。
- 90 項 Python 契約／相容性測試通過。Rust 測試為 16 通過、3 忽略。忽略的 Metal 測試不算通過。

### 尚未通過

114 題正式產物全部超過 65,536 bytes，範圍為 85,484–106,445 bytes。最大 PostgreSQL 組合為 107,422 bytes，超出 41,886 bytes。檔案、block 與模型 token 數未超限。所有支援範圍內的拒絕仍算失敗。

尚須縮減共用 runtime、完成全部新產物品質檢查、Metal smoke、獨立資料庫／物件版本／中斷／並行恢復／migration 行為檢查。M2 沒有通過。本 issue 與 M2 保持 open。

### 封存位置

本機工作尚未提交或推送。報告為 som-rs/M2_FLOW_WORK.md。證據位於 som-rs/evals/m2-flow-2026-10-02/r1/snapshot-002/result.json。

來源補充封存 SHA-256：{archive}

目前題庫為 som-core/data/flow-v7-r10。它的需求檔與 r9 逐位元組相同。因新增 v7 database block，模型輸入綁定索引另行封存。acceptance-seal-002 的規格封存 SHA-256：440a139ed98c69bf6e74ef428a8dbdc26eb85bc07f444d84f2872b76faa92d43。

未啟動新權重訓練或付費代理量測。current、直接寫碼基線、原始失敗與既有工作樹變更均保留。Claude 實驗紀錄缺口、真實雲端、token 採用與預設切換留在 M5。
"""
    results = []
    for number, text in details.items():
        path = "issues/" + str(number)
        before = api(path)
        assert before["state"] == "open" and before["milestone"]["number"] == 2
        (args.output / f"issue-{number}-before.json").write_text(json.dumps(before, indent=2) + "\n")
        addition = common.format(details=text, archive=receipt["source_archive_sha256"])
        body = before["body"] + addition
        payload = args.output / f"issue-{number}-payload.json"
        payload.write_text(json.dumps({"body": body}, ensure_ascii=False) + "\n")
        api(path, payload=payload)
        after = api(path)
        (args.output / f"issue-{number}-after.json").write_text(json.dumps(after, indent=2) + "\n")
        assert after["state"] == "open" and after["body"] == body
        assert after["body"].startswith(before["body"])
        assert after["labels"] == before["labels"] and after["milestone"]["number"] == 2
        results.append({"number": number, "state": "open", "original_body_preserved": True,
                        "body_sha256": hashlib.sha256(body.encode()).hexdigest()})
    milestone = api("milestones/2")
    assert milestone["state"] == "open"
    (args.output / "milestone-2.json").write_text(json.dumps(milestone, indent=2) + "\n")
    result = {"issues": results, "milestone": {"number": 2, "state": "open"}, "passed": True}
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

#![cfg(feature = "legacy-model")]
use serde_json::Value;
use som::model::Decoder;
use std::fs;
use std::path::PathBuf;

#[test]
#[ignore = "requires the local Metal device and the current trained model"]
fn current_model_selects_all_hundred_operation_sequences() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let model = Decoder::load(&root.join("models/current")).unwrap();
    let cases =
        fs::read_to_string(root.join("../som-core/data/service-regression-v4/heldout.jsonl"))
            .unwrap();
    let mut previous = 0;
    let mut new = 0;
    for (index, line) in cases.lines().enumerate() {
        let row: Value = serde_json::from_str(line).unwrap();
        let generated: Value =
            serde_json::from_str(&model.generate(&row["prompt"].to_string()).unwrap()).unwrap();
        let selected = generated["files"][2]["blocks"]
            .as_array()
            .unwrap()
            .iter()
            .skip(1)
            .take(5)
            .map(|block| block["id"].as_str().unwrap())
            .collect::<Vec<_>>();
        let expected = row["dsl"]["files"][2]["blocks"]
            .as_array()
            .unwrap()
            .iter()
            .skip(1)
            .take(5)
            .map(|block| block["id"].as_str().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(selected, expected, "operation sequence {index}");
        if index < 50 {
            previous += 1;
        } else {
            new += 1;
        }
    }
    assert_eq!((previous, new), (50, 50));
}

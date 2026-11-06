use serde_json::{Value, json};
use sha2::{Digest, Sha256};

fn main() {
    let input = std::env::args().nth(1).expect("frozen corpus path");
    let corpus: Value = serde_json::from_slice(&std::fs::read(input).unwrap()).unwrap();
    let cases = corpus["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 1170, "Use the complete frozen corpus.");
    let mut records = Vec::new();
    for case in cases {
        let source = case["source"].as_str().unwrap();
        let old = format!("{:?}\t{:?}", baseline::parse_document(source), baseline::check_syntax(source));
        let new = format!("{:?}\t{:?}", candidate::parse_document(source), candidate::check_syntax(source));
        assert_eq!(old, new, "Legacy NLP result changed for {}", case["id"]);
        records.push(json!({"id":case["id"],"report_sha256":format!("{:x}",Sha256::digest(old.as_bytes()))}));
    }
    println!("{}", serde_json::to_string_pretty(&json!({"passed":true,"case_count":cases.len(),"records":records})).unwrap());
}

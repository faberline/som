//! Offline codec proof. This executable performs no model inference or assembly.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, BufRead};

#[derive(Debug, Default, PartialEq)]
struct Options {
    compact: bool,
    identity: bool,
    m3_input: bool,
    m3_semantic_input: bool,
    m3_bound_input: bool,
}

fn options(arguments: impl IntoIterator<Item = String>) -> Result<Options, String> {
    let mut selected = Options::default();
    for argument in arguments {
        match argument.as_str() {
            "--compact" if !selected.compact => selected.compact = true,
            "--identity" if !selected.identity => selected.identity = true,
            "--m3-input" if !selected.m3_input => selected.m3_input = true,
            "--m3-semantic-input" if !selected.m3_semantic_input => selected.m3_semantic_input = true,
            "--m3-bound-input" if !selected.m3_bound_input => selected.m3_bound_input = true,
            _ => return Err(format!("unknown or repeated argument: {argument}")),
        }
    }
    if (selected.m3_input || selected.m3_semantic_input || selected.m3_bound_input) && !selected.compact {
        return Err("M3 input flags require --compact".into());
    }
    if [selected.m3_input, selected.m3_semantic_input, selected.m3_bound_input].into_iter().filter(|flag| *flag).count() > 1 {
        return Err("select exactly one M3 input policy".into());
    }
    Ok(selected)
}

fn evaluate(row: &Value, catalog: &Value, m3_input: bool, m3_semantic_input: bool, m3_bound_input: bool) -> Result<Value, som::SomError> {
    let prompt = &row["prompt"];
    let features = if m3_bound_input {
        som::flow_codec::features_m3_bound_with(prompt, catalog)?
    } else if m3_semantic_input {
        som::flow_codec::features_m3_semantic_with(prompt, catalog)?
    } else if m3_input {
        som::flow_codec::features_m3_with(prompt, catalog)?
    } else {
        som::flow_codec::features_with(prompt, catalog)?
    };
    if row["action"] == "prompt" {
        return Ok(json!({"status": "ok", "features": features}));
    }
    let dsl = if let Some(encoded) = row["encoded"].as_str() {
        som::flow_codec::decode_with(prompt, encoded, catalog)?
    } else {
        row["dsl"].clone()
    };
    let encoded = som::flow_codec::encode_with(prompt, &dsl, catalog)?;
    let decoded = som::flow_codec::decode_with(prompt, &encoded, catalog)?;
    Ok(json!({"status": "ok", "features": features, "encoded": encoded, "dsl": decoded}))
}

fn main() {
    let selected = options(std::env::args().skip(1)).unwrap_or_else(|error| {
        eprintln!("{error}\nusage: som-flow-codec [--compact] [--identity] [--m3-input | --m3-semantic-input | --m3-bound-input]");
        std::process::exit(2);
    });
    let compact = selected.compact;
    let catalog = if compact {
        &*som::flow::COMPACT_CATALOG
    } else {
        &*som::flow::CATALOG
    };
    if selected.identity {
        let source = if compact {
            som::flow::COMPACT_CATALOG_TEXT
        } else {
            som::flow::CATALOG_TEXT
        };
        let version = if compact {
            som::flow_codec::COMPACT_VERSION
        } else {
            som::flow_codec::VERSION
        };
        println!(
            "{}",
            json!({
                "catalog_sha256": format!("{:x}", Sha256::digest(source.as_bytes())),
                "contract_sha256": som::flow::contract_hash(),
                "codec_version": version,
                "prompt_version": som::flow::VERSION,
                "dsl_version": som::flow::DSL_VERSION,
            })
        );
        return;
    }
    for line in io::stdin().lock().lines() {
        let result = match line {
            Ok(line) => match serde_json::from_str::<Value>(&line) {
                Ok(row) => match evaluate(&row, catalog, selected.m3_input, selected.m3_semantic_input, selected.m3_bound_input) {
                    Ok(value) => value,
                    Err(error) => {
                        json!({"status": "error", "code": error.code, "detail": error.detail})
                    }
                },
                Err(_) => json!({"status": "error", "code": "INVALID_JSON"}),
            },
            Err(error) => {
                eprintln!("{error}");
                std::process::exit(1);
            }
        };
        println!("{result}");
    }
}

#[cfg(test)]
mod tests {
    use super::{Options, options};

    #[test]
    fn arguments_are_explicit_and_order_independent() {
        for arguments in [
            vec!["--identity", "--compact"],
            vec!["--compact", "--identity"],
        ] {
            assert_eq!(
                options(arguments.into_iter().map(String::from)),
                Ok(Options {
                    compact: true,
                    identity: true,
                    m3_input: false,
                    m3_semantic_input: false,
                    m3_bound_input: false,
                })
            );
        }
        assert_eq!(options(Vec::<String>::new()), Ok(Options::default()));
    }

    #[test]
    fn unknown_or_repeated_flags_cannot_select_legacy_silently() {
        for arguments in [
            vec!["--compcat"],
            vec!["--compact", "--compact"],
            vec!["--identity", "--identity"],
            vec!["--m3-input"],
            vec!["--compact", "--m3-input", "--m3-input"],
            vec!["--m3-semantic-input"],
            vec!["--compact", "--m3-semantic-input", "--m3-semantic-input"],
            vec!["--compact", "--m3-input", "--m3-semantic-input"],
            vec!["--m3-bound-input"],
            vec!["--compact", "--m3-bound-input", "--m3-bound-input"],
            vec!["--compact", "--m3-bound-input", "--m3-semantic-input"],
        ] {
            assert!(options(arguments.into_iter().map(String::from)).is_err());
        }
    }

    #[test]
    fn m3_input_is_explicit_and_keeps_compact_output() {
        let selected = options(["--m3-input", "--compact"].map(String::from)).unwrap();
        assert!(selected.compact && selected.m3_input && !selected.identity);
    }

    #[test]
    fn semantic_m3_input_is_explicit_and_separate_from_archived_input() {
        let selected = options(["--compact", "--m3-semantic-input"].map(String::from)).unwrap();
        assert!(selected.compact && selected.m3_semantic_input);
        assert!(!selected.m3_input && !selected.identity);
    }

    #[test]
    fn bound_m3_input_cannot_silently_select_an_archived_policy() {
        let selected = options(["--compact", "--m3-bound-input"].map(String::from)).unwrap();
        assert!(selected.compact && selected.m3_bound_input);
        assert!(!selected.m3_input && !selected.m3_semantic_input);
    }
}

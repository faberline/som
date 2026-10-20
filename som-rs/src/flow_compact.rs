//! Closed template expansion for the separate compact candidate.
//! There is no planner, source input, interpreter or repair in this path.
use crate::{Block, SomError, composed, flow, flow_codec, quality::Quality};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const SUPPORT_TEXT: &str =
    include_str!("../../som-core/block_templates/flow_compact/forward_support.py.tpl");
pub const ENGINE_TEXT: &str =
    include_str!("../../som-core/block_templates/flow_compact/engine.py.tpl");

/// Emit only bounded HTTP route literals. Do not interpret source text.
pub fn compile_routes(value: &Value) -> Result<String, SomError> {
    let fail = || SomError::new("INVALID_VALUE", "invalid HTTP route fixtures");
    let text = value.as_str().filter(|text| text.len() <= 8192).ok_or_else(fail)?;
    let parsed: Value = serde_json::from_str(text).map_err(|_| fail())?;
    let routes = parsed.as_array().filter(|routes| (1..=64).contains(&routes.len())).ok_or_else(fail)?;
    let pattern = regex::Regex::new(r"^/(?:[A-Za-z0-9_-]+|\{(?:parent_id|child_id)\})(?:/(?:[A-Za-z0-9_-]+|\{(?:parent_id|child_id)\}))*$").unwrap();
    let mut lines = Vec::new();
    for route in routes {
        let object = route.as_object().filter(|object| object.len() == 2).ok_or_else(fail)?;
        let method = object.get("method").and_then(Value::as_str).filter(|method| ["get", "post", "patch", "delete"].contains(method)).ok_or_else(fail)?;
        let path = object.get("path").and_then(Value::as_str).filter(|path| path.len() <= 200 && pattern.is_match(path)).ok_or_else(fail)?;
        lines.push(format!("    ({}, {}),", flow::canonical(&json!(method)), flow::canonical(&json!(path))));
    }
    Ok(format!("(\n{}\n)", lines.join("\n")))
}

#[cfg(test)]
mod route_literal_tests {
    use super::compile_routes;
    use serde_json::json;

    #[test]
    fn route_literals_preserve_order_and_parameters() {
        let source = json!([
            {"method": "patch", "path": "/api-v1/parents/{parent_id}/children/{child_id}"},
            {"method": "get", "path": "/api-v1/parents"},
        ]).to_string();
        assert_eq!(compile_routes(&json!(source)).unwrap(),
            "(\n    (\"patch\", \"/api-v1/parents/{parent_id}/children/{child_id}\"),\n    (\"get\", \"/api-v1/parents\"),\n)");
    }

    #[test]
    fn route_literals_reject_source_and_invalid_shapes() {
        let values = [json!(null), json!([]), json!({}), json!(""), json!("[]"),
            json!("{}"), json!("not JSON"), json!(" ".repeat(8193)),
            json!(json!([{"method": "post", "path": "/x", "code": "print(1)"}]).to_string()),
            json!(json!([{"method": "exec", "path": "/x"}]).to_string()),
            json!(json!([{"method": "get", "path": 17}]).to_string()),
            json!(json!([{"method": "get", "path": "/x/{arbitrary}"}]).to_string()),
            json!(json!([{"method": "get", "path": "/../x"}]).to_string()),
            json!(json!([{"method": "get", "path": "/x//y"}]).to_string()),
            json!(json!([{"method": "get", "path": "/x\nprint(1)"}]).to_string()),
            json!(json!([{"method": "get", "path": format!("/{}", "x".repeat(200))}]).to_string()),
            json!(json!(vec![json!({"method": "get", "path": "/x"}); 65]).to_string()),
        ];
        for value in values {
            assert_eq!(compile_routes(&value).unwrap_err().code, "INVALID_VALUE", "{value}");
        }
    }
}

/// Omit unavailable SDK work without changing any selected flow operation.
pub fn compile_engine(value: &Value) -> Result<String, SomError> {
    let program = compile_program(value)?;
    let template = if value["prompt"]["capabilities"]["storage"] == "none" {
        let start = ENGINE_TEXT.find("    async def external_inverse(")
            .ok_or_else(|| SomError::new("INVALID_BLOCK", "missing fixed inverse boundary"))?;
        let end = ENGINE_TEXT[start..].find("    async def reverse(")
            .map(|offset| offset + start)
            .ok_or_else(|| SomError::new("INVALID_BLOCK", "missing fixed reverse boundary"))?;
        format!("{}{}{}", &ENGINE_TEXT[..start],
            "    async def external_inverse(self, _effect: Effect) -> None:\n        \"\"\"Preserve an invalid external journal without guessing an inverse.\"\"\"\n        raise failure(503, \"invalid external journal\")\n\n\n",
            &ENGINE_TEXT[end..])
    } else { ENGINE_TEXT.to_owned() };
    Ok(source_names(&template.replace("{{flow_program}}", &program)))
}

pub fn resolve(prompt: &Value, dsl: &Value) -> Result<Value, SomError> {
    // Catalog checks see base ids; the lowered files keep the selected self variant ids.
    flow::validate_plan_with(
        prompt,
        &crate::python_names::base_plan(prompt, dsl)?,
        &flow::COMPACT_CATALOG,
    )?;
    let mut values = composed::values(&flow::base(prompt))?;
    values.insert("flow.inputs".into(), prompt["flow"]["inputs"].clone());
    values.insert("flow.name".into(), prompt["flow"]["name"].clone());
    let catalog_hash = format!(
        "{:x}",
        Sha256::digest(flow::COMPACT_CATALOG_TEXT.as_bytes())
    );
    let hash_input =
        flow::canonical(&json!({"prompt": prompt, "dsl": dsl, "catalog_sha256": catalog_hash}));
    values.insert(
        "flow.hash".into(),
        json!(format!("{:x}", Sha256::digest(hash_input.as_bytes()))),
    );
    values.insert(
        "flow.program".into(),
        json!({"prompt": prompt, "plan": dsl["flow"]}),
    );
    let fields: Vec<String> = prompt["resources"]["child"]["fields"]
        .as_array()
        .unwrap()
        .iter()
        .map(|field| {
            format!(
                "{}:{}",
                flow::canonical(&field["name"]),
                flow::canonical(&field["type"])
            )
        })
        .collect();
    values.insert(
        "flow.custom_fields".into(),
        json!(format!("{{{}}}", fields.join(","))),
    );
    values.insert(
        "flow.versioned".into(),
        json!(
            flow::selected_ids(&dsl["flow"])
                .iter()
                .any(|id| id.ends_with(".object.put"))
        ),
    );
    let mut result = flow::file_symbols(dsl)?;
    let placeholder = regex::Regex::new(r"\{([A-Za-z_.]+)\}").unwrap();
    for file in result["files"].as_array_mut().unwrap() {
        let mut failure = None;
        let path = placeholder
            .replace_all(
                file["path"].as_str().unwrap(),
                |capture: &regex::Captures<'_>| match values
                    .get(&capture[1])
                    .and_then(Value::as_str)
                {
                    Some(value) => value.to_owned(),
                    None => {
                        failure = Some(capture[1].to_owned());
                        String::new()
                    }
                },
            )
            .into_owned();
        if failure.is_some() {
            return Err(SomError::new("INVALID_REF", "unknown compact path binding"));
        }
        file["path"] = json!(path);
        for block in file["blocks"].as_array_mut().unwrap() {
            let id = block["id"].clone();
            for reference in block["params"].as_object_mut().unwrap().values_mut() {
                let binding = reference["$ref"]
                    .as_str()
                    .ok_or_else(|| SomError::new("INVALID_REF", "typed binding required"))?;
                *reference = if binding == "block.id" {
                    id.clone()
                } else {
                    values
                        .get(binding)
                        .ok_or_else(|| SomError::new("INVALID_REF", "unknown compact binding"))?
                        .clone()
                };
            }
        }
    }
    Ok(result)
}

pub fn assemble(
    prompt: &Value,
    dsl: &Value,
    blocks: &BTreeMap<String, Block>,
    quality: &Quality,
) -> Result<BTreeMap<String, String>, SomError> {
    composed::verify_blocks_with(blocks, &flow::COMPACT_CATALOG)?;
    flow_codec::features_with(prompt, &flow::COMPACT_CATALOG)?;
    flow_codec::encode_with(
        prompt,
        &crate::python_names::base_plan(prompt, dsl)?,
        &flow::COMPACT_CATALOG,
    )?;
    let lowered = resolve(prompt, dsl)?;
    quality.finalize_with_byte_limit(render_resolved(&lowered, blocks)?, None)
}

/// Render an already checked, safely bound compact plan before formatting.
pub fn render_resolved(
    lowered: &Value,
    blocks: &BTreeMap<String, Block>,
) -> Result<BTreeMap<String, String>, SomError> {
    let lowered_files = lowered["files"].as_array()
        .ok_or_else(|| SomError::new("INVALID_DSL", "compact files must be a list"))?;
    let mut files = BTreeMap::new();
    for file in lowered_files {
        files.extend(crate::assemble_with_byte_limit(
            &json!({"version": "som.dsl.v2", "files": [file]}),
            blocks,
            None,
        )?);
    }
    Ok(files)
}

fn quoted(value: &Value) -> String {
    if let Some(text) = value.as_str() {
        if text.chars().any(|character| literal_confusables().contains(&character)) {
            let encoded = literal_fragment(text);
            if encoded.chars().count() <= 60 {
                return format!("\"{encoded}\"");
            }
            let mut pieces = Vec::new();
            let mut current = String::new();
            for character in text.chars() {
                let fragment = literal_fragment(&character.to_string());
                if current.chars().count() + fragment.chars().count() > 40 {
                    pieces.push(format!("\"{current}\""));
                    current.clear();
                }
                current.push_str(&fragment);
            }
            if !current.is_empty() { pieces.push(format!("\"{current}\"")); }
            return format!("(\n{}\n)", pieces.join("\n"));
        }
        let chars: Vec<char> = text.chars().collect();
        if chars.len() > 60 {
            let pieces: Vec<String> = chars
                .chunks(40)
                .map(|piece| flow::canonical(&json!(piece.iter().collect::<String>())))
                .collect();
            return format!("(\n{}\n)", pieces.join("\n"));
        }
    }
    flow::canonical(value)
}

fn literal_confusables() -> &'static BTreeSet<char> {
    static CHARACTERS: std::sync::OnceLock<BTreeSet<char>> = std::sync::OnceLock::new();
    CHARACTERS.get_or_init(|| {
        let policy: Value = serde_json::from_str(include_str!("../contracts/python-literal-encoding.v1.json"))
            .expect("compiled literal encoding contract must be valid JSON");
        assert_eq!(policy["version"], "som.python-literal-encoding.v1");
        assert_eq!(policy["ruff"], "ruff 0.16.6");
        assert_eq!(policy["preview"], false);
        policy["codepoints"].as_array().unwrap().iter()
            .map(|point| char::from_u32(point.as_u64().unwrap() as u32).unwrap()).collect()
    })
}

fn literal_fragment(text: &str) -> String {
    let encoded = flow::canonical(&json!(text));
    let inner = &encoded[1..encoded.len() - 1];
    inner.chars().map(|character| {
        if literal_confusables().contains(&character) {
            if character as u32 <= 0xffff { format!("\\u{:04x}", character as u32) }
            else { format!("\\U{:08x}", character as u32) }
        } else { character.to_string() }
    }).collect()
}

#[cfg(test)]
mod literal_encoding_tests {
    use super::*;

    #[test]
    fn confusables_use_python_escapes_without_normalizing_values() {
        assert_eq!(quoted(&json!("型別：保留引用與順序")), "\"型別\\uff1a保留引用與順序\"");
        assert_eq!(quoted(&json!("aΑa")), "\"a\\u0391a\"");
        assert_eq!(quoted(&json!("a\u{1d400}a")), "\"a\\U0001d400a\"");
        assert_eq!(quoted(&json!("合法中文字")), "\"合法中文字\"");
    }

    #[test]
    fn escaped_literal_chunks_remain_short_and_ascii_outputs_unchanged() {
        let source = quoted(&json!("：".repeat(80)));
        assert!(source.starts_with("(\n") && source.ends_with("\n)"));
        assert!(source.lines().all(|line| line.len() <= 42));
        assert_eq!(quoted(&json!("title")), "\"title\"");
        assert_eq!(quoted(&json!("x".repeat(61))),
            format!("(\n\"{}\"\n\"{}\"\n)", "x".repeat(40), "x".repeat(21)));
    }
}

struct Compiler<'a> {
    catalog: &'a Value,
    prompt: &'a Value,
    lines: Vec<String>,
    variables: BTreeMap<String, String>,
    kinds: BTreeMap<String, String>,
    inputs: BTreeMap<String, String>,
    input_positions: BTreeMap<String, usize>,
    sequence: usize,
}

// Python keeps an integer JSON spelling as an int, even for a typed float.
// Compare that integer exactly before considering an f64 fractional part.
fn compare_integer_float(integer: i128, float: f64) -> Option<std::cmp::Ordering> {
    use std::cmp::Ordering;
    if !float.is_finite() { return None; }
    // JSON integer spellings are bounded by i64::MIN and u64::MAX. These
    // powers of two are exact f64 bounds; the cast below cannot saturate.
    if float < -9_223_372_036_854_775_808.0 { return Some(Ordering::Greater); }
    if float >= 18_446_744_073_709_551_616.0 { return Some(Ordering::Less); }
    let whole = float.trunc() as i128;
    let ordering = integer.cmp(&whole);
    if ordering != Ordering::Equal { return Some(ordering); }
    Some(float.fract().partial_cmp(&0.0)?.reverse())
}

fn compare_literal_numbers(left: &serde_json::Number, right: &serde_json::Number)
    -> Option<std::cmp::Ordering>
{
    let integer = |number: &serde_json::Number| number.as_i64().map(i128::from)
        .or_else(|| number.as_u64().map(i128::from));
    match (integer(left), integer(right)) {
        (Some(a), Some(b)) => Some(a.cmp(&b)),
        (Some(a), None) => compare_integer_float(a, right.as_f64()?),
        (None, Some(b)) => compare_integer_float(b, left.as_f64()?).map(std::cmp::Ordering::reverse),
        (None, None) => left.as_f64()?.partial_cmp(&right.as_f64()?),
    }
}

/// Fold only literal references. Admission remains the existing plan validator.
fn constant_predicate(condition: &Value) -> Option<bool> {
    use std::cmp::Ordering;
    let left = condition.get("left")?.get("literal")?;
    let op = condition.get("op")?.as_str()?;
    if op == "is_null" { return Some(left.is_null()); }
    let right = condition.get("right")?.get("literal")?;
    if condition["left"]["type"] != condition["right"]["type"] { return None; }
    let ordering = match (left, right) {
        (Value::Null, Value::Null) if ["eq", "ne"].contains(&op) => Ordering::Equal,
        (Value::Null, _) | (_, Value::Null) if ["eq", "ne"].contains(&op) =>
            return Some(op == "ne"),
        (Value::Bool(a), Value::Bool(b)) if ["eq", "ne"].contains(&op) => a.cmp(b),
        (Value::Number(a), Value::Number(b)) => compare_literal_numbers(a, b)?,
        // UTF-8 lexicographic order matches Python Unicode code point order.
        // Valid ISO date strings have the same order as their date objects.
        (Value::String(a), Value::String(b))
            if ["string", "date"].contains(&condition["left"]["type"].as_str()?) => a.cmp(b),
        _ => return None,
    };
    match op {
        "eq" => Some(ordering == Ordering::Equal),
        "ne" => Some(ordering != Ordering::Equal),
        "lt" => Some(ordering == Ordering::Less),
        "le" => Some(ordering != Ordering::Greater),
        "gt" => Some(ordering == Ordering::Greater),
        "ge" => Some(ordering != Ordering::Less),
        _ => None,
    }
}

#[cfg(test)]
mod constant_predicate_tests {
    use super::constant_predicate;
    use serde_json::{Value, json};

    fn reference(source: &str, kind: &str) -> Value {
        json!({"literal": serde_json::from_str::<Value>(source).unwrap(), "type":kind})
    }

    #[test]
    fn exact_python_number_ordering_for_all_six_operators() {
        let cases = [
            ("1", "1", 0), ("-2147483648", "2147483647", -1),
            ("9007199254740993", "9007199254740992.0", 1),
            ("9007199254740993", "9007199254740994.0", -1),
            ("18446744073709551615", "18446744073709551616.0", -1),
            ("18446744073709551615", "18446744073709549568.0", 1),
            ("-9223372036854775808", "-9223372036854775808.0", 0),
            ("-9223372036854775807", "-9223372036854775808.0", 1),
            ("0", "-0.0", 0), ("-0.0", "0.0", 0),
            ("0", "5e-324", -1), ("0", "-5e-324", 1),
            ("-2", "-2.5", 1), ("2", "2.5", -1),
            ("0", "1e308", -1), ("0", "-1e308", 1),
            ("1.5", "2.5", -1), ("1", "1.0", 0),
        ];
        for (a, b, order) in cases {
            for (left, right, order) in [(a, b, order), (b, a, -order)] {
                for (op, expected) in [("eq", order == 0), ("ne", order != 0),
                    ("lt", order < 0), ("le", order <= 0), ("gt", order > 0), ("ge", order >= 0)] {
                    let condition = json!({"op":op,"left":reference(left,"float"),"right":reference(right,"float")});
                    assert_eq!(constant_predicate(&condition), Some(expected), "{left} {op} {right}");
                }
            }
        }
    }

    #[test]
    fn boolean_unicode_date_and_null_values_match_python() {
        for (kind, a, b, equal) in [("boolean", "true", "false", false),
            ("boolean", "true", "true", true), ("string", "\"é\"", "\"é\"", false),
            ("string", "\"中\"", "\"中\"", true), ("date", "\"2024-02-29\"", "\"2024-02-29\"", true),
            ("string?", "null", "null", true), ("string?", "null", "\"x\"", false)] {
            for (op, expected) in [("eq", equal), ("ne", !equal)] {
                assert_eq!(constant_predicate(&json!({"op":op,"left":reference(a,kind),"right":reference(b,kind)})), Some(expected));
            }
        }
        for (kind, a, b) in [("string", "\"é\"", "\"中\""),
            ("string", "\"😀\"", "\"😁\""), ("date", "\"2024-02-29\"", "\"2024-03-01\"")] {
            assert_eq!(constant_predicate(&json!({"op":"lt","left":reference(a,kind),"right":reference(b,kind)})), Some(true));
        }
        assert_eq!(constant_predicate(&json!({"op":"is_null","left":reference("null","string?")})), Some(true));
        assert_eq!(constant_predicate(&json!({"op":"is_null","left":reference("0","integer")})), Some(false));
    }

    #[test]
    fn references_and_unsupported_comparisons_are_not_folded() {
        let values = [
            json!({"op":"eq","left":{"input":"a"},"right":reference("1","integer")}),
            json!({"op":"eq","left":reference("1","integer"),"right":{"step":"made","field":"id"}}),
            json!({"op":"is_null","left":{"input":"optional"}}),
            json!({"op":"eq","left":reference("true","boolean"),"right":reference("1","integer")}),
            json!({"op":"lt","left":reference("null","string?"),"right":reference("null","string?")}),
            json!({"op":"lt","left":reference("false","boolean"),"right":reference("true","boolean")}),
        ];
        for value in values { assert_eq!(constant_predicate(&value), None, "{value}"); }
    }
}

impl Compiler<'_> {
    fn expression(&self, reference: &Value) -> String {
        if let Some(input) = reference.get("input") {
            return format!("ctx.inputs.input_{}", self.input_positions[input.as_str().unwrap()]);
        }
        if let Some(value) = reference.get("literal") {
            if reference["type"] == "date" {
                return format!("date.fromisoformat({})", quoted(value));
            }
            return match value {
                Value::Bool(true) => "True".into(),
                Value::Bool(false) => "False".into(),
                _ => quoted(value),
            };
        }
        let mut expression = self.variables[reference["step"].as_str().unwrap()].clone();
        let mut kind = self.kinds[reference["step"].as_str().unwrap()].clone();
        let field = reference["field"].as_str().unwrap();
        if !field.is_empty() {
            for item in field.split('.').filter(|part| !part.is_empty()) {
                expression = if kind == "identity" {
                    format!("{expression}[{}]", quoted(&json!(item)))
                } else if ["parent_row", "child_row"].contains(&kind.as_str())
                    && ["created_at", "updated_at"].contains(&item) {
                    format!("text_value(model_json({expression})[{}])", quoted(&json!(item)))
                } else {
                    format!("{expression}.{item}")
                };
                kind = flow::member(self.prompt, &kind, item).expect("checked member");
            }
        }
        expression
    }

    fn data_expression(&self, reference: &Value) -> String {
        let value = self.expression(reference);
        let kind = flow::ref_type(self.prompt, reference, &self.inputs, &self.kinds).expect("checked data reference");
        if ["parent_input", "child_input", "parent_patch", "child_patch"].contains(&kind.as_str()) {
            return format!("model_json({value}, True)");
        }
        format!("json_object({value})")
    }

    fn children_expression(&self, reference: &Value) -> String {
        let value = self.expression(reference);
        let kind = flow::ref_type(self.prompt, reference, &self.inputs, &self.kinds).expect("checked children reference");
        if kind == "children_input" {
            value
        } else { format!("TypeAdapter(list[ChildInputData]).validate_python({value})") }
    }

    fn parent_expression(&self, reference: &Value) -> String {
        let value = self.expression(reference);
        let kind = flow::ref_type(self.prompt, reference, &self.inputs, &self.kinds).expect("checked parent reference");
        if kind == "parent_input" { value }
        else { format!("ParentInputData.model_validate({value})") }
    }

    fn emit(&mut self, text: String, depth: usize) {
        self.lines.push(format!("{}{text}", "    ".repeat(depth)));
    }

    fn walk(&mut self, nodes: &Value, depth: usize, group: &str) {
        for node in nodes.as_array().unwrap() {
            let identity = node["id"].as_str().unwrap();
            let name = node["name"].as_str().unwrap();
            let operation = self.catalog[identity]["operation"].as_str().unwrap();
            if operation == "transaction" {
                self.emit("async with ctx.transaction():".into(), depth);
                self.walk(&node["steps"], depth + 1, name);
                continue;
            }
            if operation == "if" {
                let condition = &node["condition"];
                let left = self.expression(&condition["left"]);
                let op = condition["op"].as_str().unwrap();
                let predicate = if let Some(value) = constant_predicate(condition) {
                    if value { "True".into() } else { "False".into() }
                } else { match op {
                    "is_null" => format!("{left} is None"),
                    "eq" | "ne" => {
                        let singleton = condition["right"]["literal"].is_boolean();
                        let operator = match (op, singleton) {
                            ("eq", true) => " is ",
                            ("ne", true) => " is not ",
                            ("eq", false) => " == ",
                            _ => " != ",
                        };
                        format!("{left}{operator}{}", self.expression(&condition["right"]))
                    }
                    _ => {
                        let operator = match op { "lt" => " < ", "le" => " <= ", "gt" => " > ", _ => " >= " };
                        format!("{left}{operator}{}", self.expression(&condition["right"]))
                    },
                }};
                let branch = format!("_branch{}", self.sequence);
                self.sequence += 1;
                self.emit(format!("{branch} = {predicate}"), depth);
                self.emit(
                    format!("ctx.record.branches[{}] = {branch}", quoted(&node["name"])),
                    depth,
                );
                if group.is_empty() {
                    self.emit("await ctx.persist()".into(), depth);
                }
                let previous = self.variables.clone();
                let previous_kinds = self.kinds.clone();
                let mut export_kinds = BTreeMap::new();
                let exports: BTreeMap<String, String> = node["exports"]
                    .as_object()
                    .unwrap()
                    .keys()
                    .enumerate()
                    .map(|(index, name)| (name.clone(), format!("_value{}", self.sequence + index)))
                    .collect();
                self.sequence += exports.len();
                // Empty arms with one export only select a value. A conditional
                // expression keeps that selection lazy and satisfies SIM108.
                let conditional_export = exports.len() == 1
                    && ["then", "else"].iter().all(|arm| node[*arm].as_array().unwrap().is_empty());
                let mut conditional_values = Vec::new();
                for arm in ["then", "else"] {
                    self.variables = previous.clone();
                    self.kinds = previous_kinds.clone();
                    if arm == "else" && op == "is_null" {
                        let kind = flow::ref_type(self.prompt, &condition["left"], &self.inputs, &self.kinds).expect("checked null refinement");
                        self.kinds.insert(format!("@{}", flow::canonical(&condition["left"])), kind.trim_end_matches('?').to_owned());
                    }
                    if !conditional_export {
                        self.emit(
                            if arm == "then" {
                                format!("if {}:", if op == "is_null" { &predicate } else { &branch })
                            } else {
                                "else:".into()
                            },
                            depth,
                        );
                    }
                    let begin = self.lines.len();
                    self.walk(&node[arm], depth + 1, group);
                    for (name, variable) in &exports {
                        let value = self.expression(&node["exports"][name][arm]);
                        if conditional_export {
                            conditional_values.push(value);
                        } else {
                            self.emit(format!("{variable} = {value}"), depth + 1);
                        }
                        export_kinds.insert(name.clone(), flow::ref_type(self.prompt, &node["exports"][name][arm], &self.inputs, &self.kinds).expect("checked export type"));
                    }
                    if !conditional_export && self.lines.len() == begin {
                        self.emit("pass".into(), depth + 1);
                    }
                }
                if conditional_export {
                    let variable = exports.values().next().unwrap();
                    let test = if op == "is_null" { &predicate } else { &branch };
                    self.emit(
                        format!("{variable} = {} if {test} else {}", conditional_values[0], conditional_values[1]),
                        depth,
                    );
                }
                self.variables = previous;
                self.variables.extend(exports.clone());
                self.kinds = previous_kinds;
                self.kinds.extend(export_kinds);
                for (name, variable) in exports {
                    self.emit(
                        format!("ctx.record.values[{}] = result_json({variable})", quoted(&json!(name))),
                        depth,
                    );
                }
                continue;
            }
            let variable = format!("_value{}", self.sequence);
            self.sequence += 1;
            let arguments = &node["args"];
            let effect = if node["undo"].is_null() {
                "None".into()
            } else {
                let mut args = vec![format!("step={}", quoted(&node["name"]))];
                if !group.is_empty() { args.push(format!("group={}", quoted(&json!(group)))); }
                let inverse = node["undo"].as_str().unwrap().rsplit('.').next().unwrap();
                args.push(format!("undo={}", quoted(&json!(inverse))));
                format!("Effect({})", args.join(", "))
            };
            let call = match operation {
                "authorize" => "{\"sub\": ctx.record.owner, \"role\": \"editor\"}".into(),
                "workflow.run" => format!(
                    "await ctx.export({})",
                    self.expression(&arguments["parent_id"])
                ),
                "workflow.import" => format!(
                    "await ctx.batch({effect}, {}, csv_records({}))",
                    self.expression(&arguments["parent_id"]),
                    self.expression(&arguments["csv"])
                ),
                "batch.create" => format!(
                    "await ctx.batch_create({effect}, {}, {})",
                    self.parent_expression(&arguments["parent"]),
                    self.children_expression(&arguments["children"])
                ),
                "object.get" => format!(
                    "await run_in_threadpool(flow_object_get, {})",
                    self.expression(&arguments["key"])
                ),
                "object.put" => format!(
                    "await ctx.put({effect}, {}, {})",
                    self.expression(&arguments["key"]),
                    self.expression(&arguments["body"])
                ),
                _ => {
                    let (role, method) = operation.split_once('.').unwrap();
                    let mut args = vec![
                        format!("role={}", quoted(&json!(role))),
                        format!("operation={}", quoted(&json!(method))),
                    ];
                    for key in ["parent_id", "child_id"] {
                        if let Some(reference) = arguments.get(key) {
                            let destination = if key == format!("{role}_id")
                                && ["get", "patch"].contains(&method)
                            {
                                "row_id"
                            } else {
                                "parent_id"
                            };
                            args.push(format!(
                                "{destination}={}",
                                self.expression(reference)
                            ));
                        }
                    }
                    if let Some(reference) = arguments.get("data") {
                        args.push(format!("data={}", self.data_expression(reference)));
                    }
                    format!(
                        "await ctx.resource(CrudRequest({}), {effect})",
                        args.join(", ")
                    )
                }
            };
            let kind = flow::output_type(operation);
            if operation == "authorize" {
                self.emit(format!("{variable}: Identity = {call}"), depth);
                self.emit(format!("ctx.record.values[{}] = json_value({variable})", quoted(&node["name"])), depth);
                self.emit("await ctx.persist()".into(), depth);
            } else if operation == "object.put" {
                self.emit(format!("{variable} = {call}"), depth);
            } else {
                let checker = if kind.ends_with("_rows") {
                    format!("TypeAdapter({}).validate_python", annotation(kind))
                } else {
                    format!("{}.model_validate", annotation(kind))
                };
                let result = if operation.starts_with("parent.") || operation.starts_with("child.") {
                    format!("{checker}({call})")
                } else { call };
                let checkpoint = if group.is_empty() { "" } else { ", False" };
                self.emit(format!("{variable} = await ctx.save_result({}, {result}{checkpoint})", quoted(&node["name"])), depth);
            }
            if identity.ends_with(".checked") {
                self.emit(format!("integer({variable}.id)"), depth);
            }
            self.variables.insert(name.into(), variable.clone());
            self.kinds.insert(name.into(), kind.into());
        }
    }
}

fn annotation(kind: &str) -> String {
    let nullable = kind.ends_with('?');
    let kind = kind.trim_end_matches('?');
    let base = match kind {
        "string" | "bytes" => "str",
        "integer" => "int",
        "float" => "float",
        "boolean" => "bool",
        "date" => "date",
        "json" => "JsonValue",
        "identity" => "Identity",
        "object" => "FlowObject",
        "workflow" => "WorkflowReceipt",
        "batch" => "BatchRead",
        "parent_row" => "ParentRow",
        "child_row" => "ChildRow",
        "parent_rows" => "list[ParentRow]",
        "child_rows" => "list[ChildRow]",
        "parent_input" => "ParentInputData",
        "child_input" => "ChildInputData",
        "parent_patch" => "ParentPatchData",
        "child_patch" => "ChildPatchData",
        "children_input" => "list[ChildInputData]",
        _ => unreachable!("validated result type"),
    };
    format!("{base}{}", if nullable { " | None" } else { "" })
}

pub fn compile_program(value: &Value) -> Result<String, SomError> {
    crate::object(value, &["prompt", "plan"], "compact program").map_err(|_| {
        SomError::new(
            "INVALID_REF",
            "compact program requires a typed prompt and plan",
        )
    })?;
    let prompt = &value["prompt"];
    let tree = &value["plan"];
    let catalog = &flow::CATALOG["flow_blocks"];
    flow::validate_tree(prompt, tree, catalog)?;
    let mut compiler = Compiler {
        catalog,
        prompt,
        lines: vec![
            "async def forward(ctx: FlowContext) -> JsonObject:".into(),
            "    \"\"\"Run the selected forward blocks once.\"\"\"".into(),
        ],
        variables: BTreeMap::new(),
        kinds: BTreeMap::new(),
        inputs: prompt["flow"]["inputs"].as_array().unwrap().iter().map(|item| (item["name"].as_str().unwrap().into(), item["type"].as_str().unwrap().into())).collect(),
        input_positions: prompt["flow"]["inputs"].as_array().unwrap().iter().enumerate().map(|(index, item)| (item["name"].as_str().unwrap().into(), index)).collect(),
        sequence: 0,
    };
    compiler.walk(&tree["steps"], 1, "");
    let results: Vec<String> = tree["result"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(key, reference)| {
            format!(
                "{}: {}",
                quoted(&json!(key)),
                compiler.expression(reference)
            )
        })
        .collect();
    compiler.emit(
        format!(
            "output = FlowResult.model_validate({{{}}})",
            results.join(", ")
        ),
        1,
    );
    compiler.emit(
        "return model_json(output)".into(),
        1,
    );
    let matches: Vec<_> = regex::Regex::new(r"(?m)^    async def (\w+)\(")
        .unwrap()
        .captures_iter(SUPPORT_TEXT)
        .map(|capture| (capture.get(0).unwrap().start(), capture[1].to_owned()))
        .collect();
    let mut methods = BTreeMap::new();
    for (index, (start, name)) in matches.iter().enumerate() {
        let end = matches
            .get(index + 1)
            .map(|(start, _)| *start)
            .unwrap_or(SUPPORT_TEXT.len());
        let body = SUPPORT_TEXT[*start..end]
            .strip_prefix("    ")
            .unwrap()
            .replace("\n    ", "\n");
        methods.insert(
            name.clone(),
            body.replace(
                &format!("async def {name}(self,"),
                &format!("async def flow_{name}(ctx: FlowContext,"),
            )
            .replace("self.", "ctx.")
            .trim_end()
            .to_owned(),
        );
    }
    let selected: BTreeSet<_> = flow::selected_ids(tree)
        .iter()
        .map(|id| catalog[id]["operation"].as_str().unwrap().to_owned())
        .collect();
    let mut used = BTreeSet::new();
    if selected.contains("workflow.run") {
        used.insert("export");
    }
    if selected.contains("workflow.import") || selected.contains("batch.create") {
        used.insert("batch");
    }
    if selected.contains("batch.create") {
        used.insert("batch_create");
    }
    if selected.contains("object.put") {
        used.insert("put");
    }
    let types = flow::result_types(prompt)?;
    let mut supports = Vec::new();
    let fields: Vec<String> = types
        .iter()
        .enumerate()
        .map(|(index, (name, kind))| {
            format!(
                "    result_{index}: {} = Field(alias={})",
                annotation(kind),
                quoted(&json!(name))
            )
        })
        .collect();
    supports.push(format!("class FlowResult(BaseModel):\n    \"\"\"Validate the declared results at their JSON boundary.\"\"\"\n    model_config = ConfigDict(extra=\"forbid\", strict=True, allow_inf_nan=False)\n{}", fields.join("\n")));
    for name in &used {
        supports.push(rewrite_calls(&methods[*name], &used));
    }
    let result = rewrite_calls(&compiler.lines.join("\n"), &used);
    supports.push(result);
    Ok(source_names(&format!("{}\n", supports.join("\n\n\n"))))
}

/// Lower fixed calls without changing user data in strings or comments.
fn rewrite_calls(source: &str, used: &BTreeSet<&str>) -> String {
    let replacements: Vec<(Vec<char>, String)> = used.iter().map(|name| {
        (format!("ctx.{name}(").chars().collect(), format!("flow_{name}(ctx, "))
    }).collect();
    let chars: Vec<char> = source.chars().collect();
    let mut result = String::new();
    let mut index = 0;
    while index < chars.len() {
        let start = index;
        let ch = chars[index];
        if ch == '#' {
            while index < chars.len() && chars[index] != '\n' { index += 1; }
        } else if ch == '\'' || ch == '"' {
            let size = if chars.get(index..index + 3).is_some_and(|part| part == [ch, ch, ch]) { 3 } else { 1 };
            index += size;
            while index < chars.len() {
                if chars[index] == '\\' { index = (index + 2).min(chars.len()); }
                else if chars.get(index..index + size).is_some_and(|part| part.iter().all(|item| *item == ch)) { index += size; break; }
                else { index += 1; }
            }
        } else {
            let boundary = index == 0 || !(chars[index - 1].is_alphanumeric() || "_.".contains(chars[index - 1]));
            if boundary {
                if let Some((pattern, replacement)) = replacements.iter().find(|(pattern, _)| chars.get(index..index + pattern.len()).is_some_and(|part| part == pattern)) {
                    result.push_str(replacement);
                    index += pattern.len();
                    continue;
                }
            }
            index += 1;
        }
        result.extend(&chars[start..index]);
    }
    result
}

/// Replace private identifiers outside literals and comments, before binding.
pub fn source_names(source: &str) -> String {
    let names: BTreeMap<&str, &str> = BTreeMap::from([
        ("BoundedInput", "_Bounded"),
        ("PartialInput", "_Partial"),
        ("ManagedRead", "_ManagedRead"),
        ("BatchCreate", "_BatchCreate"),
        ("BatchRead", "_BatchRead"),
        ("FlowStore", "_Store"),
        ("FlowContext", "_Context"),
        ("RunRecord", "_Run"),
        ("CrudRequest", "_RequestData"),
        ("ResourceSpec", "_Resource"),
        ("JsonObject", "_JsonMap"),
        ("JsonValue", "_Json"),
        ("ParentInput", "_ParentInput"),
        ("ParentChanges", "_ParentChanges"),
        ("ParentView", "_ParentView"),
        ("ChildInput", "_ChildInput"),
        ("ChildChanges", "_ChildChanges"),
        ("ChildView", "_ChildView"),
        ("flow_object_get", "object_get"),
        ("flow_object_put", "object_put"),
        ("flow_object_reverse", "object_reverse"),
        ("flow_object_version", "object_version"),
    ]);
    let chars: Vec<char> = source.chars().collect();
    let mut result = String::new();
    let mut index = 0;
    while index < chars.len() {
        let start = index;
        let ch = chars[index];
        if ch == '#' {
            while index < chars.len() && chars[index] != '\n' {
                index += 1;
            }
            if index < chars.len() {
                index += 1;
            }
        } else if ch == '\'' || ch == '"' {
            let width = if chars.get(index..index + 3) == Some(&[ch, ch, ch]) {
                3
            } else {
                1
            };
            index += width;
            while index < chars.len() {
                if chars[index] == '\\' {
                    index += 2;
                } else if chars
                    .get(index..index + width)
                    .is_some_and(|part| part.iter().all(|item| *item == ch))
                {
                    index += width;
                    break;
                } else {
                    index += 1;
                }
            }
        } else if ch.is_ascii_alphabetic() || ch == '_' {
            index += 1;
            while index < chars.len()
                && (chars[index].is_ascii_alphanumeric() || chars[index] == '_')
            {
                index += 1;
            }
            let name: String = chars[start..index].iter().collect();
            result.push_str(names.get(name.as_str()).unwrap_or(&name.as_str()));
            continue;
        } else {
            index += 1;
        }
        result.extend(chars[start..index.min(chars.len())].iter());
    }
    result
}

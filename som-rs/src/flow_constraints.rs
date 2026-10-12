//! Prefix contracts for lossless flow tokens. No labels or planner are read.

use crate::{SomError, flow, flow_codec, ident};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub const VERSION: &str = "som.flow.constrained-decoding.v1";
pub const POLICY_TEXT: &str = include_str!("../contracts/flow-decode-policy.v1.json");
type Types = BTreeMap<String, String>;

fn invalid(detail: &str) -> SomError { SomError::new("INVALID_DSL", detail) }
fn text(value: &Value) -> &str { value.as_str().unwrap_or("") }
fn safe_name(value: &Value) -> bool {
    ident(value, "flow prefix name").is_ok() && text(value).len() <= 64
        && !text(value).starts_with('_') && !text(value).starts_with("model_")
}
fn keys(kind: &str) -> &'static [&'static str] {
    match kind {
        "root" => &["files", "flow", "version"],
        "file" => &["blocks", "path"],
        "block" => &["id", "name", "params", "refs"],
        "binding" => &["$ref"], "symbol" => &["symbol"],
        "flow" => &["name", "result", "steps"],
        "node" => &["args", "condition", "else", "exports", "id", "name", "steps", "then", "undo"],
        "args" => &["parent_id", "child_id", "data", "parent", "children", "csv", "key", "body"],
        "condition" => &["op", "left", "right"],
        "export" => &["then", "else"],
        "reference" => &["input", "step", "field", "literal", "type"],
        _ => &[],
    }
}

#[derive(Clone)]
struct Frame {
    kind: &'static str,
    path: Vec<String>,
    value: Value,
    key: Option<String>,
}

pub struct State {
    prompt: Value,
    metadata: Value,
    pool: Vec<Value>,
    ids: BTreeMap<String, u32>,
    inputs: Types,
    public_visible: Types,
    public_all: Types,
    stack: Vec<Frame>,
    root: Option<Value>,
    complete: bool,
    tokens: usize,
}

impl State {
    pub fn new(prompt: &Value, metadata: &Value) -> Result<Self, SomError> {
        flow::validate(prompt)?;
        let pool = flow_codec::pool_with(prompt, false, metadata)?;
        let ids = pool.iter().enumerate().map(|(index, value)|
            (flow::canonical(value), index as u32 + 8)).collect();
        let inputs = prompt["flow"]["inputs"].as_array().unwrap().iter().map(|item|
            (text(&item["name"]).to_owned(), text(&item["type"]).to_owned())).collect();
        let mut result = Self { prompt: prompt.clone(), metadata: metadata.clone(), pool, ids,
            inputs, public_visible: Types::new(), public_all: Types::new(),
            stack: Vec::new(), root: None, complete: false, tokens: 0 };
        result.public_visible = result.walk_types(&prompt["flow"]["steps"], &Types::new(), true)?;
        result.public_all = result.public_visible.clone();
        fn collect(nodes: &Value, output: &mut Types, pending: &mut BTreeMap<String, Value>) {
            for node in nodes.as_array().into_iter().flatten() {
                let op = text(&node["op"]);
                if !["transaction", "if"].contains(&op) {
                    output.insert(text(&node["name"]).into(), flow::output_type(op).into());
                }
                if let Some(exports) = node["exports"].as_object() {
                    pending.extend(exports.iter().map(|(name, value)| (name.clone(), value.clone())));
                }
                for key in ["steps", "then", "else"] { collect(&node[key], output, pending); }
            }
        }
        let mut pending = BTreeMap::new();
        collect(&prompt["flow"]["steps"], &mut result.public_all, &mut pending);
        for _ in 0..32 {
            if pending.is_empty() { break; }
            for (name, binding) in pending.clone() {
                if let Ok(kind) = flow::ref_type(prompt, &binding["then"], &result.inputs, &result.public_all) {
                    result.public_all.insert(name.clone(), kind); pending.remove(&name);
                }
            }
        }
        Ok(result)
    }

    fn op<'a>(&'a self, node: &'a Value, public: bool) -> &'a str {
        if public { text(&node["op"]) }
        else { text(&self.metadata["flow_blocks"][text(&node["id"])]["operation"]) }
    }

    fn refine(&self, condition: &Value, visible: &mut Types) -> Result<(), SomError> {
        if condition["op"] == "is_null" {
            let kind = flow::ref_type(&self.prompt, &condition["left"], &self.inputs, visible)?;
            visible.insert(format!("@{}", flow::canonical(&condition["left"])),
                           kind.strip_suffix('?').unwrap_or(&kind).into());
        }
        Ok(())
    }

    fn walk_types(&self, nodes: &Value, visible: &Types, public: bool) -> Result<Types, SomError> {
        let mut result = visible.clone();
        for node in nodes.as_array().into_iter().flatten() {
            let op = self.op(node, public);
            if op == "transaction" {
                result = self.walk_types(&node["steps"], &result, public)?;
            } else if op == "if" {
                let then = self.walk_types(&node["then"], &result, public)?;
                let mut refined = result.clone();
                self.refine(&node["condition"], &mut refined)?;
                let otherwise = self.walk_types(&node["else"], &refined, public)?;
                for (name, binding) in node["exports"].as_object().into_iter().flatten() {
                    let left = flow::ref_type(&self.prompt, &binding["then"], &self.inputs, &then)?;
                    let right = flow::ref_type(&self.prompt, &binding["else"], &self.inputs, &otherwise)?;
                    if left != right { return Err(invalid("branch export types differ")); }
                    result.insert(name.clone(), left);
                }
            } else if !op.is_empty() {
                result.insert(text(&node["name"]).into(), flow::output_type(op).into());
            }
        }
        Ok(result)
    }

    fn visible(&self, path: &[String]) -> Result<Types, SomError> {
        if path.first().is_some_and(|s| s == "flow") && path.get(1).is_some_and(|s| s == "result") {
            return Ok(self.public_visible.clone());
        }
        let mut result = Types::new();
        for (index, frame) in self.stack.iter().enumerate() {
            if frame.kind != "nodes" { continue; }
            if frame.path.last().is_some_and(|s| s == "else") {
                if let Some(parent) = self.stack[..index].iter().rev().find(|item| item.kind == "node") {
                    self.refine(&parent.value["condition"], &mut result)?;
                }
            }
            result = self.walk_types(&frame.value, &result, false)?;
        }
        if path.iter().any(|s| s == "exports") {
            if let Some(branch) = self.stack.iter().rev().find(|frame|
                frame.kind == "node" && path.starts_with(&frame.path)) {
                if let Some(arm) = path.last().filter(|s| ["then", "else"].contains(&s.as_str())) {
                    if arm == "else" { self.refine(&branch.value["condition"], &mut result)?; }
                    if branch.value.get(arm).is_some() {
                        result = self.walk_types(&branch.value[arm], &result, false)?;
                    } else { result.extend(self.public_all.clone()); }
                }
            }
        }
        Ok(result)
    }

    fn transaction(&self) -> bool {
        let current = self.stack.iter().rposition(|frame| frame.kind == "node");
        self.stack.iter().enumerate().any(|(index, frame)|
            Some(index) != current && frame.kind == "node" && self.op(&frame.value, false) == "transaction"
            || frame.kind == "nodes" && frame.path.len() > 2 && frame.path.last().is_some_and(|s| s == "steps"))
    }

    fn compatible(&self, meta: &Value) -> bool {
        meta["capabilities"].as_object().is_some_and(|caps|
            caps.iter().all(|(key, value)| self.prompt["capabilities"][key] == *value))
    }

    fn operation_ids(&self, node: Option<&Value>) -> Result<Vec<Value>, SomError> {
        let visible = self.visible(&["flow".into(), "steps".into()])?;
        let authorized = visible.values().any(|kind| kind == "identity");
        let transaction = self.transaction();
        let mut result = Vec::new();
        for (identity, meta) in self.metadata["flow_blocks"].as_object().unwrap() {
            let op = text(&meta["operation"]);
            if op == "undo" || !self.compatible(meta) { continue; }
            if op == "authorize" && (authorized || transaction) || op != "authorize" && !authorized { continue; }
            if op == "transaction" && transaction { continue; }
            if ["parent.create", "child.create", "batch.create", "workflow.import", "parent.patch", "child.patch"].contains(&op) && !transaction { continue; }
            if op.starts_with("object.") && transaction { continue; }
            if op == "workflow.import" && self.prompt["capabilities"]["workflow"] != "csv" { continue; }
            if let Some(args) = node.and_then(|node| node.get("args")).filter(|_| !["if", "transaction"].contains(&op)) {
                let Some(args) = args.as_object() else { continue; };
                let params = meta["params"].as_object().unwrap();
                if args.len() != params.len() || args.keys().any(|key| !params.contains_key(key)) { continue; }
                if params.iter().any(|(key, kind)| flow::ref_type(&self.prompt, &args[key], &self.inputs, &visible).map_or(true, |actual| actual != text(kind))) { continue; }
            }
            result.push(json!(identity));
        }
        Ok(result)
    }

    fn child_kind(kind: &str, key: &str) -> &'static str {
        match (kind, key) {
            ("root", "files") => "files", ("root", "flow") => "flow",
            ("file", "blocks") => "blocks", ("block", "params") => "params",
            ("block", "refs") => "refs", ("params", _) => "binding",
            ("flow", "steps") => "nodes", ("flow", "result") => "result",
            ("args" | "result" | "export", _) => "reference", ("exports", _) => "export",
            ("node", "args") => "args", ("node", "condition") => "condition",
            ("node", "then" | "else" | "steps") => "nodes", ("node", "exports") => "exports",
            ("condition", "left" | "right") => "reference", _ => "scalar",
        }
    }

    fn expected_kind(&self) -> &'static str {
        let Some(frame) = self.stack.last() else { return "root"; };
        if frame.value.is_array() { match frame.kind { "files" => "file", "blocks" => "block", "nodes" => "node", _ => "symbol" } }
        else { Self::child_kind(frame.kind, frame.key.as_deref().unwrap_or("")) }
    }

    fn next_path(&self) -> Vec<String> {
        let Some(frame) = self.stack.last() else { return Vec::new(); };
        let mut result = frame.path.clone();
        result.push(if let Some(array) = frame.value.as_array() { array.len().to_string() }
                    else { frame.key.clone().unwrap_or_default() });
        result
    }

    fn block_names(&self) -> Vec<Value> {
        self.metadata["blocks"].as_object().unwrap().values().map(|meta|
            json!(text(&meta["provides"]).split_once(':').map_or("", |(_, name)| name))).collect()
    }

    fn scalar_domain(&self, frame: &Frame) -> Result<Vec<Value>, SomError> {
        let key = frame.key.as_deref().unwrap_or("");
        let value = &frame.value;
        if value.is_array() { return Ok(self.block_names()); }
        let result = match (frame.kind, key) {
            ("root", "version") => vec![json!(flow::DSL_VERSION)],
            ("file", "path") => self.metadata["slots"].as_object().unwrap().values().cloned().collect(),
            ("block", "id") => self.metadata["blocks"].as_object().unwrap().iter().filter(|(_, meta)| self.compatible(meta)).map(|(name, _)| json!(name)).collect(),
            ("block", "name") if self.metadata["blocks"].get(text(&value["id"])).is_some() =>
                vec![json!(text(&self.metadata["blocks"][text(&value["id"])]["provides"]).split_once(':').map_or("", |(_, name)| name))],
            ("block", "name") => self.block_names(),
            ("binding", _) => self.metadata["blocks"].as_object().unwrap().values().flat_map(|meta|
                meta["bindings"].as_object().unwrap().values().map(|binding| binding["$ref"].clone())).collect(),
            ("symbol", _) => self.stack.iter().filter(|frame| frame.kind == "blocks").flat_map(|frame|
                frame.value.as_array().unwrap().iter().filter_map(|node| node.get("name").cloned())).collect(),
            ("flow", _) => vec![self.prompt["flow"]["name"].clone()],
            ("node", "id") => self.operation_ids(Some(value))?,
            ("node", "undo") => {
                let mut items = vec![Value::Null];
                items.extend(self.metadata["flow_blocks"].as_object().unwrap().iter().filter(|(_, meta)|
                    meta["operation"] == "undo" && self.compatible(meta)).map(|(id, _)| json!(id)));
                items
            }
            ("node", "name") => self.pool.iter().filter(|item| safe_name(item) && !self.inputs.contains_key(text(item))).cloned().collect(),
            ("condition", _) => ["eq", "ne", "lt", "le", "gt", "ge", "is_null"].into_iter().map(|item| json!(item)).collect(),
            ("reference", "input") => self.inputs.keys().map(|item| json!(item)).collect(),
            ("reference", "step") => self.visible(&frame.path)?.keys().filter(|item| !item.starts_with('@')).map(|item| json!(item)).collect(),
            ("reference", "type") => ["string", "integer", "float", "boolean", "date", "bytes"].into_iter().map(|item| json!(item)).collect(),
            ("reference", "field") => {
                let visible = self.visible(&frame.path)?;
                let kinds = if let Some(kind) = visible.get(text(&value["step"])) { vec![kind.clone()] } else { visible.values().cloned().collect() };
                let mut fields: BTreeSet<String> = ["", "id", "created_at", "updated_at", "sub", "role", "key", "size", "sha256", "body", "data", "parent", "children", "parent.id"].into_iter().map(str::to_owned).collect();
                fields.insert(text(&self.prompt["relation"]["foreign_key"]).into());
                for role in ["parent", "child"] { for field in self.prompt["resources"][role]["fields"].as_array().unwrap() {
                    fields.insert(text(&field["name"]).into());
                    if role == "parent" { fields.insert(format!("parent.{}", text(&field["name"]))); }
                }}
                fields.into_iter().filter(|field| kinds.iter().any(|kind|
                    flow::member(&self.prompt, kind, field).is_ok())).map(|item| json!(item)).collect()
            }
            _ => self.pool.clone(),
        };
        Ok(result)
    }

    fn key_domain(&self, frame: &Frame) -> BTreeSet<String> {
        let mut result: BTreeSet<String> = if !keys(frame.kind).is_empty() { keys(frame.kind).iter().map(|key| (*key).into()).collect() }
        else if frame.kind == "params" {
            let block = self.stack.iter().rev().skip(1).find(|frame| frame.kind == "block");
            if let Some(meta) = block.and_then(|block| self.metadata["blocks"].get(text(&block.value["id"]))) {
                meta["bindings"].as_object().unwrap().keys().cloned().collect()
            } else { self.metadata["blocks"].as_object().unwrap().values().flat_map(|meta|
                meta["bindings"].as_object().unwrap().keys().cloned()).collect() }
        } else { self.pool.iter().filter(|value| safe_name(value) &&
                     (frame.kind == "result" || !self.inputs.contains_key(text(value)))).map(|value| text(value).into()).collect() };
        if frame.kind == "reference" {
            let group = if frame.value.get("input").is_some() { Some(vec!["input"]) }
                else if frame.value.get("step").is_some() || frame.value.get("field").is_some() { Some(vec!["step", "field"]) }
                else if frame.value.get("literal").is_some() || frame.value.get("type").is_some() { Some(vec!["literal", "type"]) } else { None };
            if let Some(group) = group { result.retain(|key| group.contains(&key.as_str())); }
        }
        if let Some(map) = frame.value.as_object() { result.retain(|key| !map.contains_key(key)); }
        result
    }

    fn exact_keys(value: &Value, required: &[&str]) -> bool {
        value.as_object().is_some_and(|map| map.len() == required.len() && required.iter().all(|key| map.contains_key(*key)))
    }

    fn can_close(&self, frame: &Frame) -> Result<bool, SomError> {
        let value = &frame.value;
        if let Some(array) = value.as_array() {
            let bound = match frame.kind { "files" => 12, "nodes" => 12, _ => 64 };
            return Ok(array.len() <= bound && (!["files", "blocks"].contains(&frame.kind) || !array.is_empty()));
        }
        if frame.key.is_some() { return Ok(false); }
        if ["root", "file", "block", "binding", "symbol", "flow", "export"].contains(&frame.kind) && !Self::exact_keys(value, keys(frame.kind)) { return Ok(false); }
        match frame.kind {
            "root" => {
                let visible = self.walk_types(&value["flow"]["steps"], &Types::new(), false)?;
                for reference in value["flow"]["result"].as_object().into_iter().flatten().map(|(_, value)| value) {
                    flow::ref_type(&self.prompt, reference, &self.inputs, &visible)?;
                }
            }
            "block" => {
                let Some(meta) = self.metadata["blocks"].get(text(&value["id"])) else { return Ok(false); };
                if value["name"] != text(&meta["provides"]).split_once(':').map_or("", |(_, name)| name)
                    || value["params"] != meta["bindings"] { return Ok(false); }
            }
            "reference" => { flow::ref_type(&self.prompt, value, &self.inputs, &self.visible(&frame.path)?)?; }
            "condition" => {
                let op = text(&value["op"]);
                let required: &[&str] = if op == "is_null" { &["op", "left"] } else { &["op", "left", "right"] };
                if !Self::exact_keys(value, required) { return Ok(false); }
                let visible = self.visible(&frame.path)?;
                let left = flow::ref_type(&self.prompt, &value["left"], &self.inputs, &visible)?;
                if op == "is_null" { return Ok(left.ends_with('?')); }
                let right = flow::ref_type(&self.prompt, &value["right"], &self.inputs, &visible)?;
                return Ok(left == right && ["string", "integer", "float", "boolean", "date"].contains(&left.trim_end_matches('?'))
                    && (["eq", "ne"].contains(&op) || !left.ends_with('?') && left != "boolean"));
            }
            "node" => {
                let meta = &self.metadata["flow_blocks"][text(&value["id"])];
                let op = text(&meta["operation"]);
                let required: &[&str] = match op { "transaction" => &["id", "name", "steps"],
                    "if" => &["id", "name", "condition", "then", "else", "exports"],
                    _ => &["id", "name", "args", "undo"] };
                if !Self::exact_keys(value, required) || !safe_name(&value["name"])
                    || !self.operation_ids(Some(value))?.contains(&value["id"]) { return Ok(false); }
                if !["if", "transaction"].contains(&op) {
                    let inverse = if op.ends_with(".patch") { json!("restore_patch") }
                        else if ["parent.create", "child.create", "batch.create", "workflow.import", "object.put"].contains(&op) { json!("delete_created") }
                        else { Value::Null };
                    let selected = if value["undo"].is_null() { Value::Null }
                        else { self.metadata["flow_blocks"][text(&value["undo"])]["inverse"].clone() };
                    if selected != inverse { return Ok(false); }
                }
                if op == "if" { self.walk_types(&json!([value]), &self.visible(&frame.path)?, false)?; }
            }
            _ => {}
        }
        Ok(true)
    }

    pub fn allowed(&self) -> Result<Vec<u32>, SomError> {
        if self.tokens >= 2048 || self.complete { return Ok(Vec::new()); }
        if self.root.is_some() { return Ok(vec![1]); }
        let Some(frame) = self.stack.last() else { return Ok(vec![2]); };
        let mut result = BTreeSet::new();
        if self.can_close(frame).unwrap_or(false) { result.insert(if frame.value.is_array() { 5 } else { 3 }); }
        if frame.value.is_object() && frame.key.is_none() {
            for key in self.key_domain(frame) { if let Some(id) = self.ids.get(&flow::canonical(&json!(key))) { result.insert(*id); } }
        } else if self.expected_kind() == "scalar" {
            for value in self.scalar_domain(frame)? { if let Some(id) = self.ids.get(&flow::canonical(&value)) { result.insert(*id); } }
        } else if self.stack.len() < 32 { result.insert(if ["files", "blocks", "nodes", "refs"].contains(&self.expected_kind()) { 4 } else { 2 }); }
        Ok(result.into_iter().collect())
    }

    pub fn advance(&mut self, token: u32) -> Result<(), SomError> {
        if !self.allowed()?.contains(&token) { return Err(invalid("token violates the flow prefix contract")); }
        self.advance_checked(token)
    }

    pub fn advance_checked(&mut self, token: u32) -> Result<(), SomError> {
        self.tokens += 1;
        if token == 1 { self.complete = true; return Ok(()); }
        if token == 2 || token == 4 {
            let kind = self.expected_kind(); let path = self.next_path();
            self.stack.push(Frame { kind, path, key: None,
                value: if token == 2 { Value::Object(Map::new()) } else { json!([]) } });
            return Ok(());
        }
        let value = if token == 3 || token == 5 {
            self.stack.pop().ok_or_else(|| invalid("no container to close"))?.value
        } else { self.pool.get((token - 8) as usize).ok_or_else(|| invalid("unknown binding"))?.clone() };
        let Some(frame) = self.stack.last_mut() else { self.root = Some(value); return Ok(()); };
        if let Some(array) = frame.value.as_array_mut() { array.push(value); }
        else if frame.key.is_none() { frame.key = Some(text(&value).into()); }
        else { frame.value.as_object_mut().unwrap().insert(frame.key.take().unwrap(), value); }
        Ok(())
    }

    pub fn context(&self) -> Value {
        let Some(frame) = self.stack.last() else { return json!({"kind":"root", "path":[]}); };
        let choice = if frame.key.as_deref() == Some("id") { "block" }
            else if frame.kind == "reference" { "reference" }
            else if frame.kind == "node" && frame.key.as_deref() == Some("name") { "order" }
            else { "structure_or_binding" };
        json!({"kind":frame.kind,"path":frame.path,"key":frame.key,"choice":choice})
    }

    pub fn root(&self) -> Option<&Value> { self.root.as_ref() }
    pub fn complete(&self) -> bool { self.complete }
}

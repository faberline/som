//! Typed, variable block plans. No reference planner is linked into the runtime.
use crate::{SomError, object, related};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

pub const VERSION: &str = "som.prompt.v6";
pub const CONTRACT_TEXT: &str = include_str!("../../som-rs/contracts/related-business.v1.md");
pub const CATALOG_TEXT: &str = include_str!("../../som-code-python/data/block-contracts/composed.json");
pub static CATALOG: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(CATALOG_TEXT).expect("compiled block contracts"));
const OPS: [&str; 5] = ["create", "get", "list", "patch", "delete"];
fn err(code: &'static str, detail: &str) -> SomError { SomError::new(code, detail) }

pub fn verify_blocks(blocks: &BTreeMap<String,crate::Block>) -> Result<(), SomError> {
    verify_blocks_with(blocks, &CATALOG)
}

pub fn verify_blocks_with(blocks: &BTreeMap<String,crate::Block>, catalog: &Value) -> Result<(), SomError> {
    for (identity, meta) in catalog["blocks"].as_object().unwrap() {
        let block = blocks.get(identity).ok_or_else(||err("INVALID_BLOCK", "contract block is missing"))?;
        let value = json!({"id":block.id,"version":block.version,"imports":block.imports,"params":block.params,"template":block.template});
        let hash = format!("{:x}", Sha256::digest(serde_json::to_vec(&value).unwrap()));
        if meta["source_sha256"] != hash || meta["params"] != value["params"] {
            return Err(err("INVALID_BLOCK", "block bytes or parameter types differ from their contract"));
        }
    }
    Ok(())
}

pub fn legacy(p: &Value) -> Value {
    let mut q = p.clone();
    let map = q.as_object_mut().unwrap();
    map.remove("contract"); map.remove("operations");
    map.insert("version".into(), json!(related::VERSION));
    let mut logic: Vec<Value> = ["parent", "child"].iter().flat_map(|scope| related::CRUD.iter().map(move |step| json!({"scope":scope,"step":step}))).collect();
    logic.push(json!({"scope":"batch","step":related::BATCH}));
    map.insert("logic".into(), json!(logic));
    map.insert("tests".into(), json!([related::TEST]));
    q
}

pub fn validate(p: &Value) -> Result<(), SomError> {
    object(p, &["version","contract","package","resources","relation","operations","capabilities","migration"], "prompt")?;
    if p["version"] != VERSION { return Err(err("UNSUPPORTED_VERSION", "unsupported composed prompt")); }
    let hash = format!("{:x}", Sha256::digest(CONTRACT_TEXT.as_bytes()));
    if p["contract"] != json!({"id":"related-business.v1","sha256":hash}) { return Err(err("UNSUPPORTED_CONTRACT", "contract id or hash does not match")); }
    let ops = p["operations"].as_array().filter(|s| (1..=11).contains(&s.len())).ok_or_else(|| err("UNSUPPORTED_LOGIC", "expected 1..11 unique scoped operations"))?;
    let mut seen = BTreeSet::new();
    for op in ops {
        object(op, &["scope","action"], "operation")?;
        let scope = op["scope"].as_str().unwrap_or("");
        let action = op["action"].as_str().unwrap_or("");
        if !seen.insert((scope,action)) || !(["parent","child"].contains(&scope) && OPS.contains(&action) || (scope == "batch" && action == "batch_create")) {
            return Err(err("UNSUPPORTED_LOGIC", "unsupported or duplicate scoped operation"));
        }
    }
    related::validate(&legacy(p))
}

pub fn values(p: &Value) -> Result<BTreeMap<String,Value>, SomError> {
    validate(p)?;
    let mut values = related::values(&legacy(p));
    let routes: Vec<Value> = p["operations"].as_array().unwrap().iter().map(|op| {
        let scope = op["scope"].as_str().unwrap(); let action = op["action"].as_str().unwrap();
        let key = if scope == "batch" { "batch.path".to_owned() } else { format!("{scope}{}", if ["get","patch","delete"].contains(&action) { ".item_path" } else { ".path" }) };
        let method = match action { "create"|"batch_create"=>"post", "get"|"list"=>"get", "patch"=>"patch", _=>"delete" };
        json!({"method":method,"path":values[&key]})
    }).collect();
    values.insert("contract.routes".into(), json!(serde_json::to_string(&routes).unwrap()));
    Ok(values)
}

pub fn features(p: &Value) -> Result<String, SomError> {
    validate(p)?;
    let mut out = String::new(); let caps = &p["capabilities"];
    for key in ["database","auth","storage","workflow"] { out += &format!("<cap:{key}:{}>", caps[key].as_str().unwrap()); }
    for role in ["parent","child"] {
        let r = &p["resources"][role];
        out += &format!("<resource:{role}><query:{}>", related::query_kind(r));
    }
    out += "<operations>";
    for op in p["operations"].as_array().unwrap() { out += &format!("<op:{}:{}>",op["scope"].as_str().unwrap(),op["action"].as_str().unwrap()); }
    for _ in p["operations"].as_array().unwrap().len()..11 { out += "<op:none>"; }
    out += "</operations><plan>";
    Ok(out)
}

pub fn decode(text: &str) -> Result<Value, SomError> {
    let regex = regex::Regex::new(r"<[^<>\s]+>").unwrap();
    let tokens: Vec<&str> = regex.find_iter(text).map(|m| m.as_str()).collect();
    if tokens.concat() != text || tokens.last() != Some(&"<eos>") { return Err(err("INVALID_DSL", "malformed or incomplete block plan")); }
    let mut files: Vec<Value> = vec![];
    let mut file: Option<Value> = None; let mut block: Option<Value> = None; let mut deps = false;
    for token in &tokens[..tokens.len()-1] {
        if let Some(slot) = token.strip_prefix("<file:").and_then(|t|t.strip_suffix('>')) {
            if file.is_some() || block.is_some() { return Err(err("INVALID_DSL", "unexpected file")); }
            let path = CATALOG["slots"].get(slot).ok_or_else(||err("INVALID_PATH","unknown file slot"))?;
            file = Some(json!({"path":path,"blocks":[]}));
        } else if let Some(id) = token.strip_prefix("<block:").and_then(|t|t.strip_suffix('>')) {
            if file.is_none() || block.is_some() { return Err(err("INVALID_DSL", "unexpected block")); }
            block = Some(json!({"id":id,"name":null,"params":{},"refs":[]})); deps = false;
        } else if let Some(name) = token.strip_prefix("<name:").and_then(|t|t.strip_suffix('>')) {
            let b = block.as_mut().ok_or_else(||err("INVALID_DSL","unexpected block name"))?;
            if deps { b["refs"].as_array_mut().unwrap().push(json!(name)); }
            else if b["name"].is_null() { b["name"] = json!(name); }
            else { return Err(err("INVALID_DSL","unexpected block name")); }
        } else if let Some(binding) = token.strip_prefix("<bind:").and_then(|t|t.strip_suffix('>')) {
            let b = block.as_mut().ok_or_else(||err("INVALID_DSL","unexpected binding"))?;
            let parts: Vec<&str> = binding.split(':').collect();
            if deps || b["name"].is_null() || parts.len()!=2 || b["params"].get(parts[0]).is_some() { return Err(err("INVALID_DSL","duplicate or malformed binding")); }
            b["params"][parts[0]] = json!({"$ref":parts[1]});
        } else if *token == "<deps>" && block.as_ref().is_some_and(|b| !b["name"].is_null()) && !deps { deps = true; }
        else if *token == "<end>" && block.is_some() && deps { file.as_mut().unwrap()["blocks"].as_array_mut().unwrap().push(block.take().unwrap()); }
        else if *token == "</file>" && block.is_none() && file.as_ref().is_some_and(|f| !f["blocks"].as_array().unwrap().is_empty()) { files.push(file.take().unwrap()); }
        else { return Err(err("INVALID_DSL","unexpected structural token")); }
    }
    if file.is_some() || block.is_some() || files.is_empty() { return Err(err("INVALID_DSL","incomplete block plan")); }
    Ok(json!({"version":"som.dsl.v2","files":files}))
}

pub fn validate_plan(p: &Value, dsl: &Value) -> Result<(), SomError> {
    validate_plan_with(p, dsl, &CATALOG)
}

pub fn validate_plan_with(p: &Value, dsl: &Value, catalog: &Value) -> Result<(), SomError> {
    validate(p)?;
    if dsl.as_object().is_none_or(|m| m.len()!=2 || !m.contains_key("version") || !m.contains_key("files")) || dsl["version"]!="som.dsl.v2" || !dsl["files"].is_array() { return Err(err("INVALID_DSL","expected DSL v2")); }
    let files = dsl["files"].as_array().unwrap();
    let count: usize = files.iter().filter_map(|f|f["blocks"].as_array()).map(|a|a.len()).sum();
    if !(1..=12).contains(&files.len()) || count>64 { return Err(err("SCOPE_TOO_LARGE","plan exceeds file/block limit")); }
    let mut provided = BTreeSet::new(); let mut requirements = BTreeSet::new(); let mut routes = vec![]; let mut file_seen = BTreeSet::new(); let mut operations = vec![];
    let caps=&p["capabilities"];
    for f in files {
        object(f,&["path","blocks"],"file")?;
        let slot = catalog["slots"].as_object().unwrap().iter().find(|(_,v)| **v==f["path"]).map(|(k,_)|k.as_str()).ok_or_else(||err("INVALID_PATH","unknown file slot"))?;
        let entries=f["blocks"].as_array().filter(|a|!a.is_empty()).ok_or_else(||err("INVALID_DSL","duplicate or empty file"))?;
        if !file_seen.insert(slot) { return Err(err("INVALID_DSL","duplicate or empty file")); }
        let mut names: BTreeMap<String,String> = BTreeMap::new(); let mut closed=false;
        for b in entries {
            object(b,&["id","name","params","refs"],"block")?;
            let meta=b["id"].as_str().and_then(|s|catalog["blocks"].get(s)).ok_or_else(||err("UNSUPPORTED_BLOCK","unapproved composed block"))?;
            if meta["slot"]!=slot || meta["capabilities"].as_object().unwrap().iter().any(|(k,v)| caps[k]!=*v) { return Err(err("INVALID_COMPOSITION","incompatible file or capability")); }
            if let Some(query)=meta["query"].as_array() { if related::query_kind(&p["resources"][query[0].as_str().unwrap()]) != query[1].as_str().unwrap() { return Err(err("INVALID_COMPOSITION","incompatible query block")); } }
            if (!meta["operation"].is_null() || meta["provides"] == "app:workflow") && meta["authorization"] != "require_access" {
                return Err(err("INVALID_COMPOSITION", "route lacks an authorization contract"));
            }
            if (meta["operation"] == json!(["batch", "batch_create"]) || meta["provides"] == "app:workflow" && caps["workflow"] == "csv") && meta["transaction"] != "atomic" {
                return Err(err("INVALID_COMPOSITION", "operation lacks an atomic transaction contract"));
            }
            if b["params"]!=meta["bindings"] { return Err(err("INVALID_REF","parameter bindings do not satisfy block types and roles")); }
            let name=b["name"].as_str().unwrap_or("");
            let refs=b["refs"].as_array().ok_or_else(||err("INVALID_REF","invalid dependency reference"))?;
            if name.is_empty() || !name.chars().enumerate().all(|(i,c)| c=='_' || c.is_ascii_alphabetic() || i>0 && c.is_ascii_digit()) || names.contains_key(name) || refs.iter().any(|r|r.as_str().is_none_or(|s|!names.contains_key(s))) || refs.iter().map(|r|r.as_str()).collect::<BTreeSet<_>>().len()!=refs.len() { return Err(err("INVALID_REF","invalid dependency reference")); }
            let node=meta["provides"].as_str().unwrap().to_owned();
            if provided.contains(&node) { return Err(err("INVALID_COMPOSITION","duplicate symbol or route")); }
            let referenced: BTreeSet<String> = refs.iter().map(|r|names[r.as_str().unwrap()].clone()).collect();
            let needs: BTreeSet<String>=meta["requires"].as_array().unwrap().iter().map(|n|n.as_str().unwrap().to_owned()).collect();
            if needs.iter().any(|n|n.split(':').next()==Some(slot) && !referenced.contains(n)) { return Err(err("INVALID_REF","missing typed dependency")); }
            if slot=="app" {
                if node=="app:tail" { if provided.iter().any(|n:&String|n.starts_with("app:") && n!="app:head" && !referenced.contains(n)) { return Err(err("INVALID_REF","router close must reference all routes")); } closed=true; }
                else if closed && node!="app:app" { return Err(err("INVALID_COMPOSITION","route after router close")); }
            }
            names.insert(name.to_owned(),node.clone()); provided.insert(node); requirements.extend(needs);
            if !meta["operation"].is_null() { operations.push(meta["operation"].clone()); }
            routes.extend(meta["routes"].as_array().unwrap().iter().map(|r|r.as_str().unwrap().to_owned()));
        }
    }
    let wanted: Vec<Value>=p["operations"].as_array().unwrap().iter().map(|o|json!([o["scope"],o["action"]])).collect();
    if operations!=wanted { return Err(err("INVALID_COMPOSITION","requested operations are missing, extra or out of order")); }
    let mut needed:BTreeSet<String>=catalog["required_common"].as_array().unwrap().iter().map(|n|n.as_str().unwrap().to_owned()).collect();
    needed.extend(catalog["required_database"][caps["database"].as_str().unwrap()].as_array().unwrap().iter().map(|n|n.as_str().unwrap().to_owned()));
    if caps["storage"]!="none" { needed.insert("integrations:storage_common".into()); }
    if !needed.is_subset(&provided) || !requirements.is_subset(&provided) { return Err(err("INVALID_COMPOSITION","missing service capability or dependency")); }
    if routes.iter().collect::<BTreeSet<_>>().len()!=routes.len() { return Err(err("INVALID_COMPOSITION","duplicate route")); }
    Ok(())
}

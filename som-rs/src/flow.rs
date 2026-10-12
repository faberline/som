//! Bounded flow contracts. This module never chooses or repairs a model plan.
use crate::{SomError, composed, ident, object};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

pub const VERSION: &str = "som.prompt.v7";
pub const DSL_VERSION: &str = "som.dsl.v3";
pub const CONTRACT: &str = "related-flow.v1";
pub const CONTRACT_TEXT: &str = include_str!("../contracts/related-flow.v1.md");
pub const CATALOG_TEXT: &str = include_str!("../../som-code-python/data/block-contracts/flow-v7.json");
pub static CATALOG: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(CATALOG_TEXT).expect("compiled flow contracts"));
pub const COMPACT_CATALOG_TEXT: &str = include_str!("../../som-code-python/data/block-contracts/flow-v7-compact.json");
pub static COMPACT_CATALOG: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(COMPACT_CATALOG_TEXT).expect("compiled compact flow contracts"));

fn err(code: &'static str, detail: &str) -> SomError { SomError::new(code, detail) }
pub fn contract_hash() -> String { format!("{:x}", Sha256::digest(CONTRACT_TEXT.as_bytes())) }
pub fn base(prompt: &Value) -> Value {
    let mut result = prompt.clone();
    if let Some(map) = result.as_object_mut() {
        map.remove("flow");
        map.insert("version".into(), json!(composed::VERSION));
        map.insert("contract".into(), json!({"id":"related-business.v1", "sha256":format!("{:x}", Sha256::digest(composed::CONTRACT_TEXT.as_bytes()))}));
    }
    result
}

fn name(value: &Value) -> Result<String, SomError> {
    let value = ident(value, "flow name")?;
    if value.len() > 64 || value.starts_with('_') || value.starts_with("model_") {
        return Err(err("INVALID_VALUE", "unsafe flow name"));
    }
    Ok(value)
}
fn scalar(kind: &str) -> bool { ["string","integer","float","boolean","date","bytes"].contains(&kind) }
fn input_type(kind: &str) -> bool {
    scalar(kind) && kind != "bytes" || ["parent_input","child_input","parent_patch","child_patch","children_input"].contains(&kind)
}
fn mutation(op: &str) -> bool {
    ["parent.create","child.create","batch.create","workflow.import","parent.patch","child.patch","object.put"].contains(&op)
}
fn arguments(op: &str) -> Option<Vec<(&'static str, &'static str)>> {
    Some(match op {
        "authorize"|"parent.list" => vec![],
        "parent.create" => vec![("data","parent_input")],
        "child.create" => vec![("parent_id","integer"),("data","child_input")],
        "parent.get"|"child.list"|"workflow.run" => vec![("parent_id","integer")],
        "child.get" => vec![("parent_id","integer"),("child_id","integer")],
        "parent.patch" => vec![("parent_id","integer"),("data","parent_patch")],
        "child.patch" => vec![("parent_id","integer"),("child_id","integer"),("data","child_patch")],
        "batch.create" => vec![("parent","parent_input"),("children","children_input")],
        "workflow.import" => vec![("parent_id","integer"),("csv","string")],
        "object.put" => vec![("key","string"),("body","bytes")],
        "object.get" => vec![("key","string")],
        _ => return None,
    })
}
pub(crate) fn output_type(op: &str) -> &'static str {
    match op {
        "parent.create"|"parent.get"|"parent.patch" => "parent_row",
        "child.create"|"child.get"|"child.patch" => "child_row",
        "authorize" => "identity", "parent.list" => "parent_rows",
        "child.list"|"workflow.import" => "child_rows", "batch.create" => "batch",
        "workflow.run" => "workflow", _ => "object",
    }
}
fn date(value: &str) -> bool {
    if value.len()!=10 || value.as_bytes()[4]!=b'-' || value.as_bytes()[7]!=b'-' || !value.bytes().enumerate().all(|(i,c)| i==4 || i==7 || c.is_ascii_digit()) { return false; }
    let year: u32 = value[..4].parse().unwrap_or(0);
    let month: usize = value[5..7].parse().unwrap_or(0);
    let day: u32 = value[8..].parse().unwrap_or(0);
    let leap = year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400));
    let days = [0,31,if leap {29} else {28},31,30,31,30,31,31,30,31,30,31];
    year>0 && month>0 && month<13 && day>0 && day<=days[month]
}
fn literal(value: &Value, kind: &str) -> Result<(), SomError> {
    use base64::Engine;
    let valid = match kind {
        "string" => value.is_string(),
        "integer" => value.as_i64().is_some_and(|n| i32::try_from(n).is_ok()),
        "float" => value.as_f64().is_some_and(f64::is_finite),
        "boolean" => value.is_boolean(),
        "date" => value.as_str().is_some_and(date),
        "bytes" => value.as_str().and_then(|s|base64::engine::general_purpose::STANDARD.decode(s).ok()).is_some_and(|b| !b.is_empty() && b.len()<=1048576),
        _ => false,
    };
    if valid { Ok(()) } else { Err(err("INVALID_REF", "literal type mismatch")) }
}
pub(crate) fn member(prompt: &Value, kind: &str, field: &str) -> Result<String, SomError> {
    if field.is_empty() { return Ok(kind.into()); }
    if let Some((first,rest))=field.split_once('.') { return member(prompt,&member(prompt,kind,first)?,rest); }
    let fixed=match (kind,field) {
        ("identity","sub"|"role")|("object","key"|"sha256")|("workflow","sha256")=>Some("string"),
        ("object"|"workflow","size")=>Some("integer"),
        ("object"|"workflow","body")=>Some("bytes"),
        ("workflow","data")=>Some("json"),
        ("batch","parent")=>Some("parent_row"), ("batch","children")=>Some("child_rows"),
        _=>None,
    };
    if let Some(kind)=fixed { return Ok(kind.into()); }
    if ["parent_row","child_row"].contains(&kind) {
        let role=kind.split('_').next().unwrap();
        if field=="id" || role=="child" && prompt["relation"]["foreign_key"]==field { return Ok("integer".into()); }
        if ["created_at","updated_at"].contains(&field) { return Ok("string".into()); }
        for definition in prompt["resources"][role]["fields"].as_array().unwrap() {
            if definition["name"]==field {
                let kind=match definition["type"].as_str().unwrap() { "str"=>"string", "int"=>"integer", "bool"=>"boolean", k=>k };
                return Ok(format!("{kind}{}",if definition["required"]==false && definition["default"].is_null() {"?"} else {""}));
            }
        }
    }
    Err(err("INVALID_REF", "unknown result field"))
}
pub(crate) fn ref_type(prompt: &Value, value: &Value, inputs: &BTreeMap<String,String>, visible: &BTreeMap<String,String>) -> Result<String, SomError> {
    let map=value.as_object().ok_or_else(||err("INVALID_REF", "typed reference required"))?;
    if let Some(kind) = visible.get(&format!("@{}", canonical(value))) {
        return Ok(kind.clone());
    }
    if map.len()==1 && map.contains_key("input") {
        return map["input"].as_str().and_then(|n|inputs.get(n)).cloned().ok_or_else(||err("INVALID_REF","unknown input"));
    }
    if map.len()==2 && map.contains_key("step") && map.contains_key("field") {
        let kind=map["step"].as_str().and_then(|n|visible.get(n)).ok_or_else(||err("INVALID_REF","step not visible"))?;
        return member(prompt,kind,map["field"].as_str().ok_or_else(||err("INVALID_REF","field must be a string"))?);
    }
    if map.len()==2 && map.contains_key("literal") && map.contains_key("type") {
        if let Some(kind)=map["type"].as_str().filter(|k|scalar(k)) { literal(&map["literal"],kind)?; return Ok(kind.into()); }
    }
    Err(err("INVALID_REF", "unknown reference kind"))
}

struct Checker<'a> {
    prompt: &'a Value,
    inputs: BTreeMap<String,String>,
    names: BTreeSet<String>,
    authorized: bool,
    actions: usize,
    branches: usize,
    transactions: usize,
}
impl Checker<'_> {
    fn predicate(&self, condition: &Value, visible: &BTreeMap<String,String>) -> Result<(), SomError> {
        let op=condition["op"].as_str().unwrap_or("");
        object(condition,if op=="is_null" {&["op","left"]} else {&["op","left","right"]},"condition")?;
        let left=ref_type(self.prompt,&condition["left"],&self.inputs,visible)?;
        if op=="is_null" {
            if !left.ends_with('?') {return Err(err("INVALID_REF","null check requires nullable scalar"));}
        } else {
            let right=ref_type(self.prompt,&condition["right"],&self.inputs,visible)?;
            if !["eq","ne","lt","le","gt","ge"].contains(&op) || left!=right || !scalar(left.trim_end_matches('?')) || left=="bytes" {
                return Err(err("INVALID_REF","incompatible comparison"));
            }
            if !["eq","ne"].contains(&op) && (left.ends_with('?') || left=="boolean") {return Err(err("INVALID_REF","unsafe ordered comparison"));}
        }
        Ok(())
    }
    fn walk(&mut self, nodes: &Value, visible: &BTreeMap<String,String>, depth: usize, transaction: bool) -> Result<BTreeMap<String,String>,SomError> {
        let nodes=nodes.as_array().ok_or_else(||err("INVALID_SCHEMA","steps must be a list"))?;
        let mut local=visible.clone();
        for node in nodes {
            if node.as_object().is_none_or(|m| !m.contains_key("name") || !m.contains_key("op")) { return Err(err("INVALID_SCHEMA","invalid flow node")); }
            let node_name=name(&node["name"])?;
            if !self.names.insert(node_name.clone()) {return Err(err("INVALID_REF","duplicate step name"));}
            let op=node["op"].as_str().ok_or_else(||err("UNSUPPORTED_LOGIC","unsupported flow action"))?;
            match op {
                "transaction"=>{
                    object(node,&["name","op","steps"],"transaction")?;
                    if !self.authorized || transaction {return Err(err("INVALID_TRANSACTION","unauthorized or nested transaction"));}
                    self.transactions+=1;
                    if self.transactions>2 {return Err(err("SCOPE_TOO_LARGE","flow node budget exceeded"));}
                    local=self.walk(&node["steps"],&local,depth,true)?;
                }
                "if"=>{
                    object(node,&["name","op","condition","then","else","exports"],"if")?;
                    if !self.authorized {return Err(err("INVALID_AUTHORIZATION","authorization must precede branching"));}
                    self.branches+=1;
                    if self.branches>2 || depth>=2 {return Err(err("SCOPE_TOO_LARGE","branch budget exceeded"));}
                    self.predicate(&node["condition"],&local)?;
                    let mut non_null = local.clone();
                    if node["condition"]["op"] == "is_null" {
                        let reference = &node["condition"]["left"];
                        let kind = ref_type(self.prompt, reference, &self.inputs, &local)?;
                        non_null.insert(format!("@{}", canonical(reference)), kind.trim_end_matches('?').into());
                    }
                    let yes=self.walk(&node["then"],&local,depth+1,transaction)?;
                    let no=self.walk(&node["else"],&non_null,depth+1,transaction)?;
                    let exports=node["exports"].as_object().ok_or_else(||err("INVALID_REF","exports must be a map"))?;
                    for (export,binding) in exports {
                        name(&json!(export))?;
                        object(binding,&["then","else"],"branch export")?;
                        let a=ref_type(self.prompt,&binding["then"],&self.inputs,&yes)?;
                        let b=ref_type(self.prompt,&binding["else"],&self.inputs,&no)?;
                        if a!=b || !self.names.insert(export.clone()) {return Err(err("INVALID_REF","branch export mismatch"));}
                        local.insert(export.clone(),a);
                    }
                }
                _=>{
                    let args=arguments(op).ok_or_else(||err("UNSUPPORTED_LOGIC","unsupported flow action"))?;
                    object(node,&["name","op","args","undo"],"action")?;
                    self.actions+=1;
                    if self.actions>8 {return Err(err("SCOPE_TOO_LARGE","flow node budget exceeded"));}
                    if op=="authorize" {
                        if self.authorized || !local.is_empty() || transaction {return Err(err("INVALID_AUTHORIZATION","authorization must be first"));}
                        self.authorized=true;
                    } else if !self.authorized {return Err(err("INVALID_AUTHORIZATION","missing authorization"));}
                    if mutation(op) && op!="object.put" && !transaction {return Err(err("INVALID_TRANSACTION","database mutation lacks transaction"));}
                    if op.starts_with("object.") && (transaction || self.prompt["capabilities"]["storage"]=="none") {return Err(err("INVALID_TRANSACTION","incompatible provider or transaction"));}
                    if op=="workflow.import" && self.prompt["capabilities"]["workflow"]!="csv" {return Err(err("INVALID_COMPOSITION","CSV capability required"));}
                    let keys:Vec<_>=args.iter().map(|(key,_)|*key).collect();
                    object(&node["args"],&keys,"action arguments")?;
                    for (key,kind) in args {if ref_type(self.prompt,&node["args"][key],&self.inputs,&local)?!=kind {return Err(err("INVALID_REF","wrong argument type"));}}
                    let undo=if op.ends_with(".patch") {json!("restore_patch")} else if mutation(op) {json!("delete_created")} else {Value::Null};
                    if node["undo"]!=undo {return Err(err("INVALID_COMPENSATION","missing or incompatible compensation"));}
                    local.insert(node_name,output_type(op).into());
                }
            }
        }
        Ok(local)
    }
}
pub fn validate(prompt: &Value) -> Result<(),SomError> {
    object(prompt,&["version","contract","package","resources","relation","operations","capabilities","migration","flow"],"prompt")?;
    if prompt["version"]!=VERSION {return Err(err("UNSUPPORTED_VERSION","expected prompt v7"));}
    if prompt["contract"]!=json!({"id":CONTRACT,"sha256":contract_hash()}) {return Err(err("UNSUPPORTED_CONTRACT","flow contract differs"));}
    composed::validate(&base(prompt))?;
    let path=prompt["resources"]["parent"]["path"].as_str().unwrap();
    if ["/flows","/flow-runs"].iter().any(|r|path==*r || path.starts_with(&format!("{r}/"))) {return Err(err("INVALID_VALUE","resource route collides with flows"));}
    let flow=&prompt["flow"];
    object(flow,&["name","inputs","steps","result"],"flow")?;
    name(&flow["name"])?;
    let inputs=flow["inputs"].as_array().filter(|a|a.len()<=8).ok_or_else(||err("SCOPE_TOO_LARGE","at most eight typed inputs"))?;
    let mut checker=Checker {prompt,inputs:BTreeMap::new(),names:BTreeSet::new(),authorized:false,actions:0,branches:0,transactions:0};
    for input in inputs {
        object(input,&["name","type"],"flow input")?;
        let n=name(&input["name"])?;
        let kind=input["type"].as_str().filter(|k|input_type(k)).ok_or_else(||err("INVALID_REF","unsupported input type"))?;
        if checker.inputs.insert(n.clone(),kind.into()).is_some() {return Err(err("INVALID_REF","duplicate input"));}
        checker.names.insert(n);
    }
    let visible=checker.walk(&flow["steps"],&BTreeMap::new(),0,false)?;
    if !checker.authorized {return Err(err("INVALID_AUTHORIZATION","flow requires authorization"));}
    for (n,value) in flow["result"].as_object().ok_or_else(||err("INVALID_REF","result must be a map"))? {
        name(&json!(n))?;
        if ref_type(prompt,value,&checker.inputs,&visible)?=="bytes" {return Err(err("INVALID_REF","binary result requires metadata"));}
    }
    Ok(())
}
/// Match the reference JSON spelling without changing scalar types or values.
/// In particular, Python uses scientific notation below 1e-4 and two exponent digits.
pub fn canonical(value: &Value) -> String {
    match value {
        Value::Object(map) => format!("{{{}}}", map.iter().map(|(key, value)|
            format!("{}:{}", serde_json::to_string(key).unwrap(), canonical(value)))
            .collect::<Vec<_>>().join(",")),
        Value::Array(items) => format!("[{}]", items.iter().map(canonical).collect::<Vec<_>>().join(",")),
        Value::Number(number) if number.is_f64() => {
            let text = number.to_string();
            if let Some((mantissa, exponent)) = text.split_once("e-") {
                return format!("{mantissa}e-{:02}", exponent.parse::<u32>().unwrap());
            }
            let float = number.as_f64().unwrap();
            if float != 0.0 && float.abs() < 0.0001 && !text.contains('e') {
                let (sign, digits) = if let Some(positive) = text.strip_prefix('-') {("-", positive)} else {("", text.as_str())};
                let digits = digits.strip_prefix("0.").unwrap();
                let zeros = digits.bytes().take_while(|digit| *digit == b'0').count();
                let significant = &digits[zeros..];
                let fraction = if significant.len() > 1 {format!(".{}", &significant[1..])} else {String::new()};
                return format!("{sign}{}{fraction}e-{:02}", &significant[..1], zeros + 1);
            }
            text
        }
        _ => serde_json::to_string(value).unwrap(),
    }
}

fn capability(prompt:&Value, metadata:&Value) -> bool {
    metadata["capabilities"].as_object().is_some_and(|caps|caps.iter().all(|(key,value)|prompt["capabilities"][key]==*value))
}
fn reconstruct(prompt:&Value,nodes:&Value,catalog:&Value) -> Result<Value,SomError> {
    let mut result=Vec::new();
    for node in nodes.as_array().ok_or_else(||err("INVALID_DSL","flow plan nodes must be a list"))? {
        let id=node["id"].as_str().ok_or_else(||err("INVALID_DSL","flow block missing"))?;
        let meta=catalog.get(id).ok_or_else(||err("UNSUPPORTED_BLOCK","unapproved flow block"))?;
        if !capability(prompt,meta) {return Err(err("INVALID_COMPOSITION","incompatible flow capability"));}
        let mut item=node.as_object().ok_or_else(||err("INVALID_DSL","invalid flow node"))?.clone();
        item.remove("id"); item.insert("op".into(),meta["operation"].clone());
        match meta["operation"].as_str().unwrap_or("") {
            "transaction"=>{item.insert("steps".into(),reconstruct(prompt,&node["steps"],catalog)?);}
            "if"=>{item.insert("then".into(),reconstruct(prompt,&node["then"],catalog)?);item.insert("else".into(),reconstruct(prompt,&node["else"],catalog)?);}
            _ if !node["undo"].is_null()=>{
                let inverse=node["undo"].as_str().and_then(|id|catalog.get(id)).filter(|m|!m["inverse"].is_null()).ok_or_else(||err("INVALID_COMPENSATION","unapproved inverse"))?;
                if !capability(prompt,inverse) {return Err(err("INVALID_COMPENSATION","incompatible inverse provider"));}
                item.insert("undo".into(),inverse["inverse"].clone());
            }
            _=>{}
        }
        result.push(Value::Object(item));
    }
    Ok(json!(result))
}
pub fn validate_tree(prompt:&Value,tree:&Value,catalog:&Value) -> Result<(),SomError> {
    validate(prompt)?;
    object(tree,&["name","steps","result"],"flow plan")?;
    let mut reconstructed=tree.clone();
    reconstructed["steps"]=reconstruct(prompt,&tree["steps"],catalog)?;
    let mut expected=prompt["flow"].clone(); expected.as_object_mut().unwrap().remove("inputs");
    if canonical(&reconstructed)!=canonical(&expected) {return Err(err("INVALID_COMPOSITION","flow semantic obligations differ"));}
    Ok(())
}
pub fn selected_ids(tree:&Value) -> BTreeSet<String> {
    fn walk(nodes:&Value,out:&mut BTreeSet<String>) {
        if let Some(nodes)=nodes.as_array() {
            for node in nodes {
                if let Some(id)=node["id"].as_str() {out.insert(id.into());}
                if node.get("steps").is_some() {walk(&node["steps"],out);}
                else if node.get("then").is_some() {walk(&node["then"],out);walk(&node["else"],out);}
                else if let Some(undo)=node["undo"].as_str() {out.insert(undo.into());}
            }
        }
    }
    let mut result=BTreeSet::new(); walk(&tree["steps"],&mut result); result
}
pub fn validate_plan(prompt:&Value,dsl:&Value) -> Result<(),SomError> {
    validate_plan_with(prompt, dsl, &CATALOG)
}
pub fn validate_plan_with(prompt:&Value,dsl:&Value,metadata:&Value) -> Result<(),SomError> {
    validate(prompt)?;
    object(dsl,&["version","files","flow"],"DSL v3")?;
    if dsl["version"]!=DSL_VERSION {return Err(err("UNSUPPORTED_VERSION","expected DSL v3"));}
    validate_tree(prompt,&dsl["flow"],&metadata["flow_blocks"])?;
    composed::validate_plan_with(&base(prompt),&file_symbols(dsl)?,metadata)?;
    let emitted:BTreeSet<String>=dsl["files"].as_array().unwrap().iter().flat_map(|f|f["blocks"].as_array().unwrap()).filter_map(|b|b["id"].as_str().filter(|id|id.starts_with("flow.")).map(str::to_owned)).collect();
    if emitted!=selected_ids(&dsl["flow"]) {return Err(err("INVALID_REF","selected flow helpers missing or extra"));}
    Ok(())
}
pub(crate) fn file_symbols(dsl:&Value)->Result<Value,SomError> {
    let mut lowered=json!({"version":"som.dsl.v2","files":dsl["files"]});
    for file in lowered["files"].as_array_mut().ok_or_else(||err("INVALID_DSL","files must be a list"))? {
        for block in file["blocks"].as_array_mut().ok_or_else(||err("INVALID_DSL","file blocks must be a list"))? {
            let refs=block["refs"].as_array().ok_or_else(||err("INVALID_REF","program symbols must be a list"))?;
            let mut symbols=vec![];
            for reference in refs {
                let symbol=reference.as_object().filter(|m|m.len()==1).and_then(|m|m.get("symbol")).and_then(Value::as_str).ok_or_else(||err("INVALID_REF","explicit program symbol required"))?;
                symbols.push(json!(symbol));
            }
            block["refs"]=json!(symbols);
        }
    }
    Ok(lowered)
}
pub fn result_types(prompt: &Value) -> Result<BTreeMap<String, String>, SomError> {
    validate(prompt)?;
    let inputs = prompt["flow"]["inputs"].as_array().unwrap();
    let mut checker = Checker {
        prompt,
        inputs: inputs.iter().map(|item| (
            item["name"].as_str().unwrap().to_owned(),
            item["type"].as_str().unwrap().to_owned(),
        )).collect(),
        names: inputs.iter().map(|item| item["name"].as_str().unwrap().to_owned()).collect(),
        authorized: false, actions: 0, branches: 0, transactions: 0,
    };
    let visible = checker.walk(&prompt["flow"]["steps"], &BTreeMap::new(), 0, false)?;
    prompt["flow"]["result"].as_object().unwrap().iter().map(|(name, reference)| {
        Ok((name.clone(), ref_type(prompt, reference, &checker.inputs, &visible)?))
    }).collect()
}
pub fn resolve(prompt:&Value,dsl:&Value) -> Result<Value,SomError> {
    validate_plan(prompt,dsl)?;
    let mut values=composed::values(&base(prompt))?;
    values.insert("dsl.flow".into(),json!(canonical(&dsl["flow"])));
    values.insert("flow.inputs".into(),prompt["flow"]["inputs"].clone());
    // Preserve field order in workflow CSV headers, without ordered-map features.
    let pairs:Vec<String>=prompt["resources"]["child"]["fields"].as_array().unwrap().iter().map(|f|format!("{}:{}",serde_json::to_string(&f["name"]).unwrap(),serde_json::to_string(&f["type"]).unwrap())).collect();
    values.insert("flow.custom_fields".into(),json!(format!("{{{}}}",pairs.join(","))));
    values.insert("flow.versioned".into(),json!(selected_ids(&dsl["flow"]).iter().any(|id|id.ends_with(".object.put"))));
    let mut lowered=file_symbols(dsl)?;
    for file in lowered["files"].as_array_mut().unwrap() {
        file["path"]=json!(file["path"].as_str().unwrap().replace("{package}",prompt["package"].as_str().unwrap()).replace("{migration.revision}",prompt["migration"]["revision"].as_str().unwrap()));
        for block in file["blocks"].as_array_mut().unwrap() {
            let id=block["id"].clone();
            for value in block["params"].as_object_mut().unwrap().values_mut() {
                let binding=value["$ref"].as_str().ok_or_else(||err("INVALID_REF","unknown prompt binding"))?;
                *value=if binding=="block.id" {id.clone()} else {values.get(binding).ok_or_else(||err("INVALID_REF","unknown prompt binding"))?.clone()};
            }
        }
    }
    Ok(lowered)
}
pub fn input_fields(inputs:&Value,parent:&Value,child:&Value) -> Result<Vec<BTreeMap<String,String>>,SomError> {
    let parent=ident(parent,"parent name")?; let child=ident(child,"child name")?;
    let inputs=inputs.as_array().filter(|a|a.len()<=8).ok_or_else(||err("INVALID_VALUE","invalid flow inputs"))?;
    let mut seen=BTreeSet::new(); let mut out=vec![];
    for input in inputs {
        object(input,&["name","type"],"input field")?; let n=name(&input["name"])?;
        if !seen.insert(n.clone()) {return Err(err("INVALID_VALUE","duplicate flow input"));}
        let kind=match input["type"].as_str().unwrap_or("") {
            "string"=>"Annotated[str, Field(strict=True, max_length=1048576)]".into(), "integer"=>"Annotated[int, Field(strict=True, ge=-(2**31), lt=2**31)]".into(),
            "float"=>"Annotated[float, Field(strict=True, allow_inf_nan=False)]".into(), "boolean"=>"Annotated[bool, Field(strict=True)]".into(), "date"=>"date".into(),
            "parent_input"=>format!("{parent}Create"), "parent_patch"=>format!("{parent}Patch"),
            "child_input"=>format!("{child}Create"), "child_patch"=>format!("{child}Patch"),
            "children_input"=>format!("Annotated[list[{child}Create], Field(min_length=1, max_length=20)]"),
            _=>return Err(err("INVALID_VALUE","invalid flow input type")),
        };
        out.push(BTreeMap::from([("name".into(),n),("input_type".into(),kind)]));
    }
    Ok(out)
}

pub fn semantic_view(prompt: &Value) -> Result<Value, SomError> {
    validate(prompt)?;
    let inputs: BTreeMap<String, String> = prompt["flow"]["inputs"].as_array().unwrap().iter().enumerate().map(|(i,v)|(v["name"].as_str().unwrap().into(),format!("i{i}"))).collect();
    let input_types:BTreeMap<String,String>=prompt["flow"]["inputs"].as_array().unwrap().iter().map(|v|(v["name"].as_str().unwrap().into(),v["type"].as_str().unwrap().into())).collect();
    fn collect(prompt:&Value,nodes:&Value,inputs:&BTreeMap<String,String>,names:&mut BTreeMap<String,String>,kinds:&mut BTreeMap<String,String>) -> Result<(),SomError> {
        for node in nodes.as_array().unwrap() {
            let n=node["name"].as_str().unwrap(); names.insert(n.into(),format!("n{}",names.len()));
            match node["op"].as_str().unwrap() {
                "transaction"=>collect(prompt,&node["steps"],inputs,names,kinds)?,
                "if"=>{
                    collect(prompt,&node["then"],inputs,names,kinds)?;collect(prompt,&node["else"],inputs,names,kinds)?;
                    for (n,b) in node["exports"].as_object().unwrap() {
                        names.insert(n.clone(),format!("n{}",names.len()));
                        let kind=ref_type(prompt,&b["then"],inputs,kinds)?; kinds.insert(n.clone(),kind);
                    }
                }
                op=>{kinds.insert(n.into(),output_type(op).into());}
            }
        }
        Ok(())
    }
    let mut names=BTreeMap::new(); let mut kinds=BTreeMap::new();
    collect(prompt,&prompt["flow"]["steps"],&input_types,&mut names,&mut kinds)?;
    let fields:BTreeMap<String,BTreeMap<String,String>>=["parent","child"].into_iter().map(|role|(role.into(),prompt["resources"][role]["fields"].as_array().unwrap().iter().enumerate().map(|(i,f)|(f["name"].as_str().unwrap().into(),format!("f{i}"))).collect())).collect();
    struct Projection<'a> { prompt:&'a Value, inputs:&'a BTreeMap<String,String>, names:&'a BTreeMap<String,String>, kinds:&'a BTreeMap<String,String>, fields:&'a BTreeMap<String,BTreeMap<String,String>> }
    impl Projection<'_> {
        fn normalize(&self,value:&Value) -> Value {
            if let Some(list)=value.as_array() {return json!(list.iter().map(|v|self.normalize(v)).collect::<Vec<_>>());}
            let Some(map)=value.as_object() else {return value.clone();};
            if map.len()==2 && map.contains_key("step") && map.contains_key("field") {
                let step=map["step"].as_str().unwrap(); let kind=&self.kinds[step];
                let mut role=kind.split('_').next().unwrap();
                let selected=map["field"].as_str().unwrap();
                let mut parts:Vec<String>=if selected.is_empty() {vec![]} else {selected.split('.').map(str::to_owned).collect()};
                if kind=="batch" && parts.first().is_some_and(|p|p=="parent") {
                    role="parent";
                    for part in &mut parts[1..] {if let Some(field)=self.fields[role].get(part) {*part=field.clone();}}
                } else if let Some(fields)=self.fields.get(role) {
                    for part in &mut parts {
                        if role=="child" && self.prompt["relation"]["foreign_key"]==*part {*part="parent_fk".into();}
                        else if let Some(field)=fields.get(part) {*part=field.clone();}
                    }
                }
                return json!({"step":self.names[step],"field":parts.join(".")});
            }
            let mut out=serde_json::Map::new();
            for (key,item) in map {
                let value=match key.as_str() {
                    "literal"=>json!("typed_literal"),
                    "input"=>json!(self.inputs.get(item.as_str().unwrap()).map(String::as_str).unwrap_or(item.as_str().unwrap())),
                    "step"=>json!(self.names.get(item.as_str().unwrap()).map(String::as_str).unwrap_or(item.as_str().unwrap())),
                    "name"=>{
                        if ["type","required","unique"].iter().all(|k|map.contains_key(*k)) {item.clone()}
                        else {json!(self.names.get(item.as_str().unwrap()).or_else(||self.inputs.get(item.as_str().unwrap())).map(String::as_str).unwrap_or("resource"))}
                    }
                    "package"|"table"|"path"|"foreign_key"|"revision"|"down_revision"|"flow_name"=>json!(key),
                    "exports"=>json!(item.as_object().unwrap().iter().map(|(n,b)|(self.names[n].clone(),self.normalize(b))).collect::<BTreeMap<_,_>>()),
                    "result"=>{
                        let mut bindings:Vec<Value>=item.as_object().unwrap().values().map(|b|self.normalize(b)).collect();
                        bindings.sort_by_key(|v|serde_json::to_string(v).unwrap());
                        json!(bindings.into_iter().enumerate().map(|(i,b)|(format!("r{i}"),b)).collect::<BTreeMap<_,_>>())
                    }
                    _=>self.normalize(item),
                };
                out.insert(key.clone(),value);
            }
            Value::Object(out)
        }
    }
    let projection=Projection {prompt,inputs:&inputs,names:&names,kinds:&kinds,fields:&fields};
    let mut result=prompt.clone(); result["flow"]["name"]=json!("flow_name");
    for role in ["parent","child"] {
        for (i,definition) in result["resources"][role]["fields"].as_array_mut().unwrap().iter_mut().enumerate() {definition["name"]=json!(format!("f{i}"));}
        for key in ["filter_field","date_field"] {
            if let Some(field)=result["resources"][role]["query"][key].as_str().and_then(|n|fields[role].get(n)) {result["resources"][role]["query"][key]=json!(field);}
        }
    }
    for key in ["value_field","group_field"] {
        if let Some(field)=result["capabilities"][key].as_str().and_then(|n|fields["child"].get(n)) {result["capabilities"][key]=json!(field);}
    }
    Ok(projection.normalize(&result))
}

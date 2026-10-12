//! Exact structural decoding. All scalar bindings come from checked input.
use crate::{SomError,flow};
use serde_json::{Value,json};
use std::collections::BTreeSet;

pub const VERSION:&str="som.flow.codec.v1";
pub const COMPACT_VERSION:&str="som.flow.compact-codec.v1";
pub const LIMIT:usize=2048;
const FIXED:&str="version files flow path blocks id name params refs $ref steps condition then else exports result input step field literal type args undo op inputs package resources parent child relation foreign_key on_delete operations scope action capabilities database auth storage workflow value_field group_field migration revision down_revision contract sha256 fields required unique default query filter_field date_field max_limit eq ne lt le gt ge is_null authorize transaction if delete_created restore_patch key body parent_id child_id data children csv create get list patch delete batch_create string integer float boolean date bytes str int bool parent_input child_input parent_patch child_patch children_input parent_row child_row parent_rows child_rows identity object batch json workflow sub role size sha256 id created_at updated_at resource typed_literal flow_name table path foreign_key revision down_revision parent_fk";
fn err(code:&'static str,detail:&str)->SomError {SomError::new(code,detail)}
pub fn pool(prompt:&Value,normalized:bool)->Result<Vec<Value>,SomError> {
    pool_with(prompt, normalized, &flow::CATALOG)
}
pub fn pool_with(prompt:&Value,normalized:bool,metadata:&Value)->Result<Vec<Value>,SomError> {
    flow::validate(prompt)?;
    let mut result = static_pool_with(metadata);
    let mut seen:BTreeSet<String>=result.iter().map(|v|serde_json::to_string(v).unwrap()).collect();
    fn visit(value:&Value,result:&mut Vec<Value>,seen:&mut BTreeSet<String>) {
        match value {
            Value::Object(map)=>for (key,value) in map {visit(&json!(key),result,seen);visit(value,result,seen);},
            Value::Array(list)=>for item in list {visit(item,result,seen);},
            _=>if seen.insert(serde_json::to_string(value).unwrap()) {result.push(value.clone());},
        }
    }
    if !normalized {visit(prompt,&mut result,&mut seen);}
    visit(&flow::semantic_view(prompt)?,&mut result,&mut seen);
    if result.len()>1016 {return Err(err("SCOPE_TOO_LARGE","scalar binding table exceeds vocabulary"));}
    Ok(result)
}

fn static_pool_with(metadata: &Value) -> Vec<Value> {
    let mut fixed:BTreeSet<String>=FIXED.split_whitespace().map(str::to_owned).collect();
    fixed.extend([flow::VERSION,flow::DSL_VERSION,flow::CONTRACT,"som.dsl.v2","symbol","postgresql","mongodb","jwt","firebase","google_oauth","none","s3","gcs","json_export","gzip_export","csv","decimal_summary","statistics","restrict"].into_iter().map(str::to_owned));
    for (prefix,count) in [("n",32),("i",8),("f",8),("r",8)] {for i in 0..count {fixed.insert(format!("{prefix}{i}"));}}
    fixed.extend(metadata["blocks"].as_object().unwrap().keys().cloned());
    fixed.extend(metadata["slots"].as_object().unwrap().values().map(|v|v.as_str().unwrap().to_owned()));
    for entry in metadata["blocks"].as_object().unwrap().values() {
        fixed.insert(entry["provides"].as_str().unwrap().split_once(':').unwrap().1.into());
        for (key,binding) in entry["bindings"].as_object().unwrap() {fixed.insert(key.clone());fixed.insert(binding["$ref"].as_str().unwrap().into());}
    }
    let mut result=vec![Value::Null,json!(false),json!(true)]; result.extend(fixed.into_iter().map(Value::String));
    result
}
fn serialize(value:&Value,pool:&[Value],out:&mut Vec<String>)->Result<(),SomError> {
    match value {
        Value::Object(map)=>{
            out.push("<map>".into());
            for (key,value) in map {serialize(&json!(key),pool,out)?;serialize(value,pool,out)?;}
            out.push("</map>".into());
        }
        Value::Array(list)=>{
            out.push("<list>".into());for value in list {serialize(value,pool,out)?;}out.push("</list>".into());
        }
        _=>{
            let key=serde_json::to_string(value).unwrap();
            let index=pool.iter().position(|v|serde_json::to_string(v).unwrap()==key).ok_or_else(||err("INVALID_REF","unknown scalar binding"))?;
            out.push(format!("<v:{index}>"));
        }
    }
    Ok(())
}
pub const M3_INPUT_VERSION: &str = "som.flow.m3-input.v1";
pub const M3_INPUT_TEXT: &str = include_str!("../contracts/m3-input-order.v1.json");
pub const M3_SEMANTIC_INPUT_VERSION: &str = "som.flow.m3-input.v2";
pub const M3_SEMANTIC_INPUT_TEXT: &str = include_str!("../contracts/m3-input-semantics.v2.json");
pub const M3_BOUND_INPUT_VERSION: &str = "som.flow.m3-input.v3";
pub const M3_BOUND_INPUT_TEXT: &str = include_str!("../contracts/m3-input-bindings.v3.json");
const SEMANTIC_ESCAPE: usize = 759;
const SEMANTIC_BYTE_START: usize = 760;

fn semantic_pool_with(metadata: &Value) -> Result<Vec<Value>, SomError> {
    let mut values = static_pool_with(metadata);
    let known = values.iter().filter_map(Value::as_str).collect::<BTreeSet<_>>();
    let mut extra = ["", "parent.id", "authorize", "batch.create", "child.create",
        "child.get", "child.list", "child.patch", "object.get", "object.put",
        "parent.create", "parent.get", "parent.list", "parent.patch",
        "workflow.import", "workflow.run"].into_iter().map(str::to_owned).collect::<BTreeSet<_>>();
    for index in 0..8 { extra.insert(format!("parent.f{index}")); }
    let additions = extra.into_iter().filter(|item| !known.contains(item.as_str()))
        .map(Value::String).collect::<Vec<_>>();
    values.extend(additions);
    if values.len() > SEMANTIC_ESCAPE {
        return Err(err("SCOPE_TOO_LARGE", "semantic vocabulary overlaps byte tokens"));
    }
    Ok(values)
}

fn serialize_semantic(value: &Value, pool: &[Value], out: &mut Vec<String>) -> Result<(), SomError> {
    let indices = pool.iter().enumerate().map(|(index, item)|
        (serde_json::to_string(item).unwrap(), index)).collect::<std::collections::BTreeMap<_, _>>();
    serialize_semantic_indexed(value, &indices, out)
}

fn serialize_semantic_indexed(value: &Value, indices: &std::collections::BTreeMap<String, usize>, out: &mut Vec<String>) -> Result<(), SomError> {
    match value {
        Value::Object(map) => {
            out.push("<map>".into());
            for (key, item) in map {
                serialize_semantic_indexed(&json!(key), indices, out)?;
                serialize_semantic_indexed(item, indices, out)?;
            }
            out.push("</map>".into());
        }
        Value::Array(items) => {
            out.push("<list>".into());
            for item in items { serialize_semantic_indexed(item, indices, out)?; }
            out.push("</list>".into());
        }
        _ => {
            let spelling = serde_json::to_string(value).unwrap();
            if let Some(index) = indices.get(&spelling) {
                out.push(format!("<v:{index}>"));
            } else {
                out.extend(["<list>".into(), format!("<v:{SEMANTIC_ESCAPE}>")]);
                out.extend(spelling.bytes().map(|byte| format!("<v:{}>", SEMANTIC_BYTE_START + usize::from(byte))));
                out.push("</list>".into());
            }
        }
    }
    Ok(())
}

pub fn input_policy_text(version: &str) -> Option<&'static str> {
    match version {
        M3_INPUT_VERSION => Some(M3_INPUT_TEXT),
        M3_SEMANTIC_INPUT_VERSION => Some(M3_SEMANTIC_INPUT_TEXT),
        M3_BOUND_INPUT_VERSION => Some(M3_BOUND_INPUT_TEXT),
        _ => None,
    }
}

pub fn features_m3_with(prompt: &Value, metadata: &Value) -> Result<String, SomError> {
    let original = features_with(prompt, metadata)?;
    let values = pool_with(prompt, true, metadata)?;
    let view = flow::semantic_view(prompt)?;
    let mut old_root = Vec::new();
    serialize(&view, &values, &mut old_root)?;
    let prefix = format!("<input>{}", old_root.concat());
    let suffix = original.strip_prefix(&prefix)
        .ok_or_else(|| err("INVALID_SCHEMA", "legacy flow input prefix differs"))?;
    let mut tokens = vec!["<input>".into(), "<map>".into()];
    let map = view.as_object().unwrap();
    for key in ["capabilities", "operations"] {
        serialize(&json!(key), &values, &mut tokens)?;
        serialize(&map[key], &values, &mut tokens)?;
    }
    for (key, value) in map {
        if key != "capabilities" && key != "operations" {
            serialize(&json!(key), &values, &mut tokens)?;
            serialize(value, &values, &mut tokens)?;
        }
    }
    tokens.push("</map>".into());
    Ok(tokens.concat() + suffix)
}

pub fn features(prompt:&Value)->Result<String,SomError> {
    features_with(prompt, &flow::CATALOG)
}
pub fn features_with(prompt:&Value,metadata:&Value)->Result<String,SomError> {
    let values=pool_with(prompt,true,metadata)?;let outputs=pool_with(prompt,false,metadata)?;
    fn binding(kind:&str,slot:&str,value:&Value,values:&[Value],outputs:&[Value],out:&mut Vec<String>)->Result<(),SomError> {
        out.push("<list>".into());serialize(&json!(kind),values,out)?;serialize(&json!(slot),values,out)?;serialize(value,outputs,out)?;out.push("</list>".into());Ok(())
    }
    let mut out=vec!["<input>".into()];serialize(&flow::semantic_view(prompt)?,&values,&mut out)?;out.push("<list>".into());
    binding("flow","flow_name",&prompt["flow"]["name"],&values,&outputs,&mut out)?;
    for (i,input) in prompt["flow"]["inputs"].as_array().unwrap().iter().enumerate() {binding("input",&format!("i{i}"),&input["name"],&values,&outputs,&mut out)?;}
    fn nodes(items:&Value,index:&mut usize,values:&[Value],outputs:&[Value],out:&mut Vec<String>)->Result<(),SomError> {
        for node in items.as_array().unwrap() {
            binding("step",&format!("n{index}"),&node["name"],values,outputs,out)?;*index+=1;
            match node["op"].as_str().unwrap() {
                "transaction"=>nodes(&node["steps"],index,values,outputs,out)?,
                "if"=>{
                    nodes(&node["then"],index,values,outputs,out)?;nodes(&node["else"],index,values,outputs,out)?;
                    for name in node["exports"].as_object().unwrap().keys() {binding("step",&format!("n{index}"),&json!(name),values,outputs,out)?;*index+=1;}
                }
                _=>{}
            }
        }
        Ok(())
    }
    nodes(&prompt["flow"]["steps"],&mut 0,&values,&outputs,&mut out)?;
    for role in ["parent","child"] {for (i,field) in prompt["resources"][role]["fields"].as_array().unwrap().iter().enumerate() {binding(role,&format!("f{i}"),&field["name"],&values,&outputs,&mut out)?;}}
    binding("relation","parent_fk",&prompt["relation"]["foreign_key"],&values,&outputs,&mut out)?;
    fn constants(value:&Value,values:&[Value],outputs:&[Value],out:&mut Vec<String>)->Result<(),SomError> {
        match value {
            Value::Object(map)=>{
                if map.len()==2 && map.contains_key("literal") && map.contains_key("type") {binding("literal",map["type"].as_str().unwrap(),&map["literal"],values,outputs,out)?;}
                else {for value in map.values() {constants(value,values,outputs,out)?;}}
            }
            Value::Array(list)=>for item in list {constants(item,values,outputs,out)?;},
            _=>{}
        }
        Ok(())
    }
    constants(&prompt["flow"],&values,&outputs,&mut out)?;
    for (name,reference) in prompt["flow"]["result"].as_object().unwrap() {
        let mut copy=prompt.clone();copy["flow"]["result"]=json!({name:reference});
        out.push("<list>".into());serialize(&json!("result"),&values,&mut out)?;
        serialize(&flow::semantic_view(&copy)?["flow"]["result"]["r0"],&values,&mut out)?;
        serialize(&json!(name),&outputs,&mut out)?;out.push("</list>".into());
    }
    out.extend(["</list>".into(),"<plan>".into()]);
    if out.len()>LIMIT {return Err(err("SCOPE_TOO_LARGE","flow input exceeds 2048 tokens"));}Ok(out.concat())
}
fn features_semantic_base(prompt:&Value,metadata:&Value)->Result<String,SomError> {
    let values=semantic_pool_with(metadata)?;let outputs=pool_with(prompt,false,metadata)?;
    fn binding(kind:&str,slot:&str,value:&Value,values:&[Value],outputs:&[Value],out:&mut Vec<String>)->Result<(),SomError> {
        out.push("<list>".into());serialize_semantic(&json!(kind),values,out)?;serialize_semantic(&json!(slot),values,out)?;serialize(value,outputs,out)?;out.push("</list>".into());Ok(())
    }
    let mut out=vec!["<input>".into()];serialize_semantic(&flow::semantic_view(prompt)?,&values,&mut out)?;out.push("<list>".into());
    binding("flow","flow_name",&prompt["flow"]["name"],&values,&outputs,&mut out)?;
    for (i,input) in prompt["flow"]["inputs"].as_array().unwrap().iter().enumerate() {binding("input",&format!("i{i}"),&input["name"],&values,&outputs,&mut out)?;}
    fn nodes(items:&Value,index:&mut usize,values:&[Value],outputs:&[Value],out:&mut Vec<String>)->Result<(),SomError> {
        for node in items.as_array().unwrap() {
            binding("step",&format!("n{index}"),&node["name"],values,outputs,out)?;*index+=1;
            match node["op"].as_str().unwrap() {
                "transaction"=>nodes(&node["steps"],index,values,outputs,out)?,
                "if"=>{
                    nodes(&node["then"],index,values,outputs,out)?;nodes(&node["else"],index,values,outputs,out)?;
                    for name in node["exports"].as_object().unwrap().keys() {binding("step",&format!("n{index}"),&json!(name),values,outputs,out)?;*index+=1;}
                }
                _=>{}
            }
        }
        Ok(())
    }
    nodes(&prompt["flow"]["steps"],&mut 0,&values,&outputs,&mut out)?;
    for role in ["parent","child"] {for (i,field) in prompt["resources"][role]["fields"].as_array().unwrap().iter().enumerate() {binding(role,&format!("f{i}"),&field["name"],&values,&outputs,&mut out)?;}}
    binding("relation","parent_fk",&prompt["relation"]["foreign_key"],&values,&outputs,&mut out)?;
    fn constants(value:&Value,values:&[Value],outputs:&[Value],out:&mut Vec<String>)->Result<(),SomError> {
        match value {
            Value::Object(map)=>{
                if map.len()==2 && map.contains_key("literal") && map.contains_key("type") {binding("literal",map["type"].as_str().unwrap(),&map["literal"],values,outputs,out)?;}
                else {for value in map.values() {constants(value,values,outputs,out)?;}}
            }
            Value::Array(list)=>for item in list {constants(item,values,outputs,out)?;},
            _=>{}
        }
        Ok(())
    }
    constants(&prompt["flow"],&values,&outputs,&mut out)?;
    for (name,reference) in prompt["flow"]["result"].as_object().unwrap() {
        let mut copy=prompt.clone();copy["flow"]["result"]=json!({name:reference});
        out.push("<list>".into());serialize_semantic(&json!("result"),&values,&mut out)?;
        serialize_semantic(&flow::semantic_view(&copy)?["flow"]["result"]["r0"],&values,&mut out)?;
        serialize(&json!(name),&outputs,&mut out)?;out.push("</list>".into());
    }
    out.extend(["</list>".into(),"<plan>".into()]);
    if out.len()>LIMIT {return Err(err("SCOPE_TOO_LARGE","flow input exceeds 2048 tokens"));}Ok(out.concat())
}
pub fn features_m3_semantic_with(prompt: &Value, metadata: &Value) -> Result<String, SomError> {
    let original = features_semantic_base(prompt, metadata)?;
    let values = semantic_pool_with(metadata)?;
    let view = flow::semantic_view(prompt)?;
    let mut old_root = Vec::new();
    serialize_semantic(&view, &values, &mut old_root)?;
    let prefix = format!("<input>{}", old_root.concat());
    let suffix = original.strip_prefix(&prefix)
        .ok_or_else(|| err("INVALID_SCHEMA", "semantic flow input prefix differs"))?;
    let mut tokens = vec!["<input>".into(), "<map>".into()];
    let map = view.as_object().unwrap();
    for key in ["capabilities", "operations"] {
        serialize_semantic(&json!(key), &values, &mut tokens)?;
        serialize_semantic(&map[key], &values, &mut tokens)?;
    }
    for (key, value) in map {
        if key != "capabilities" && key != "operations" {
            serialize_semantic(&json!(key), &values, &mut tokens)?;
            serialize_semantic(value, &values, &mut tokens)?;
        }
    }
    tokens.push("</map>".into());
    let result = tokens.concat() + suffix;
    if result.matches('<').count() > LIMIT {
        return Err(err("SCOPE_TOO_LARGE", "flow input exceeds 2048 tokens"));
    }
    Ok(result)
}

pub fn features_m3_bound_with(prompt: &Value, metadata: &Value) -> Result<String, SomError> {
    let original = features_m3_semantic_with(prompt, metadata)?;
    let end = "</list><plan>";
    let prefix = original.strip_suffix(end)
        .ok_or_else(|| err("INVALID_SCHEMA", "semantic binding suffix differs"))?;
    fn collect(value: &Value, prompt: &Value, bindings: &mut std::collections::BTreeMap<String, String>) -> Result<(), SomError> {
        match value {
            Value::Object(map) if map.len() == 2 && map.contains_key("step") && map.contains_key("field") => {
                let field = map["field"].as_str().unwrap();
                if let Some((role, name)) = field.split_once('.') {
                    let fields = prompt["resources"][role]["fields"].as_array()
                        .ok_or_else(|| err("INVALID_REF", "unsupported compound selector"))?;
                    let slot = fields.iter().position(|item| item["name"] == name)
                        .map(|index| format!("f{index}")).unwrap_or_else(|| name.to_owned());
                    bindings.insert(format!("{role}.{slot}"), field.to_owned());
                }
            }
            Value::Object(map) => for item in map.values() { collect(item, prompt, bindings)?; },
            Value::Array(items) => for item in items { collect(item, prompt, bindings)?; },
            _ => {}
        }
        Ok(())
    }
    let mut bindings = std::collections::BTreeMap::new();
    collect(&prompt["flow"], prompt, &mut bindings)?;
    let values = semantic_pool_with(metadata)?;
    let outputs = pool_with(prompt, false, metadata)?;
    let mut tokens = Vec::new();
    for (slot, value) in bindings {
        tokens.push("<list>".into());
        serialize_semantic(&json!("field"), &values, &mut tokens)?;
        serialize_semantic(&json!(slot), &values, &mut tokens)?;
        serialize(&json!(value), &outputs, &mut tokens)?;
        tokens.push("</list>".into());
    }
    let result = prefix.to_owned() + &tokens.concat() + end;
    if result.matches('<').count() > LIMIT {
        return Err(err("SCOPE_TOO_LARGE", "flow input exceeds 2048 tokens"));
    }
    Ok(result)
}

#[cfg(test)]
mod semantic_input_tests {
    use super::*;

    fn collision_pair() -> (Value, Value) {
        let first_line = include_str!("../../som-core/data/flow-v7-r10/dev.jsonl")
            .lines().next().unwrap();
        let mut first = serde_json::from_str::<Value>(first_line).unwrap()["prompt"].clone();
        first["capabilities"]["workflow"] = json!("json_export");
        first["capabilities"]["value_field"] = Value::Null;
        first["capabilities"]["group_field"] = Value::Null;
        first["flow"] = json!({"name":"inspect",
            "inputs":[{"name":"identifier","type":"integer"}],
            "steps":[{"name":"access","op":"authorize","args":{},"undo":null},
                {"name":"read","op":"child.list","undo":null,
                    "args":{"parent_id":{"input":"identifier"}}}],
            "result":{"value":{"input":"identifier"}}});
        let mut second = first.clone();
        second["flow"]["steps"][1]["op"] = json!("workflow.run");
        (first, second)
    }

    #[test]
    fn semantic_inputs_distinguish_the_proven_operator_collision() {
        let (first, second) = collision_pair();
        let catalog = &*flow::COMPACT_CATALOG;
        assert_eq!(features_m3_with(&first, catalog).unwrap(),
            features_m3_with(&second, catalog).unwrap());
        assert_ne!(features_m3_semantic_with(&first, catalog).unwrap(),
            features_m3_semantic_with(&second, catalog).unwrap());
    }

    #[test]
    fn compound_destinations_distinguish_literal_name_collisions() {
        use std::io::BufRead;
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../som-core/data/flow-m3-r9/train.jsonl");
        let line = std::io::BufReader::new(std::fs::File::open(path).unwrap())
            .lines().next().unwrap().unwrap();
        let mut first = serde_json::from_str::<Value>(&line).unwrap()["prompt"].clone();
        first["flow"] = json!({"name":"inspect", "inputs":[
            {"name":"parent_data","type":"parent_input"},
            {"name":"children","type":"children_input"}],
            "steps":[{"name":"n0","op":"authorize","args":{},"undo":null},
                {"name":"n1","op":"transaction","steps":[
                    {"name":"n2","op":"batch.create","undo":"delete_created",
                        "args":{"parent":{"input":"parent_data"},"children":{"input":"children"}}}]}],
            "result":{"body":{"literal":"parent.title","type":"string"},
                "data":{"step":"n2","field":"parent.title"},
                "size":{"step":"n2","field":"parent.memo"}}});
        let mut second = first.clone();
        second["flow"]["result"]["body"]["literal"] = json!("parent.memo");
        let catalog = &*flow::COMPACT_CATALOG;
        assert_eq!(features_m3_semantic_with(&first, catalog).unwrap(),
            features_m3_semantic_with(&second, catalog).unwrap());
        assert_ne!(features_m3_bound_with(&first, catalog).unwrap(),
            features_m3_bound_with(&second, catalog).unwrap());
    }

    #[test]
    fn semantic_words_and_unknown_bytes_do_not_share_a_scalar_index() {
        let values = semantic_pool_with(&flow::COMPACT_CATALOG).unwrap();
        assert!(values.len() <= SEMANTIC_ESCAPE);
        for (first, second) in [(json!("parent.f3"), json!("parent.f4")),
                (json!("unknown-one"), json!("unknown-two")),
                (json!("字"), json!("串"))] {
            let mut left = Vec::new();
            let mut right = Vec::new();
            serialize_semantic(&first, &values, &mut left).unwrap();
            serialize_semantic(&second, &values, &mut right).unwrap();
            assert_ne!(left, right);
        }
        let mut unknown = Vec::new();
        serialize_semantic(&json!(100), &values, &mut unknown).unwrap();
        assert_eq!(unknown, ["<list>", "<v:759>", "<v:809>", "<v:808>", "<v:808>", "</list>"]);
    }
}

pub fn encode(prompt:&Value,dsl:&Value)->Result<String,SomError> {
    encode_with(prompt, dsl, &flow::CATALOG)
}
pub fn encode_with(prompt:&Value,dsl:&Value,metadata:&Value)->Result<String,SomError> {
    flow::validate_plan_with(prompt,dsl,metadata)?;
    let mut out=vec![];serialize(dsl,&pool_with(prompt,false,metadata)?,&mut out)?;out.push("<eos>".into());
    if out.len()>LIMIT {return Err(err("SCOPE_TOO_LARGE","flow output exceeds 2048 tokens"));}Ok(out.concat())
}
pub fn decode(prompt:&Value,text:&str)->Result<Value,SomError> {
    decode_with(prompt, text, &flow::CATALOG)
}
pub fn decode_with(prompt:&Value,text:&str,metadata:&Value)->Result<Value,SomError> {
    let pool=pool_with(prompt,false,metadata)?;
    let regex=regex::Regex::new(r"<[^<>\s]+>").unwrap();
    let tokens:Vec<&str>=regex.find_iter(text).map(|m|m.as_str()).collect();
    if tokens.concat()!=text || tokens.last()!=Some(&"<eos>") {return Err(err("INVALID_DSL","malformed or incomplete flow token sequence"));}
    if tokens.len()>LIMIT {return Err(err("SCOPE_TOO_LARGE","flow output exceeds 2048 tokens"));}
    fn read(tokens:&[&str],pool:&[Value],index:&mut usize,depth:usize)->Result<Value,SomError> {
        if depth>32 || *index>=tokens.len()-1 {return Err(err("INVALID_DSL","truncated or deep flow sequence"));}
        let token=tokens[*index];*index+=1;
        if ["<map>","<list>"].contains(&token) {
            let end=if token=="<map>" {"</map>"} else {"</list>"};
            let mut list=vec![];let mut map=serde_json::Map::new();
            while *index<tokens.len()-1 && tokens[*index]!=end {
                let value=read(tokens,pool,index,depth+1)?;
                if token=="<map>" {
                    let key=value.as_str().ok_or_else(||err("INVALID_DSL","invalid map key"))?;
                    if map.contains_key(key) {return Err(err("INVALID_DSL","duplicate map key"));}
                    map.insert(key.into(),read(tokens,pool,index,depth+1)?);
                } else {list.push(value);}
            }
            if *index>=tokens.len()-1 {return Err(err("INVALID_DSL","unclosed container"));}*index+=1;
            return Ok(if token=="<map>" {Value::Object(map)} else {json!(list)});
        }
        let raw=token.strip_prefix("<v:").and_then(|s|s.strip_suffix('>')).filter(|n|!n.is_empty() && n.bytes().all(|b|b.is_ascii_digit()) && (n.len()==1 || !n.starts_with('0'))).ok_or_else(||err("INVALID_DSL","unknown structural token"))?;
        let n:usize=raw.parse().map_err(|_|err("INVALID_DSL","unknown scalar binding"))?;
        pool.get(n).cloned().ok_or_else(||err("INVALID_DSL","unknown scalar binding"))
    }
    let mut index=0;let dsl=read(&tokens,&pool,&mut index,0)?;
    if index!=tokens.len()-1 {return Err(err("INVALID_DSL","extra tokens after plan"));}
    flow::validate_plan_with(prompt,&dsl,metadata)?;Ok(dsl)
}

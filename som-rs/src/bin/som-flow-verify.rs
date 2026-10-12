//! Offline v7 parity and budget gates. Never called by production inference.
use serde_json::{Value,json};
use std::io::{self,BufRead};

fn evaluate(row:&Value,blocks:&std::collections::BTreeMap<String,som::Block>)->Result<Value,som::SomError> {
    let prompt=&row["prompt"];som::flow::validate(prompt)?;
    if row["action"]=="prompt" {return Ok(json!({"status":"ok","features":som::flow_codec::features(prompt)?,"semantic":som::flow::semantic_view(prompt)?}));}
    let dsl=if let Some(text)=row["encoded"].as_str() {som::flow_codec::decode(prompt,text)?} else {row["dsl"].clone()};
    som::flow::validate_plan(prompt,&dsl)?;
    let resolved=som::flow::resolve(prompt,&dsl)?;
    if row["action"]=="resolve" {return Ok(json!({"status":"ok","dsl":dsl,"resolved":resolved,"features":som::flow_codec::features(prompt)?,"encoded":som::flow_codec::encode(prompt,&dsl)?}));}
    if row["action"]=="diagnostic_files" {
        let path=row["formatter_model"].as_str().ok_or_else(||som::SomError{code:"INVALID_VALUE",detail:"formatter fixture missing".into()})?;
        let quality=som::quality::Quality::load(std::path::Path::new(path))?.ok_or_else(||som::SomError{code:"OUTPUT_QUALITY_FAILED",detail:"formatter fixture is not pinned".into()})?;
        let mut draft=std::collections::BTreeMap::new();
        for file in resolved["files"].as_array().unwrap() {
            let assembled=som::assemble(&json!({"version":"som.dsl.v2","files":[file]}),blocks)?;
            for (path,source) in assembled {
                let source=if path.ends_with(".py") {quality.format_source(&path,&som::quality::assemble_imports(&source),prompt["package"].as_str().unwrap())?} else {source};
                draft.insert(path,source);
            }
        }
        return Ok(json!({"status":"diagnostic","product_acceptance":false,"draft_files":draft}));
    }
    Ok(json!({"status":"ok","files":som::assemble(&resolved,blocks)?}))
}
fn main() {
    let path=std::env::args().nth(1).expect("approved blocks path");
    let blocks=som::load_blocks(std::path::Path::new(&path)).expect("block library");
    som::composed::verify_blocks_with(&blocks,&som::flow::CATALOG).expect("bound block hashes");
    for line in io::stdin().lock().lines().map_while(Result::ok) {
        let result=match serde_json::from_str::<Value>(&line) {
            Ok(row)=>match evaluate(&row,&blocks) {Ok(value)=>value,Err(error)=>json!({"status":"error","code":error.code,"detail":error.detail})},
            Err(_)=>json!({"status":"error","code":"INVALID_JSON"}),
        };
        println!("{result}");
    }
}

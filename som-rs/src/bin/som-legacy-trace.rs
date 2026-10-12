//! Capture the legacy model's raw choices on synthetic regression prompts.
use serde_json::{Value,json};
use std::io::{self,BufRead};
fn main() {
    let model=std::env::args().nth(1).expect("frozen legacy model path");
    let blocks=std::env::args().nth(2).expect("frozen blocks path");
    let decoder=som::model::Decoder::load(std::path::Path::new(&model)).expect("Metal model");
    let blocks=som::load_blocks(std::path::Path::new(&blocks)).expect("blocks");
    for line in io::stdin().lock().lines().map_while(Result::ok) {
        let prompt:Value=serde_json::from_str(&line).expect("prompt JSON");
        let result=decoder.diagnostic_operation_trace(&line).and_then(|mut trace| {
            let resolved=som::resolve_prompt_refs(&prompt,&trace["dsl"])?;
            trace["files"]=json!(som::assemble(&resolved,&blocks)?);
            trace["prompt"]=prompt;trace["status"]=json!("ok");Ok(trace)
        });
        match result {Ok(value)=>println!("{value}"),Err(error)=>println!("{}",json!({"status":"error","code":error.code,"detail":error.detail}))}
    }
}

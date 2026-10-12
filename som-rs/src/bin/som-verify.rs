//! Offline parity tool. Never used by production inference.
use serde_json::{Value, json};
use std::io::{self, BufRead};

fn run(row: &Value, blocks: &std::collections::BTreeMap<String, som::Block>, quality: Option<&som::quality::Quality>) -> Result<Value, som::SomError> {
    let p=&row["prompt"];
    som::validate_prompt(p)?;
    let dsl=if let Some(text)=row["encoded"].as_str() { som::composed::decode(text)? } else { row["dsl"].clone() };
    if p["version"]==som::composed::VERSION {
        if let Some(quality)=quality {som::composed::validate_plan_with(p,&dsl,&quality.catalog)?;}
        else {som::composed::validate_plan(p,&dsl)?;}
    }
    let resolved=som::resolve_prompt_refs(p,&dsl)?;
    let files=som::assemble(&resolved,blocks)?;
    let files=if p["version"] == som::composed::VERSION {
        if let Some(quality)=quality {quality.finalize(files)?} else {files}
    } else {files};
    Ok(json!({"status":"ok","files":files,"dsl":dsl,"features":if p["version"]==som::composed::VERSION {som::composed::features(p)?} else {String::new()}}))
}

fn main() {
    let path=std::env::args().nth(1).expect("approved blocks path");
    let blocks=som::load_blocks(std::path::Path::new(&path)).expect("block library");
    let quality=std::env::args().nth(2).map(|path|som::quality::Quality::load(std::path::Path::new(&path)).expect("quality profile")).flatten();
    let evaluate = |line: &str| {
        match serde_json::from_str::<Value>(line) {
                Ok(row)=>match run(&row,&blocks,quality.as_ref()) {Ok(v)=>v,Err(e)=>json!({"status":"error","code":e.code,"detail":e.detail})},
                Err(_)=>json!({"status":"error","code":"INVALID_JSON"}),
        }
    };
    let workers = std::env::var("SOM_VERIFY_WORKERS").ok()
        .and_then(|value| value.parse::<usize>().ok()).unwrap_or(1).clamp(1,4);
    if workers == 1 {
        for line in io::stdin().lock().lines().map_while(Result::ok) {
            println!("{}", evaluate(&line));
        }
    } else {
        // Offline batches retain input order. Production never uses this tool.
        let lines: Vec<String> = io::stdin().lock().lines().map_while(Result::ok).collect();
        for chunk in lines.chunks(workers) {
            let values = std::thread::scope(|scope| {
                let handles: Vec<_> = chunk.iter().map(|line| {
                    let evaluate = &evaluate;
                    scope.spawn(move || evaluate(line))
                }).collect();
                handles.into_iter().map(|handle|handle.join().unwrap()).collect::<Vec<_>>()
            });
            for value in values { println!("{value}"); }
        }
    }
}

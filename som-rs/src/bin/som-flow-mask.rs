//! Read-only prefix oracle for Python/Rust mask parity.
use std::io::{self, BufRead};
use serde_json::{Value, json};
use som::{flow, flow_constraints::State};

fn main() {
    for line in io::stdin().lock().lines() {
        let result = (|| {
            let request: Value = serde_json::from_str(&line.map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
            let mut state = State::new(&request["prompt"], &flow::COMPACT_CATALOG).map_err(|e| e.code.to_owned())?;
            if let Some(tokens) = request["tokens"].as_array() {
                let mut trace = Vec::new();
                for (position, token) in tokens.iter().enumerate() {
                    let selected = token.as_u64().ok_or("token must be an integer")? as u32;
                    let allowed = state.allowed().map_err(|e| e.code.to_owned())?;
                    if !allowed.contains(&selected) {
                        return Ok(json!({"trace":trace,"code":"INVALID_DSL",
                            "position":position,"allowed":allowed}));
                    }
                    trace.push(json!({"allowed":allowed,"context":state.context()}));
                    state.advance_checked(selected).map_err(|e| e.code.to_owned())?;
                }
                return Ok(json!({"trace":trace,"allowed":state.allowed().map_err(|e|e.code.to_owned())?,
                    "complete":state.complete(),"root":state.root()}));
            }
            for token in request["prefix"].as_array().ok_or("prefix must be an array")? {
                state.advance(token.as_u64().ok_or("token must be an integer")? as u32).map_err(|e| e.code.to_owned())?;
            }
            Ok::<_, String>(json!({"allowed":state.allowed().map_err(|e|e.code.to_owned())?,
                "context":state.context(),"complete":state.complete(),"root":state.root()}))
        })();
        match result { Ok(value) => println!("{value}"), Err(code) => println!("{}",json!({"code":code})) }
    }
}

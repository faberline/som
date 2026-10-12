//! Native same-weight constrained/unconstrained control. It writes no files.
use clap::Parser;
use serde_json::{Value, json};
use std::{fs, path::PathBuf};
use som::model::Decoder;

fn error(code: &'static str, detail: impl Into<String>) -> som::SomError {
    som::SomError { code, detail: detail.into() }
}

#[derive(Parser)]
struct Args {
    #[arg(long)] model: PathBuf,
    #[arg(long)] prompt: PathBuf,
    #[arg(long)] constrained: bool,
}

fn main() {
    let args = Args::parse();
    let result = (|| {
        let prompt: Value = serde_json::from_slice(&fs::read(args.prompt)
            .map_err(|e| error("INVALID_JSON", e.to_string()))?)
            .map_err(|e| error("INVALID_JSON", e.to_string()))?;
        let model = Decoder::load(&args.model)?;
        model.warmup()?;
        let text = model.diagnostic_flow_sequence(&prompt, args.constrained)?;
        let dsl: Value = serde_json::from_str(&text).map_err(|e| error("INVALID_DSL", e.to_string()))?;
        Ok::<_, som::SomError>(json!({"status":"ok","dsl":dsl}))
    })();
    match result {
        Ok(value) => println!("{value}"),
        Err(error) => { println!("{}",json!({"status":"error","code":error.code,"detail":error.detail})); std::process::exit(2); }
    }
}

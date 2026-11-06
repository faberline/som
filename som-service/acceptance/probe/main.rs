//! Executes the generated library. All expected values live outside this crate.
use generated_client::{ClientError, DeferClient};
use serde_json::{json, Value};

fn error_record(error: ClientError) -> Value {
    let debug = format!("{error:?}");
    let display = format!("{error}");
    let mut record = match error {
        ClientError::InvalidConfiguration { reason } => json!({"kind":"InvalidConfiguration","reason":reason}),
        ClientError::InvalidInput { parameter, reason } => json!({"kind":"InvalidInput","parameter":parameter,"reason":reason}),
        ClientError::Network { timeout, status, body } => json!({"kind":"Network","timeout":timeout,"status":status,"body":body}),
        ClientError::Http { status, body, native } => json!({"kind":"Http","status":status,"body":body,"native":native}),
        ClientError::InvalidResponse { status, body } => json!({"kind":"InvalidResponse","status":status,"body":body}),
    };
    record["debug"] = json!(debug);
    record["display"] = json!(display);
    record
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cases: Vec<Value> = serde_json::from_slice(&std::fs::read(&args[1]).unwrap()).unwrap();
    let mut records = Vec::new();
    for case in cases {
        let mut record = match DeferClient::new(case["base_url"].as_str().unwrap(), case["token"].as_str()) {
            Err(error) => error_record(error),
            Ok(client) => {
                let debug = format!("{client:?}");
                let mut result = if case["configure_only"].as_bool().unwrap_or(false) {
                    json!({"kind":"Configured"})
                } else {
                    match client.read_task_status(case["queue"].as_str().unwrap(), case["task_id"].as_str().unwrap()).await {
                        Ok(value) => json!({"kind":"Ok","body":value}),
                        Err(error) => error_record(error),
                    }
                };
                result["client_debug"] = json!(debug);
                result
            }
        };
        record["id"] = case["id"].clone();
        records.push(record);
    }
    std::fs::write(&args[2], serde_json::to_vec_pretty(&records).unwrap()).unwrap();
}

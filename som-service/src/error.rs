use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceSpan {
    pub line: usize,
    pub column: usize,
    pub end_line: usize,
    pub end_column: usize,
    pub byte_start: usize,
    pub byte_end: usize,
}

#[derive(Clone, Debug)]
pub struct Error {
    pub status: u16,
    pub body: Value,
}
impl Error {
    pub fn new(code: &str, detail: impl Into<String>, pointer: &str) -> Self {
        let detail = detail.into();
        let allowed = match code {
            "UNSUPPORTED_OPERATION" => "Bind operation ALIAS to task_status.",
            "UNSUPPORTED_TARGET" => "Target rust-service-client.v1.",
            "UNSUPPORTED_CONTRACT" => {
                "Bind service defer using contract \"defer.http@5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0\"."
            }
            _ => "The pinned som.prompt.service.v1 task-status contract.",
        };
        Self::from_body(
            json!({"status":"error","code":code,"detail":detail,"diagnostics":[{
                "code":code,"cause":detail,"prompt_pointer":pointer,
                "line":1,"column":1,"end_line":1,"end_column":1,"byte_start":0,"byte_end":0,
                "allowed_form":allowed
            }]}),
        )
    }
    pub fn from_body(body: Value) -> Self {
        let status = match body["code"].as_str().unwrap_or("") {
            "UNSUPPORTED_VERSION"
            | "UNSUPPORTED_CONTRACT"
            | "UNSUPPORTED_OPERATION"
            | "UNSUPPORTED_TARGET" => 422,
            "REQUEST_TOO_LARGE" => 413,
            _ => 400,
        };
        Self { status, body }
    }
    pub fn with_span(mut self, span: &SourceSpan) -> Self {
        let value = serde_json::to_value(span).expect("span serialization");
        for field in [
            "line",
            "column",
            "end_line",
            "end_column",
            "byte_start",
            "byte_end",
        ] {
            self.body["diagnostics"][0][field] = value[field].clone();
        }
        self
    }
    pub fn with_map(self, map: &BTreeMap<String, SourceSpan>) -> Self {
        let mut pointer = self.body["diagnostics"][0]["prompt_pointer"]
            .as_str()
            .unwrap_or("")
            .to_owned();
        loop {
            if let Some(span) = map.get(&pointer) {
                return self.with_span(span);
            }
            if pointer.is_empty() {
                return self;
            }
            pointer.truncate(pointer.rfind('/').unwrap_or(0));
        }
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.body)
    }
}
impl std::error::Error for Error {}

//! SOM CNL diagnostics and the adapter from NLP syntax to typed prompts.
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
#[path = "cnl_diagnostics.rs"]
mod diagnostics;
#[path = "cnl_semantics.rs"]
mod semantics;
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct SourceSpan {
    pub line: usize,
    pub column: usize,
    pub end_line: usize,
    pub end_column: usize,
    pub byte_start: usize,
    pub byte_end: usize,
}
#[derive(Clone, Debug, Serialize)]
pub struct Diagnostic {
    pub code: String,
    #[serde(flatten)]
    pub span: SourceSpan,
    pub prompt_pointer: Option<String>,
    pub cause: String,
    pub allowed_form: String,
}
#[derive(Clone, Debug, Serialize)]
pub struct CompilerError {
    pub status: String,
    pub code: String,
    pub detail: String,
    pub diagnostics: Vec<Diagnostic>,
}
impl CompilerError {
    pub fn response(&self) -> Value {
        serde_json::to_value(self).expect("serializable error")
    }
    pub fn http_status(&self) -> u16 {
        match self.code.as_str() {
            "UNSUPPORTED_VERSION"
            | "UNSUPPORTED_CONTRACT"
            | "UNSUPPORTED_LOGIC"
            | "UNSUPPORTED_TEST"
            | "UNSUPPORTED_BLOCK"
            | "SCOPE_TOO_LARGE" => 422,
            "ASSET_UNAVAILABLE" => 503,
            "OUTPUT_QUALITY_FAILED" => 500,
            _ => 400,
        }
    }
}
impl std::fmt::Display for CompilerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.detail)
    }
}
impl std::error::Error for CompilerError {}
pub type SourceMap = BTreeMap<String, SourceSpan>;
#[derive(Clone, Debug)]
pub struct ParsedCnl {
    pub prompt: Value,
    pub source_map: SourceMap,
}
pub(crate) fn error(
    code: &str,
    span: SourceSpan,
    pointer: Option<&str>,
    cause: impl Into<String>,
    allowed: impl Into<String>,
) -> CompilerError {
    let cause = cause.into();
    CompilerError {
        status: "error".into(),
        code: code.into(),
        detail: cause.clone(),
        diagnostics: vec![Diagnostic {
            code: code.into(),
            span,
            prompt_pointer: pointer.map(str::to_owned),
            cause,
            allowed_form: allowed.into(),
        }],
    }
}
#[path = "cnl_lower.rs"]
mod lower;

pub fn parse(source: &str) -> Result<ParsedCnl, CompilerError> {
    let syntax = faberline_nlp_cnl::parse_document(source);
    lower::lower(&syntax)
}

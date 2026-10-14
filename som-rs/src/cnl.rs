//! Frozen som.cnl.v1 parser. No models, corpora, or Python production parser.
use serde::Serialize;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
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
#[derive(Clone, Debug)]
struct Token {
    text: String,
    span: SourceSpan,
}
#[derive(Clone, Debug)]
struct Line {
    tokens: Vec<Token>,
    span: SourceSpan,
    normal: String,
}
fn joined(a: &SourceSpan, b: &SourceSpan) -> SourceSpan {
    SourceSpan {
        line: a.line,
        column: a.column,
        end_line: b.end_line,
        end_column: b.end_column,
        byte_start: a.byte_start,
        byte_end: b.byte_end,
    }
}
fn lex(source: &str) -> Result<(Vec<Line>, SourceSpan), CompilerError> {
    let mut lines = vec![];
    let mut offset = 0;
    let mut eof = SourceSpan {
        line: 1,
        column: 1,
        end_line: 1,
        end_column: 1,
        byte_start: 0,
        byte_end: 0,
    };
    for (i, raw) in source.split_inclusive('\n').enumerate() {
        let lf = raw.ends_with('\n');
        let mut text = if lf { &raw[..raw.len() - 1] } else { raw };
        if lf && text.ends_with('\r') {
            text = &text[..text.len() - 1];
        }
        let sp = |a: usize, b: usize| SourceSpan {
            line: i + 1,
            column: text[..a].chars().count() + 1,
            end_line: i + 1,
            end_column: text[..b].chars().count() + 1,
            byte_start: offset + a,
            byte_end: offset + b,
        };
        if let Some(p) = text.find('\r').or_else(|| {
            if offset == 0 && text.starts_with('\u{feff}') {
                Some(0)
            } else {
                None
            }
        }) {
            return Err(error(
                "CNL_SYNTAX",
                sp(p, p + text[p..].chars().next().unwrap().len_utf8()),
                None,
                "Bare CR and BOM are not allowed.",
                "Use LF or CRLF and no BOM.",
            ));
        }
        let trim = text.trim_matches([' ', '\t']);
        let start = text.len() - text.trim_start_matches([' ', '\t']).len();
        if !trim.is_empty() {
            let end = start + trim.len();
            let whole = sp(start, end);
            if !trim.ends_with('.') {
                return Err(error(
                    "CNL_SYNTAX",
                    whole,
                    None,
                    "A sentence needs its final period.",
                    "End the complete sentence with a period.",
                ));
            }
            let body_end = end - 1;
            let mut p = start;
            let mut tokens = vec![];
            while p < body_end {
                while p < body_end && matches!(text.as_bytes()[p], b' ' | b'\t') {
                    p += 1;
                }
                if p >= body_end {
                    break;
                }
                let begin = p;
                if text.as_bytes()[p] == b'"' {
                    p += 1;
                    let mut escaped = false;
                    let mut closed = false;
                    while p < body_end {
                        let ch = text[p..].chars().next().unwrap();
                        p += ch.len_utf8();
                        if escaped {
                            escaped = false;
                        } else if ch == '\\' {
                            escaped = true;
                        } else if ch == '"' {
                            closed = true;
                            break;
                        }
                    }
                    if !closed || serde_json::from_str::<String>(&text[begin..p]).is_err() {
                        return Err(error(
                            "CNL_SYNTAX",
                            sp(begin, p),
                            None,
                            "Invalid JSON string literal.",
                            "Use a quoted JSON string with valid escapes.",
                        ));
                    }
                    if p < body_end && !matches!(text.as_bytes()[p], b' ' | b'\t') {
                        return Err(error(
                            "CNL_SYNTAX",
                            sp(begin, p),
                            None,
                            "A JSON string needs a token boundary.",
                            "Separate grammar tokens with spaces or tabs.",
                        ));
                    }
                } else {
                    while p < body_end && !matches!(text.as_bytes()[p], b' ' | b'\t') {
                        p += text[p..].chars().next().unwrap().len_utf8();
                    }
                }
                tokens.push(Token {
                    text: text[begin..p].into(),
                    span: sp(begin, p),
                });
            }
            let normal = tokens
                .iter()
                .map(|t| t.text.as_str())
                .collect::<Vec<_>>()
                .join(" ")
                + ".";
            lines.push(Line {
                tokens,
                span: whole,
                normal,
            });
        }
        let column = if lf { 1 } else { text.chars().count() + 1 };
        let line = if lf { i + 2 } else { i + 1 };
        eof = SourceSpan {
            line,
            column,
            end_line: line,
            end_column: column,
            byte_start: offset + raw.len(),
            byte_end: offset + raw.len(),
        };
        offset += raw.len();
    }
    Ok((lines, eof))
}
struct Cursor {
    line: Line,
    at: usize,
}
impl Cursor {
    fn token(&mut self) -> Result<Token, CompilerError> {
        let t = self.line.tokens.get(self.at).cloned().ok_or_else(|| {
            error(
                "CNL_SYNTAX",
                self.line.span.clone(),
                None,
                "Incomplete sentence.",
                "Use the complete fixed sentence form.",
            )
        })?;
        self.at += 1;
        Ok(t)
    }
    fn word(&mut self, w: &str) -> Result<(), CompilerError> {
        let t = self.token()?;
        if t.text != w {
            return Err(error(
                "CNL_SYNTAX",
                t.span,
                None,
                format!("Expected {w}."),
                format!("Use the keyword {w}."),
            ));
        }
        Ok(())
    }
    fn words(&mut self, w: &[&str]) -> Result<(), CompilerError> {
        for x in w {
            self.word(x)?;
        }
        Ok(())
    }
    fn end(&self) -> Result<(), CompilerError> {
        if self.at < self.line.tokens.len() {
            Err(error(
                "CNL_SYNTAX",
                self.line.tokens[self.at].span.clone(),
                None,
                "Unexpected trailing token.",
                "Use only the fixed sentence form.",
            ))
        } else {
            Ok(())
        }
    }
    fn ident(&mut self) -> Result<Token, CompilerError> {
        let t = self.token()?;
        if !identifier(&t.text) {
            return Err(error(
                "CNL_SYNTAX",
                t.span,
                None,
                "Invalid identifier token.",
                "Use an ASCII Python identifier.",
            ));
        }
        Ok(t)
    }
    fn json(&mut self) -> Result<(Value, Token), CompilerError> {
        let t = self.token()?;
        let v = serde_json::from_str::<Value>(&t.text).map_err(|_| {
            error(
                "CNL_SYNTAX",
                t.span.clone(),
                None,
                "Invalid JSON scalar.",
                "Use one JSON scalar literal.",
            )
        })?;
        if v.is_object() || v.is_array() {
            return Err(error(
                "CNL_SYNTAX",
                t.span,
                None,
                "Objects and arrays are not scalar literals.",
                "Use a JSON scalar or declared typed input.",
            ));
        }
        Ok((v, t))
    }
}
fn identifier(s: &str) -> bool {
    let mut it = s.bytes();
    it.next()
        .is_some_and(|b| b.is_ascii_alphabetic() || b == b'_')
        && it.all(|b| b.is_ascii_alphanumeric() || b == b'_')
}
enum SyntaxTask {
    List {
        path: String,
        end: String,
        index: usize,
    },
    Expected(String),
    Exports {
        path: String,
        name: String,
    },
}
struct Parser {
    lines: Vec<Line>,
    at: usize,
    eof: SourceSpan,
    version: usize,
    map: SourceMap,
    names: BTreeMap<String, (String, String)>,
    actions: usize,
    branches: usize,
    transactions: usize,
    syntax_only: bool,
    syntax_tasks: Vec<SyntaxTask>,
}
impl Parser {
    fn peek(&self) -> Option<&Line> {
        self.lines.get(self.at)
    }
    fn normal(&self) -> &str {
        self.peek().map(|l| l.normal.as_str()).unwrap_or("")
    }
    fn take(&mut self) -> Result<Cursor, CompilerError> {
        let line = self.peek().cloned().ok_or_else(|| {
            error(
                "CNL_MISSING",
                self.eof.clone(),
                None,
                "Unexpected end of input.",
                "Supply the required closing sentence.",
            )
        })?;
        self.at += 1;
        Ok(Cursor { line, at: 0 })
    }
    fn repeated_scalar(&self, context: &str) -> Option<CompilerError> {
        let l = self.peek()?;
        let n = l.normal.as_str();
        let direct = [
            ("Use package ", "/package"),
            ("Use prompt version ", "/version"),
            ("Bind contract ", "/contract/id"),
            ("Use migration revision ", "/migration/revision"),
            (
                "Use previous migration revision ",
                "/migration/down_revision",
            ),
            (
                "Use no previous migration revision.",
                "/migration/down_revision",
            ),
            ("Use database ", "/capabilities/database"),
            ("Use authentication ", "/capabilities/auth"),
            ("Use object storage ", "/capabilities/storage"),
            ("Use workflow value field ", "/capabilities/value_field"),
            ("Use no workflow value field.", "/capabilities/value_field"),
            ("Use workflow group field ", "/capabilities/group_field"),
            ("Use no workflow group field.", "/capabilities/group_field"),
            ("Use workflow ", "/capabilities/workflow"),
        ];
        let mut ptr = direct
            .into_iter()
            .find(|(prefix, _)| n.starts_with(prefix))
            .map(|(_, ptr)| ptr.to_owned());
        for (prefix, key) in [
            ("Use resource name ", "name"),
            ("Use table ", "table"),
            ("Use path ", "path"),
        ] {
            if n.starts_with(prefix) {
                let role = if context.starts_with("/resources/parent") {
                    Some("/resources/parent")
                } else if context.starts_with("/resources/child") {
                    Some("/resources/child")
                } else if context.starts_with("/resource/") {
                    Some("/resource")
                } else {
                    None
                };
                ptr = role.map(|r| format!("{r}/{key}")).or_else(|| {
                    self.map
                        .iter()
                        .filter(|(p, _)| {
                            p.ends_with(&format!("/{key}"))
                                && (p.starts_with("/resource/") || p.starts_with("/resources/"))
                        })
                        .max_by_key(|(_, s)| s.byte_start)
                        .map(|(p, _)| p.clone())
                });
            }
        }
        let ptr = ptr?;
        if self.map.contains_key(&ptr) {
            Some(self.duplicate(&ptr, l, "scalar clause"))
        } else {
            None
        }
    }
    fn missing_scalar(&self, p: &str, form: &str) -> CompilerError {
        let end = if p.starts_with("/resources/parent/") {
            "End parent resource."
        } else if p.starts_with("/resources/child/") {
            "End child resource."
        } else if p.starts_with("/resource/") {
            "End resource."
        } else if p.starts_with("/capabilities/") {
            "End capabilities."
        } else {
            "End request."
        };
        let span = self.lines[self.at..]
            .iter()
            .find(|l| l.normal == end)
            .map(|l| l.span.clone())
            .unwrap_or_else(|| self.eof.clone());
        error(
            "CNL_MISSING",
            span,
            Some(p),
            "Required scalar clause is missing.",
            form,
        )
    }
    fn expected(&mut self, s: &str) -> Result<(), CompilerError> {
        if self.normal() == s {
            self.take()?;
            Ok(())
        } else {
            if let Some(e) = self.repeated_scalar("") {
                return Err(e);
            }
            let span = self
                .peek()
                .map(|l| l.span.clone())
                .unwrap_or_else(|| self.eof.clone());
            let code = if self.peek().is_none() {
                "CNL_MISSING"
            } else if self.normal().starts_with("End ") {
                "CNL_BLOCK_MISMATCH"
            } else {
                "CNL_SYNTAX"
            };
            Err(error(code, span, None, format!("Expected {s}"), s))
        }
    }
    fn locate(&mut self, p: &str, t: &Token) {
        if self.syntax_only && p.starts_with("/flow/") {
            return;
        }
        self.map.insert(p.into(), t.span.clone());
    }
    fn span(&mut self, p: &str, l: &Line) {
        if self.syntax_only && p.starts_with("/flow/") {
            return;
        }
        self.map.insert(p.into(), l.span.clone());
    }
    fn scalar(&mut self, w: &[&str], p: &str, string: bool) -> Result<Value, CompilerError> {
        let prefix = w.join(" ") + " ";
        if !self.normal().starts_with(&prefix) {
            if let Some(e) = self.repeated_scalar(p) {
                return Err(e);
            }
            if self.peek().is_none()
                || self.normal().starts_with("End ")
                || self.normal().starts_with("Use ")
                || self.normal().starts_with("Begin ")
                || self.normal().starts_with("Field ")
            {
                return Err(self.missing_scalar(
                    p,
                    &format!(
                        "{} {}.",
                        w.join(" "),
                        if string { "JSON_STRING" } else { "IDENT" }
                    ),
                ));
            }
        }
        let mut c = self.take()?;
        c.words(w)?;
        let t = c.token()?;
        c.end()?;
        let v = if string {
            serde_json::from_str::<String>(&t.text)
                .map(Value::String)
                .map_err(|_| {
                    error(
                        "CNL_SYNTAX",
                        t.span.clone(),
                        None,
                        "Expected JSON string.",
                        "Use a quoted JSON string.",
                    )
                })?
        } else {
            if !identifier(&t.text) {
                return Err(error(
                    "CNL_SYNTAX",
                    t.span,
                    None,
                    "Invalid identifier token.",
                    "Use an ASCII Python identifier.",
                ));
            }
            json!(t.text)
        };
        self.locate(p, &t);
        Ok(v)
    }
    fn duplicate(&self, p: &str, l: &Line, what: &str) -> CompilerError {
        error(
            "CNL_DUPLICATE",
            l.span.clone(),
            Some(p),
            format!("Duplicate {what}."),
            format!("Declare {what} exactly once."),
        )
    }
    fn missing(&self, p: &str, l: &Line, cause: &str, allowed: &str) -> CompilerError {
        error("CNL_MISSING", l.span.clone(), Some(p), cause, allowed)
    }
    fn query(&mut self, p: &str) -> Result<Value, CompilerError> {
        self.expected("Begin query.")?;
        let mut values = Map::new();
        let order = ["filter_field", "date_field", "max_limit"];
        let mut ordinal = 0;
        loop {
            let l = self.peek().cloned().ok_or_else(|| {
                error(
                    "CNL_MISSING",
                    self.eof.clone(),
                    Some(p),
                    "Missing query end.",
                    "End query.",
                )
            })?;
            if l.normal == "End query." {
                for (k, cause, form) in [
                    (
                        "filter_field",
                        "The query block has no exact-filter selector clause.",
                        "Use Filter exactly on field IDENT. or Use no exact filter.",
                    ),
                    (
                        "date_field",
                        "The query block has no date-filter selector clause.",
                        "Use Filter before date on field IDENT. or Use no date filter.",
                    ),
                    (
                        "max_limit",
                        "The query block has no result limit clause.",
                        "Limit query results to UINT.",
                    ),
                ] {
                    if (k != "date_field" || self.version >= 4) && !values.contains_key(k) {
                        return Err(self.missing(&format!("{p}/{k}"), &l, cause, form));
                    }
                }
                self.span(p, &l);
                self.take()?;
                break;
            }
            let mut c = self.take()?;
            let (k, v, t) = if l.normal == "Use no exact filter." {
                ("filter_field", Value::Null, c.line.tokens[2].clone())
            } else if l.normal == "Use no date filter." {
                ("date_field", Value::Null, c.line.tokens[2].clone())
            } else if l.normal.starts_with("Filter exactly on field ") {
                c.words(&["Filter", "exactly", "on", "field"])?;
                let t = c.ident()?;
                c.end()?;
                ("filter_field", json!(t.text), t)
            } else if l.normal.starts_with("Filter before date on field ") {
                c.words(&["Filter", "before", "date", "on", "field"])?;
                let t = c.ident()?;
                c.end()?;
                ("date_field", json!(t.text), t)
            } else if l.normal.starts_with("Limit query results to ") {
                c.words(&["Limit", "query", "results", "to"])?;
                let t = c.token()?;
                c.end()?;
                if !t.text.bytes().all(|b| b.is_ascii_digit()) {
                    return Err(error(
                        "CNL_SYNTAX",
                        t.span,
                        None,
                        "Query limit must be an unsigned integer.",
                        "Limit query results to UINT.",
                    ));
                }
                // UINT syntax is already checked. Integer range is semantic.
                // A placeholder lets the syntax pass inspect all later clauses.
                let n = if self.syntax_only {
                    t.text.parse::<u64>().unwrap_or(0)
                } else {
                    t.text.parse::<u64>().map_err(|_| {
                        error(
                            "INVALID_VALUE",
                            t.span.clone(),
                            Some(&format!("{p}/max_limit")),
                            "Query limit is outside the integer range.",
                            "Use an integer from 20 through 100.",
                        )
                    })?
                };
                ("max_limit", json!(n), t)
            } else {
                return Err(error(
                    "CNL_SYNTAX",
                    l.span,
                    None,
                    "Unknown query sentence.",
                    "Use the fixed query selectors and limit.",
                ));
            };
            if values.contains_key(k) {
                return Err(self.duplicate(&format!("{p}/{k}"), &l, k));
            }
            if k == "date_field" && self.version < 4 {
                return Err(error(
                    "CNL_SYNTAX",
                    l.span,
                    None,
                    "Date selector is unavailable in this prompt version.",
                    "Use only the exact filter and limit.",
                ));
            }
            let next = order.iter().position(|x| *x == k).unwrap();
            if next < ordinal {
                return Err(error(
                    "CNL_SYNTAX",
                    l.span,
                    None,
                    "Query clauses are out of order.",
                    "Declare exact filter, date filter, then limit.",
                ));
            }
            ordinal = next;
            self.locate(&format!("{p}/{k}"), &t);
            values.insert(k.into(), v);
        }
        Ok(Value::Object(values))
    }
    fn resource(&mut self, role: &str, p: &str) -> Result<Value, CompilerError> {
        let label = if role.is_empty() {
            "resource".into()
        } else {
            format!("{role} resource")
        };
        self.expected(&format!("Begin {label}."))?;
        let name = self.scalar(&["Use", "resource", "name"], &format!("{p}/name"), false)?;
        let table = self.scalar(&["Use", "table"], &format!("{p}/table"), false)?;
        let path = self.scalar(&["Use", "path"], &format!("{p}/path"), true)?;
        let mut fields = vec![];
        let mut seen = BTreeSet::new();
        while self.normal().starts_with("Field ") {
            let mut c = self.take()?;
            let l = c.line.clone();
            c.word("Field")?;
            let t = c.ident()?;
            let fp = format!("{p}/fields/{}", fields.len());
            if !seen.insert(t.text.clone()) {
                return Err(error(
                    "CNL_DUPLICATE",
                    t.span,
                    Some(&format!("{fp}/name")),
                    "Duplicate field name.",
                    "Use unique field names within the resource.",
                ));
            }
            self.locate(&format!("{fp}/name"), &t);
            c.word("is")?;
            let rt = c.token()?;
            let required = match rt.text.as_str() {
                "required" => true,
                "optional" => false,
                _ => {
                    return Err(error(
                        "CNL_SYNTAX",
                        rt.span,
                        None,
                        "Expected required or optional.",
                        "Use required or optional.",
                    ));
                }
            };
            self.locate(&format!("{fp}/required"), &rt);
            let ty = c.token()?;
            if !["str", "int", "float", "bool", "date"].contains(&ty.text.as_str()) {
                return Err(error(
                    "CNL_SYNTAX",
                    ty.span,
                    None,
                    format!("Field token {} is outside the grammar vocabulary.", ty.text),
                    "str, int, float, bool, or date when the prompt version allows date.",
                ));
            }
            self.locate(&format!("{fp}/type"), &ty);
            c.words(&["and", "is"])?;
            let ut = c.token()?;
            let unique = if ut.text == "unique" {
                true
            } else if ut.text == "not" {
                c.word("unique")?;
                false
            } else {
                return Err(error(
                    "CNL_SYNTAX",
                    ut.span,
                    None,
                    "Expected unique or not unique.",
                    "Use unique or not unique.",
                ));
            };
            self.locate(&format!("{fp}/unique"), &ut);
            let mut f = json!({"name":t.text,"type":ty.text,"required":required,"unique":unique});
            if c.at < c.line.tokens.len() {
                c.words(&["with", "default"])?;
                let (v, dt) = c.json()?;
                f["default"] = v;
                self.locate(&format!("{fp}/default"), &dt);
            } else if !required && self.version >= 3 {
                f["default"] = Value::Null;
                self.span(&format!("{fp}/default"), &l);
            }
            c.end()?;
            self.span(&fp, &l);
            fields.push(f);
        }
        let mut r = json!({"name":name,"table":table,"path":path,"fields":fields});
        if !role.is_empty() {
            r["query"] = self.query(&format!("{p}/query"))?;
        }
        let l = self.peek().cloned().unwrap_or(Line {
            tokens: vec![],
            span: self.eof.clone(),
            normal: String::new(),
        });
        if r["fields"].as_array().unwrap().is_empty() {
            return Err(self.missing(
                &format!("{p}/fields"),
                &l,
                "Resource needs at least one field.",
                "Field IDENT is required|optional TYPE and is unique|not unique.",
            ));
        }
        self.expected(&format!("End {label}."))?;
        self.span(p, &l);
        Ok(r)
    }
    fn declare(&mut self, name: &Token, p: &str, label: &str) -> Result<(), CompilerError> {
        if let Some((prior, _)) = self.names.get(&name.text) {
            return Err(error(
                "CNL_DUPLICATE",
                name.span.clone(),
                Some(p),
                format!(
                    "{label} name {} conflicts with the earlier {} name {}.",
                    name.text,
                    prior.to_lowercase(),
                    name.text
                ),
                "Use unique names across inputs and nodes.",
            ));
        }
        self.names.insert(
            name.text.clone(),
            (
                label.into(),
                if self.syntax_only {
                    String::new()
                } else {
                    p.into()
                },
            ),
        );
        self.locate(p, name);
        Ok(())
    }
    fn value(&mut self, c: &mut Cursor, p: &str) -> Result<Value, CompilerError> {
        let kind = c.token()?;
        let start = kind.span.clone();
        let v = match kind.text.as_str() {
            "input" => {
                let t = c.ident()?;
                self.locate(&format!("{p}/input"), &t);
                json!({"input":t.text})
            }
            "step" => {
                let t = c.ident()?;
                self.locate(&format!("{p}/step"), &t);
                c.word("field")?;
                let (f, ft) = c.json()?;
                if !f.is_string() {
                    return Err(error(
                        "CNL_SYNTAX",
                        ft.span,
                        None,
                        "Result field must be a JSON string.",
                        "step IDENT field JSON_STRING",
                    ));
                }
                self.locate(&format!("{p}/field"), &ft);
                json!({"step":t.text,"field":f})
            }
            "literal" => {
                let t = c.token()?;
                if !["string", "integer", "float", "boolean", "date", "bytes"]
                    .contains(&t.text.as_str())
                {
                    return Err(error(
                        "CNL_SYNTAX",
                        t.span,
                        None,
                        "Unknown scalar literal type.",
                        "Use string, integer, float, boolean, date, or bytes.",
                    ));
                }
                self.locate(&format!("{p}/type"), &t);
                let (v, vt) = c.json()?;
                self.locate(&format!("{p}/literal"), &vt);
                json!({"type":t.text,"literal":v})
            }
            _ => {
                return Err(error(
                    "CNL_SYNTAX",
                    kind.span,
                    None,
                    "Unknown runtime reference form.",
                    "Use input NAME, step NAME field JSON_STRING, or literal TYPE JSON_SCALAR.",
                ));
            }
        };
        if !self.syntax_only {
            self.map
                .insert(p.into(), joined(&start, &c.line.tokens[c.at - 1].span));
        }
        Ok(v)
    }
    fn syntax_exports(&mut self, p: &str, name: &str) -> Result<(), CompilerError> {
        let mut exports = Map::new();
        if self.normal() == "Export no values." {
            self.take()?;
        } else {
            while self.normal().starts_with("Export ") {
                let mut ec = self.take()?;
                ec.word("Export")?;
                let et = ec.ident()?;
                self.declare(&et, &format!("{p}/exports/{}", et.text), "Export")?;
                ec.words(&["from", "then"])?;
                let a = self.value(&mut ec, &format!("{p}/exports/{}/then", et.text))?;
                ec.words(&["and", "from", "else"])?;
                let b = self.value(&mut ec, &format!("{p}/exports/{}/else", et.text))?;
                ec.end()?;
                exports.insert(et.text, json!({"then":a,"else":b}));
            }
            if exports.is_empty() {
                let span = self
                    .peek()
                    .map(|l| l.span.clone())
                    .unwrap_or_else(|| self.eof.clone());
                return Err(error(
                    "CNL_MISSING",
                    span,
                    Some(&format!("{p}/exports")),
                    "Branch export declaration is missing.",
                    "Export no values. or Export IDENT from then VALUE and from else VALUE.",
                ));
            }
        }
        self.expected(&format!("End branch {}.", name))?;
        Ok(())
    }
    // Check the entire flow grammar on a heap stack before semantic budgets.
    // The second pass retains the old bounded AST and all validator gates.
    fn syntax_node_list(&mut self, p: &str, end: &str) -> Result<Vec<Value>, CompilerError> {
        self.syntax_tasks.push(SyntaxTask::List {
            path: p.into(),
            end: end.into(),
            index: 0,
        });
        while let Some(task) = self.syntax_tasks.pop() {
            match task {
                SyntaxTask::Expected(sentence) => {
                    self.expected(&sentence)?;
                }
                SyntaxTask::Exports { path, name } => {
                    self.syntax_exports(&path, &name)?;
                }
                SyntaxTask::List { path, end, index } => {
                    if self.normal() == end {
                        self.expected(&end)?;
                        continue;
                    }
                    let l = self.peek().ok_or_else(|| {
                        error(
                            "CNL_MISSING",
                            self.eof.clone(),
                            Some(&path),
                            "Missing block end.",
                            &end,
                        )
                    })?;
                    if l.normal.starts_with("End ") {
                        return Err(error(
                            "CNL_BLOCK_MISMATCH",
                            l.span.clone(),
                            Some(&path),
                            "Mismatched block delimiter.",
                            &end,
                        ));
                    }
                    let node_path = format!("{path}/{index}");
                    self.syntax_tasks.push(SyntaxTask::List {
                        path,
                        end,
                        index: index + 1,
                    });
                    self.node(&node_path, 0, false)?;
                }
            }
        }
        Ok(vec![])
    }
    fn node_list(
        &mut self,
        p: &str,
        end: &str,
        depth: usize,
        tx: bool,
    ) -> Result<Vec<Value>, CompilerError> {
        if self.syntax_only {
            return self.syntax_node_list(p, end);
        }
        let mut nodes = vec![];
        while self.normal() != end {
            let l = self.peek().ok_or_else(|| {
                error(
                    "CNL_MISSING",
                    self.eof.clone(),
                    Some(p),
                    "Missing block end.",
                    end,
                )
            })?;
            if l.normal.starts_with("End ") {
                return Err(error(
                    "CNL_BLOCK_MISMATCH",
                    l.span.clone(),
                    Some(p),
                    "Mismatched block delimiter.",
                    end,
                ));
            }
            nodes.push(self.node(&format!("{p}/{}", nodes.len()), depth, tx)?);
        }
        if let Some(l) = self.peek().cloned() {
            self.span(p, &l);
        }
        self.expected(end)?;
        Ok(nodes)
    }
    fn node(&mut self, p: &str, depth: usize, tx: bool) -> Result<Value, CompilerError> {
        let mut c = self.take()?;
        let l = c.line.clone();
        c.word("Begin")?;
        let kind = c.token()?;
        let name = c.ident()?;
        if !["step", "transaction", "branch"].contains(&kind.text.as_str()) {
            return Err(error(
                "CNL_SYNTAX",
                kind.span,
                None,
                "Unknown node kind.",
                "Begin step, transaction, or branch.",
            ));
        }
        self.declare(
            &name,
            &format!("{p}/name"),
            if kind.text == "step" {
                "Step"
            } else if kind.text == "transaction" {
                "Transaction"
            } else {
                "Branch"
            },
        )?;
        self.span(p, &l);
        if kind.text == "transaction" {
            c.end()?;
            self.locate(&format!("{p}/op"), &kind);
            if self.syntax_only {
                self.syntax_tasks.push(SyntaxTask::List {
                    path: format!("{p}/steps"),
                    end: format!("End transaction {}.", name.text),
                    index: 0,
                });
                return Ok(Value::Null);
            }
            self.transactions += 1;
            if tx {
                return Err(error(
                    "INVALID_TRANSACTION",
                    kind.span,
                    Some(&format!("{p}/op")),
                    "Nested transactions are not allowed.",
                    "Use a nonnested transaction.",
                ));
            }
            if self.transactions > 2 {
                return Err(error(
                    "SCOPE_TOO_LARGE",
                    kind.span,
                    Some(&format!("{p}/op")),
                    "Too many transaction scopes.",
                    "Use at most two transactions.",
                ));
            }
            let steps = self.node_list(
                &format!("{p}/steps"),
                &format!("End transaction {}.", name.text),
                depth,
                true,
            )?;
            return Ok(json!({"name":name.text,"op":"transaction","steps":steps}));
        }
        if kind.text == "branch" {
            c.word("when")?;
            self.locate(&format!("{p}/op"), &kind);
            self.branches += 1;
            if !self.syntax_only && (self.branches > 2 || depth >= 2) {
                return Err(error(
                    "SCOPE_TOO_LARGE",
                    kind.span,
                    Some(&format!("{p}/op")),
                    "Branch budget exceeded.",
                    "Use at most two branches and depth two.",
                ));
            }
            let left = self.value(&mut c, &format!("{p}/condition/left"))?;
            c.word("is")?;
            let cmp = c.token()?;
            let op = match cmp.text.as_str() {
                "null" => "is_null",
                "equal" => {
                    c.word("to")?;
                    "eq"
                }
                "not" => {
                    c.words(&["equal", "to"])?;
                    "ne"
                }
                "less" => {
                    c.word("than")?;
                    "lt"
                }
                "greater" => {
                    c.word("than")?;
                    "gt"
                }
                "at" => {
                    let t = c.token()?;
                    match t.text.as_str() {
                        "most" => "le",
                        "least" => "ge",
                        _ => {
                            return Err(error(
                                "CNL_SYNTAX",
                                t.span,
                                None,
                                "Unknown comparison.",
                                "Use at most or at least.",
                            ));
                        }
                    }
                }
                _ => {
                    return Err(error(
                        "CNL_SYNTAX",
                        cmp.span,
                        None,
                        "Unknown comparison.",
                        "Use a fixed comparison or is null.",
                    ));
                }
            };
            self.locate(&format!("{p}/condition/op"), &cmp);
            let mut condition = json!({"op":op,"left":left});
            if op != "is_null" {
                condition["right"] = self.value(&mut c, &format!("{p}/condition/right"))?;
            }
            c.end()?;
            if self.syntax_only {
                self.syntax_tasks.push(SyntaxTask::Exports {
                    path: p.into(),
                    name: name.text,
                });
                self.syntax_tasks.push(SyntaxTask::List {
                    path: format!("{p}/else"),
                    end: "End else.".into(),
                    index: 0,
                });
                self.syntax_tasks
                    .push(SyntaxTask::Expected("Begin else.".into()));
                self.syntax_tasks.push(SyntaxTask::List {
                    path: format!("{p}/then"),
                    end: "End then.".into(),
                    index: 0,
                });
                self.syntax_tasks
                    .push(SyntaxTask::Expected("Begin then.".into()));
                return Ok(Value::Null);
            }
            self.expected("Begin then.")?;
            let yes = self.node_list(&format!("{p}/then"), "End then.", depth + 1, tx)?;
            self.expected("Begin else.")?;
            let no = self.node_list(&format!("{p}/else"), "End else.", depth + 1, tx)?;
            let mut exports = Map::new();
            if self.normal() == "Export no values." {
                self.take()?;
            } else {
                while self.normal().starts_with("Export ") {
                    let mut ec = self.take()?;
                    ec.word("Export")?;
                    let et = ec.ident()?;
                    self.declare(&et, &format!("{p}/exports/{}", et.text), "Export")?;
                    ec.words(&["from", "then"])?;
                    let a = self.value(&mut ec, &format!("{p}/exports/{}/then", et.text))?;
                    ec.words(&["and", "from", "else"])?;
                    let b = self.value(&mut ec, &format!("{p}/exports/{}/else", et.text))?;
                    ec.end()?;
                    exports.insert(et.text, json!({"then":a,"else":b}));
                }
                if exports.is_empty() {
                    let span = self
                        .peek()
                        .map(|l| l.span.clone())
                        .unwrap_or_else(|| self.eof.clone());
                    return Err(error(
                        "CNL_MISSING",
                        span,
                        Some(&format!("{p}/exports")),
                        "Branch export declaration is missing.",
                        "Export no values. or Export IDENT from then VALUE and from else VALUE.",
                    ));
                }
            }
            self.expected(&format!("End branch {}.", name.text))?;
            return Ok(
                json!({"name":name.text,"op":"if","condition":condition,"then":yes,"else":no,"exports":exports}),
            );
        }
        c.words(&["with", "operation"])?;
        let op = c.token()?;
        c.end()?;
        if ![
            "authorize",
            "parent.create",
            "parent.get",
            "parent.list",
            "parent.patch",
            "child.create",
            "child.get",
            "child.list",
            "child.patch",
            "batch.create",
            "workflow.run",
            "workflow.import",
            "object.put",
            "object.get",
        ]
        .contains(&op.text.as_str())
        {
            return Err(error(
                "CNL_SYNTAX",
                op.span,
                None,
                "Operation token is not in the CNL operation vocabulary.",
                "Use one operation listed by the grammar.",
            ));
        }
        self.locate(&format!("{p}/op"), &op);
        self.actions += 1;
        if !self.syntax_only && self.actions > 8 {
            return Err(error(
                "SCOPE_TOO_LARGE",
                op.span,
                Some(&format!("{p}/op")),
                "Action budget exceeded.",
                "Use at most eight action nodes.",
            ));
        }
        let mut args = Map::new();
        while self.normal().starts_with("Set argument ") {
            let mut a = self.take()?;
            let al = a.line.clone();
            a.words(&["Set", "argument"])?;
            let key = a.ident()?;
            let ap = format!("{p}/args/{}", key.text);
            if args.contains_key(&key.text) {
                return Err(self.duplicate(&ap, &al, "argument"));
            }
            a.word("to")?;
            let v = self.value(&mut a, &ap)?;
            a.end()?;
            args.insert(key.text, v);
        }
        let ul = self.peek().cloned().ok_or_else(|| {
            error(
                "CNL_MISSING",
                self.eof.clone(),
                Some(&format!("{p}/undo")),
                "Missing undo declaration.",
                "Declare the exact undo or use no undo.",
            )
        })?;
        let undo = if ul.normal == "On failure, use no undo." {
            self.take()?;
            if !self.syntax_only {
                self.map.insert(
                    format!("{p}/undo"),
                    joined(&ul.tokens[3].span, &ul.tokens[4].span),
                );
            }
            Value::Null
        } else if ul.normal.starts_with("On failure, undo with ") {
            let mut u = self.take()?;
            u.words(&["On", "failure,", "undo", "with"])?;
            let t = u.token()?;
            u.end()?;
            if !["delete_created", "restore_patch"].contains(&t.text.as_str()) {
                return Err(error(
                    "CNL_SYNTAX",
                    t.span,
                    None,
                    "Unknown undo mode.",
                    "Use delete_created, restore_patch, or no undo.",
                ));
            }
            self.locate(&format!("{p}/undo"), &t);
            json!(t.text)
        } else {
            return Err(self.missing(
                &format!("{p}/undo"),
                &ul,
                "Missing undo declaration.",
                "Declare the exact undo or use no undo.",
            ));
        };
        self.span(&format!("{p}/args"), &ul);
        self.expected(&format!("End step {}.", name.text))?;
        Ok(json!({"name":name.text,"op":op.text,"args":args,"undo":undo}))
    }
    fn flow(&mut self) -> Result<Value, CompilerError> {
        let mut c = self.take()?;
        c.words(&["Begin", "flow"])?;
        let name = c.ident()?;
        c.end()?;
        self.locate("/flow/name", &name);
        self.span("/flow", &c.line);
        let mut inputs = vec![];
        if self.normal() == "Use no inputs." {
            self.take()?;
        } else {
            while self.normal().starts_with("Input ") {
                let mut c = self.take()?;
                c.word("Input")?;
                let n = c.ident()?;
                let p = format!("/flow/inputs/{}/name", inputs.len());
                self.declare(&n, &p, "Input")?;
                c.words(&["has", "type"])?;
                let t = c.token()?;
                c.end()?;
                if ![
                    "string",
                    "integer",
                    "float",
                    "boolean",
                    "date",
                    "parent_input",
                    "child_input",
                    "parent_patch",
                    "child_patch",
                    "children_input",
                ]
                .contains(&t.text.as_str())
                {
                    return Err(error(
                        "CNL_SYNTAX",
                        t.span,
                        None,
                        "Unsupported input type token.",
                        "Use a type listed by the CNL input grammar.",
                    ));
                }
                self.locate(&format!("/flow/inputs/{}/type", inputs.len()), &t);
                inputs.push(json!({"name":n.text,"type":t.text}));
                if !self.syntax_only && inputs.len() > 8 {
                    return Err(error(
                        "SCOPE_TOO_LARGE",
                        n.span,
                        Some("/flow/inputs"),
                        "Too many typed inputs.",
                        "Use at most eight inputs.",
                    ));
                }
            }
            if inputs.is_empty() {
                let span = self
                    .peek()
                    .map(|l| l.span.clone())
                    .unwrap_or_else(|| self.eof.clone());
                return Err(error(
                    "CNL_MISSING",
                    span,
                    Some("/flow/inputs"),
                    "Input declaration is missing.",
                    "Declare inputs or Use no inputs.",
                ));
            }
        }
        self.expected("Begin steps.")?;
        let steps = self.node_list("/flow/steps", "End steps.", 0, false)?;
        let mut result = Map::new();
        if self.normal() == "Return no values." {
            self.take()?;
        } else {
            while self.normal().starts_with("Return ") {
                let mut c = self.take()?;
                let l = c.line.clone();
                c.word("Return")?;
                let key = c.ident()?;
                let p = format!("/flow/result/{}", key.text);
                if result.contains_key(&key.text) {
                    return Err(self.duplicate(&p, &l, "result name"));
                }
                self.locate(&format!("{p}/@key"), &key);
                c.word("from")?;
                let v = self.value(&mut c, &p)?;
                c.end()?;
                result.insert(key.text, v);
            }
            if result.is_empty() {
                let span = self
                    .peek()
                    .map(|l| l.span.clone())
                    .unwrap_or_else(|| self.eof.clone());
                return Err(error(
                    "CNL_MISSING",
                    span,
                    Some("/flow/result"),
                    "Result declaration is missing.",
                    "Return no values. or Return IDENT from VALUE.",
                ));
            }
        }
        self.expected(&format!("End flow {}.", name.text))?;
        Ok(json!({"name":name.text,"inputs":inputs,"steps":steps,"result":result}))
    }
    fn document(&mut self) -> Result<Value, CompilerError> {
        let h = self.take()?;
        if h.line.normal != "Use SOM CNL version 1." {
            let span = h
                .line
                .tokens
                .last()
                .map(|t| t.span.clone())
                .unwrap_or(h.line.span);
            return Err(error(
                "CNL_SYNTAX",
                span,
                None,
                "Unsupported CNL language version in header.",
                "Use SOM CNL version 1.",
            ));
        }
        let mut c = self.take()?;
        c.words(&["Use", "prompt", "version"])?;
        let t = c.token()?;
        c.end()?;
        let version = t.text.parse::<usize>().unwrap_or(0);
        // Dispatch guard: an unknown version has no grammar family to scan.
        if !(1..=7).contains(&version) {
            return Err(error(
                "UNSUPPORTED_VERSION",
                t.span,
                Some("/version"),
                "Prompt version is outside the supported set 1 through 7.",
                "Use prompt version 1 through 7.",
            ));
        }
        if t.text != version.to_string() {
            return Err(error(
                "CNL_SYNTAX",
                t.span,
                Some("/version"),
                "Prompt version token is not a fixed grammar choice.",
                "Use prompt version 1 through 7.",
            ));
        }
        self.version = version;
        self.locate("/version", &t);
        let package = self.scalar(&["Use", "package"], "/package", false)?;
        let mut p = json!({"version":format!("som.prompt.v{version}"),"package":package});
        if version >= 6 {
            let mut c = self.take()?;
            c.words(&["Bind", "contract"])?;
            let id = c.token()?;
            c.words(&["with", "SHA-256"])?;
            let (hash, ht) = c.json()?;
            c.end()?;
            if !self.syntax_only
                && !["related-business.v1", "related-flow.v1"].contains(&id.text.as_str())
            {
                return Err(error(
                    "UNSUPPORTED_CONTRACT",
                    id.span,
                    Some("/contract/id"),
                    "Unknown public contract.",
                    "Bind the public contract for this prompt version.",
                ));
            }
            self.locate("/contract/id", &id);
            self.locate("/contract/sha256", &ht);
            p["contract"] = json!({"id":id.text,"sha256":hash});
        }
        if version <= 4 {
            p["resource"] = self.resource("", "/resource")?;
            if version >= 2 {
                p["query"] = self.query("/query")?;
            }
        } else {
            let a = self.resource("parent", "/resources/parent")?;
            let b = self.resource("child", "/resources/child")?;
            p["resources"] = json!({"parent":a,"child":b});
            let mut c = self.take()?;
            c.words(&["Relate", "child", "to", "parent", "with", "foreign", "key"])?;
            let fk = c.ident()?;
            c.words(&["and", "restrict", "deletion"])?;
            c.end()?;
            self.locate("/relation/foreign_key", &fk);
            self.span("/relation/on_delete", &c.line);
            p["relation"] = json!({"foreign_key":fk.text,"on_delete":"restrict"});
        }
        let revision = self.scalar(
            &["Use", "migration", "revision"],
            "/migration/revision",
            false,
        )?;
        let down = if self.normal() == "Use no previous migration revision." {
            let c = self.take()?;
            self.span("/migration/down_revision", &c.line);
            Value::Null
        } else {
            self.scalar(
                &["Use", "previous", "migration", "revision"],
                "/migration/down_revision",
                false,
            )?
        };
        p["migration"] = json!({"revision":revision,"down_revision":down});
        if version >= 5 {
            if self.normal() == "Use no capabilities." && version == 5 {
                self.take()?;
            } else {
                self.expected("Begin capabilities.")?;
                let mut cap = Map::new();
                for (k, words, allowed) in [
                    (
                        "database",
                        vec!["Use", "database"],
                        vec!["postgresql", "mongodb"],
                    ),
                    (
                        "auth",
                        vec!["Use", "authentication"],
                        vec!["jwt", "firebase", "google_oauth"],
                    ),
                    (
                        "storage",
                        vec!["Use", "object", "storage"],
                        vec!["none", "s3", "gcs"],
                    ),
                    (
                        "workflow",
                        vec!["Use", "workflow"],
                        vec![
                            "json_export",
                            "gzip_export",
                            "csv",
                            "decimal_summary",
                            "statistics",
                        ],
                    ),
                ] {
                    if !self.normal().starts_with(&(words.join(" ") + " ")) {
                        if let Some(e) = self.repeated_scalar("/capabilities") {
                            return Err(e);
                        }
                        if self.normal().starts_with("Use ")
                            || self.normal() == "End capabilities."
                            || self.peek().is_none()
                        {
                            return Err(self.missing_scalar(
                                &format!("/capabilities/{k}"),
                                &format!("{} ENUM.", words.join(" ")),
                            ));
                        }
                    }
                    let mut c = self.take()?;
                    c.words(&words)?;
                    let t = c.token()?;
                    c.end()?;
                    if !allowed.contains(&t.text.as_str()) {
                        return Err(error(
                            "CNL_SYNTAX",
                            t.span,
                            None,
                            format!("Unknown {k} capability token."),
                            allowed.join(", "),
                        ));
                    }
                    self.locate(&format!("/capabilities/{k}"), &t);
                    cap.insert(k.into(), json!(t.text));
                }
                for k in ["value", "group"] {
                    let ptr = format!("/capabilities/{k}_field");
                    let no = format!("Use no workflow {k} field.");
                    let v = if self.normal() == no {
                        let c = self.take()?;
                        self.span(&ptr, &c.line);
                        Value::Null
                    } else {
                        self.scalar(&["Use", "workflow", k, "field"], &ptr, false)?
                    };
                    cap.insert(format!("{k}_field"), v);
                }
                self.expected("End capabilities.")?;
                p["capabilities"] = Value::Object(cap);
            }
        }
        self.expected("Begin routes.")?;
        let mut routes = vec![];
        let mut seen = BTreeSet::new();
        while self.normal() != "End routes." {
            if let Some(e) = self.repeated_scalar("") {
                return Err(e);
            }
            let mut c = self.take()?;
            let l = c.line.clone();
            let value = if version <= 4 {
                json!(l.normal)
            } else if version == 5 {
                c.word("For")?;
                let t = c.token()?;
                let role = t.text.strip_suffix(':').unwrap_or("");
                if !["parent", "child", "batch"].contains(&role) {
                    return Err(error(
                        "CNL_SYNTAX",
                        t.span,
                        None,
                        "Unknown related route scope.",
                        "For parent, child, or batch: the fixed sentence.",
                    ));
                }
                let sentence = c.line.tokens[c.at..]
                    .iter()
                    .map(|t| t.text.as_str())
                    .collect::<Vec<_>>()
                    .join(" ")
                    + ".";
                json!({"scope":role,"step":sentence})
            } else if l.normal == "Allow batch creation." {
                json!({"scope":"batch","action":"batch_create"})
            } else {
                c.word("Allow")?;
                let action = c.token()?;
                c.word("on")?;
                let role = c.token()?;
                c.end()?;
                if !["create", "get", "list", "patch", "delete"].contains(&action.text.as_str())
                    || !["parent", "child"].contains(&role.text.as_str())
                {
                    return Err(error(
                        "CNL_SYNTAX",
                        l.span,
                        None,
                        "Unknown selected operation.",
                        "Allow ACTION on parent|child. or Allow batch creation.",
                    ));
                }
                json!({"scope":role.text,"action":action.text})
            };
            let rp = if version >= 6 { "operations" } else { "logic" };
            let ptr = format!("/{rp}/{}", routes.len());
            if !seen.insert(value.to_string()) {
                return Err(self.duplicate(&ptr, &l, "route operation"));
            }
            self.span(&ptr, &l);
            if version >= 6 {
                self.span(&format!("{ptr}/scope"), &l);
                self.span(&format!("{ptr}/action"), &l);
            }
            routes.push(value);
        }
        if routes.is_empty() {
            let l = self.peek().cloned().unwrap();
            return Err(self.missing(
                if version >= 6 {
                    "/operations"
                } else {
                    "/logic"
                },
                &l,
                "The routes block has no operation.",
                "Declare the required fixed route sentences.",
            ));
        }
        self.span(
            if version >= 6 {
                "/operations"
            } else {
                "/logic"
            },
            &self.peek().cloned().unwrap(),
        );
        self.expected("End routes.")?;
        p[if version >= 6 { "operations" } else { "logic" }] = json!(routes);
        if version <= 5 {
            self.expected("Begin checks.")?;
            let c = self.take()?;
            self.span("/tests/0", &c.line);
            p["tests"] = json!([c.line.normal]);
            self.expected("End checks.")?;
        }
        if version == 7 {
            p["flow"] = self.flow()?;
        }
        self.expected("End request.")?;
        if let Some(l) = self.peek() {
            return Err(error(
                "CNL_SYNTAX",
                l.span.clone(),
                None,
                "Unexpected content after End request.",
                "Supply one request only.",
            ));
        }
        Ok(p)
    }
}
pub fn parse(source: &str) -> Result<ParsedCnl, CompilerError> {
    let (lines, eof) = lex(source)?;
    let mut syntax = Parser {
        lines: lines.clone(),
        at: 0,
        eof: eof.clone(),
        version: 0,
        map: BTreeMap::new(),
        names: BTreeMap::new(),
        actions: 0,
        branches: 0,
        transactions: 0,
        syntax_only: true,
        syntax_tasks: vec![],
    };
    syntax.document()?;
    drop(syntax);
    let mut p = Parser {
        lines,
        at: 0,
        eof,
        version: 0,
        map: BTreeMap::new(),
        names: BTreeMap::new(),
        actions: 0,
        branches: 0,
        transactions: 0,
        syntax_only: false,
        syntax_tasks: vec![],
    };
    let prompt = p.document()?;
    if let Err(e) = crate::validate_prompt(&prompt) {
        return Err(semantics::localize(&prompt, &e, &p.map));
    }
    Ok(ParsedCnl {
        prompt,
        source_map: p.map,
    })
}

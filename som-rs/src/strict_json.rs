//! Strict JSON envelopes for the new compiler endpoints. Legacy JSON is unchanged.
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug)]
pub struct Span {
    pub start: usize,
    pub end: usize,
}

pub struct Document {
    pub value: Value,
    pub spans: BTreeMap<String, Span>,
    source: String,
}

fn position(source: &str, offset: usize) -> (usize, usize) {
    let prefix = &source[..offset.min(source.len())];
    (
        prefix.bytes().filter(|b| *b == b'\n').count() + 1,
        prefix.rsplit('\n').next().unwrap_or("").chars().count() + 1,
    )
}

pub fn diagnostic(
    source: &str,
    span: Span,
    pointer: Option<&str>,
    code: &str,
    cause: &str,
    allowed: &str,
) -> Value {
    let (line, column) = position(source, span.start);
    let (end_line, end_column) = position(source, span.end);
    json!({"status":"error","code":code,"detail":cause,"diagnostics":[{
        "code":code,"line":line,"column":column,"end_line":end_line,"end_column":end_column,
        "byte_start":span.start,"byte_end":span.end,"prompt_pointer":pointer,
        "cause":cause,"allowed_form":allowed
    }]})
}

pub fn envelope_error(code: &str, detail: &str, allowed: &str) -> Value {
    diagnostic("", Span { start: 0, end: 0 }, None, code, detail, allowed)
}

impl Document {
    pub fn error(&self, pointer: &str, code: &str, detail: &str, allowed: &str) -> Value {
        let mut selected = pointer.to_owned();
        while !self.spans.contains_key(&selected) && !selected.is_empty() {
            selected.truncate(selected.rfind('/').unwrap_or(0));
        }
        let span = self
            .spans
            .get(&selected)
            .copied()
            .unwrap_or(Span { start: 0, end: 0 });
        diagnostic(&self.source, span, Some(&selected), code, detail, allowed)
    }
}

struct Reader<'a> {
    source: &'a str,
    at: usize,
    spans: BTreeMap<String, Span>,
}
impl Reader<'_> {
    fn ws(&mut self) {
        while self
            .source
            .as_bytes()
            .get(self.at)
            .is_some_and(|b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
        {
            self.at += 1;
        }
    }
    fn failure(&self, start: usize, end: usize, pointer: &str, cause: &str) -> Value {
        diagnostic(
            self.source,
            Span { start, end },
            Some(pointer),
            "INVALID_JSON",
            cause,
            "One valid JSON value with unique object keys.",
        )
    }
    fn next_end(&self) -> usize {
        self.at
            + self.source[self.at..]
                .chars()
                .next()
                .map(char::len_utf8)
                .unwrap_or(0)
    }
    fn string(&mut self, pointer: &str) -> Result<String, Value> {
        let start = self.at;
        if self.source.as_bytes().get(start) != Some(&b'"') {
            return Err(self.failure(start, self.next_end(), pointer, "Expected a JSON string."));
        }
        self.at += 1;
        let mut escaped = false;
        while self.at < self.source.len() {
            let ch = self.source[self.at..].chars().next().unwrap();
            self.at += ch.len_utf8();
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                return serde_json::from_str(&self.source[start..self.at]).map_err(|_| {
                    self.failure(
                        start,
                        self.at,
                        pointer,
                        "Invalid JSON string or Unicode escape.",
                    )
                });
            }
        }
        Err(self.failure(start, self.at, pointer, "Unclosed JSON string."))
    }
    fn value(&mut self, pointer: &str, depth: usize) -> Result<Value, Value> {
        self.ws();
        let start = self.at;
        if depth > 128 {
            return Err(self.failure(
                start,
                self.next_end(),
                pointer,
                "JSON nesting exceeds 128 levels.",
            ));
        }
        let value = match self.source.as_bytes().get(self.at).copied() {
            Some(b'{') => {
                self.at += 1;
                self.ws();
                let mut result = Map::new();
                if self.source.as_bytes().get(self.at) == Some(&b'}') {
                    self.at += 1;
                    Value::Object(result)
                } else {
                    loop {
                        self.ws();
                        let key_start = self.at;
                        let key = self.string(pointer)?;
                        let child =
                            format!("{}/{}", pointer, key.replace('~', "~0").replace('/', "~1"));
                        if result.contains_key(&key) {
                            return Err(self.failure(
                                key_start,
                                self.at,
                                &child,
                                "Duplicate JSON member.",
                            ));
                        }
                        self.ws();
                        if self.source.as_bytes().get(self.at) != Some(&b':') {
                            return Err(self.failure(
                                self.at,
                                self.next_end(),
                                &child,
                                "Expected a colon after a JSON member name.",
                            ));
                        }
                        self.at += 1;
                        result.insert(key, self.value(&child, depth + 1)?);
                        self.ws();
                        match self.source.as_bytes().get(self.at) {
                            Some(b',') => self.at += 1,
                            Some(b'}') => {
                                self.at += 1;
                                break;
                            }
                            _ => {
                                return Err(self.failure(
                                    self.at,
                                    self.next_end(),
                                    pointer,
                                    "Expected a comma or closing object brace.",
                                ));
                            }
                        }
                    }
                    Value::Object(result)
                }
            }
            Some(b'[') => {
                self.at += 1;
                self.ws();
                let mut result = Vec::new();
                if self.source.as_bytes().get(self.at) == Some(&b']') {
                    self.at += 1;
                } else {
                    loop {
                        result
                            .push(self.value(&format!("{}/{}", pointer, result.len()), depth + 1)?);
                        self.ws();
                        match self.source.as_bytes().get(self.at) {
                            Some(b',') => self.at += 1,
                            Some(b']') => {
                                self.at += 1;
                                break;
                            }
                            _ => {
                                return Err(self.failure(
                                    self.at,
                                    self.next_end(),
                                    pointer,
                                    "Expected a comma or closing array bracket.",
                                ));
                            }
                        }
                    }
                }
                Value::Array(result)
            }
            Some(b'"') => Value::String(self.string(pointer)?),
            _ => {
                while self.at < self.source.len()
                    && !matches!(
                        self.source.as_bytes()[self.at],
                        b' ' | b'\t' | b'\n' | b'\r' | b',' | b']' | b'}'
                    )
                {
                    self.at += self.source[self.at..].chars().next().unwrap().len_utf8();
                }
                let value: Value = serde_json::from_str(&self.source[start..self.at])
                    .map_err(|_| self.failure(start, self.at, pointer, "Invalid JSON scalar."))?;
                if value.is_object() || value.is_array() {
                    return Err(self.failure(start, self.at, pointer, "Invalid JSON scalar."));
                }
                value
            }
        };
        self.spans.insert(
            pointer.into(),
            Span {
                start,
                end: self.at,
            },
        );
        Ok(value)
    }
}

pub fn parse(source: &str) -> Result<Document, Value> {
    let mut reader = Reader {
        source,
        at: 0,
        spans: BTreeMap::new(),
    };
    let value = reader.value("", 0)?;
    reader.ws();
    if reader.at != source.len() {
        return Err(reader.failure(reader.at, reader.next_end(), "", "Trailing JSON content."));
    }
    Ok(Document {
        value,
        spans: reader.spans,
        source: source.into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn duplicate_keys_do_not_silently_replace_values() {
        let error = parse("{\"x\":1,\"x\":2}").err().unwrap();
        assert_eq!(error["code"], "INVALID_JSON");
        assert_eq!(error["diagnostics"][0]["column"], 8);
    }
    #[test]
    fn typed_numbers_unicode_and_pointer_positions_survive() {
        let doc = parse("{\"中文\":[1,1.0,-0.0,null],\"x\":true}").unwrap();
        assert!(doc.value["中文"][0].is_i64());
        assert!(doc.value["中文"][1].is_f64());
        assert!(doc.value["中文"][2].as_f64().unwrap().is_sign_negative());
        let error = doc.error("/中文/2", "INVALID_REF", "Example", "literal float -0.0");
        assert_eq!(error["diagnostics"][0]["column"], 14);
        assert_eq!(error["diagnostics"][0]["byte_start"], 17);
    }
    #[test]
    fn bad_unicode_and_trailing_commas_fail() {
        for source in [
            r#"{"x":"\ud800"}"#,
            r#"{"x":1,}"#,
            "[1,]",
            "1 2",
            "[NaN]",
            "{\"x\":}",
        ] {
            assert!(parse(source).is_err(), "{source}");
        }
    }
}

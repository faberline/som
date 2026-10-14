//! Diagnostic paths only. Existing validators still decide admission.
//! Add this as an independent crate module. The CNL parser is unchanged.
use crate::cnl::{CompilerError, Diagnostic, SourceMap, SourceSpan};
#[path = "cnl_semantics.rs"]
mod semantics;
fn error(
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

use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

fn escape(key: &str) -> String {
    key.replace('~', "~0").replace('/', "~1")
}
fn child(p: &str, key: &str) -> String {
    format!("{p}/{}", escape(key))
}
fn object(v: &Value, keys: &[&str], p: &str) -> Result<(), String> {
    let Some(m) = v.as_object() else {
        return Err(p.into());
    };
    if let Some(key) = m.keys().find(|k| !keys.contains(&k.as_str())) {
        return Err(child(p, key));
    }
    if keys.iter().any(|k| !m.contains_key(*k)) {
        return Err(p.into());
    }
    Ok(())
}
fn string(v: &Value, p: &str) -> Result<(), String> {
    if v.is_string() { Ok(()) } else { Err(p.into()) }
}
fn reference(v: &Value, p: &str) -> Result<(), String> {
    let Some(m) = v.as_object() else {
        return Err(p.into());
    };
    if m.contains_key("input") {
        object(v, &["input"], p)?;
        string(&v["input"], &child(p, "input"))
    } else if m.contains_key("step") {
        object(v, &["step", "field"], p)?;
        string(&v["step"], &child(p, "step"))?;
        string(&v["field"], &child(p, "field"))
    } else if m.contains_key("literal") {
        object(v, &["literal", "type"], p)?;
        string(&v["type"], &child(p, "type"))
    } else {
        Err(p.into())
    }
}
fn nodes(v: &Value, p: &str, depth: usize) -> Result<(), String> {
    if depth > 128 {
        return Err(p.into());
    }
    let Some(ns) = v.as_array() else {
        return Err(p.into());
    };
    for (i, n) in ns.iter().enumerate() {
        let np = child(p, &i.to_string());
        string(&n["name"], &child(&np, "name"))?;
        string(&n["op"], &child(&np, "op"))?;
        match n["op"].as_str() {
            Some("transaction") => {
                object(n, &["name", "op", "steps"], &np)?;
                nodes(&n["steps"], &child(&np, "steps"), depth + 1)?;
            }
            Some("if") => {
                object(
                    n,
                    &["name", "op", "condition", "then", "else", "exports"],
                    &np,
                )?;
                let cp = child(&np, "condition");
                let c = &n["condition"];
                string(&c["op"], &child(&cp, "op"))?;
                object(
                    c,
                    if c["op"] == "is_null" {
                        &["op", "left"]
                    } else {
                        &["op", "left", "right"]
                    },
                    &cp,
                )?;
                reference(&c["left"], &child(&cp, "left"))?;
                if c["op"] != "is_null" {
                    reference(&c["right"], &child(&cp, "right"))?;
                }
                nodes(&n["then"], &child(&np, "then"), depth + 1)?;
                nodes(&n["else"], &child(&np, "else"), depth + 1)?;
                let ep = child(&np, "exports");
                let Some(exports) = n["exports"].as_object() else {
                    return Err(ep);
                };
                for (key, b) in exports {
                    let bp = child(&ep, key);
                    object(b, &["then", "else"], &bp)?;
                    reference(&b["then"], &child(&bp, "then"))?;
                    reference(&b["else"], &child(&bp, "else"))?;
                }
            }
            _ => {
                object(n, &["name", "op", "args", "undo"], &np)?;
                let ap = child(&np, "args");
                let Some(args) = n["args"].as_object() else {
                    return Err(ap);
                };
                for (key, v) in args {
                    reference(v, &child(&ap, key))?;
                }
            }
        }
    }
    Ok(())
}
fn query(v: &Value, p: &str, date: bool) -> Result<(), String> {
    object(
        v,
        if date {
            &["filter_field", "date_field", "max_limit"]
        } else {
            &["filter_field", "max_limit"]
        },
        p,
    )?;
    Ok(())
}
fn resource(v: &Value, p: &str, version: u8, related: bool) -> Result<(), String> {
    object(
        v,
        if related {
            &["name", "table", "path", "fields", "query"]
        } else {
            &["name", "table", "path", "fields"]
        },
        p,
    )?;
    for k in ["name", "table", "path"] {
        string(&v[k], &child(p, k))?;
    }
    let fp = child(p, "fields");
    let Some(fields) = v["fields"].as_array() else {
        return Err(fp);
    };
    for (i, f) in fields.iter().enumerate() {
        let p = child(&fp, &i.to_string());
        object(
            f,
            if version >= 3 && f["required"] == false {
                &["name", "type", "required", "unique", "default"]
            } else {
                &["name", "type", "required", "unique"]
            },
            &p,
        )?;
        for k in ["name", "type"] {
            string(&f[k], &child(&p, k))?;
        }
        for k in ["required", "unique"] {
            if !f[k].is_boolean() {
                return Err(child(&p, k));
            }
        }
    }
    if related {
        query(&v["query"], &child(p, "query"), true)?;
    }
    Ok(())
}
fn shape(p: &Value, version: u8) -> Result<(), String> {
    let mut keys = vec!["version", "package", "migration"];
    if version <= 4 {
        keys.extend(["resource", "logic", "tests"]);
        if version >= 2 {
            keys.push("query");
        }
    } else {
        keys.extend(["resources", "relation"]);
        if version == 5 {
            keys.extend(["logic", "tests"]);
            if p.get("capabilities").is_some() {
                keys.push("capabilities");
            }
        } else {
            keys.extend(["contract", "operations", "capabilities"]);
            if version == 7 {
                keys.push("flow");
            }
        }
    }
    object(p, &keys, "")?;
    string(&p["package"], "/package")?;
    if version <= 4 {
        resource(&p["resource"], "/resource", version, false)?;
        if version >= 2 {
            query(&p["query"], "/query", version >= 4)?;
        }
    } else {
        object(&p["resources"], &["parent", "child"], "/resources")?;
        object(&p["relation"], &["foreign_key", "on_delete"], "/relation")?;
        for role in ["parent", "child"] {
            resource(
                &p["resources"][role],
                &format!("/resources/{role}"),
                version,
                true,
            )?;
        }
    }
    if version >= 6 {
        object(&p["contract"], &["id", "sha256"], "/contract")?;
    }
    if version >= 5 && p.get("capabilities").is_some() {
        object(
            &p["capabilities"],
            &[
                "database",
                "auth",
                "storage",
                "workflow",
                "value_field",
                "group_field",
            ],
            "/capabilities",
        )?;
    }
    object(
        &p["migration"],
        &["revision", "down_revision"],
        "/migration",
    )?;
    if version == 7 {
        let f = &p["flow"];
        object(f, &["name", "inputs", "steps", "result"], "/flow")?;
        string(&f["name"], "/flow/name")?;
        let Some(inputs) = f["inputs"].as_array() else {
            return Err("/flow/inputs".into());
        };
        for (i, input) in inputs.iter().enumerate() {
            let p = format!("/flow/inputs/{i}");
            object(input, &["name", "type"], &p)?;
            string(&input["name"], &child(&p, "name"))?;
            string(&input["type"], &child(&p, "type"))?;
        }
        nodes(&f["steps"], "/flow/steps", 0)?;
        let Some(result) = f["result"].as_object() else {
            return Err("/flow/result".into());
        };
        for (k, v) in result {
            reference(v, &child("/flow/result", k))?;
        }
    }
    // These arrays are read with unwrap by the reused CNL locator.
    for k in if version <= 5 {
        vec!["logic", "tests"]
    } else {
        vec!["operations"]
    } {
        let Some(a) = p[k].as_array() else {
            return Err(child("", k));
        };
        for (i, v) in a.iter().enumerate() {
            let ptr = format!("/{k}/{i}");
            if k == "logic" && version == 5 {
                object(v, &["scope", "step"], &ptr)?;
                for key in ["scope", "step"] {
                    string(&v[key], &child(&ptr, key))?;
                }
            } else if k == "operations" {
                object(v, &["scope", "action"], &ptr)?;
                for key in ["scope", "action"] {
                    string(&v[key], &child(&ptr, key))?;
                }
            } else {
                string(v, &ptr)?;
            }
        }
    }
    Ok(())
}
fn source_map(v: &Value, p: &str, out: &mut SourceMap, depth: usize) {
    if depth > 128 {
        return;
    }
    let tag = out.len() + 1;
    out.insert(
        p.into(),
        SourceSpan {
            line: 1,
            column: tag,
            end_line: 1,
            end_column: tag + 1,
            byte_start: tag,
            byte_end: tag + 1,
        },
    );
    if let Some(m) = v.as_object() {
        for (k, v) in m {
            source_map(v, &child(p, k), out, depth + 1);
        }
    } else if let Some(a) = v.as_array() {
        for (i, v) in a.iter().enumerate() {
            source_map(v, &child(p, &i.to_string()), out, depth + 1);
        }
    }
}
fn named_nodes(v: &Value, p: &str, out: &mut Vec<(String, String, String)>, depth: usize) {
    if depth > 128 {
        return;
    }
    if let Some(ns) = v.as_array() {
        for (i, n) in ns.iter().enumerate() {
            let np = child(p, &i.to_string());
            if let (Some(name), Some(op)) = (n["name"].as_str(), n["op"].as_str()) {
                out.push((name.into(), op.into(), np.clone()));
            }
            for key in ["steps", "then", "else"] {
                if n.get(key).is_some() {
                    named_nodes(&n[key], &child(&np, key), out, depth + 1);
                }
            }
            if let Some(exports) = n["exports"].as_object() {
                for key in exports.keys() {
                    out.push((
                        key.clone(),
                        "export".into(),
                        child(&child(&np, "exports"), key),
                    ));
                }
            }
        }
    }
}
fn safe_name(s: &str) -> bool {
    let mut it = s.bytes();
    it.next().is_some_and(|b| b.is_ascii_alphabetic())
        && it.all(|b| b.is_ascii_alphanumeric() || b == b'_')
        && s.len() <= 64
        && !s.starts_with("model_")
        && !matches!(
            s,
            "False"
                | "None"
                | "True"
                | "and"
                | "as"
                | "assert"
                | "async"
                | "await"
                | "break"
                | "class"
                | "continue"
                | "def"
                | "del"
                | "elif"
                | "else"
                | "except"
                | "finally"
                | "for"
                | "from"
                | "global"
                | "if"
                | "import"
                | "in"
                | "is"
                | "lambda"
                | "nonlocal"
                | "not"
                | "or"
                | "pass"
                | "raise"
                | "return"
                | "try"
                | "while"
                | "with"
                | "yield"
        )
}
fn identifier(s: &str) -> bool {
    let mut it = s.bytes();
    it.next()
        .is_some_and(|b| b.is_ascii_alphabetic() || b == b'_')
        && it.all(|b| b.is_ascii_alphanumeric() || b == b'_')
        && (!s.starts_with('_') && s.len() <= 64 && !s.starts_with("model_") && safe_name(s)
            || s.starts_with('_')
            || s.len() > 64
            || s.starts_with("model_"))
}
fn related_identifier_pointer(p: &Value, detail: &str) -> Option<String> {
    for role in ["parent", "child"] {
        let r = &p["resources"][role];
        let rp = format!("/resources/{role}");
        for key in ["name", "table"] {
            if detail == format!("resource.{key}: expected identifier")
                && r[key].as_str().is_none_or(|s| !identifier(s))
            {
                return Some(child(&rp, key));
            }
        }
        for key in ["filter_field", "date_field"] {
            if detail == format!("{key}: expected identifier")
                && !r["query"][key].is_null()
                && r["query"][key].as_str().is_none_or(|s| !identifier(s))
            {
                return Some(child(&child(&rp, "query"), key));
            }
        }
        if let Some(index) = detail
            .strip_prefix("fields[")
            .and_then(|s| s.split_once(']'))
            .and_then(|(n, _)| n.parse::<usize>().ok())
        {
            if let Some(fields) = r["fields"].as_array() {
                if let Some(f) = fields.get(index) {
                    let fp = format!("{rp}/fields/{index}");
                    if detail == format!("fields[{index}].name: expected identifier")
                        && f["name"].as_str().is_none_or(|s| !identifier(s))
                    {
                        return Some(child(&fp, "name"));
                    }
                    if detail == format!("fields[{index}]: invalid field") {
                        let name = f["name"].as_str().unwrap_or("");
                        if ["id", "metadata", "registry", "created_at", "updated_at"]
                            .contains(&name)
                            || fields[..index]
                                .iter()
                                .any(|previous| previous["name"] == f["name"])
                        {
                            return Some(child(&fp, "name"));
                        }
                        if !matches!(
                            f["type"].as_str(),
                            Some("str" | "int" | "bool" | "float" | "date")
                        ) {
                            return Some(child(&fp, "type"));
                        }
                    }
                }
            }
        }
    }
    None
}
pub fn prompt_pointer(p: &Value, e: &crate::SomError) -> Option<String> {
    if e.code == "UNSUPPORTED_VERSION" {
        return Some("/version".into());
    }
    if e.detail.starts_with("prompt.")
        && !matches!(e.code, "UNSUPPORTED_LOGIC" | "UNSUPPORTED_TEST")
    {
        let prefix = e.detail.split(':').next()?;
        return Some(
            prefix
                .strip_prefix("prompt")?
                .replace('.', "/")
                .replace('[', "/")
                .replace(']', ""),
        );
    }
    let version = p["version"]
        .as_str()?
        .strip_prefix("som.prompt.v")?
        .parse::<u8>()
        .ok()?;
    if !(1..=7).contains(&version) {
        return Some("/version".into());
    }
    if let Err(pointer) = shape(p, version) {
        return Some(pointer);
    }
    if version >= 5 {
        if let Some(pointer) = related_identifier_pointer(p, &e.detail) {
            return Some(pointer);
        }
    }
    if e.detail.starts_with("unsupported capability: ") {
        return Some(format!(
            "/capabilities/{}",
            e.detail.strip_prefix("unsupported capability: ")?
        ));
    }
    if e.code == "UNSUPPORTED_TEST" {
        return Some(
            if p["tests"].as_array()?.is_empty() {
                "/tests"
            } else {
                "/tests/0"
            }
            .into(),
        );
    }
    if e.code == "UNSUPPORTED_LOGIC" && version >= 6 && e.detail != "unsupported flow action" {
        let mut seen = BTreeSet::new();
        for (i, op) in p["operations"].as_array()?.iter().enumerate() {
            let (scope, action) = (op["scope"].as_str()?, op["action"].as_str()?);
            if !seen.insert((scope, action))
                || !(matches!(scope, "parent" | "child")
                    && matches!(action, "create" | "get" | "list" | "patch" | "delete")
                    || scope == "batch" && action == "batch_create")
            {
                return Some(format!("/operations/{i}"));
            }
        }
        return Some("/operations".into());
    }
    if version == 7 {
        let f = &p["flow"];
        let inputs = f["inputs"].as_array()?;
        if e.detail == "at most eight typed inputs" {
            return Some("/flow/inputs/8".into());
        }
        if e.detail == "unsupported input type" {
            for (i, v) in inputs.iter().enumerate() {
                if !matches!(
                    v["type"].as_str()?,
                    "string"
                        | "integer"
                        | "float"
                        | "boolean"
                        | "date"
                        | "parent_input"
                        | "child_input"
                        | "parent_patch"
                        | "child_patch"
                        | "children_input"
                ) {
                    return Some(format!("/flow/inputs/{i}/type"));
                }
            }
        }
        let mut declarations = vec![(
            f["name"].as_str()?.into(),
            "flow".into(),
            "/flow/name".into(),
        )];
        for (i, v) in inputs.iter().enumerate() {
            declarations.push((
                v["name"].as_str()?.into(),
                "input".into(),
                format!("/flow/inputs/{i}/name"),
            ));
        }
        named_nodes(&f["steps"], "/flow/steps", &mut declarations, 0);
        for k in f["result"].as_object()?.keys() {
            declarations.push((k.clone(), "result".into(), child("/flow/result", k)));
        }
        if e.detail == "unsafe flow name" || e.detail.starts_with("flow name:") {
            if let Some((_, kind, ptr)) = declarations.iter().find(|(name, _, _)| !safe_name(name))
            {
                return Some(
                    if matches!(kind.as_str(), "flow" | "input" | "export" | "result") {
                        ptr.clone()
                    } else {
                        child(ptr, "name")
                    },
                );
            }
        }
        if matches!(
            e.detail.as_str(),
            "duplicate input" | "duplicate step name" | "branch export mismatch"
        ) {
            let mut seen = BTreeSet::new();
            for (name, kind, ptr) in &declarations {
                if kind == "flow" || kind == "result" {
                    continue;
                }
                if !seen.insert(name) {
                    return Some(if kind == "input" {
                        ptr.clone()
                    } else if kind == "export" {
                        ptr.clone()
                    } else {
                        child(ptr, "name")
                    });
                }
            }
        }
        if e.detail == "unsupported flow action" {
            if let Some((_, _, ptr)) = declarations.iter().find(|(_, op, _)| {
                !matches!(
                    op.as_str(),
                    "flow"
                        | "input"
                        | "export"
                        | "result"
                        | "transaction"
                        | "if"
                        | "authorize"
                        | "parent.create"
                        | "parent.get"
                        | "parent.list"
                        | "parent.patch"
                        | "child.create"
                        | "child.get"
                        | "child.list"
                        | "child.patch"
                        | "batch.create"
                        | "workflow.run"
                        | "workflow.import"
                        | "object.put"
                        | "object.get"
                )
            }) {
                return Some(child(ptr, "op"));
            }
        }
        if e.code == "SCOPE_TOO_LARGE" {
            let (mut actions, mut branches, mut transactions) = (0, 0, 0);
            for (_, op, ptr) in &declarations {
                match op.as_str() {
                    "flow" | "input" | "export" | "result" => continue,
                    "transaction" => transactions += 1,
                    "if" => branches += 1,
                    _ => actions += 1,
                };
                let branch_depth = ptr.matches("/then/").count() + ptr.matches("/else/").count();
                if actions > 8
                    || transactions > 2
                    || branches > 2
                    || op == "if" && branch_depth >= 2
                {
                    return Some(child(ptr, "op"));
                }
            }
        }
    }
    let mut map = BTreeMap::new();
    source_map(p, "", &mut map, 0);
    // All as_array/as_object/as_str unwrap preconditions of the CNL-only walker
    // were checked above. It cannot admit or repair the request.
    let diagnostic = semantics::localize(p, e, &map)
        .diagnostics
        .into_iter()
        .next()?;
    diagnostic.prompt_pointer.as_ref()?;
    map.iter()
        .find(|(_, span)| span.byte_start == diagnostic.span.byte_start)
        .map(|(pointer, _)| pointer.clone())
}

/// The returned envelope uses the existing raw document token span. No tag
/// positions from the internal CNL location walker escape this function.
pub fn prompt_error(
    document: &crate::strict_json::Document,
    prompt: &Value,
    error: &crate::SomError,
    base: &str,
) -> Value {
    let pointer = prompt_pointer(prompt, error)
        .map(|p| format!("{base}{p}"))
        .unwrap_or_else(|| base.into());
    document.error(
        &pointer,
        error.code,
        &error.detail,
        "A prompt that meets the pinned compiler contract.",
    )
}

fn different(actual: &Value, expected: &Value, p: &str, depth: usize) -> Option<String> {
    if depth > 128 {
        return Some(p.into());
    }
    if let (Some(a), Some(e)) = (actual.as_object(), expected.as_object()) {
        for key in a.keys() {
            if !e.contains_key(key) {
                return Some(child(p, key));
            }
        }
        for (key, value) in e {
            let Some(a) = a.get(key) else {
                return Some(p.into());
            };
            if let Some(ptr) = different(a, value, &child(p, key), depth + 1) {
                return Some(ptr);
            }
        }
        return None;
    }
    if let (Some(a), Some(e)) = (actual.as_array(), expected.as_array()) {
        for (i, (a, e)) in a.iter().zip(e).enumerate() {
            if let Some(ptr) = different(a, e, &child(p, &i.to_string()), depth + 1) {
                return Some(ptr);
            }
        }
        if a.len() > e.len() {
            return Some(child(p, &e.len().to_string()));
        }
        if a.len() < e.len() {
            return Some(p.into());
        }
        return None;
    }
    if actual.is_object() != expected.is_object() || actual.is_array() != expected.is_array() {
        return Some(p.into());
    }
    if crate::flow::canonical(actual) != crate::flow::canonical(expected) {
        Some(p.into())
    } else {
        None
    }
}
fn flow_difference(
    actual: &Value,
    expected: &Value,
    catalog: &Value,
    capabilities: &Value,
    p: &str,
    depth: usize,
) -> Option<String> {
    if depth > 128 {
        return Some(p.into());
    }
    let (Some(a), Some(e)) = (actual.as_array(), expected.as_array()) else {
        return Some(p.into());
    };
    for (i, (a, e)) in a.iter().zip(e).enumerate() {
        let np = child(p, &i.to_string());
        let Some(map) = a.as_object() else {
            return Some(np);
        };
        let Some(meta) = a["id"].as_str().and_then(|id| catalog.get(id)) else {
            return Some(child(&np, "id"));
        };
        if meta["capabilities"]
            .as_object()
            .is_some_and(|m| m.iter().any(|(k, v)| capabilities[k] != *v))
        {
            return Some(child(&np, "id"));
        }
        if meta["operation"] != e["op"] {
            return Some(child(&np, "id"));
        }
        let expected_keys = e
            .as_object()?
            .keys()
            .filter(|k| *k != "op")
            .map(String::as_str)
            .chain(std::iter::once("id"))
            .collect::<Vec<_>>();
        if let Err(pointer) = object(a, &expected_keys, &np) {
            return Some(pointer);
        }
        if let Some(ptr) = different(&a["name"], &e["name"], &child(&np, "name"), 0) {
            return Some(ptr);
        }
        match meta["operation"].as_str() {
            Some("transaction") => {
                if let Some(ptr) = flow_difference(
                    &a["steps"],
                    &e["steps"],
                    catalog,
                    capabilities,
                    &child(&np, "steps"),
                    depth + 1,
                ) {
                    return Some(ptr);
                }
            }
            Some("if") => {
                if let Some(ptr) = different(
                    &a["condition"],
                    &e["condition"],
                    &child(&np, "condition"),
                    0,
                ) {
                    return Some(ptr);
                }
                for arm in ["then", "else"] {
                    if let Some(ptr) = flow_difference(
                        &a[arm],
                        &e[arm],
                        catalog,
                        capabilities,
                        &child(&np, arm),
                        depth + 1,
                    ) {
                        return Some(ptr);
                    }
                }
                if let Some(ptr) =
                    different(&a["exports"], &e["exports"], &child(&np, "exports"), 0)
                {
                    return Some(ptr);
                }
            }
            _ => {
                if let Some(ptr) = different(&a["args"], &e["args"], &child(&np, "args"), 0) {
                    return Some(ptr);
                }
                let undo = if a["undo"].is_null() {
                    Value::Null
                } else {
                    match a["undo"].as_str().and_then(|id| catalog.get(id)) {
                        Some(meta) if !meta["inverse"].is_null() => {
                            if meta["capabilities"]
                                .as_object()
                                .is_some_and(|m| m.iter().any(|(k, v)| capabilities[k] != *v))
                            {
                                return Some(child(&np, "undo"));
                            }
                            meta["inverse"].clone()
                        }
                        _ => return Some(child(&np, "undo")),
                    }
                };
                if let Some(ptr) = different(&undo, &e["undo"], &child(&np, "undo"), 0) {
                    return Some(ptr);
                }
            }
        }
        let _ = map;
    }
    if a.len() > e.len() {
        return Some(child(p, &e.len().to_string()));
    }
    if a.len() < e.len() {
        return Some(p.into());
    }
    None
}
fn selected_flow_ids(nodes: &Value, selected: &mut BTreeSet<String>, depth: usize) {
    if depth > 128 {
        return;
    }
    if let Some(nodes) = nodes.as_array() {
        for node in nodes {
            if let Some(id) = node["id"].as_str() {
                selected.insert(id.into());
            }
            for key in ["steps", "then", "else"] {
                if node.get(key).is_some() {
                    selected_flow_ids(&node[key], selected, depth + 1);
                }
            }
            if let Some(id) = node["undo"].as_str() {
                selected.insert(id.into());
            }
        }
    }
}
pub fn bundle_pointer(
    bundle: &Value,
    error: &crate::SomError,
    expected_legacy: Option<&Value>,
) -> Option<String> {
    let base = "/bundle";
    if let Err(pointer) = object(
        bundle,
        &["version", "prompt", "dsl", "assets", "hashes"],
        base,
    ) {
        return Some(pointer);
    }
    if error.code == "BUNDLE_HASH_MISMATCH" {
        for key in ["prompt_sha256", "dsl_sha256", "payload_sha256"] {
            if error.detail.starts_with(key) {
                return Some(format!("{base}/hashes/{key}"));
            }
        }
        return Some(format!("{base}/hashes"));
    }
    if error.code == "ASSET_HASH_MISMATCH" {
        return Some(format!("{base}/assets"));
    }
    if let Err(pointer) = object(
        &bundle["hashes"],
        &["prompt_sha256", "dsl_sha256", "payload_sha256"],
        &format!("{base}/hashes"),
    ) {
        return Some(pointer);
    }
    if bundle["version"] != "som.dsl-bundle.v1" {
        return Some(format!("{base}/version"));
    }
    let prompt = &bundle["prompt"];
    if let Err(e) = crate::validate_prompt(prompt) {
        if e.code == error.code && e.detail == error.detail {
            return prompt_pointer(prompt, error).map(|p| format!("{base}/prompt{p}"));
        }
    }
    let version = prompt["version"]
        .as_str()?
        .strip_prefix("som.prompt.v")?
        .parse::<u8>()
        .ok()?;
    let dsl = &bundle["dsl"];
    let dp = format!("{base}/dsl");
    if let Err(pointer) = object(
        dsl,
        if version == 7 {
            &["version", "files", "flow"]
        } else {
            &["version", "files"]
        },
        &dp,
    ) {
        return Some(pointer);
    }
    let expected_version = if version <= 2 {
        "som.dsl.v1"
    } else if version < 7 {
        "som.dsl.v2"
    } else {
        "som.dsl.v3"
    };
    if dsl["version"] != expected_version {
        return Some(child(&dp, "version"));
    }
    // Flow obligations precede file-plan checks in the existing v7 validator.
    if version == 7 {
        let tree = &dsl["flow"];
        let p = child(&dp, "flow");
        if let Err(ptr) = object(tree, &["name", "steps", "result"], &p) {
            return Some(ptr);
        }
        if let Some(ptr) = different(
            &tree["name"],
            &prompt["flow"]["name"],
            &child(&p, "name"),
            0,
        ) {
            return Some(ptr);
        }
        if let Some(ptr) = flow_difference(
            &tree["steps"],
            &prompt["flow"]["steps"],
            &crate::flow::COMPACT_CATALOG["flow_blocks"],
            &prompt["capabilities"],
            &child(&p, "steps"),
            0,
        ) {
            return Some(ptr);
        }
        if let Some(ptr) = different(
            &tree["result"],
            &prompt["flow"]["result"],
            &child(&p, "result"),
            0,
        ) {
            return Some(ptr);
        }
    }
    let fp = child(&dp, "files");
    let Some(files) = dsl["files"].as_array() else {
        return Some(fp);
    };
    if files.is_empty() {
        return Some(fp);
    }
    if files.len() > if version <= 2 { 8 } else { 12 } {
        return Some(fp);
    }
    let quality_catalog: Value = serde_json::from_str(crate::quality::CATALOG_TEXT).ok()?;
    let catalog = if version == 7 {
        &*crate::flow::COMPACT_CATALOG
    } else {
        &quality_catalog
    };
    let mut total = 0;
    let mut seen_paths = BTreeSet::new();
    let mut operation_pointers = vec![];
    let mut emitted_ids = vec![];
    let mut route_container = None;
    for (i, file) in files.iter().enumerate() {
        let p = child(&fp, &i.to_string());
        if let Err(ptr) = object(file, &["path", "blocks"], &p) {
            return Some(ptr);
        }
        let Some(path) = file["path"].as_str() else {
            return Some(child(&p, "path"));
        };
        if !seen_paths.insert(path) {
            return Some(child(&p, "path"));
        }
        if version >= 6
            && !catalog["slots"]
                .as_object()?
                .values()
                .any(|value| value == path)
        {
            return Some(child(&p, "path"));
        }
        let bp = child(&p, "blocks");
        let Some(blocks) = file["blocks"].as_array() else {
            return Some(bp);
        };
        if blocks.is_empty() {
            return Some(bp);
        }
        total += blocks.len();
        if total > 64 {
            return Some(bp);
        }
        let target = expected_legacy
            .and_then(|dsl| dsl["files"].as_array())
            .and_then(|files| files.iter().find(|f| f["path"] == file["path"]));
        if let Some(target) = target {
            if target["blocks"].as_array()?.len() != blocks.len() {
                return Some(bp);
            }
        }
        let mut names = BTreeSet::new();
        for (j, block) in blocks.iter().enumerate() {
            let p = child(&bp, &j.to_string());
            if let Err(ptr) = object(block, &["id", "name", "params", "refs"], &p) {
                return Some(ptr);
            }
            let Some(id) = block["id"].as_str() else {
                return Some(child(&p, "id"));
            };
            let Some(name) = block["name"].as_str() else {
                return Some(child(&p, "name"));
            };
            if !identifier(name) || names.contains(name) {
                return Some(child(&p, "name"));
            }
            let Some(params) = block["params"].as_object() else {
                return Some(child(&p, "params"));
            };
            let _ = params;
            if version >= 6 {
                let Some(meta) = catalog["blocks"].get(id) else {
                    return Some(child(&p, "id"));
                };
                if let Some(ptr) =
                    different(&block["params"], &meta["bindings"], &child(&p, "params"), 0)
                {
                    return Some(ptr);
                }
                if !meta["operation"].is_null() {
                    operation_pointers.push((meta["operation"].clone(), child(&p, "id")));
                    route_container = Some(bp.clone());
                }
                if meta["slot"] == "app" {
                    route_container = Some(bp.clone());
                }
            } else if let Some(target) = target
                .and_then(|t| t["blocks"].as_array())
                .and_then(|a| a.get(j))
            {
                if block["id"] != target["id"] {
                    return Some(child(&p, "id"));
                }
                if let Some(ptr) =
                    different(&block["params"], &target["params"], &child(&p, "params"), 0)
                {
                    return Some(ptr);
                }
            }
            let rp = child(&p, "refs");
            let Some(refs) = block["refs"].as_array() else {
                return Some(rp);
            };
            let mut seen_refs = BTreeSet::new();
            for (k, r) in refs.iter().enumerate() {
                let ptr = child(&rp, &k.to_string());
                let name = if version == 7 {
                    if let Err(p) = object(r, &["symbol"], &ptr) {
                        return Some(p);
                    }
                    r["symbol"].as_str()
                } else {
                    r.as_str()
                };
                let Some(name) = name else {
                    return Some(ptr);
                };
                if !names.contains(name) || !seen_refs.insert(name) {
                    return Some(if version == 7 {
                        child(&ptr, "symbol")
                    } else {
                        ptr
                    });
                }
            }
            names.insert(name);
            emitted_ids.push((id.to_owned(), child(&p, "id"), bp.clone()));
        }
    }
    if version == 7 {
        let tree = &dsl["flow"];
        let p = child(&dp, "flow");
        if let Err(pointer) = object(tree, &["name", "steps", "result"], &p) {
            return Some(pointer);
        }
        if let Some(ptr) = different(
            &tree["name"],
            &prompt["flow"]["name"],
            &child(&p, "name"),
            0,
        ) {
            return Some(ptr);
        }
        if let Some(ptr) = flow_difference(
            &tree["steps"],
            &prompt["flow"]["steps"],
            &catalog["flow_blocks"],
            &prompt["capabilities"],
            &child(&p, "steps"),
            0,
        ) {
            return Some(ptr);
        }
        if let Some(ptr) = different(
            &tree["result"],
            &prompt["flow"]["result"],
            &child(&p, "result"),
            0,
        ) {
            return Some(ptr);
        }
        if error.detail == "selected flow helpers missing or extra" {
            let mut selected = BTreeSet::new();
            selected_flow_ids(&tree["steps"], &mut selected, 0);
            for (id, ptr, _) in &emitted_ids {
                if id.starts_with("flow.") && !selected.contains(id) {
                    return Some(ptr.clone());
                }
            }
            return Some(
                emitted_ids
                    .iter()
                    .find(|(id, _, _)| id.starts_with("flow."))
                    .map(|(_, _, container)| container.clone())
                    .unwrap_or(fp),
            );
        }
    }
    if version >= 6 && error.detail == "requested operations are missing, extra or out of order" {
        let wanted = prompt["operations"].as_array()?;
        for (i, (operation, ptr)) in operation_pointers.iter().enumerate() {
            if wanted
                .get(i)
                .map(|w| serde_json::json!([w["scope"], w["action"]]))
                .as_ref()
                != Some(operation)
            {
                return Some(ptr.clone());
            }
        }
        return Some(route_container.unwrap_or(fp));
    }
    // Missing obligations have no offending scalar token. Select only the
    // smallest known plan container, never a legal standard/checked block.
    if error.code == "INVALID_COMPENSATION" {
        return Some(child(&child(&dp, "flow"), "steps"));
    }
    if error.code == "INVALID_COMPOSITION"
        || error.code == "INVALID_REF"
        || error.code == "INVALID_DSL"
        || error.code == "UNSUPPORTED_BLOCK"
    {
        return Some(fp);
    }
    Some(dp)
}
pub fn bundle_error(
    document: &crate::strict_json::Document,
    bundle: &Value,
    error: &crate::SomError,
    expected_legacy: Option<&Value>,
) -> Value {
    let pointer =
        bundle_pointer(bundle, error, expected_legacy).unwrap_or_else(|| "/bundle".into());
    document.error(
        &pointer,
        error.code,
        &error.detail,
        "A complete bundle with admitted assets, valid hashes and a matching typed plan.",
    )
}

//! Error locations only. The existing Rust validator decides admission.
use super::{CompilerError, SourceMap, SourceSpan, error};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

struct At {
    pointer: String,
    token: String,
    cause: String,
    allowed: String,
}
fn at(p: &str, token: &str, cause: impl Into<String>, allowed: impl Into<String>) -> At {
    At {
        pointer: p.into(),
        token: token.into(),
        cause: cause.into(),
        allowed: allowed.into(),
    }
}
fn scalar(k: &str) -> bool {
    ["string", "integer", "float", "boolean", "date", "bytes"].contains(&k)
}
fn calendar(s: &str) -> bool {
    if s.len() != 10
        || s.as_bytes()[4] != b'-'
        || s.as_bytes()[7] != b'-'
        || !s
            .bytes()
            .enumerate()
            .all(|(i, b)| i == 4 || i == 7 || b.is_ascii_digit())
    {
        return false;
    }
    let y = s[..4].parse::<u32>().unwrap_or(0);
    let m = s[5..7].parse::<usize>().unwrap_or(0);
    let d = s[8..].parse::<u32>().unwrap_or(0);
    let leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    let days = [
        0,
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    y > 0 && m > 0 && m < 13 && d > 0 && d <= days[m]
}
fn b64(s: &str) -> bool {
    if s.is_empty() || s.len() % 4 != 0 {
        return false;
    }
    let pad = s.bytes().rev().take_while(|b| *b == b'=').count();
    if pad > 2 {
        return false;
    }
    let body = &s[..s.len() - pad];
    fn n(c: u8) -> Option<u8> {
        match c {
            b'A'..=b'Z' => Some(c - b'A'),
            b'a'..=b'z' => Some(c - b'a' + 26),
            b'0'..=b'9' => Some(c - b'0' + 52),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    }
    if !body.bytes().all(|b| n(b).is_some()) {
        return false;
    }
    let decoded = s.len() / 4 * 3 - pad;
    if decoded == 0 || decoded > 1048576 {
        return false;
    }
    let last = n(*body.as_bytes().last().unwrap()).unwrap();
    (pad != 2 || last & 15 == 0) && (pad != 1 || last & 3 == 0)
}
fn literal(v: &Value, k: &str) -> bool {
    match k {
        "string" => v.is_string(),
        "integer" => v.as_i64().is_some_and(|n| i32::try_from(n).is_ok()),
        "float" => v.as_f64().is_some_and(f64::is_finite),
        "boolean" => v.is_boolean(),
        "date" => v.as_str().is_some_and(calendar),
        "bytes" => v.as_str().is_some_and(b64),
        _ => false,
    }
}
fn output(op: &str) -> &'static str {
    match op {
        "parent.create" | "parent.get" | "parent.patch" => "parent_row",
        "child.create" | "child.get" | "child.patch" => "child_row",
        "authorize" => "identity",
        "parent.list" => "parent_rows",
        "child.list" | "workflow.import" => "child_rows",
        "batch.create" => "batch",
        "workflow.run" => "workflow",
        _ => "object",
    }
}
fn args(op: &str) -> Vec<(&'static str, &'static str)> {
    match op {
        "authorize" | "parent.list" => vec![],
        "parent.create" => vec![("data", "parent_input")],
        "child.create" => vec![("parent_id", "integer"), ("data", "child_input")],
        "parent.get" | "child.list" | "workflow.run" => vec![("parent_id", "integer")],
        "child.get" => vec![("parent_id", "integer"), ("child_id", "integer")],
        "parent.patch" => vec![("parent_id", "integer"), ("data", "parent_patch")],
        "child.patch" => vec![
            ("parent_id", "integer"),
            ("child_id", "integer"),
            ("data", "child_patch"),
        ],
        "batch.create" => vec![("parent", "parent_input"), ("children", "children_input")],
        "workflow.import" => vec![("parent_id", "integer"), ("csv", "string")],
        "object.put" => vec![("key", "string"), ("body", "bytes")],
        "object.get" => vec![("key", "string")],
        _ => vec![],
    }
}
fn mutation(op: &str) -> bool {
    [
        "parent.create",
        "child.create",
        "batch.create",
        "workflow.import",
        "parent.patch",
        "child.patch",
        "object.put",
    ]
    .contains(&op)
}
fn member(p: &Value, kind: &str, field: &str) -> Option<String> {
    let mut current = kind.to_owned();
    for part in field.split('.') {
        if !part.is_empty() {
            current = member_one(p, &current, part)?;
        }
    }
    Some(current)
}
fn member_one(p: &Value, kind: &str, field: &str) -> Option<String> {
    let fixed = match (kind, field) {
        ("identity", "sub" | "role") | ("object", "key" | "sha256") | ("workflow", "sha256") => {
            Some("string")
        }
        ("object" | "workflow", "size") => Some("integer"),
        ("object" | "workflow", "body") => Some("bytes"),
        ("workflow", "data") => Some("json"),
        ("batch", "parent") => Some("parent_row"),
        ("batch", "children") => Some("child_rows"),
        _ => None,
    };
    if let Some(k) = fixed {
        return Some(k.into());
    }
    if ["parent_row", "child_row"].contains(&kind) {
        let role = kind.split('_').next()?;
        if field == "id" || role == "child" && p["relation"]["foreign_key"] == field {
            return Some("integer".into());
        }
        if ["created_at", "updated_at"].contains(&field) {
            return Some("string".into());
        }
        for f in p["resources"][role]["fields"].as_array()? {
            if f["name"] == field {
                let t = match f["type"].as_str()? {
                    "str" => "string",
                    "int" => "integer",
                    "bool" => "boolean",
                    x => x,
                };
                return Some(format!(
                    "{t}{}",
                    if f["required"] == false && f["default"].is_null() {
                        "?"
                    } else {
                        ""
                    }
                ));
            }
        }
    }
    None
}
struct Walker<'a> {
    prompt: &'a Value,
    inputs: BTreeMap<String, String>,
    all_nodes: BTreeMap<String, String>,
    authorized: bool,
}
impl Walker<'_> {
    fn reference(
        &self,
        v: &Value,
        p: &str,
        visible: &BTreeMap<String, String>,
        argument: Option<(&str, &str)>,
    ) -> Result<String, At> {
        if let Some(k) = visible.get(&format!("@{}", crate::flow::canonical(v))) {
            return Ok(k.clone());
        }
        if let Some(n) = v["input"].as_str() {
            return self.inputs.get(n).cloned().ok_or_else(|| {
                at(
                    p,
                    &format!("{p}/input"),
                    format!("Input {n} is not declared."),
                    "Use a declared input.",
                )
            });
        }
        if let Some(n) = v["step"].as_str() {
            let kind = visible.get(n).ok_or_else(|| {
                at(
                    p,
                    &format!("{p}/step"),
                    if self
                        .all_nodes
                        .get(n)
                        .is_some_and(|target| outside_arm(target, p))
                    {
                        format!("Step {n} is outside this branch scope.")
                    } else if self.all_nodes.contains_key(n) {
                        format!("Step {n} is declared after this use.")
                    } else {
                        format!("Step {n} is not declared or is outside this scope.")
                    },
                    "Reference only an earlier visible step or input.",
                )
            })?;
            return member(self.prompt, kind, v["field"].as_str().unwrap_or("")).ok_or_else(|| {
                at(
                    p,
                    &format!("{p}/field"),
                    "Unknown result field.",
                    "Use a field in the declared result type.",
                )
            });
        }
        let kind = v["type"].as_str().unwrap_or("");
        if scalar(kind) && literal(&v["literal"], kind) {
            return Ok(kind.into());
        }
        let cause = if let Some((key, target)) = argument {
            let actual = if v["literal"].as_number().is_some_and(|n| n.is_f64()) {
                Some("float")
            } else if v["literal"].is_boolean() {
                Some("boolean")
            } else if v["literal"].is_string() {
                Some("string")
            } else {
                None
            };
            if let Some(actual) = actual {
                format!(
                    "The literal is a {actual} and does not satisfy the {target} {key} argument."
                )
            } else {
                format!("The literal does not satisfy the {target} {key} argument.")
            }
        } else {
            format!("The literal does not satisfy type {kind}.")
        };
        let allowed = if let Some((_, target)) = argument {
            format!("Use a typed {target} literal or an {target}-producing reference.")
        } else {
            format!("Use a valid {kind} JSON scalar.")
        };
        Err(at(p, &format!("{p}/literal"), cause, allowed))
    }
    fn walk(
        &mut self,
        nodes: &Value,
        p: &str,
        visible: &BTreeMap<String, String>,
        tx: bool,
    ) -> Result<BTreeMap<String, String>, At> {
        let mut local = visible.clone();
        for (i, node) in nodes.as_array().unwrap().iter().enumerate() {
            let np = format!("{p}/{i}");
            let op = node["op"].as_str().unwrap();
            let opname = format!("{np}/op");
            match op {
                "transaction" => {
                    if !self.authorized {
                        return Err(at(
                            &opname,
                            &opname,
                            "A transaction requires prior authorization.",
                            "Place an authorize step before the transaction.",
                        ));
                    }
                    if tx {
                        return Err(at(
                            &opname,
                            &opname,
                            "Nested transactions are not allowed.",
                            "Use a nonnested transaction.",
                        ));
                    }
                    local = self.walk(&node["steps"], &format!("{np}/steps"), &local, true)?;
                }
                "if" => {
                    if !self.authorized {
                        return Err(at(
                            &opname,
                            &opname,
                            "Authorization must precede branching.",
                            "Place an authorize step first.",
                        ));
                    }
                    let cp = format!("{np}/condition");
                    let cond = &node["condition"];
                    let left =
                        self.reference(&cond["left"], &format!("{cp}/left"), &local, None)?;
                    let cmp = cond["op"].as_str().unwrap();
                    if cmp == "is_null" {
                        if !left.ends_with('?') {
                            return Err(at(
                                &format!("{cp}/left"),
                                &format!("{cp}/left"),
                                "A null check requires a nullable scalar.",
                                "Check null only on a nullable reference.",
                            ));
                        }
                    } else {
                        let right =
                            self.reference(&cond["right"], &format!("{cp}/right"), &local, None)?;
                        if left != right || !scalar(left.trim_end_matches('?')) || left == "bytes" {
                            return Err(at(
                                &format!("{cp}/right"),
                                &format!("{cp}/right"),
                                "Comparison types are incompatible.",
                                "Compare matching scalar types, excluding bytes.",
                            ));
                        }
                        if !["eq", "ne"].contains(&cmp)
                            && (left.ends_with('?') || left == "boolean")
                        {
                            return Err(at(
                                &format!("{cp}/op"),
                                &format!("{cp}/op"),
                                "Ordered comparison is unsafe for this type.",
                                "Use nonnullable ordered scalar values.",
                            ));
                        }
                    }
                    let mut nonnull = local.clone();
                    if cmp == "is_null" {
                        nonnull.insert(
                            format!("@{}", crate::flow::canonical(&cond["left"])),
                            left.trim_end_matches('?').into(),
                        );
                    }
                    let yes = self.walk(&node["then"], &format!("{np}/then"), &local, tx)?;
                    let no = self.walk(&node["else"], &format!("{np}/else"), &nonnull, tx)?;
                    for (n, binding) in node["exports"].as_object().unwrap() {
                        let ep = format!("{np}/exports/{n}");
                        let a =
                            self.reference(&binding["then"], &format!("{ep}/then"), &yes, None)?;
                        let b =
                            self.reference(&binding["else"], &format!("{ep}/else"), &no, None)?;
                        if a != b {
                            return Err(at(
                                &format!("{ep}/else"),
                                &format!("{ep}/else"),
                                "Branch export types differ.",
                                "Export the same type in both arms.",
                            ));
                        }
                        local.insert(n.into(), a);
                    }
                }
                _ => {
                    if op == "authorize" {
                        if self.authorized || !local.is_empty() || tx {
                            return Err(at(
                                &opname,
                                &opname,
                                "Authorization must be first and occur once.",
                                "Place an authorize step first.",
                            ));
                        }
                        self.authorized = true;
                    } else if !self.authorized {
                        return Err(at(
                            &opname,
                            &opname,
                            "The first flow action is a database mutation and no authorize step precedes it.",
                            "Place an authorize step first.",
                        ));
                    }
                    if mutation(op) && op != "object.put" && !tx {
                        return Err(at(
                            &opname,
                            &opname,
                            "Database mutations are outside a transaction.",
                            "Place database writes inside a nonnested transaction.",
                        ));
                    }
                    if op.starts_with("object.")
                        && (tx || self.prompt["capabilities"]["storage"] == "none")
                    {
                        return Err(at(
                            &opname,
                            &opname,
                            "Object operation is incompatible with storage or transaction scope.",
                            "Select storage and keep provider calls outside database transactions.",
                        ));
                    }
                    if op == "workflow.import" && self.prompt["capabilities"]["workflow"] != "csv" {
                        return Err(at(
                            &opname,
                            &opname,
                            "workflow.import requires the csv workflow capability.",
                            "Declare csv capability when importing CSV.",
                        ));
                    }
                    let expected = args(op);
                    let actual = node["args"].as_object().unwrap();
                    let wanted: BTreeSet<&str> = expected.iter().map(|(k, _)| *k).collect();
                    let supplied: BTreeSet<&str> = actual.keys().map(String::as_str).collect();
                    if wanted != supplied {
                        let key = supplied
                            .difference(&wanted)
                            .next()
                            .copied()
                            .or_else(|| wanted.difference(&supplied).next().copied())
                            .unwrap_or("");
                        let ap = format!("{np}/args/{key}");
                        return Err(at(
                            &ap,
                            &ap,
                            "Action arguments do not match its contract.",
                            format!(
                                "Declare exactly: {}.",
                                wanted.into_iter().collect::<Vec<_>>().join(", ")
                            ),
                        ));
                    }
                    for (key, kind) in expected {
                        let ap = format!("{np}/args/{key}");
                        let got =
                            self.reference(&node["args"][key], &ap, &local, Some((key, kind)))?;
                        if got != kind {
                            return Err(at(
                                &ap,
                                &ap,
                                format!("Argument {key} has type {got}, expected {kind}."),
                                format!("Use an {kind}-producing reference."),
                            ));
                        }
                    }
                    let undo = if op.ends_with(".patch") {
                        json!("restore_patch")
                    } else if mutation(op) {
                        json!("delete_created")
                    } else {
                        Value::Null
                    };
                    if node["undo"] != undo {
                        let mode = undo.as_str().unwrap_or("no undo");
                        return Err(at(
                            &format!("{np}/undo"),
                            &format!("{np}/undo"),
                            format!("A successful {op} mutation requires {mode} compensation."),
                            if undo.is_null() {
                                "On failure, use no undo.".into()
                            } else {
                                format!("On failure, undo with {mode}.")
                            },
                        ));
                    }
                    local.insert(node["name"].as_str().unwrap().into(), output(op).into());
                }
            }
        }
        Ok(local)
    }
}
fn outside_arm(target: &str, use_pointer: &str) -> bool {
    for delimiter in ["/then/", "/else/"] {
        let mut position = 0;
        while let Some(i) = target[position..].find(delimiter) {
            let end = position + i + delimiter.len();
            if !use_pointer.starts_with(&target[..end]) {
                return true;
            }
            position = end;
        }
    }
    false
}
fn all_names(nodes: &Value, p: &str, out: &mut BTreeMap<String, String>) {
    for (i, n) in nodes.as_array().unwrap().iter().enumerate() {
        let np = format!("{p}/{i}");
        out.insert(n["name"].as_str().unwrap().into(), np.clone());
        if n["op"] == "transaction" {
            all_names(&n["steps"], &format!("{np}/steps"), out);
        }
        if n["op"] == "if" {
            all_names(&n["then"], &format!("{np}/then"), out);
            all_names(&n["else"], &format!("{np}/else"), out);
            for key in n["exports"].as_object().unwrap().keys() {
                out.insert(key.clone(), format!("{np}/exports/{key}"));
            }
        }
    }
}
fn lower(s: &str) -> bool {
    s.len() <= 40
        && s.bytes().next().is_some_and(|b| b.is_ascii_lowercase())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}
fn upper(s: &str) -> bool {
    s.len() <= 40
        && s.bytes().next().is_some_and(|b| b.is_ascii_uppercase())
        && s.bytes().all(|b| b.is_ascii_alphanumeric())
}
fn keyword(s: &str) -> bool {
    matches!(
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
fn safe_flow(s: &str) -> bool {
    s.len() <= 64 && !s.starts_with('_') && !s.starts_with("model_") && !keyword(s)
}
fn bad_node_name(nodes: &Value, p: &str) -> Option<String> {
    for (i, n) in nodes.as_array().unwrap().iter().enumerate() {
        let np = format!("{p}/{i}");
        if !safe_flow(n["name"].as_str().unwrap()) {
            return Some(format!("{np}/name"));
        }
        if n["op"] == "transaction" {
            if let Some(x) = bad_node_name(&n["steps"], &format!("{np}/steps")) {
                return Some(x);
            }
        }
        if n["op"] == "if" {
            for arm in ["then", "else"] {
                if let Some(x) = bad_node_name(&n[arm], &format!("{np}/{arm}")) {
                    return Some(x);
                }
            }
            for key in n["exports"].as_object().unwrap().keys() {
                if !safe_flow(key) {
                    return Some(format!("{np}/exports/{key}"));
                }
            }
        }
    }
    None
}
fn bad_flow_name(p: &Value) -> Option<String> {
    let f = &p["flow"];
    if !safe_flow(f["name"].as_str().unwrap()) {
        return Some("/flow/name".into());
    }
    for (i, v) in f["inputs"].as_array().unwrap().iter().enumerate() {
        if !safe_flow(v["name"].as_str().unwrap()) {
            return Some(format!("/flow/inputs/{i}/name"));
        }
    }
    if let Some(n) = bad_node_name(&f["steps"], "/flow/steps") {
        return Some(n);
    }
    for key in f["result"].as_object().unwrap().keys() {
        if !safe_flow(key) {
            return Some(format!("/flow/result/{key}"));
        }
    }
    None
}
fn named(p: &Value, ptr: &str, detail: &str, form: &str) -> At {
    let _ = p;
    at(ptr, ptr, detail, form)
}
const RESERVED: &[&str] = &[
    "parent_id",
    "child_id",
    "before",
    "limit",
    "offset",
    "db",
    "date",
    "datetime",
    "model_config",
    "model_fields",
    "model_dump",
    "revision_id",
    "settings",
    "save",
    "insert",
    "delete",
    "get",
    "find",
    "find_one",
];
fn related_location(p: &Value, e: &crate::SomError) -> Option<At> {
    let d = e.detail.as_str();
    let roles = ["parent", "child"];
    let fk = p["relation"]["foreign_key"].as_str().unwrap_or("");
    let simple = match d {
        "invalid package" => Some("/package"),
        "invalid foreign key" => Some("/relation/foreign_key"),
        "only restrict deletion is supported" => Some("/relation/on_delete"),
        "down_revision must be null" => Some("/migration/down_revision"),
        "child path conflicts with a workflow route" => Some("/resources/child/path"),
        "numeric workflow requires a numeric value_field" | "this workflow has no value_field" => {
            Some("/capabilities/value_field")
        }
        "decimal summary requires a required string group_field"
        | "this workflow has no group_field" => Some("/capabilities/group_field"),
        _ => None,
    };
    if let Some(ptr) = simple {
        return Some(named(
            p,
            ptr,
            d,
            "Use the existing version-specific typed contract.",
        ));
    }
    if d == "package: expected identifier"
        || d == "foreign_key: expected identifier"
        || d == "migration.revision: expected identifier"
    {
        let ptr = if d.starts_with("package:") {
            "/package"
        } else if d.starts_with("foreign_key:") {
            "/relation/foreign_key"
        } else {
            "/migration/revision"
        };
        return Some(at(ptr, ptr, d, "Use a nonkeyword Python identifier."));
    }
    let mut generated = BTreeSet::new();
    for role in roles {
        let r = &p["resources"][role];
        let rp = format!("/resources/{role}");
        let name = r["name"].as_str().unwrap_or("");
        let table = r["table"].as_str().unwrap_or("");
        let path = r["path"].as_str().unwrap_or("");
        if d == "invalid resource class name"
            && (!upper(name)
                || [
                    "Base",
                    "Field",
                    "ConfigDict",
                    "BaseModel",
                    "Mapped",
                    "BatchCreate",
                    "BatchRead",
                ]
                .contains(&name))
        {
            let ptr = format!("{rp}/name");
            return Some(at(
                &ptr,
                &ptr,
                d,
                "Use a safe capitalized resource name with at most 40 bytes.",
            ));
        }
        if d == "invalid table" && (!lower(table) || table == "alembic_version") {
            let ptr = format!("{rp}/table");
            return Some(at(
                &ptr,
                &ptr,
                d,
                "Use a safe lower-case table name with at most 40 bytes.",
            ));
        }
        for (k, context) in [("name", "resource.name"), ("table", "resource.table")] {
            if d == format!("{context}: expected identifier") && keyword(r[k].as_str().unwrap()) {
                let ptr = format!("{rp}/{k}");
                return Some(at(&ptr, &ptr, d, "Use a nonkeyword Python identifier."));
            }
        }
        let parts = path
            .strip_prefix('/')
            .unwrap_or("")
            .split('/')
            .collect::<Vec<_>>();
        let valid_path = path.starts_with('/')
            && (1..=3).contains(&parts.len())
            && parts.iter().all(|s| {
                s.bytes().next().is_some_and(|b| b.is_ascii_lowercase())
                    && s.bytes().all(|b| {
                        b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-'
                    })
            })
            && !parts.first().is_some_and(|s| {
                [
                    "healthz",
                    "docs",
                    "redoc",
                    "openapi.json",
                    "auth",
                    "objects",
                ]
                .contains(s)
            });
        if d == "invalid resource path" && !valid_path {
            let ptr = format!("{rp}/path");
            return Some(at(
                &ptr,
                &ptr,
                d,
                "Use one to three safe lower-case route segments.",
            ));
        }
        let fields = r["fields"].as_array().unwrap();
        if d == "expected 1..8 fields per resource" && !(1..=8).contains(&fields.len()) {
            let ptr = format!("{rp}/fields");
            return Some(at(
                &ptr,
                &ptr,
                d,
                "Use one through eight fields per resource.",
            ));
        }
        let mut seen = BTreeSet::new();
        for (i, f) in fields.iter().enumerate() {
            let fp = format!("{rp}/fields/{i}");
            let n = f["name"].as_str().unwrap();
            let ty = f["type"].as_str().unwrap();
            let required = f["required"] == true;
            let field_invalid = ["id", "metadata", "registry", "created_at", "updated_at"]
                .contains(&n)
                || !seen.insert(n);
            if d == format!("fields[{i}].name: expected identifier") && keyword(n) {
                let ptr = format!("{fp}/name");
                return Some(at(&ptr, &ptr, d, "Use a nonkeyword Python identifier."));
            }
            if d == format!("fields[{i}]: invalid field") && field_invalid {
                let ptr = format!("{fp}/name");
                return Some(at(&ptr, &ptr, d, "Use a unique nonreserved field name."));
            }
            if d == format!("fields[{i}]: expected keys")
                || d.starts_with(&format!("fields[{i}]: expected "))
            {
                if required && f.get("default").is_some() {
                    let ptr = format!("{fp}/default");
                    return Some(at(
                        &ptr,
                        &ptr,
                        d,
                        "Omit default clauses on required fields.",
                    ));
                }
            }
            if d == format!("fields[{i}].default: expected null or false")
                && !required
                && !f["default"].is_null()
                && !(ty == "bool" && f["default"] == false)
            {
                let ptr = format!("{fp}/default");
                return Some(at(
                    &ptr,
                    &ptr,
                    if f["default"].is_string() {
                        "A string default is not a null default."
                    } else {
                        "The default is not supported by this field type."
                    },
                    "Use JSON null without quotes for a null default.",
                ));
            }
            if d == "reserved field name"
                && (!lower(n) || RESERVED.contains(&n) || n == fk || n.starts_with("model_"))
            {
                let ptr = format!("{fp}/name");
                return Some(at(
                    &ptr,
                    &ptr,
                    d,
                    "Use a nonreserved lower-case field name.",
                ));
            }
            if d == "dates must be optional with null default"
                && ty == "date"
                && (required || f["unique"] == true || !f["default"].is_null())
            {
                let suffix = if required {
                    "required"
                } else if f["unique"] == true {
                    "unique"
                } else {
                    "default"
                };
                let ptr = format!("{fp}/{suffix}");
                return Some(at(
                    &ptr,
                    &ptr,
                    d,
                    "Declare optional nonunique dates with null default.",
                ));
            }
        }
        let q = &r["query"];
        for key in ["filter_field", "date_field"] {
            if d == format!("{key}: expected identifier") && q[key].as_str().is_some_and(keyword) {
                let ptr = format!("{rp}/query/{key}");
                return Some(at(&ptr, &ptr, d, "Use a nonkeyword query field name."));
            }
        }
        if d == "max_limit must be 20..100"
            && q["max_limit"]
                .as_u64()
                .is_none_or(|n| !(20..=100).contains(&n))
        {
            let ptr = format!("{rp}/query/max_limit");
            return Some(at(&ptr, &ptr, d, "Use an integer from 20 through 100."));
        }
        if d == "invalid query field" {
            for (key, ty) in [("filter_field", "bool"), ("date_field", "date")] {
                if !q[key].is_null()
                    && !fields.iter().any(|f| {
                        f["name"] == q[key]
                            && f["type"] == ty
                            && f["unique"] == false
                            && f["required"] == false
                            && (ty != "bool" || f["default"] == false)
                    })
                {
                    let ptr = format!("{rp}/query/{key}");
                    return Some(at(
                        &ptr,
                        &ptr,
                        d,
                        "Select a supported optional nonunique query field.",
                    ));
                }
            }
        }
        if d == "date filtering requires a boolean filter and one date" {
            let dates = fields
                .iter()
                .filter(|f| f["type"] == "date")
                .collect::<Vec<_>>();
            if dates.len() > 1
                || (!dates.is_empty()
                    && (q["date_field"] != dates[0]["name"] || q["filter_field"].is_null()))
                || (dates.is_empty() && !q["date_field"].is_null())
            {
                let key = if !dates.is_empty() && q["filter_field"].is_null() {
                    "filter_field"
                } else {
                    "date_field"
                };
                let ptr = format!("{rp}/query/{key}");
                return Some(at(
                    &ptr,
                    &ptr,
                    d,
                    "Select one optional date field and a boolean exact filter.",
                ));
            }
        }
        for suffix in ["", "Create", "Read", "Patch"] {
            if !generated.insert(format!("{name}{suffix}"))
                && d == "duplicate generated resource name"
            {
                let ptr = format!("{rp}/name");
                return Some(at(
                    &ptr,
                    &ptr,
                    d,
                    "Use resource names whose generated names do not collide.",
                ));
            }
        }
    }
    if d == "resource tables and paths must be distinct" {
        let key = if p["resources"]["parent"]["table"] == p["resources"]["child"]["table"] {
            "table"
        } else {
            "path"
        };
        let ptr = format!("/resources/child/{key}");
        return Some(at(
            &ptr,
            &ptr,
            d,
            "Use distinct parent and child paths and tables.",
        ));
    }
    None
}
fn common(p: &Value, e: &crate::SomError) -> Option<At> {
    if e.code == "UNSUPPORTED_CONTRACT" {
        let expected = if p["version"] == "som.prompt.v7" {
            "related-flow.v1"
        } else {
            "related-business.v1"
        };
        let key = if p["contract"]["id"] != expected {
            "id"
        } else {
            "sha256"
        };
        let ptr = format!("/contract/{key}");
        return Some(at(
            &ptr,
            &ptr,
            "Contract identity or hash differs from the pinned public contract.",
            "Bind the exact public contract ID and hash.",
        ));
    }
    let version = p["version"]
        .as_str()
        .unwrap()
        .chars()
        .last()
        .unwrap()
        .to_digit(10)
        .unwrap();
    if version == 7 && (e.detail == "unsafe flow name" || e.detail.starts_with("flow name:")) {
        if let Some(ptr) = bad_flow_name(p) {
            let token = if ptr.starts_with("/flow/result/") {
                format!("{ptr}/@key")
            } else {
                ptr.clone()
            };
            return Some(at(
                &ptr,
                &token,
                e.detail.clone(),
                "Use a safe nonreserved flow name with at most 64 bytes.",
            ));
        }
    }
    let resources: Vec<(&str, &Value)> = if version <= 4 {
        vec![("/resource", &p["resource"])]
    } else {
        vec![
            ("/resources/parent", &p["resources"]["parent"]),
            ("/resources/child", &p["resources"]["child"]),
        ]
    };
    for (rp, r) in resources {
        for (i, f) in r["fields"].as_array().unwrap().iter().enumerate() {
            let fp = format!("{rp}/fields/{i}");
            if e.detail.contains("default:")
                && version >= 3
                && f["required"] == false
                && !f["default"].is_null()
                && !(f["type"] == "bool" && f["default"] == false)
            {
                let ptr = format!("{fp}/default");
                return Some(at(
                    &ptr,
                    &ptr,
                    if f["default"].is_string() {
                        "A string default is not a null default."
                    } else {
                        "The default is not supported by this field type."
                    },
                    "Use JSON null without quotes for a null default.",
                ));
            }
        }
    }
    if version >= 5 {
        if let Some(a) = related_location(p, e) {
            return Some(a);
        }
    }
    if e.detail.starts_with("prompt.") {
        let part = e.detail.split(':').next().unwrap();
        let mut ptr = part
            .strip_prefix("prompt")
            .unwrap()
            .replace('.', "/")
            .replace('[', "/")
            .replace(']', "");
        if part.contains("fields[") && !part.contains("].") {
            if let Some(f) = p.pointer(&ptr) {
                let suffix =
                    if f.get("default").is_some() && (f["required"] == true || version <= 2) {
                        "default"
                    } else if f["type"] == "date" && version < 4 {
                        "type"
                    } else {
                        "name"
                    };
                ptr.push('/');
                ptr.push_str(suffix);
            }
        }
        if e.code == "UNSUPPORTED_LOGIC" {
            let choices = match version {
                1 => crate::LOGIC_STEPS,
                2 => crate::LOGIC_STEPS_V2,
                3 => crate::LOGIC_STEPS_V3,
                _ => crate::LOGIC_STEPS_V4,
            };
            let unknown = p["logic"]
                .as_array()
                .unwrap()
                .iter()
                .position(|s| !choices.contains(&s.as_str().unwrap()));
            ptr = unknown
                .map(|i| format!("/logic/{i}"))
                .unwrap_or_else(|| "/logic".into());
        }
        if e.code == "UNSUPPORTED_TEST" {
            ptr = "/tests/0".into();
        }
        return Some(at(
            &ptr,
            &ptr,
            e.detail.clone(),
            "Use the existing version-specific typed contract.",
        ));
    }
    if e.detail.contains("value_field") {
        return Some(at(
            "/capabilities/value_field",
            "/capabilities/value_field",
            e.detail.clone(),
            "Select an existing numeric child field for the workflow.",
        ));
    }
    if e.detail.contains("group_field") {
        return Some(at(
            "/capabilities/group_field",
            "/capabilities/group_field",
            e.detail.clone(),
            "Select an existing required string child field.",
        ));
    }
    if e.detail.contains("foreign key") {
        return Some(at(
            "/relation/foreign_key",
            "/relation/foreign_key",
            e.detail.clone(),
            "Use a nonreserved foreign key name.",
        ));
    }
    if e.detail.contains("down_revision") {
        return Some(at(
            "/migration/down_revision",
            "/migration/down_revision",
            e.detail.clone(),
            "Use no previous migration revision.",
        ));
    }
    if e.detail.contains("flow route") || e.detail.contains("resource route collides") {
        return Some(at(
            "/resources/parent/path",
            "/resources/parent/path",
            e.detail.clone(),
            "Do not use the reserved flow routes.",
        ));
    }
    if e.code == "UNSUPPORTED_TEST" {
        return Some(at(
            "/tests/0",
            "/tests/0",
            e.detail.clone(),
            "Use the exact version-specific check sentence.",
        ));
    }
    if e.code == "UNSUPPORTED_LOGIC" {
        let key = if version >= 6 { "operations" } else { "logic" };
        return Some(at(
            &format!("/{key}/0"),
            &format!("/{key}/0"),
            e.detail.clone(),
            "Use the exact supported operations for this prompt version.",
        ));
    }
    None
}
pub(super) fn localize(p: &Value, e: &crate::SomError, map: &SourceMap) -> CompilerError {
    let mut found = common(p, e);
    if found.is_none() && p["version"] == "som.prompt.v7" {
        let f = &p["flow"];
        let mut names = BTreeMap::new();
        all_names(&f["steps"], "/flow/steps", &mut names);
        let inputs = f["inputs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| {
                (
                    v["name"].as_str().unwrap().into(),
                    v["type"].as_str().unwrap().into(),
                )
            })
            .collect();
        let mut w = Walker {
            prompt: p,
            inputs,
            all_nodes: names,
            authorized: false,
        };
        match w.walk(&f["steps"], "/flow/steps", &BTreeMap::new(), false) {
            Err(a) => found = Some(a),
            Ok(visible) => {
                if !w.authorized {
                    found = Some(at(
                        "/flow/steps",
                        "/flow/steps",
                        "Flow requires authorization.",
                        "Place an authorize step first.",
                    ));
                } else {
                    for (n, r) in f["result"].as_object().unwrap() {
                        let ptr = format!("/flow/result/{n}");
                        match w.reference(r, &ptr, &visible, None) {
                            Err(a) => {
                                found = Some(a);
                                break;
                            }
                            Ok(k) if k == "bytes" => {
                                found = Some(at(
                                    &ptr,
                                    &ptr,
                                    "The flow result cannot expose bytes.",
                                    "Return a supported non-bytes value.",
                                ));
                                break;
                            }
                            _ => {}
                        }
                    }
                }
            }
        }
    }
    // This branch is defensive for future validator codes. It must not invent
    // a prompt pointer or blame an unrelated member when localization is absent.
    let Some(a) = found else {
        return error(
            e.code,
            SourceSpan {
                line: 1,
                column: 1,
                end_line: 1,
                end_column: 1,
                byte_start: 0,
                byte_end: 0,
            },
            None,
            format!(
                "{} Source location is unavailable for this validator result.",
                e.detail
            ),
            "Use the existing public typed request contract.",
        );
    };
    let mut key = a.token.as_str();
    let mut sp = map.get(key);
    while sp.is_none() {
        if let Some((parent, _)) = key.rsplit_once('/') {
            key = parent;
            sp = map.get(key);
        } else {
            break;
        }
    }
    let span = sp.cloned().unwrap_or_else(|| {
        map.values().next().cloned().unwrap_or(SourceSpan {
            line: 1,
            column: 1,
            end_line: 1,
            end_column: 1,
            byte_start: 0,
            byte_end: 0,
        })
    });
    error(e.code, span, Some(&a.pointer), a.cause, a.allowed)
}

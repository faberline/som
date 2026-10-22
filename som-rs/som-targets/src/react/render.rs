//! React block bodies (issue #127). A fixed file is one block that renders its reference bytes
//! (super::FIXED). src/schema.ts and README.md are rendered by their blocks in plan order and give
//! the bytes of tests/react-output-v1/render_reference.py, which are already in prettier's format.
use serde_json::Value;
use std::collections::BTreeMap;

const PAGE_SIZE: u64 = 20;
const LINE_WIDTH: usize = 80;

/// One prompt field in the terms of src/spec.ts FieldSpec.
pub struct Field {
    pub name: String,
    pub kind: String,
    pub required: bool,
    pub nullable: bool,
    pub unique: bool,
    pub default: Value,
}

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
    value[key]
        .as_str()
        .ok_or_else(|| format!("{key} is not a string"))
}

fn flag(value: &Value, key: &str) -> Result<bool, String> {
    value[key]
        .as_bool()
        .ok_or_else(|| format!("{key} is not a boolean"))
}

/// The fields of a resolved fields param. A v1 or v2 optional field has no default member and
/// defaults to null; an optional field is nullable when its default is null.
pub fn parse_fields(value: &Value) -> Result<Vec<Field>, String> {
    let rows = value
        .as_array()
        .ok_or_else(|| "fields is not an array".to_owned())?;
    rows.iter()
        .map(|row| {
            let required = flag(row, "required")?;
            let default = if required {
                Value::Null
            } else {
                row.get("default").cloned().unwrap_or(Value::Null)
            };
            Ok(Field {
                name: text(row, "name")?.to_owned(),
                kind: text(row, "type")?.to_owned(),
                required,
                nullable: !required && default.is_null(),
                unique: flag(row, "unique")?,
                default,
            })
        })
        .collect()
}

fn ts_type(kind: &str) -> Result<&'static str, String> {
    match kind {
        "str" | "date" => Ok("string"),
        "int" | "float" => Ok("number"),
        "bool" => Ok("boolean"),
        other => Err(format!("unsupported field type {other}")),
    }
}

fn field_type(field: &Field) -> Result<String, String> {
    let base = ts_type(&field.kind)?;
    Ok(if field.nullable {
        format!("{base} | null")
    } else {
        base.to_owned()
    })
}

/// Python's repr of a finite float, which json.dumps writes.
pub fn py_float(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .to_owned();
    }
    let sci = format!("{:e}", value.abs());
    let (mantissa, exponent) = sci.split_once('e').expect("{:e} has an exponent");
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let exponent: i64 = exponent.parse().expect("{:e} exponent");
    let decpt = exponent + 1;
    let count = digits.len() as i64;
    let body = if -4 < decpt && decpt <= 16 {
        if decpt <= 0 {
            format!("0.{}{digits}", "0".repeat((-decpt) as usize))
        } else if decpt < count {
            let (whole, fraction) = digits.split_at(decpt as usize);
            format!("{whole}.{fraction}")
        } else {
            format!("{digits}{}.0", "0".repeat((decpt - count) as usize))
        }
    } else {
        let (first, rest) = digits.split_at(1);
        let fraction = if rest.is_empty() {
            String::new()
        } else {
            format!(".{rest}")
        };
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{first}{fraction}e{sign}{:02}", exponent.abs())
    };
    if value < 0.0 {
        format!("-{body}")
    } else {
        body
    }
}

/// Python's json.dumps(value, ensure_ascii=False) for a prompt value.
pub fn literal(value: &Value) -> String {
    match value {
        Value::Number(number) if number.is_f64() => py_float(number.as_f64().unwrap_or_default()),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(literal).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(members) => format!(
            "{{{}}}",
            members
                .iter()
                .map(|(key, item)| format!("{}: {}", Value::String(key.clone()), literal(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => other.to_string(),
    }
}

fn object(head: &str, members: &[String]) -> String {
    let mut out = format!("{head}\n");
    for member in members {
        out.push_str(&format!("  {member};\n"));
    }
    out.push_str("};\n\n");
    out
}

fn header(params: &Value) -> Result<String, String> {
    Ok(format!(
        "// Generated from the {} prompt for package {}. Edit the prompt, not this file.\n\
         import type {{ FieldSpec, QuerySpec, ResourceSpec }} from \"./spec.ts\";\n\n",
        text(params, "version")?,
        text(params, "package")?
    ))
}

fn row(fields: &[Field], timestamps: bool) -> Result<String, String> {
    let mut members = vec!["id: number".to_owned()];
    for field in fields {
        members.push(format!("{}: {}", field.name, field_type(field)?));
    }
    if timestamps {
        members.push("created_at: string".to_owned());
        members.push("updated_at: string".to_owned());
    }
    Ok(object("export type Row = {", &members))
}

fn create(fields: &[Field]) -> Result<String, String> {
    let members = fields
        .iter()
        .map(|field| {
            let optional = if field.required { "" } else { "?" };
            Ok(format!("{}{optional}: {}", field.name, field_type(field)?))
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(object("export type CreateBody = {", &members))
}

fn patch(fields: &[Field]) -> Result<String, String> {
    let members = fields
        .iter()
        .map(|field| Ok(format!("{}?: {}", field.name, field_type(field)?)))
        .collect::<Result<Vec<_>, String>>()?;
    Ok(object("export type PatchBody = {", &members))
}

fn filter<'a>(fields: &'a [Field], params: &Value) -> Result<&'a Field, String> {
    let name = text(params, "filter_field")?;
    fields
        .iter()
        .find(|field| field.name == name)
        .ok_or_else(|| format!("filter field {name} is not a field"))
}

/// The date field, or None when the param is absent or null.
fn date_field(params: &Value) -> Result<Option<&str>, String> {
    match &params["date_field"] {
        Value::Null => Ok(None),
        Value::String(name) => Ok(Some(name)),
        _ => Err("date_field is not a string or null".to_owned()),
    }
}

/// ListQuery for v2 to v4: `param` is filter_value (v2) or the filter field name (v3, v4).
fn list_query(fields: &[Field], params: &Value, service: bool) -> Result<String, String> {
    let field = filter(fields, params)?;
    let param = if service {
        field.name.as_str()
    } else {
        "filter_value"
    };
    let mut members = vec![format!("{param}?: {}", ts_type(&field.kind)?)];
    if date_field(params)?.is_some() {
        members.push("before?: string".to_owned());
    }
    members.push("limit?: number".to_owned());
    members.push("offset?: number".to_owned());
    Ok(object("export type ListQuery = {", &members))
}

fn resource(params: &Value, timestamps: bool) -> Result<String, String> {
    Ok(format!(
        "export const RESOURCE: ResourceSpec = {{\n  name: {},\n  path: {},\n  timestamps: {timestamps},\n}};\n\n",
        literal(&Value::String(text(params, "name")?.to_owned())),
        literal(&Value::String(text(params, "path")?.to_owned())),
    ))
}

fn field_specs(fields: &[Field]) -> String {
    let mut out = "export const FIELDS: readonly FieldSpec[] = [\n".to_owned();
    for field in fields {
        out.push_str(&format!(
            "  {{\n    name: {},\n    type: {},\n    required: {},\n    nullable: {},\n    unique: {},\n",
            literal(&Value::String(field.name.clone())),
            literal(&Value::String(field.kind.clone())),
            field.required,
            field.nullable,
            field.unique,
        ));
        if !field.required {
            out.push_str(&format!("    default: {},\n", literal(&field.default)));
        }
        out.push_str("  },\n");
    }
    out.push_str("];\n\n");
    out
}

/// An object member on one line when prettier keeps it there, else one member per line.
fn inline_or_block(indent: &str, key: &str, members: &[(&str, String)], end: &str) -> String {
    let joined = members
        .iter()
        .map(|(name, value)| format!("{name}: {value}"))
        .collect::<Vec<_>>()
        .join(", ");
    let one = format!("{indent}{key}: {{ {joined} }}{end}");
    if one.chars().count() <= LINE_WIDTH {
        return format!("{one}\n");
    }
    let mut out = format!("{indent}{key}: {{\n");
    for (name, value) in members {
        out.push_str(&format!("{indent}  {name}: {value},\n"));
    }
    out.push_str(&format!("{indent}}}{end}\n"));
    out
}

fn query(fields: &[Field], params: &Value, service: bool) -> Result<String, String> {
    let field = filter(fields, params)?;
    let param = if service {
        field.name.clone()
    } else {
        "filter_value".to_owned()
    };
    let before = date_field(params)?.map_or(Value::Null, |name| Value::String(name.to_owned()));
    let max_limit = &params["max_limit"];
    if !max_limit.is_number() {
        return Err("max_limit is not a number".to_owned());
    }
    let mut out = "export const QUERY: QuerySpec | null = {\n".to_owned();
    out.push_str(&inline_or_block(
        "  ",
        "filter",
        &[
            ("field", literal(&Value::String(field.name.clone()))),
            ("param", literal(&Value::String(param))),
            ("type", literal(&Value::String(field.kind.clone()))),
        ],
        ",",
    ));
    out.push_str(&format!(
        "  before: {},\n  pageSize: {PAGE_SIZE},\n  maxLimit: {},\n}};\n",
        literal(&before),
        literal(max_limit)
    ));
    Ok(out)
}

fn table(rows: &[Vec<String>]) -> String {
    let columns = rows[0].len();
    let widths: Vec<usize> = (0..columns)
        .map(|index| {
            rows.iter()
                .map(|row| row[index].chars().count())
                .max()
                .unwrap_or(0)
                .max(3)
        })
        .collect();
    let line = |cells: &[String]| {
        let padded: Vec<String> = cells
            .iter()
            .zip(&widths)
            .map(|(cell, width)| {
                let pad = width.saturating_sub(cell.chars().count());
                format!("{cell}{}", " ".repeat(pad))
            })
            .collect();
        format!("| {} |\n", padded.join(" | "))
    };
    let mut out = line(&rows[0]);
    let rule: Vec<String> = widths.iter().map(|width| "-".repeat(*width)).collect();
    out.push_str(&line(&rule));
    for row in &rows[1..] {
        out.push_str(&line(row));
    }
    out
}

fn yes(value: bool) -> String {
    if value { "yes" } else { "no" }.to_owned()
}

/// README.md. `listed` is the `/` page text: every row (v1) or the filtered page (v2 to v4).
fn readme(params: &Value, filtered: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let listed = if filtered {
        let field = filter(&fields, params)?;
        let mut listed = format!(
            "the list, {PAGE_SIZE} rows per page, filtered by `{}`",
            field.name
        );
        if let Some(date) = date_field(params)? {
            listed.push_str(&format!(" and `{date}` before a date"));
        }
        listed.push('.');
        listed
    } else {
        "the list of every row in id order.".to_owned()
    };
    let mut rows = vec![
        ["Field", "Type", "Required", "Nullable", "Default"]
            .iter()
            .map(|cell| (*cell).to_owned())
            .collect::<Vec<_>>(),
    ];
    for field in &fields {
        rows.push(vec![
            format!("`{}`", field.name),
            field.kind.clone(),
            yes(field.required),
            yes(field.nullable),
            if field.required {
                String::new()
            } else {
                literal(&field.default)
            },
        ]);
    }
    let name = text(params, "name")?;
    let path = text(params, "path")?;
    Ok(format!(
        "# `{name}` front end\n\
         \n\
         A React front end for the `{path}` service that som generated from the same prompt.\n\
         \n\
         ## Run\n\
         \n\
         ```bash\n\
         npm ci\n\
         SOM_API_URL=http://127.0.0.1:8000 npm run dev\n\
         ```\n\
         \n\
         The dev server and `npm run preview` send `/api/*` to `SOM_API_URL` without the `/api` prefix.\n\
         `SOM_API_URL` defaults to `http://127.0.0.1:8000`.\n\
         \n\
         ## Check\n\
         \n\
         ```bash\n\
         npm run typecheck\n\
         npm run format:check\n\
         npm test\n\
         npm run build\n\
         ```\n\
         \n\
         ## Pages\n\
         \n\
         - `/`: {listed}\n\
         - `/new`: the create form.\n\
         - `/:id`: one row, with edit and delete.\n\
         - `/:id/edit`: the edit form. It sends only the fields you changed.\n\
         \n\
         ## Fields\n\
         \n\
         {}\
         \n\
         Only `src/schema.ts` and this file depend on the prompt. Every other file is the same in every generated product.\n",
        table(&rows)
    ))
}

fn resolve(params: &Value, prompt: &Value) -> Value {
    let Some(members) = params.as_object() else {
        return params.clone();
    };
    let resolved: serde_json::Map<String, Value> = members
        .iter()
        .map(|(key, value)| {
            let found = match value.get("$ref").and_then(Value::as_str) {
                Some(name) if value.as_object().is_some_and(|object| object.len() == 1) => prompt
                    .pointer(&format!("/{}", name.replace('.', "/")))
                    .cloned()
                    .unwrap_or(Value::Null),
                _ => value.clone(),
            };
            (key.clone(), found)
        })
        .collect();
    Value::Object(resolved)
}

/// Render every file in a validated React DSL plan.
pub fn files(prompt: &Value, dsl: &Value) -> Result<BTreeMap<String, String>, String> {
    let mut out = BTreeMap::new();
    let entries = dsl["files"]
        .as_array()
        .ok_or_else(|| "dsl.files is not an array".to_owned())?;
    for entry in entries {
        let path = text(entry, "path")?;
        let blocks = entry["blocks"]
            .as_array()
            .ok_or_else(|| format!("{path}: blocks is not an array"))?;
        let mut content = String::new();
        for item in blocks {
            let id = text(item, "id")?;
            let params = resolve(&item["params"], prompt);
            content.push_str(&block(id, &params).map_err(|detail| format!("{id}: {detail}"))?);
        }
        out.insert(path.to_owned(), content);
    }
    Ok(out)
}

/// The text of one React block.
pub fn block(id: &str, params: &Value) -> Result<String, String> {
    if let Some((_, _, text)) = super::FIXED.iter().find(|(_, block, _)| *block == id) {
        return Ok((*text).to_owned());
    }
    let fields = || parse_fields(&params["fields"]);
    match id {
        "react.schema.header" => header(params),
        "react.schema.row.plain" => row(&fields()?, false),
        "react.schema.row.timestamps" => row(&fields()?, true),
        "react.schema.create" => create(&fields()?),
        "react.schema.patch" => patch(&fields()?),
        // A v1 service takes no list query.
        "react.schema.list_query.none" => Ok(object(
            "export type ListQuery = {",
            &["limit?: never".to_owned(), "offset?: never".to_owned()],
        )),
        "react.schema.list_query.filter_value" => list_query(&fields()?, params, false),
        "react.schema.list_query.field" | "react.schema.list_query.field.date" => {
            list_query(&fields()?, params, true)
        }
        "react.schema.resource.plain" => resource(params, false),
        "react.schema.resource.timestamps" => resource(params, true),
        "react.schema.fields" => Ok(field_specs(&fields()?)),
        "react.schema.query.none" => {
            Ok("export const QUERY: QuerySpec | null = null;\n".to_owned())
        }
        "react.schema.query.filter_value" => query(&fields()?, params, false),
        "react.schema.query.field" | "react.schema.query.field.date" => {
            query(&fields()?, params, true)
        }
        "react.readme.all" => readme(params, false),
        "react.readme.filter" | "react.readme.filter.date" => readme(params, true),
        other => Err(format!("unknown React block {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn py_float_matches_python_repr() {
        for (value, expected) in [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (1.5, "1.5"),
            (-2.25, "-2.25"),
            (0.1, "0.1"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (123456.789, "123456.789"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.2345e16, "1.2345e+16"),
            (9999999999999998.0, "9999999999999998.0"),
            (1e22, "1e+22"),
            (1e300, "1e+300"),
            (5e-324, "5e-324"),
        ] {
            assert_eq!(py_float(value), expected, "{value:?}");
        }
    }

    #[test]
    fn literal_matches_json_dumps() {
        assert_eq!(literal(&json!(null)), "null");
        assert_eq!(literal(&json!(true)), "true");
        assert_eq!(literal(&json!(3)), "3");
        assert_eq!(literal(&json!(-7)), "-7");
        assert_eq!(
            literal(&serde_json::from_str::<Value>("3.0").unwrap()),
            "3.0"
        );
        assert_eq!(
            literal(&serde_json::from_str::<Value>("1e2").unwrap()),
            "100.0"
        );
        assert_eq!(
            literal(&json!("a\"b\\c\nd\u{1}é")),
            "\"a\\\"b\\\\c\\nd\\u0001é\""
        );
    }

    #[test]
    fn long_filters_break_onto_lines() {
        let short = inline_or_block(
            "  ",
            "filter",
            &[("field", "\"a\"".to_owned()), ("param", "\"a\"".to_owned())],
            ",",
        );
        assert_eq!(short, "  filter: { field: \"a\", param: \"a\" },\n");
        let long_name = format!("\"{}\"", "x".repeat(40));
        let long = inline_or_block(
            "  ",
            "filter",
            &[("field", long_name.clone()), ("param", long_name)],
            ",",
        );
        assert!(long.starts_with("  filter: {\n    field: \""));
        assert!(long.ends_with("  },\n"));
    }

    #[test]
    fn table_pads_by_characters() {
        let rows = vec![
            vec!["Field".to_owned(), "D".to_owned()],
            vec!["`é`".to_owned(), "\"ü\"".to_owned()],
        ];
        assert_eq!(
            table(&rows),
            "| Field | D   |\n| ----- | --- |\n| `é`   | \"ü\" |\n"
        );
    }
}

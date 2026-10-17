//! The workflows of a capability_postgresql product (issue #53 E). json_export answers the
//! children of a parent as compact JSON; csv, gzip_export, decimal_summary and statistics answer
//! them as the Python product does, and csv also imports children.
use serde_json::Value;

use super::super::{Field, Kind};
use super::Resource;

/// The workflow a prompt selects.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Flow {
    Json,
    Csv,
    Gzip,
    Decimal,
    Statistics,
}

impl Flow {
    /// The workflow param. Without one the workflow is json_export.
    pub(super) fn read(params: &Value) -> Result<Self, String> {
        match &params["workflow"] {
            Value::Null => Ok(Self::Json),
            Value::String(name) => match name.as_str() {
                "json_export" => Ok(Self::Json),
                "csv" => Ok(Self::Csv),
                "gzip_export" => Ok(Self::Gzip),
                "decimal_summary" => Ok(Self::Decimal),
                "statistics" => Ok(Self::Statistics),
                other => Err(format!("no capability workflow {other}")),
            },
            _ => Err("param workflow is not a string".to_owned()),
        }
    }
}

/// The workflow with the child fields it reads.
pub(super) struct Workflow {
    pub(super) flow: Flow,
    value: Option<usize>,
    group: Option<usize>,
}

impl Workflow {
    pub(super) fn read(params: &Value, child: &Resource) -> Result<Self, String> {
        let flow = Flow::read(params)?;
        let field = |key: &str| match &params[key] {
            Value::Null => Ok(None),
            Value::String(name) => child
                .fields
                .iter()
                .position(|field| field.name == *name)
                .map(Some)
                .ok_or_else(|| format!("param {key} names no child field {name}")),
            _ => Err(format!("param {key} is not a string")),
        };
        let (value, group) = (field("value_field")?, field("group_field")?);
        let numeric =
            value.is_some_and(|index| matches!(child.fields[index].kind, Kind::Int | Kind::Float));
        let grouped = group.is_some_and(|index| {
            child.fields[index].kind == Kind::Str && !child.fields[index].nullable()
        });
        match flow {
            Flow::Decimal if !numeric || !grouped => Err(
                "decimal_summary needs a numeric value_field and a required string group_field"
                    .to_owned(),
            ),
            Flow::Statistics if !numeric => {
                Err("statistics needs a numeric value_field".to_owned())
            }
            _ => Ok(Self { flow, value, group }),
        }
    }

    fn value<'a>(&self, child: &'a Resource) -> &'a Field {
        &child.fields[self.value.unwrap_or_default()]
    }

    fn group<'a>(&self, child: &'a Resource) -> &'a Field {
        &child.fields[self.group.unwrap_or_default()]
    }

    /// The README name of the workflow.
    pub(super) fn title(&self) -> &'static str {
        match self.flow {
            Flow::Json => "the JSON export workflow (`workflow: json_export`)",
            Flow::Csv => "the CSV workflow (`workflow: csv`)",
            Flow::Gzip => "the gzip export workflow (`workflow: gzip_export`)",
            Flow::Decimal => "the decimal summary workflow (`workflow: decimal_summary`)",
            Flow::Statistics => "the statistics workflow (`workflow: statistics`)",
        }
    }

    /// The README lines of the export route, and of the import route of csv.
    pub(super) fn routes(&self, parents: &str, child: &Resource) -> String {
        let export = format!("- `GET {parents}/{{parent_id}}/export`: ");
        let limit = "413 above 1000 rows.\n";
        match self.flow {
            Flow::Json => {
                format!("{export}every child of the parent as JSON, in id order. {limit}")
            }
            Flow::Csv => format!(
                "{export}every child of the parent as CSV, in id order. The header\n  holds the child fields in order and a null cell is `\\N`. {limit}- `POST {parents}/{{parent_id}}/import`: create 1 to 20 children from such a CSV body in one\n  transaction. 201 with the children, or 422 `invalid CSV input`.\n"
            ),
            Flow::Gzip => format!(
                "{export}every child of the parent as gzip-compressed JSON, in id order.\n  {limit}"
            ),
            Flow::Decimal => format!(
                "{export}`{{\"totals\": {{...}}}}`, the decimal total of `{}`\n  per `{}` as a string, by group in order. A null `{}` is left out. {limit}",
                self.value(child).name,
                self.group(child).name,
                self.value(child).name
            ),
            Flow::Statistics => format!(
                "{export}the `count`, `mean`, `median`, `minimum` and\n  `maximum` of `{}`. A null `{}` is left out. {limit}",
                self.value(child).name,
                self.value(child).name
            ),
        }
    }

    /// The export handler of a workflow other than json_export, the import handler of csv, and
    /// the helpers they use.
    pub(super) fn handlers(&self, child: &Resource) -> String {
        let fields = &child.fields;
        let has = |kind: Kind| fields.iter().any(|field| field.kind == kind);
        let mut out = String::new();
        let (doc, body, floats) = match self.flow {
            Flow::Json => return String::new(),
            Flow::Csv => (
                "/// Every child of a parent as CSV in field order, at most 1000 rows. A null cell is \\N.\n".to_owned(),
                csv_body(fields),
                has(Kind::Float),
            ),
            Flow::Gzip => (
                "/// Every child of a parent as gzip-compressed compact JSON, at most 1000 rows.\n"
                    .to_owned(),
                GZIP_BODY.to_owned(),
                false,
            ),
            Flow::Decimal => (
                format!(
                    "/// The decimal total of `{}` per `{}`, at most 1000 rows.\n",
                    self.value(child).name,
                    self.group(child).name
                ),
                decimal_body(self.value(child), self.group(child)),
                self.value(child).kind == Kind::Float,
            ),
            Flow::Statistics => (
                format!(
                    "/// The count, mean, median, minimum and maximum of `{}`, at most 1000 rows.\n",
                    self.value(child).name
                ),
                statistics_body(self.value(child)),
                true,
            ),
        };
        out += &EXPORT_HEAD.replace("@DOC@", &doc);
        out += &body;
        out += "}\n";
        if self.flow == Flow::Csv {
            out += &format!(
                "\nconst CSV_HEADER: [&str; {}] = [{}];\n",
                fields.len(),
                fields
                    .iter()
                    .map(|field| format!("{:?}", field.name))
                    .collect::<Vec<_>>()
                    .join(", ")
            );
            out += CSV_WRITE;
            if has(Kind::Bool) {
                out += PY_BOOL;
            }
        }
        if floats {
            out += PY_FLOAT;
        }
        match self.flow {
            Flow::Gzip => out += GZIP,
            Flow::Decimal => out += DECIMAL,
            Flow::Statistics => out += STATISTICS,
            Flow::Csv => out += &import(fields),
            Flow::Json => {}
        }
        out
    }
}

const EXPORT_HEAD: &str = r#"
@DOC@async fn export_children(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ApiError> {
    access?;
    let mut problems = Vec::new();
    let parent = path_id(&segments(path), "parent_id", &mut problems);
    invalid(problems)?;
    let parent = known_parent(&state.pool, parent).await?;
    let rows = db::export_children(&state.pool, parent).await?;
    if rows.len() > EXPORT_MAX_ROWS {
        return Err(ApiError::TooMany);
    }
"#;

// ---------------------------------------------------------------- csv

/// The CSV cell of a field of `row`, as csv.writer writes the Python row.
fn csv_cell(field: &Field) -> String {
    let member = format!("row.{}", field.ident());
    let null = "NULL_CELL.to_owned()";
    match (field.kind, field.nullable()) {
        (Kind::Str, false) => member,
        (Kind::Str, true) => format!("{member}.unwrap_or_else(|| {null})"),
        (Kind::Int | Kind::Date, false) => format!("{member}.to_string()"),
        (Kind::Int | Kind::Date, true) => {
            format!("{member}.map_or_else(|| {null}, |value| value.to_string())")
        }
        (Kind::Float, false) => format!("py_float({member})"),
        (Kind::Float, true) => format!("{member}.map_or_else(|| {null}, py_float)"),
        (Kind::Bool, false) => format!("py_bool({member}).to_owned()"),
        (Kind::Bool, true) => {
            format!("{member}.map_or_else(|| {null}, |value| py_bool(value).to_owned())")
        }
    }
}

fn csv_body(fields: &[Field]) -> String {
    let cells: Vec<String> = fields.iter().map(csv_cell).collect();
    format!(
        "    let mut content = String::new();\n    csv_line(&mut content, &CSV_HEADER.map(str::to_owned));\n    for row in rows {{\n        csv_line(&mut content, &[{}]);\n    }}\n    Ok(([(CONTENT_TYPE, \"text/csv; charset=utf-8\")], content).into_response())\n",
        cells.join(", ")
    )
}

const CSV_WRITE: &str = r#"
/// The cell of a null, in both directions.
const NULL_CELL: &str = "\\N";

/// One csv.writer row with the line terminator "\n". A cell with a comma, a quote or a line break
/// is quoted, and so is a lone empty cell.
fn csv_line(out: &mut String, cells: &[String]) {
    for (index, cell) in cells.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        if cell.contains([',', '"', '\n', '\r']) || (cells.len() == 1 && cell.is_empty()) {
            out.push('"');
            out.push_str(&cell.replace('"', "\"\""));
            out.push('"');
        } else {
            out.push_str(cell);
        }
    }
    out.push('\n');
}
"#;

const PY_BOOL: &str = r#"
/// str(bool).
fn py_bool(value: bool) -> &'static str {
    if value { "True" } else { "False" }
}
"#;

const PY_FLOAT: &str = r#"
/// repr(float): the shortest digits that read back, in fixed notation from 1e-4 below 1e16.
fn py_float(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_owned();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf" } else { "-inf" }.to_owned();
    }
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let magnitude = exponent.unsigned_abs() as usize;
    let body = if (-4..16).contains(&exponent) {
        if exponent < 0 {
            format!("0.{}{digits}", "0".repeat(magnitude - 1))
        } else if digits.len() <= magnitude + 1 {
            format!("{digits}{}.0", "0".repeat(magnitude + 1 - digits.len()))
        } else {
            format!("{}.{}", &digits[..=magnitude], &digits[magnitude + 1..])
        }
    } else {
        let (head, tail) = digits.split_at(1);
        let point = if tail.is_empty() { "" } else { "." };
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{head}{point}{tail}e{sign}{magnitude:02}")
    };
    format!("{sign}{body}")
}
"#;

/// The reader of a CSV cell for a field kind.
fn csv_reader(kind: Kind) -> &'static str {
    match kind {
        Kind::Str => "csv_str",
        Kind::Int => "csv_int",
        Kind::Float => "csv_float",
        Kind::Bool => "csv_bool",
        Kind::Date => "csv_date",
    }
}

fn import(fields: &[Field]) -> String {
    let readers: Vec<&str> = fields.iter().map(|field| csv_reader(field.kind)).collect();
    let mut out = format!(
        "\n/// The reader of each column: the field value as pydantic reads it from a string, or None.\nconst CSV_CELLS: [fn(&str) -> Option<Value>; {}] = [{}];\n",
        readers.len(),
        readers.join(", ")
    );
    for (kind, reader) in [
        (Kind::Str, CSV_STR),
        (Kind::Int, CSV_INT),
        (Kind::Float, CSV_FLOAT),
        (Kind::Bool, CSV_BOOL),
        (Kind::Date, CSV_DATE),
    ] {
        if fields.iter().any(|field| field.kind == kind) {
            out += reader;
        }
    }
    out + CSV_READ + IMPORT
}

const CSV_STR: &str = r#"
fn csv_str(cell: &str) -> Option<Value> {
    Some(Value::from(cell))
}
"#;

const CSV_INT: &str = r#"
/// Digits with single underscores between them, as Python int() reads them.
fn py_digits(text: &str) -> bool {
    !text.is_empty()
        && !text.starts_with('_')
        && !text.ends_with('_')
        && !text.contains("__")
        && text.bytes().all(|byte| byte.is_ascii_digit() || byte == b'_')
}

/// An int: spaces around, one sign, digits and an optional fraction of zeros.
fn csv_int(cell: &str) -> Option<Value> {
    let text = cell.trim();
    let (negative, unsigned) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text.strip_prefix('+').unwrap_or(text)),
    };
    let whole = match unsigned.split_once('.') {
        Some((whole, fraction)) => {
            if fraction.is_empty() || !fraction.bytes().all(|byte| byte == b'0') {
                return None;
            }
            whole
        }
        None => unsigned,
    };
    if !py_digits(whole) {
        return None;
    }
    let digits: String = whole.chars().filter(|c| *c != '_').collect();
    let magnitude: i64 = digits.parse().ok()?;
    Some(Value::from(if negative { -magnitude } else { magnitude }))
}
"#;

const CSV_FLOAT: &str = r#"
/// A finite float: spaces around, and single underscores between digits.
fn csv_float(cell: &str) -> Option<Value> {
    let text = cell.trim();
    if text.starts_with('_') || text.ends_with('_') || text.contains("__") {
        return None;
    }
    let value: f64 = text.replace('_', "").parse().ok()?;
    value.is_finite().then(|| Value::from(value))
}
"#;

const CSV_BOOL: &str = r#"
fn csv_bool(cell: &str) -> Option<Value> {
    match cell.to_ascii_lowercase().as_str() {
        "0" | "off" | "f" | "false" | "n" | "no" => Some(Value::Bool(false)),
        "1" | "on" | "t" | "true" | "y" | "yes" => Some(Value::Bool(true)),
        _ => None,
    }
}
"#;

const CSV_DATE: &str = r#"
/// A date: YYYY-MM-DD, that date at exactly midnight, or a Unix timestamp of a midnight.
fn csv_date(cell: &str) -> Option<Value> {
    csv_day(cell).map(|day| Value::from(day.format("%Y-%m-%d").to_string()))
}

fn csv_day(text: &str) -> Option<chrono::NaiveDate> {
    let bytes = text.as_bytes();
    let shaped = bytes.len() >= 10
        && bytes[4] == b'-'
        && bytes[7] == b'-'
        && [0, 1, 2, 3, 5, 6, 8, 9].iter().all(|&index| bytes[index].is_ascii_digit());
    if !shaped {
        return timestamp_day(text);
    }
    let number = |range: std::ops::Range<usize>| text[range].parse::<u32>().ok();
    let year = i32::try_from(number(0..4)?).ok()?;
    let day = chrono::NaiveDate::from_ymd_opt(year, number(5..7)?, number(8..10)?)?;
    (year >= 1 && (bytes.len() == 10 || midnight(&bytes[10..]))).then_some(day)
}

/// A separator, then 00:00, optional zero seconds with a zero fraction, and an optional offset.
fn midnight(rest: &[u8]) -> bool {
    let Some((separator, rest)) = rest.split_first() else {
        return false;
    };
    if !matches!(separator, b'T' | b't' | b'_' | b' ') {
        return false;
    }
    let Some(mut rest) = rest.strip_prefix(b"00:00") else {
        return false;
    };
    if let Some(after) = rest.strip_prefix(b":00") {
        rest = after;
        if let Some((b'.' | b',', after)) = rest.split_first() {
            let digits = after.iter().take_while(|byte| byte.is_ascii_digit()).count();
            if digits == 0 || after[..digits.min(6)].iter().any(|byte| *byte != b'0') {
                return false;
            }
            rest = &after[digits..];
        }
    }
    zone(rest)
}

/// Nothing, Z, or a sign with hours below 24 and minutes below 60.
fn zone(rest: &[u8]) -> bool {
    let rest = match rest {
        [] | [b'Z' | b'z'] => return true,
        [b'+' | b'-', rest @ ..] | [0xe2, 0x88, 0x92, rest @ ..] => rest,
        _ => return false,
    };
    let (hours, minutes) = match rest {
        [h1, h2, b':', m1, m2] | [h1, h2, m1, m2] => ([*h1, *h2], [*m1, *m2]),
        _ => return false,
    };
    let number = |pair: [u8; 2]| {
        pair.iter()
            .all(u8::is_ascii_digit)
            .then(|| (pair[0] - b'0') * 10 + (pair[1] - b'0'))
    };
    number(hours).is_some_and(|hours| hours < 24) && number(minutes).is_some_and(|minutes| minutes < 60)
}

/// A Unix timestamp in seconds, or in milliseconds above 2e10, that is exactly a midnight.
fn timestamp_day(text: &str) -> Option<chrono::NaiveDate> {
    const DAY: i128 = 86_400_000_000;
    const WATERSHED: i128 = 20_000_000_000;
    let unsigned = text.strip_prefix(['+', '-']).unwrap_or(text);
    let (whole, fraction) = match unsigned.split_once('.') {
        Some((whole, fraction)) => (whole, Some(fraction)),
        None => (unsigned, None),
    };
    let digits = |part: &str| part.bytes().all(|byte| byte.is_ascii_digit());
    if whole.is_empty() || !digits(whole) || !digits(fraction.unwrap_or_default()) {
        return None;
    }
    let micros = if fraction.is_none() {
        let seconds = i128::from(text.parse::<i64>().ok()?);
        if seconds.abs() > WATERSHED { seconds * 1000 } else { seconds * 1_000_000 }
    } else {
        let mut value: f64 = text.parse().ok()?;
        if value.abs() > 2e10 {
            value /= 1000.0;
        }
        if value.abs() >= 9.0e18 {
            return None;
        }
        let whole = i128::from(value.trunc() as i64);
        let fraction = (value.fract().abs() * 1e6).round() as i128;
        let fraction = if value < 0.0 { -fraction } else { fraction };
        (if whole.abs() > WATERSHED { whole * 1000 } else { whole * 1_000_000 }) + fraction
    };
    if micros % DAY != 0 {
        return None;
    }
    let days = i32::try_from(micros / DAY).ok()?.checked_add(719_163)?;
    let date = chrono::NaiveDate::from_num_days_from_ce_opt(days)?;
    (1..=9999).contains(&chrono::Datelike::year(&date)).then_some(date)
}
"#;

const CSV_READ: &str = r#"
const CSV_FIELD_LIMIT: usize = 131_072;

#[derive(Clone, Copy, PartialEq, Eq)]
enum CsvState {
    StartRecord,
    StartField,
    InField,
    InQuoted,
    QuoteInQuoted,
    EatCrnl,
}

/// csv.reader(strict=True) as CPython 3.12 reads a text, one line at a time.
struct CsvReader {
    state: CsvState,
    record: Vec<String>,
    field: String,
    length: usize,
}

impl CsvReader {
    fn add(&mut self, c: char) -> Option<()> {
        if self.length >= CSV_FIELD_LIMIT {
            return None;
        }
        self.field.push(c);
        self.length += 1;
        Some(())
    }

    fn save(&mut self) {
        self.record.push(std::mem::take(&mut self.field));
        self.length = 0;
    }

    /// One character, or None at the end of a line. None back is a csv.Error.
    fn step(&mut self, c: Option<char>) -> Option<()> {
        use CsvState::{EatCrnl, InField, InQuoted, QuoteInQuoted, StartField, StartRecord};
        match (self.state, c) {
            (StartRecord | InQuoted, None) | (EatCrnl, Some('\n' | '\r')) => {}
            (StartRecord, Some('\n' | '\r')) => self.state = EatCrnl,
            (StartField | InField | QuoteInQuoted, None | Some('\n' | '\r')) => {
                self.save();
                self.state = if c.is_none() { StartRecord } else { EatCrnl };
            }
            (StartRecord | StartField, Some('"')) => self.state = InQuoted,
            (StartRecord | StartField | InField | QuoteInQuoted, Some(',')) => {
                self.save();
                self.state = StartField;
            }
            (StartRecord | StartField | InField, Some(c)) => {
                self.add(c)?;
                self.state = InField;
            }
            (InQuoted, Some('"')) => self.state = QuoteInQuoted,
            (InQuoted, Some(c)) => self.add(c)?,
            (QuoteInQuoted, Some('"')) => {
                self.add('"')?;
                self.state = InQuoted;
            }
            (QuoteInQuoted | EatCrnl, Some(_)) => return None,
            (EatCrnl, None) => self.state = StartRecord,
        }
        Some(())
    }
}

/// The lines of io.StringIO(text, newline=""), each with its line break.
fn csv_lines(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut lines = Vec::new();
    let (mut start, mut index) = (0, 0);
    while index < bytes.len() {
        let end = match bytes[index] {
            b'\r' if bytes.get(index + 1) == Some(&b'\n') => index + 2,
            b'\n' | b'\r' => index + 1,
            _ => {
                index += 1;
                continue;
            }
        };
        lines.push(&text[start..end]);
        (start, index) = (end, end);
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Every record of a text, or None for a csv.Error.
fn csv_records(text: &str) -> Option<Vec<Vec<String>>> {
    let mut reader = CsvReader {
        state: CsvState::StartRecord,
        record: Vec::new(),
        field: String::new(),
        length: 0,
    };
    let mut records = Vec::new();
    for line in csv_lines(text) {
        for c in line.chars() {
            reader.step(Some(c))?;
        }
        reader.step(None)?;
        if reader.state == CsvState::StartRecord {
            records.push(std::mem::take(&mut reader.record));
        }
    }
    (reader.state == CsvState::StartRecord).then_some(records)
}
"#;

const IMPORT: &str = r#"
const IMPORT_MAX_ROWS: usize = 20;
const IMPORT_MAX_CHARS: usize = 1_048_576;
/// Past this many bytes the body surely holds more than IMPORT_MAX_CHARS characters.
const IMPORT_MAX_BYTES: usize = 16 * 1024 * 1024;
const TOO_LONG: &str = "String should have at most 1048576 characters";

/// The children of an import: a header of the child fields in order, then 1 to 20 rows. Blank
/// lines are skipped. None is 422 invalid CSV input.
fn csv_import(text: &str) -> Option<Vec<ChildCreate>> {
    let mut records = csv_records(text)?.into_iter();
    if records.next()? != CSV_HEADER {
        return None;
    }
    let mut children = Vec::new();
    for record in records.filter(|record| !record.is_empty()) {
        if record.len() != CSV_HEADER.len() {
            return None;
        }
        let mut object = serde_json::Map::new();
        for ((name, read), cell) in CSV_HEADER.iter().zip(CSV_CELLS).zip(&record) {
            let value = if cell == NULL_CELL { Value::Null } else { read(cell)? };
            object.insert((*name).to_owned(), value);
        }
        let child: ChildCreate = serde_json::from_value(Value::Object(object)).ok()?;
        if !child.problems().is_empty() {
            return None;
        }
        children.push(child);
        if children.len() > IMPORT_MAX_ROWS {
            return None;
        }
    }
    (!children.is_empty()).then_some(children)
}

/// A str body as FastAPI reads it.
enum TextBody {
    Read(String),
    Invalid,
    Nonfinite,
    /// Not UTF-8: the Python product fails on it with 500.
    Broken,
}

impl TextBody {
    /// After the caller is checked: a non-finite number replaces every other problem.
    fn finish(self, problems: Vec<Value>) -> Result<String, ApiError> {
        match self {
            Self::Nonfinite => Err(ApiError::Finite),
            Self::Broken => Err(ApiError::Internal),
            Self::Read(text) if problems.is_empty() => Ok(text),
            Self::Read(_) | Self::Invalid => Err(ApiError::Invalid(problems)),
        }
    }
}

/// A JSON body must be a string; any other body is the text itself. A JSON syntax error refuses
/// at once, before the caller is checked. Other body problems join the path problems.
fn read_text(
    headers: &HeaderMap,
    payload: Result<Bytes, BytesRejection>,
    problems: &mut Vec<Value>,
) -> Result<TextBody, ApiError> {
    let bytes = match payload {
        Ok(bytes) => bytes,
        Err(rejection) if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE => {
            problems.push(problem("string_too_long", json!(["body"]), TOO_LONG));
            return Ok(TextBody::Invalid);
        }
        Err(_) => {
            problems.push(problem("value_error", json!(["body"]), "The request body could not be read"));
            return Ok(TextBody::Invalid);
        }
    };
    if bytes.is_empty() {
        problems.push(problem("missing", json!(["body"]), "Field required"));
        return Ok(TextBody::Invalid);
    }
    let text = if json_type(headers) {
        let (value, nonfinite) = python_json_detail(&bytes).map_err(|error| {
            ApiError::Invalid(vec![problem("json_invalid", json!(["body", error.column()]), "JSON decode error")])
        })?;
        if nonfinite {
            return Ok(TextBody::Nonfinite);
        }
        match value {
            Value::String(text) => text,
            Value::Null => {
                problems.push(problem("missing", json!(["body"]), "Field required"));
                return Ok(TextBody::Invalid);
            }
            _ => {
                problems.push(problem("string_type", json!(["body"]), "Input should be a valid string"));
                return Ok(TextBody::Invalid);
            }
        }
    } else {
        match String::from_utf8(bytes.to_vec()) {
            Ok(text) => text,
            Err(_) => return Ok(TextBody::Broken),
        }
    };
    if text.chars().count() > IMPORT_MAX_CHARS {
        problems.push(problem("string_too_long", json!(["body"]), TOO_LONG));
        return Ok(TextBody::Invalid);
    }
    Ok(TextBody::Read(text))
}

/// 1 to 20 children from CSV in one transaction, so a failing row leaves no child behind. CSV
/// problems come before a missing parent, as in the Python product.
async fn import_children(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<(StatusCode, Json<Vec<ChildRead>>), ApiError> {
    let mut problems = Vec::new();
    let parent = path_id(&segments(path), "parent_id", &mut problems);
    let text = read_text(&headers, payload, &mut problems)?;
    access?;
    let text = text.finish(problems)?;
    let records = csv_import(&text).ok_or(ApiError::Csv)?;
    let parent = known_parent(&state.pool, parent).await?;
    let mut transaction = state.pool.begin().await?;
    let mut children = Vec::new();
    for record in records {
        let row = db::insert_child(&mut *transaction, parent, record).await.map_err(missing_parent)?;
        children.push(ChildRead::from(row));
    }
    transaction.commit().await?;
    Ok((StatusCode::CREATED, Json(children)))
}
"#;

// ---------------------------------------------------------------- gzip_export

const GZIP_BODY: &str = r#"    let rows: Vec<ChildRead> = rows.into_iter().map(ChildRead::from).collect();
    let content = serde_json::to_vec(&rows).map_err(|_| ApiError::Internal)?;
    Ok(([(CONTENT_TYPE, "application/gzip")], gzip(&content)?).into_response())
"#;

const GZIP: &str = r#"
/// gzip.compress(content, mtime=0): zlib at level 9 with a gzip header and wrapper.
fn gzip(content: &[u8]) -> Result<Vec<u8>, ApiError> {
    let mut deflate = flate2::Compress::new_gzip(flate2::Compression::best(), 15);
    let mut out = Vec::with_capacity(content.len() / 2 + 64);
    loop {
        if out.len() == out.capacity() {
            out.reserve(out.capacity());
        }
        let read = usize::try_from(deflate.total_in()).map_err(|_| ApiError::Internal)?;
        match deflate.compress_vec(&content[read..], &mut out, flate2::FlushCompress::Finish) {
            Ok(flate2::Status::StreamEnd) => return Ok(out),
            Ok(_) => {}
            Err(_) => return Err(ApiError::Internal),
        }
    }
}
"#;

// ---------------------------------------------------------------- decimal_summary

fn decimal_body(value: &Field, group: &Field) -> String {
    let member = value.ident();
    let read = if value.nullable() {
        format!(
            "        let Some(value) = row.{member} else {{\n            continue;\n        }};\n"
        )
    } else {
        format!("        let value = row.{member};\n")
    };
    let text = if value.kind == Kind::Float {
        "&py_float(value)"
    } else {
        "&value.to_string()"
    };
    format!(
        "    let mut totals: std::collections::BTreeMap<String, Decimal> = std::collections::BTreeMap::new();\n    for row in rows {{\n{read}        let value = Decimal::parse({text}).ok_or(ApiError::Internal)?;\n        let total = totals.entry(row.{}).or_insert_with(Decimal::zero);\n        *total = total.add(&value);\n    }}\n    let totals: serde_json::Map<String, Value> = totals\n        .into_iter()\n        .map(|(group, total)| (group, Value::String(total.text())))\n        .collect();\n    let content = serde_json::to_vec(&json!({{\"totals\": totals}})).map_err(|_| ApiError::Internal)?;\n    Ok(([(CONTENT_TYPE, \"application/json\")], content).into_response())\n",
        group.ident()
    )
}

const DECIMAL: &str = r#"
/// A Python Decimal in the default context: 28 significant digits, rounded half even.
struct Decimal {
    negative: bool,
    /// The coefficient, most significant digit first, with no leading zero but a lone one.
    digits: Vec<u8>,
    exponent: i64,
}

impl Decimal {
    fn zero() -> Self {
        Self { negative: false, digits: vec![0], exponent: 0 }
    }

    /// Decimal(text) of the str() of a Python int or float.
    fn parse(text: &str) -> Option<Self> {
        let (negative, text) = match text.strip_prefix('-') {
            Some(rest) => (true, rest),
            None => (false, text),
        };
        let (mantissa, power) = match text.split_once(['e', 'E']) {
            Some((mantissa, power)) => (mantissa, power.parse::<i64>().ok()?),
            None => (text, 0),
        };
        let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
        let digits: Vec<u8> = whole
            .bytes()
            .chain(fraction.bytes())
            .map(|byte| byte.checked_sub(b'0').filter(|digit| *digit < 10))
            .collect::<Option<_>>()?;
        if digits.is_empty() {
            return None;
        }
        let places = i64::try_from(fraction.len()).ok()?;
        Some(Self::new(negative, digits, power - places))
    }

    /// Leading zeros go, and a coefficient past 28 digits rounds half even.
    fn new(negative: bool, mut digits: Vec<u8>, mut exponent: i64) -> Self {
        let first = digits.iter().position(|digit| *digit != 0).unwrap_or(digits.len() - 1);
        digits.drain(..first);
        if digits.len() > 28 {
            let dropped = digits.split_off(28);
            exponent += i64::try_from(dropped.len()).unwrap_or(i64::MAX);
            let rest = dropped[1..].iter().any(|digit| *digit != 0);
            if dropped[0] > 5 || (dropped[0] == 5 && (rest || digits[27] % 2 == 1)) {
                match digits.iter().rposition(|digit| *digit != 9) {
                    Some(index) => {
                        digits[index] += 1;
                        digits[index + 1..].fill(0);
                    }
                    None => {
                        digits.fill(0);
                        digits[0] = 1;
                        exponent += 1;
                    }
                }
            }
        }
        Self { negative, digits, exponent }
    }

    /// The coefficient with zeros appended down to an exponent at or below its own.
    fn aligned(&self, exponent: i64) -> Vec<u8> {
        let mut digits = self.digits.clone();
        let zeros = usize::try_from(self.exponent - exponent).unwrap_or_default();
        digits.resize(digits.len() + zeros, 0);
        digits
    }

    fn add(&self, other: &Self) -> Self {
        let exponent = self.exponent.min(other.exponent);
        let (left, right) = (self.aligned(exponent), other.aligned(exponent));
        if self.negative == other.negative {
            return Self::new(self.negative, digit_sum(&left, &right), exponent);
        }
        match significant(&left).len().cmp(&significant(&right).len()).then_with(|| significant(&left).cmp(significant(&right))) {
            std::cmp::Ordering::Equal => Self::new(false, vec![0], exponent),
            std::cmp::Ordering::Greater => Self::new(self.negative, digit_difference(&left, &right), exponent),
            std::cmp::Ordering::Less => Self::new(other.negative, digit_difference(&right, &left), exponent),
        }
    }

    /// str(Decimal): scientific notation for a positive exponent or a small number.
    fn text(&self) -> String {
        let digits: String = self.digits.iter().map(|digit| char::from(b'0' + digit)).collect();
        let length = i64::try_from(digits.len()).unwrap_or(i64::MAX);
        let left = self.exponent + length;
        let dot = if self.exponent <= 0 && left > -6 { left } else { 1 };
        let zeros = |count: i64| "0".repeat(usize::try_from(count).unwrap_or_default());
        let mut out = String::from(if self.negative { "-" } else { "" });
        if dot <= 0 {
            out += &format!("0.{}{digits}", zeros(-dot));
        } else if dot >= length {
            out += &format!("{digits}{}", zeros(dot - length));
        } else {
            let (head, tail) = digits.split_at(usize::try_from(dot).unwrap_or_default());
            out += &format!("{head}.{tail}");
        }
        if left != dot {
            out += &format!("E{:+}", left - dot);
        }
        out
    }
}

/// The digits from the first non-zero one.
fn significant(digits: &[u8]) -> &[u8] {
    let first = digits.iter().position(|digit| *digit != 0).unwrap_or(digits.len());
    &digits[first..]
}

fn digit_sum(left: &[u8], right: &[u8]) -> Vec<u8> {
    let (mut left, mut right) = (left.iter().rev(), right.iter().rev());
    let mut out = Vec::new();
    let mut carry = 0;
    loop {
        let (x, y) = (left.next(), right.next());
        if x.is_none() && y.is_none() {
            break;
        }
        let total = x.copied().unwrap_or_default() + y.copied().unwrap_or_default() + carry;
        out.push(total % 10);
        carry = total / 10;
    }
    if carry > 0 {
        out.push(carry);
    }
    out.reverse();
    out
}

/// left - right, where left is the larger.
fn digit_difference(left: &[u8], right: &[u8]) -> Vec<u8> {
    let mut right = right.iter().rev();
    let mut out = Vec::new();
    let mut borrow = 0;
    for &x in left.iter().rev() {
        let y = right.next().copied().unwrap_or_default() + borrow;
        borrow = u8::from(x < y);
        out.push(x + 10 * borrow - y);
    }
    out.reverse();
    out
}
"#;

// ---------------------------------------------------------------- statistics

fn statistics_body(value: &Field) -> String {
    let member = value.ident();
    let pick = if value.nullable() {
        format!("filter_map(|row| row.{member})")
    } else {
        format!("map(|row| row.{member})")
    };
    let widen = if value.kind == Kind::Int {
        ".map(f64::from)"
    } else {
        ""
    };
    format!(
        "    let mut values: Vec<f64> = rows.iter().{pick}{widen}.collect();\n    let content = statistics(&mut values)?;\n    Ok(([(CONTENT_TYPE, \"application/json\")], content).into_response())\n"
    )
}

const STATISTICS: &str = r##"
/// math.fsum: the correctly rounded sum, or None on an intermediate overflow.
fn fsum(values: &[f64]) -> Option<f64> {
    let mut partials: Vec<f64> = Vec::new();
    for &value in values {
        let mut x = value;
        let mut kept = 0;
        for index in 0..partials.len() {
            let mut y = partials[index];
            if x.abs() < y.abs() {
                (x, y) = (y, x);
            }
            let hi = x + y;
            let lo = y - (hi - x);
            if lo != 0.0 {
                partials[kept] = lo;
                kept += 1;
            }
            x = hi;
        }
        partials.truncate(kept);
        if x != 0.0 {
            if !x.is_finite() {
                return None;
            }
            partials.push(x);
        }
    }
    let Some(mut hi) = partials.pop() else {
        return Some(0.0);
    };
    let mut lo = 0.0;
    while let Some(y) = partials.pop() {
        let x = hi;
        hi = x + y;
        lo = y - (hi - x);
        if lo != 0.0 {
            break;
        }
    }
    if let Some(&next) = partials.last() {
        if (lo < 0.0 && next < 0.0) || (lo > 0.0 && next > 0.0) {
            let y = lo * 2.0;
            let x = hi + y;
            if y == x - hi {
                hi = x;
            }
        }
    }
    Some(hi)
}

/// statistics.fmean, statistics.median, min and max as compact JSON. A result past the float
/// range fails as the Python product does, with 500.
fn statistics(values: &mut [f64]) -> Result<String, ApiError> {
    let Some(&first) = values.first() else {
        return Ok(r#"{"count":0,"mean":null,"median":null,"minimum":null,"maximum":null}"#.to_owned());
    };
    let count = values.len();
    let mean = fsum(values).ok_or(ApiError::Internal)? / count as f64;
    let minimum = values.iter().fold(first, |low, &value| if value < low { value } else { low });
    let maximum = values.iter().fold(first, |high, &value| if value > high { value } else { high });
    values.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    let middle = count / 2;
    let median = if count.is_multiple_of(2) {
        (values[middle - 1] + values[middle]) / 2.0
    } else {
        values[middle]
    };
    if !mean.is_finite() || !median.is_finite() {
        return Err(ApiError::Internal);
    }
    Ok(format!(
        r#"{{"count":{count},"mean":{},"median":{},"minimum":{},"maximum":{}}}"#,
        py_float(mean),
        py_float(median),
        py_float(minimum),
        py_float(maximum)
    ))
}
"##;

//! CNL of one resource set (`Service resource version 1.`), lowered to the typed
//! prompt v3. Each sentence is one line that ends with a period. The scanner
//! keeps a source map from each prompt pointer to the words that wrote it, so
//! that a validator error points at the sentence or word that is wrong.
use crate::resource::PROMPT_VERSION_V3;
use crate::{Error, SourceSpan};
use serde_json::{Value, json};
use std::collections::BTreeMap;

type Map = BTreeMap<String, SourceSpan>;

const HEADER_FORM: &str = "Service resource version 1.";
const PACKAGE_FORM: &str = "Package NAME.";
const TARGET_FORM: &str = "Target PROFILE.";
const RESOURCE_FORM: &str = "Resource NAME.";
const FIELD_FORM: &str = "Field NAME of RESOURCE is TYPE required|optional [CONSTRAINTS].";
const APPLY_FORM: &str = "Apply STANDARD to RESOURCE.";

const SENTENCE_STARTS: [&str; 6] = ["Service", "Package", "Target", "Resource", "Field", "Apply"];
const STRING_KEYS: [&str; 3] = ["min", "max", "upper_letters"];
const INTEGER_KEYS: [&str; 2] = ["min", "max"];

/// Whether the first non-blank line starts with the words `Service resource`.
pub(crate) fn is_resource_source(source: &str) -> bool {
    source
        .split_inclusive('\n')
        .map(content)
        .find(|line| words_of(line).next().is_some())
        .is_some_and(|line| {
            let mut words = words_of(line);
            // `resource.` too: a header with no version is still a resource source.
            words.next() == Some("Service")
                && matches!(words.next(), Some("resource" | "resource."))
        })
}

fn words_of(line: &str) -> impl Iterator<Item = &str> {
    line.split([' ', '\t']).filter(|word| !word.is_empty())
}

/// A line without its line break and without trailing spaces or tabs.
fn content(raw: &str) -> &str {
    raw.trim_end_matches(['\n', '\r', ' ', '\t'])
}

/// The typed prompt v3 of the source, and the source map of its pointers.
pub(crate) fn parse(source: &str) -> Result<(Value, Map), Error> {
    let sentences = lex(source)?;
    let mut map = Map::new();
    let mut package = Value::Null;
    let mut target = Value::Null;
    let mut resources: Vec<Draft> = Vec::new();

    // The first sentence is the header: `is_resource_source` ensures it starts
    // with `Service resource`.
    if let Some(header_sentence) = sentences.first() {
        header(header_sentence, &mut map)?;
    }
    let mut expect = Expect::Package;

    for sentence in sentences.iter().skip(1) {
        let first = &sentence.words[0];
        if !SENTENCE_STARTS.contains(&first.text) {
            return Err(syntax("Unknown sentence.", &first.span, expect.form()));
        }
        if !expect.allows(first.text) {
            return Err(syntax(
                "This sentence is not allowed here.",
                &first.span,
                expect.form(),
            ));
        }
        match first.text {
            "Package" => {
                let name = name_of(sentence, PACKAGE_FORM)?;
                package = json!(name.text);
                map.insert("/package".to_owned(), name.span.clone());
                expect = Expect::Target;
            }
            "Target" => {
                let name = name_of(sentence, TARGET_FORM)?;
                target = json!(name.text);
                map.insert("/target".to_owned(), name.span.clone());
                expect = Expect::Resource;
            }
            "Resource" => {
                let r = resources.len();
                let name = name_of(sentence, RESOURCE_FORM)?;
                map.insert(format!("/resources/{r}"), sentence.span.clone());
                map.insert(format!("/resources/{r}/name"), name.span.clone());
                resources.push(Draft {
                    name: name.text.to_owned(),
                    fields: Vec::new(),
                    standards: Vec::new(),
                });
                expect = Expect::Body;
            }
            // Only `Body` allows these, and `Body` is entered after a `Resource`.
            "Field" => {
                let r = resources.len() - 1;
                let draft = &mut resources[r];
                let j = draft.fields.len();
                let field = field(
                    sentence,
                    &draft.name,
                    &format!("/resources/{r}/fields/{j}"),
                    &mut map,
                )?;
                draft.fields.push(field);
            }
            _ => {
                let r = resources.len() - 1;
                let draft = &mut resources[r];
                let (standard, span) = apply(sentence, &draft.name)?;
                let k = draft.standards.len();
                map.insert(format!("/resources/{r}/standards/{k}"), span);
                draft.standards.push(json!(standard));
            }
        }
    }

    let resources: Vec<Value> = resources
        .into_iter()
        .map(|draft| json!({"name": draft.name, "fields": draft.fields, "standards": draft.standards}))
        .collect();
    let prompt = json!({
        "version": PROMPT_VERSION_V3,
        "package": package,
        "target": target,
        "resources": resources,
    });
    Ok((prompt, map))
}

/// A resource while its sentences are read.
struct Draft {
    name: String,
    fields: Vec<Value>,
    standards: Vec<Value>,
}

/// What the next sentence may be.
#[derive(Clone, Copy)]
enum Expect {
    Package,
    Target,
    Resource,
    Body,
}

impl Expect {
    fn form(self) -> &'static str {
        match self {
            Expect::Package => PACKAGE_FORM,
            Expect::Target => TARGET_FORM,
            Expect::Resource | Expect::Body => RESOURCE_FORM,
        }
    }

    fn allows(self, first: &str) -> bool {
        match self {
            Expect::Package => first == "Package",
            Expect::Target => first == "Target",
            Expect::Resource => first == "Resource",
            Expect::Body => matches!(first, "Resource" | "Field" | "Apply"),
        }
    }
}

struct Word<'a> {
    text: &'a str,
    span: SourceSpan,
}

struct Sentence<'a> {
    words: Vec<Word<'a>>,
    span: SourceSpan,
}

struct Line<'a> {
    number: usize,
    start: usize,
    text: &'a str,
}

/// Splits the source into sentences. Every non-blank line must end with a period.
fn lex(source: &str) -> Result<Vec<Sentence<'_>>, Error> {
    let mut sentences = Vec::new();
    let mut start = 0;
    for (index, raw) in source.split_inclusive('\n').enumerate() {
        let line = Line {
            number: index + 1,
            start,
            text: content(raw),
        };
        start += raw.len();
        let mut words = split_line(&line);
        let (Some(first), Some(last)) = (words.first(), words.last()) else {
            continue;
        };
        let span = join(&first.span, &last.span);
        if !line.text.ends_with('.') {
            return Err(syntax(
                "A sentence ends with a period.",
                &span,
                form_for(first.text),
            ));
        }
        if let Some(last) = words.last_mut() {
            last.text = &last.text[..last.text.len() - 1];
            last.span.byte_end -= 1;
            last.span.end_column -= 1;
            if last.text.is_empty() {
                words.pop();
            }
        }
        if words.is_empty() {
            return Err(syntax("Unknown sentence.", &span, HEADER_FORM));
        }
        sentences.push(Sentence { words, span });
    }
    Ok(sentences)
}

fn split_line<'a>(line: &Line<'a>) -> Vec<Word<'a>> {
    let mut words = Vec::new();
    let mut start: Option<usize> = None;
    for (at, ch) in line.text.char_indices() {
        match (ch, start) {
            (' ' | '\t', Some(from)) => {
                words.push(word(line, from, at));
                start = None;
            }
            (' ' | '\t', None) => {}
            (_, None) => start = Some(at),
            (_, Some(_)) => {}
        }
    }
    if let Some(from) = start {
        words.push(word(line, from, line.text.len()));
    }
    words
}

fn word<'a>(line: &Line<'a>, from: usize, to: usize) -> Word<'a> {
    Word {
        text: &line.text[from..to],
        span: span(line, from, to),
    }
}

/// The span of the bytes `from..to` of a line; columns count characters from 1.
fn span(line: &Line<'_>, from: usize, to: usize) -> SourceSpan {
    SourceSpan {
        line: line.number,
        column: line.text[..from].chars().count() + 1,
        end_line: line.number,
        end_column: line.text[..to].chars().count() + 1,
        byte_start: line.start + from,
        byte_end: line.start + to,
    }
}

/// From the start of `a` to the end of `b`; both lie on one line.
fn join(a: &SourceSpan, b: &SourceSpan) -> SourceSpan {
    SourceSpan {
        line: a.line,
        column: a.column,
        end_line: b.end_line,
        end_column: b.end_column,
        byte_start: a.byte_start,
        byte_end: b.byte_end,
    }
}

fn form_for(first: &str) -> &'static str {
    match first {
        "Package" => PACKAGE_FORM,
        "Target" => TARGET_FORM,
        "Resource" => RESOURCE_FORM,
        "Field" => FIELD_FORM,
        "Apply" => APPLY_FORM,
        _ => HEADER_FORM,
    }
}

fn syntax(detail: &str, span: &SourceSpan, form: &str) -> Error {
    Error::new("CNL_SYNTAX", detail, "")
        .with_span(span)
        .with_allowed_form(form)
}

fn word_at<'s, 'a>(
    sentence: &'s Sentence<'a>,
    index: usize,
    form: &str,
) -> Result<&'s Word<'a>, Error> {
    sentence
        .words
        .get(index)
        .ok_or_else(|| syntax("A word is missing.", &sentence.span, form))
}

fn fixed(sentence: &Sentence<'_>, index: usize, want: &str, form: &str) -> Result<(), Error> {
    let word = word_at(sentence, index, form)?;
    if word.text == want {
        Ok(())
    } else {
        Err(syntax(
            &format!("Expected the word {want}."),
            &word.span,
            form,
        ))
    }
}

fn no_extra(sentence: &Sentence<'_>, from: usize, form: &str) -> Result<(), Error> {
    match sentence.words.get(from) {
        Some(extra) => Err(syntax("This word is not allowed here.", &extra.span, form)),
        None => Ok(()),
    }
}

/// `Package NAME.`, `Target PROFILE.` and `Resource NAME.`: one name word.
fn name_of<'s, 'a>(sentence: &'s Sentence<'a>, form: &str) -> Result<&'s Word<'a>, Error> {
    let name = word_at(sentence, 1, form)?;
    no_extra(sentence, 2, form)?;
    Ok(name)
}

/// `Service resource version 1.`
fn header(sentence: &Sentence<'_>, map: &mut Map) -> Result<(), Error> {
    fixed(sentence, 1, "resource", HEADER_FORM)?;
    fixed(sentence, 2, "version", HEADER_FORM)?;
    let number = word_at(sentence, 3, HEADER_FORM)?;
    if number.text != "1" {
        return Err(Error::new(
            "UNSUPPORTED_VERSION",
            "Only Service resource version 1 is supported.",
            "/version",
        )
        .with_span(&number.span)
        .with_allowed_form(HEADER_FORM));
    }
    no_extra(sentence, 4, HEADER_FORM)?;
    map.insert("/version".to_owned(), number.span.clone());
    Ok(())
}

/// `Field NAME of RESOURCE is TYPE required|optional [CONSTRAINTS].`
fn field(sentence: &Sentence<'_>, open: &str, at: &str, map: &mut Map) -> Result<Value, Error> {
    let name = word_at(sentence, 1, FIELD_FORM)?;
    fixed(sentence, 2, "of", FIELD_FORM)?;
    let resource = word_at(sentence, 3, FIELD_FORM)?;
    if resource.text != open {
        return Err(syntax(
            "The sentence names another resource than the open one.",
            &resource.span,
            FIELD_FORM,
        ));
    }
    fixed(sentence, 4, "is", FIELD_FORM)?;
    let kind = word_at(sentence, 5, FIELD_FORM)?;
    let flag = word_at(sentence, 6, FIELD_FORM)?;
    let required = match flag.text {
        "required" => true,
        "optional" => false,
        _ => {
            return Err(syntax(
                "Expected required or optional.",
                &flag.span,
                FIELD_FORM,
            ));
        }
    };

    let mut out = json!({"name": name.text, "type": kind.text, "required": required});
    map.insert(at.to_owned(), sentence.span.clone());
    map.insert(format!("{at}/name"), name.span.clone());
    map.insert(format!("{at}/type"), kind.span.clone());
    map.insert(format!("{at}/required"), flag.span.clone());
    match kind.text {
        "string" => numbers(sentence, &mut out, at, &STRING_KEYS, false, map)?,
        "integer" => numbers(sentence, &mut out, at, &INTEGER_KEYS, true, map)?,
        "enum" => values(sentence, &mut out, at, map)?,
        // Any other type is copied as written; the validator refuses it.
        _ => {}
    }
    Ok(out)
}

/// Constraints `KEY N`, in the order of `keys`, each at most once.
fn numbers(
    sentence: &Sentence<'_>,
    out: &mut Value,
    at: &str,
    keys: &[&str],
    negative_ok: bool,
    map: &mut Map,
) -> Result<(), Error> {
    let mut previous: Option<usize> = None;
    let mut index = 7;
    while let Some(keyword) = sentence.words.get(index) {
        let position = keys
            .iter()
            .position(|key| *key == keyword.text)
            .filter(|p| previous.is_none_or(|prev| *p > prev));
        let Some(position) = position else {
            return Err(syntax(
                "This constraint is unknown, repeated or out of order.",
                &keyword.span,
                FIELD_FORM,
            ));
        };
        let number = sentence
            .words
            .get(index + 1)
            .ok_or_else(|| syntax("A number is missing.", &keyword.span, FIELD_FORM))?;
        let value = whole_number(number.text)
            .filter(|n| negative_ok || *n >= 0)
            .ok_or_else(|| syntax("Expected a whole number.", &number.span, FIELD_FORM))?;
        out[keyword.text] = json!(value);
        map.insert(
            format!("{at}/{}", keyword.text),
            join(&keyword.span, &number.span),
        );
        previous = Some(position);
        index += 2;
    }
    Ok(())
}

/// `values V1 V2 ...`, the last constraint of an enum.
fn values(sentence: &Sentence<'_>, out: &mut Value, at: &str, map: &mut Map) -> Result<(), Error> {
    let Some(keyword) = sentence.words.get(7) else {
        return Ok(());
    };
    if keyword.text != "values" {
        return Err(syntax("Expected values.", &keyword.span, FIELD_FORM));
    }
    let list: Vec<&str> = sentence
        .words
        .iter()
        .skip(8)
        .map(|word| word.text)
        .collect();
    let Some(last) = sentence.words.last().filter(|_| !list.is_empty()) else {
        return Err(syntax(
            "Expected at least one value.",
            &keyword.span,
            FIELD_FORM,
        ));
    };
    out["values"] = json!(list);
    map.insert(format!("{at}/values"), join(&keyword.span, &last.span));
    Ok(())
}

/// `Apply STANDARD WORDS to RESOURCE.` Returns the standard name and its span.
fn apply(sentence: &Sentence<'_>, open: &str) -> Result<(String, SourceSpan), Error> {
    let words = &sentence.words;
    if words.len() < 4 || words[words.len() - 2].text != "to" {
        return Err(syntax(
            "Expected Apply STANDARD to RESOURCE.",
            &sentence.span,
            APPLY_FORM,
        ));
    }
    let resource = &words[words.len() - 1];
    if resource.text != open {
        return Err(syntax(
            "The sentence names another resource than the open one.",
            &resource.span,
            APPLY_FORM,
        ));
    }
    let standard = &words[1..words.len() - 2];
    let name = standard
        .iter()
        .map(|word| word.text)
        .collect::<Vec<_>>()
        .join(" ");
    let span = join(&standard[0].span, &standard[standard.len() - 1].span);
    Ok((name, span))
}

/// `-?(0|[1-9][0-9]*)` in the range of i64; `-0` is not a whole number here.
fn whole_number(text: &str) -> Option<i64> {
    let digits = text.strip_prefix('-').unwrap_or(text);
    let well_formed = match digits.as_bytes() {
        [b'0'] => true,
        [b'1'..=b'9', rest @ ..] => rest.iter().all(u8::is_ascii_digit),
        _ => false,
    };
    if !well_formed || text == "-0" {
        return None;
    }
    text.parse::<i64>().ok()
}

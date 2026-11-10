use crate::{Error, SourceSpan, model};
use faberline_nlp_cnl::service::{
    ServiceEventKind, ServiceSlot, ServiceSyntaxIssue, ServiceSyntaxRule,
};
use faberline_nlp_cnl::{
    BaseIssue, IssueKind, SERVICE_SYNTAX_API_VERSION, Token, parse_service_document,
};
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn span(s: &faberline_nlp_cnl::SourceSpan) -> SourceSpan {
    SourceSpan {
        line: s.line,
        column: s.column,
        end_line: s.end_line,
        end_column: s.end_column,
        byte_start: s.byte_start,
        byte_end: s.byte_end,
    }
}
fn slot_pointer(slot: Option<ServiceSlot>, v2: bool) -> String {
    let pointer = match slot {
        Some(ServiceSlot::Header) => "/version",
        Some(ServiceSlot::Package) => "/package",
        Some(ServiceSlot::Target) => "/target",
        Some(ServiceSlot::ServiceBinding) => "/service",
        Some(ServiceSlot::OperationBinding) if !v2 => "/operation",
        Some(ServiceSlot::Input(i)) if !v2 => return format!("/operation/inputs/{i}"),
        _ if v2 => "/operations",
        _ => "",
    };
    pointer.into()
}
fn form(slot: Option<ServiceSlot>, v2: bool) -> &'static str {
    match slot {
        Some(ServiceSlot::Header) if v2 => "Service prompt version 2.",
        Some(ServiceSlot::Header) => "Service prompt version 1.",
        Some(ServiceSlot::Package) => "Package NAME.",
        Some(ServiceSlot::Target) => "Target PROFILE.",
        Some(ServiceSlot::ServiceBinding) => "Bind service NAME using contract JSON_STRING.",
        Some(ServiceSlot::OperationBinding) => "Bind operation ALIAS to OPERATION.",
        Some(ServiceSlot::Input(_)) => "Accept NAME as TYPE for PARAMETER.",
        Some(ServiceSlot::Body) => "Send body NAME as TYPE.",
        Some(ServiceSlot::End) if v2 => "End of source after the last operation.",
        Some(ServiceSlot::End) => "End of source after two input statements.",
        None if v2 => "A version 2 service document with one or more operations.",
        None => "Seven complete service statements with a final period on each line.",
    }
}
fn syntax_error(issue: ServiceSyntaxIssue, v2: bool) -> Error {
    let code = match issue.kind {
        IssueKind::UnsupportedVersion => "UNSUPPORTED_VERSION",
        IssueKind::Missing => "CNL_MISSING",
        IssueKind::Duplicate => "CNL_DUPLICATE",
        _ => "CNL_SYNTAX",
    };
    let mut pointer = slot_pointer(issue.slot, v2);
    let detail = match &issue.rule {
        ServiceSyntaxRule::UnsupportedVersion => {
            "Only service prompt versions 1 and 2 are supported.".to_owned()
        }
        ServiceSyntaxRule::MissingStatement => {
            format!("Missing statement: {}", form(issue.slot, v2))
        }
        ServiceSyntaxRule::DuplicateStatement => "A singleton statement is repeated.".to_owned(),
        ServiceSyntaxRule::DuplicateInput => {
            if !v2 {
                pointer.push_str("/name");
            }
            "An input name is repeated.".to_owned()
        }
        ServiceSyntaxRule::ExtraInput => "Only two input statements are supported.".to_owned(),
        ServiceSyntaxRule::UnexpectedStatement => {
            format!("Expected statement: {}", form(issue.slot, v2))
        }
        ServiceSyntaxRule::DuplicateOperation => "An operation alias is repeated.".to_owned(),
        ServiceSyntaxRule::InputBeforeOperation => {
            "An input must follow a Bind operation statement.".to_owned()
        }
        ServiceSyntaxRule::BodyBeforeOperation => {
            "A body must follow a Bind operation statement.".to_owned()
        }
        ServiceSyntaxRule::DuplicateBody => {
            "An operation has at most one body statement.".to_owned()
        }
        ServiceSyntaxRule::ExpectedWord => "Expected an unquoted word.".to_owned(),
        ServiceSyntaxRule::Base(base) => match base {
            BaseIssue::BareCrOrBom { .. } => {
                "A bare carriage return or BOM is not allowed.".to_owned()
            }
            BaseIssue::MissingPeriod { .. } => "A statement must end with a period.".to_owned(),
            BaseIssue::InvalidJsonString { .. } => {
                "Invalid JSON string or Unicode escape.".to_owned()
            }
            BaseIssue::JsonStringBoundary { .. } => {
                "A quoted token must have a token boundary.".to_owned()
            }
            BaseIssue::IncompleteSentence => "Incomplete statement.".to_owned(),
            BaseIssue::ExpectedKeyword { expected, actual } => {
                format!("Expected {expected}, found {actual}.")
            }
            BaseIssue::TrailingToken { actual } => format!("Unexpected trailing token: {actual}."),
            BaseIssue::ExpectedJsonString { .. } => "Expected a JSON string.".to_owned(),
            BaseIssue::UnexpectedEof => "Unexpected end of source.".to_owned(),
            BaseIssue::ExpectedSentence { expected, .. } => {
                format!("Expected statement: {expected}")
            }
            BaseIssue::Duplicate { .. } => "A statement is repeated.".to_owned(),
            _ => "Invalid service statement token.".to_owned(),
        },
    };
    let mut error = Error::new(code, detail, &pointer).with_span(&span(&issue.span));
    error.body["diagnostics"][0]["allowed_form"] = json!(form(issue.slot, v2));
    error
}
fn token(map: &mut BTreeMap<String, SourceSpan>, pointer: &str, t: &Token) {
    map.insert(pointer.into(), span(&t.span));
}
/// Widens the span at `key` (or creates it) so that it ends where `next` ends.
fn extend(map: &mut BTreeMap<String, SourceSpan>, key: &str, next: &SourceSpan) {
    map.entry(key.into())
        .and_modify(|s| {
            s.end_line = next.end_line;
            s.end_column = next.end_column;
            s.byte_end = next.byte_end;
        })
        .or_insert_with(|| next.clone());
}
/// Adds one input (or body, when `parameter` is `None`) to the newest version 2 operation.
fn v2_input(
    prompt: &mut Value,
    map: &mut BTreeMap<String, SourceSpan>,
    index: usize,
    at: &faberline_nlp_cnl::SourceSpan,
    name: &Token,
    ty: &Token,
    parameter: Option<&Token>,
) -> Result<(), Error> {
    let Some(op) = prompt["operations"]
        .as_array()
        .map_or(0, Vec::len)
        .checked_sub(1)
    else {
        return Err(Error::new(
            "CNL_SYNTAX",
            "An input must follow a Bind operation statement.",
            "/operations",
        )
        .with_span(&span(at)));
    };
    let base = format!("/operations/{op}/inputs/{index}");
    let at = span(at);
    map.insert(base.clone(), at.clone());
    token(map, &format!("{base}/name"), name);
    token(map, &format!("{base}/type"), ty);
    let parameter_text = match parameter {
        Some(p) => {
            token(map, &format!("{base}/parameter"), p);
            p.text.clone()
        }
        None => "body".to_owned(),
    };
    for key in [
        format!("/operations/{op}"),
        format!("/operations/{op}/inputs"),
        "/operations".to_owned(),
    ] {
        extend(map, &key, &at);
    }
    prompt["operations"][op]["inputs"]
        .as_array_mut()
        .expect("array")
        .push(json!({"name":name.text,"type":ty.text,"parameter":parameter_text}));
    Ok(())
}
pub fn parse(source: &str) -> Result<(Value, BTreeMap<String, SourceSpan>), Error> {
    let report = parse_service_document(source);
    if report.api_version != SERVICE_SYNTAX_API_VERSION || report.api_version != 1 {
        return Err(Error::new(
            "UNSUPPORTED_VERSION",
            "Unsupported NLP service syntax interface.",
            "",
        ));
    }
    let v2 = matches!(
        report.events.first().map(|e| &e.kind),
        Some(ServiceEventKind::Header { version }) if version.text == "2"
    );
    if let Some(issue) = report.issue {
        return Err(syntax_error(issue, v2));
    }
    let mut prompt = if v2 {
        json!({"version":model::PROMPT_VERSION_V2,"package":"","target":"","service":{"name":"","contract":""},"operations":[]})
    } else {
        json!({"version":model::PROMPT_VERSION,"package":"","target":"","service":{"name":"","contract":""},"operation":{"alias":"","name":"","inputs":[]}})
    };
    let mut map = BTreeMap::new();
    let mut whole = span(&report.eof);
    whole.line = 1;
    whole.column = 1;
    whole.byte_start = 0;
    map.insert(String::new(), whole);
    let mut input_count = 0;
    for event in report.events {
        match event.kind {
            ServiceEventKind::Header { version } => token(&mut map, "/version", &version),
            ServiceEventKind::Package { name } => {
                token(&mut map, "/package", &name);
                prompt["package"] = json!(name.text);
            }
            ServiceEventKind::Target { profile } => {
                token(&mut map, "/target", &profile);
                prompt["target"] = json!(profile.text);
            }
            ServiceEventKind::ServiceBinding { service, contract } => {
                map.insert("/service".into(), span(&event.span));
                token(&mut map, "/service/name", &service);
                token(&mut map, "/service/contract", &contract);
                prompt["service"]["name"] = json!(service.text);
                let value: String = serde_json::from_str(&contract.text).map_err(|_| {
                    Error::new("CNL_SYNTAX", "Expected a JSON string.", "/service/contract")
                        .with_span(&span(&contract.span))
                })?;
                prompt["service"]["contract"] = json!(value);
            }
            ServiceEventKind::OperationBinding { alias, operation } if v2 => {
                let op = prompt["operations"].as_array().map_or(0, Vec::len);
                let op_span = span(&event.span);
                let op_pointer = format!("/operations/{op}");
                map.insert(op_pointer.clone(), op_span.clone());
                extend(&mut map, "/operations", &op_span);
                token(&mut map, &format!("{op_pointer}/alias"), &alias);
                token(&mut map, &format!("{op_pointer}/name"), &operation);
                prompt["operations"]
                    .as_array_mut()
                    .expect("array")
                    .push(json!({"alias":alias.text,"name":operation.text,"inputs":[]}));
                input_count = 0;
            }
            ServiceEventKind::OperationBinding { alias, operation } => {
                map.insert("/operation".into(), span(&event.span));
                token(&mut map, "/operation/alias", &alias);
                token(&mut map, "/operation/name", &operation);
                prompt["operation"]["alias"] = json!(alias.text);
                prompt["operation"]["name"] = json!(operation.text);
            }
            ServiceEventKind::InputBinding {
                name,
                ty,
                parameter,
            } if v2 => {
                v2_input(
                    &mut prompt,
                    &mut map,
                    input_count,
                    &event.span,
                    &name,
                    &ty,
                    Some(&parameter),
                )?;
                input_count += 1;
            }
            ServiceEventKind::InputBinding {
                name,
                ty,
                parameter,
            } => {
                let pointer = format!("/operation/inputs/{input_count}");
                map.insert(pointer.clone(), span(&event.span));
                token(&mut map, &format!("{pointer}/name"), &name);
                token(&mut map, &format!("{pointer}/type"), &ty);
                token(&mut map, &format!("{pointer}/parameter"), &parameter);
                let input_span = span(&event.span);
                map.entry("/operation/inputs".into())
                    .and_modify(|s| {
                        s.end_line = input_span.end_line;
                        s.end_column = input_span.end_column;
                        s.byte_end = input_span.byte_end;
                    })
                    .or_insert(input_span);
                prompt["operation"]["inputs"]
                    .as_array_mut()
                    .expect("array")
                    .push(json!({"name":name.text,"type":ty.text,"parameter":parameter.text}));
                input_count += 1;
            }
            ServiceEventKind::BodyBinding { name, ty } => {
                if !v2 {
                    return Err(Error::new(
                        "CNL_SYNTAX",
                        "A body statement needs service prompt version 2.",
                        "",
                    )
                    .with_span(&span(&event.span)));
                }
                v2_input(
                    &mut prompt,
                    &mut map,
                    input_count,
                    &event.span,
                    &name,
                    &ty,
                    None,
                )?;
                input_count += 1;
            }
        }
    }
    if v2 {
        model::validate_v2(&prompt).map_err(|e| e.with_map(&map))?;
    } else {
        model::validate(&prompt).map_err(|e| e.with_map(&map))?;
    }
    Ok((prompt, map))
}

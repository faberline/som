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
fn slot_pointer(slot: Option<ServiceSlot>) -> String {
    match slot {
        Some(ServiceSlot::Header) => "/version".into(),
        Some(ServiceSlot::Package) => "/package".into(),
        Some(ServiceSlot::Target) => "/target".into(),
        Some(ServiceSlot::ServiceBinding) => "/service".into(),
        Some(ServiceSlot::OperationBinding) => "/operation".into(),
        Some(ServiceSlot::Input(i)) => format!("/operation/inputs/{i}"),
        _ => String::new(),
    }
}
fn form(slot: Option<ServiceSlot>) -> &'static str {
    match slot {
        Some(ServiceSlot::Header) => "Service prompt version 1.",
        Some(ServiceSlot::Package) => "Package NAME.",
        Some(ServiceSlot::Target) => "Target PROFILE.",
        Some(ServiceSlot::ServiceBinding) => "Bind service NAME using contract JSON_STRING.",
        Some(ServiceSlot::OperationBinding) => "Bind operation ALIAS to OPERATION.",
        Some(ServiceSlot::Input(_)) => "Accept NAME as TYPE for PARAMETER.",
        Some(ServiceSlot::End) => "End of source after two input statements.",
        None => "Seven complete service statements with a final period on each line.",
    }
}
fn syntax_error(issue: ServiceSyntaxIssue) -> Error {
    let code = match issue.kind {
        IssueKind::UnsupportedVersion => "UNSUPPORTED_VERSION",
        IssueKind::Missing => "CNL_MISSING",
        IssueKind::Duplicate => "CNL_DUPLICATE",
        _ => "CNL_SYNTAX",
    };
    let mut pointer = slot_pointer(issue.slot);
    let detail = match &issue.rule {
        ServiceSyntaxRule::UnsupportedVersion => {
            "Only service prompt version 1 is supported.".to_owned()
        }
        ServiceSyntaxRule::MissingStatement => format!("Missing statement: {}", form(issue.slot)),
        ServiceSyntaxRule::DuplicateStatement => "A singleton statement is repeated.".to_owned(),
        ServiceSyntaxRule::DuplicateInput => {
            pointer.push_str("/name");
            "An input name is repeated.".to_owned()
        }
        ServiceSyntaxRule::ExtraInput => "Only two input statements are supported.".to_owned(),
        ServiceSyntaxRule::UnexpectedStatement => {
            format!("Expected statement: {}", form(issue.slot))
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
    error.body["diagnostics"][0]["allowed_form"] = json!(form(issue.slot));
    error
}
fn token(map: &mut BTreeMap<String, SourceSpan>, pointer: &str, t: &Token) {
    map.insert(pointer.into(), span(&t.span));
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
    if let Some(issue) = report.issue {
        return Err(syntax_error(issue));
    }
    let mut prompt = json!({"version":model::PROMPT_VERSION,"package":"","target":"","service":{"name":"","contract":""},"operation":{"alias":"","name":"","inputs":[]}});
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
        }
    }
    model::validate(&prompt).map_err(|e| e.with_map(&map))?;
    Ok((prompt, map))
}

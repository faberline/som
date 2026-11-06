//! Render NLP syntax facts with SOM's public diagnostic contract.
use super::{CompilerError, SourceSpan, error};
use faberline_nlp_cnl as nlp;
use nlp::{
    BaseIssue, Capability, FlowIssue, FlowNameKind, IssueKind, QuerySelector, RequestIssue,
    ResourceIssue, ScopePart, SyntaxRule, SyntaxSlot,
};
use std::fmt::Write;

pub(super) fn render(issue: &nlp::SyntaxIssue) -> CompilerError {
    let code = match issue.kind {
        IssueKind::Syntax => "CNL_SYNTAX",
        IssueKind::Missing => "CNL_MISSING",
        IssueKind::Duplicate => "CNL_DUPLICATE",
        IssueKind::BlockMismatch => "CNL_BLOCK_MISMATCH",
        IssueKind::UnsupportedVersion => "UNSUPPORTED_VERSION",
    };
    let (cause, allowed) = match &issue.fact.rule {
        SyntaxRule::Base(fact) => base_text(fact),
        SyntaxRule::Request(fact) => request_text(fact),
        SyntaxRule::Resource(fact) => resource_text(fact),
        SyntaxRule::Flow(fact) => flow_text(fact),
    };
    let pointer = issue.fact.scope.as_deref().map(scope_pointer);
    let span = &issue.span;
    error(
        code,
        SourceSpan {
            line: span.line,
            column: span.column,
            end_line: span.end_line,
            end_column: span.end_column,
            byte_start: span.byte_start,
            byte_end: span.byte_end,
        },
        pointer.as_deref(),
        cause,
        allowed,
    )
}

fn scope_pointer(scope: &[ScopePart]) -> String {
    let mut pointer = String::new();
    for part in scope {
        pointer.push('/');
        match part {
            ScopePart::Slot(slot) => pointer.push_str(slot_name(*slot)),
            ScopePart::Index(index) => {
                write!(&mut pointer, "{index}").expect("writing to a String cannot fail");
            }
            ScopePart::Name(name) => pointer.push_str(&name.replace('~', "~0").replace('/', "~1")),
        }
    }
    pointer
}

fn slot_name(slot: SyntaxSlot) -> &'static str {
    match slot {
        SyntaxSlot::Version => "version",
        SyntaxSlot::Package => "package",
        SyntaxSlot::Contract => "contract",
        SyntaxSlot::ContractId => "id",
        SyntaxSlot::Resource => "resource",
        SyntaxSlot::Resources => "resources",
        SyntaxSlot::Parent => "parent",
        SyntaxSlot::Child => "child",
        SyntaxSlot::Name => "name",
        SyntaxSlot::Table => "table",
        SyntaxSlot::Path => "path",
        SyntaxSlot::Fields => "fields",
        SyntaxSlot::Query => "query",
        SyntaxSlot::ExactFilter => "filter_field",
        SyntaxSlot::DateFilter => "date_field",
        SyntaxSlot::QueryLimit => "max_limit",
        SyntaxSlot::Migration => "migration",
        SyntaxSlot::Revision => "revision",
        SyntaxSlot::PreviousRevision => "down_revision",
        SyntaxSlot::Capabilities => "capabilities",
        SyntaxSlot::Database => "database",
        SyntaxSlot::Authentication => "auth",
        SyntaxSlot::Storage => "storage",
        SyntaxSlot::Workflow => "workflow",
        SyntaxSlot::WorkflowValue => "value_field",
        SyntaxSlot::WorkflowGroup => "group_field",
        SyntaxSlot::LegacyRoutes => "logic",
        SyntaxSlot::SelectedRoutes => "operations",
        SyntaxSlot::Flow => "flow",
        SyntaxSlot::Steps => "steps",
        SyntaxSlot::Then => "then",
        SyntaxSlot::Else => "else",
        SyntaxSlot::Inputs => "inputs",
        SyntaxSlot::Arguments => "args",
        SyntaxSlot::Undo => "undo",
        SyntaxSlot::Exports => "exports",
        SyntaxSlot::Results => "result",
    }
}

fn base_text(issue: &BaseIssue) -> (String, String) {
    match issue {
        BaseIssue::BareCrOrBom { .. } => (
            "Bare CR and BOM are not allowed.".into(),
            "Use LF or CRLF and no BOM.".into(),
        ),
        BaseIssue::MissingPeriod { .. } => (
            "A sentence needs its final period.".into(),
            "End the complete sentence with a period.".into(),
        ),
        BaseIssue::InvalidJsonString { .. } => (
            "Invalid JSON string literal.".into(),
            "Use a quoted JSON string with valid escapes.".into(),
        ),
        BaseIssue::JsonStringBoundary { .. } => (
            "A JSON string needs a token boundary.".into(),
            "Separate grammar tokens with spaces or tabs.".into(),
        ),
        BaseIssue::IncompleteSentence => (
            "Incomplete sentence.".into(),
            "Use the complete fixed sentence form.".into(),
        ),
        BaseIssue::ExpectedKeyword { expected, .. } => (
            format!("Expected {expected}."),
            format!("Use the keyword {expected}."),
        ),
        BaseIssue::TrailingToken { .. } => (
            "Unexpected trailing token.".into(),
            "Use only the fixed sentence form.".into(),
        ),
        BaseIssue::InvalidIdentifier { .. } => (
            "Invalid identifier token.".into(),
            "Use an ASCII Python identifier.".into(),
        ),
        BaseIssue::InvalidJsonScalar { .. } => (
            "Invalid JSON scalar.".into(),
            "Use one JSON scalar literal.".into(),
        ),
        BaseIssue::NonScalarJson { .. } => (
            "Objects and arrays are not scalar literals.".into(),
            "Use a JSON scalar or declared typed input.".into(),
        ),
        BaseIssue::UnexpectedEof => (
            "Unexpected end of input.".into(),
            "Supply the required closing sentence.".into(),
        ),
        BaseIssue::MissingScalar { form } => {
            ("Required scalar clause is missing.".into(), form.clone())
        }
        BaseIssue::ExpectedSentence { expected, .. } => {
            (format!("Expected {expected}"), expected.clone())
        }
        BaseIssue::ExpectedJsonString { .. } => (
            "Expected JSON string.".into(),
            "Use a quoted JSON string.".into(),
        ),
        BaseIssue::Duplicate { item } => (
            format!("Duplicate {item}."),
            format!("Declare {item} exactly once."),
        ),
    }
}

fn request_text(issue: &RequestIssue) -> (String, String) {
    match issue {
        RequestIssue::InvalidHeader { .. } => (
            "Unsupported CNL language version in header.".into(),
            "Use SOM CNL version 1.".into(),
        ),
        RequestIssue::UnsupportedVersion { .. } => (
            "Prompt version is outside the supported set 1 through 7.".into(),
            "Use prompt version 1 through 7.".into(),
        ),
        RequestIssue::NoncanonicalVersion { .. } => (
            "Prompt version token is not a fixed grammar choice.".into(),
            "Use prompt version 1 through 7.".into(),
        ),
        RequestIssue::TrailingContent { .. } => (
            "Unexpected content after End request.".into(),
            "Supply one request only.".into(),
        ),
        RequestIssue::UnknownCapability { kind, expected, .. } => {
            let key = match kind {
                Capability::Database => "database",
                Capability::Authentication => "auth",
                Capability::Storage => "storage",
                Capability::Workflow => "workflow",
                Capability::WorkflowValue => "value_field",
                Capability::WorkflowGroup => "group_field",
            };
            (
                format!("Unknown {key} capability token."),
                expected.join(", "),
            )
        }
        RequestIssue::UnknownRouteScope { .. } => (
            "Unknown related route scope.".into(),
            "For parent, child, or batch: the fixed sentence.".into(),
        ),
        RequestIssue::UnknownSelectedOperation { .. } => (
            "Unknown selected operation.".into(),
            "Allow ACTION on parent|child. or Allow batch creation.".into(),
        ),
        RequestIssue::MissingRoute => (
            "The routes block has no operation.".into(),
            "Declare the required fixed route sentences.".into(),
        ),
    }
}

fn resource_text(issue: &ResourceIssue) -> (String, String) {
    match issue {
        ResourceIssue::MissingQueryEnd => ("Missing query end.".into(), "End query.".into()),
        ResourceIssue::MissingQuerySelector { selector } => match selector {
            QuerySelector::ExactFilter => (
                "The query block has no exact-filter selector clause.".into(),
                "Use Filter exactly on field IDENT. or Use no exact filter.".into(),
            ),
            QuerySelector::DateFilter => (
                "The query block has no date-filter selector clause.".into(),
                "Use Filter before date on field IDENT. or Use no date filter.".into(),
            ),
            QuerySelector::Limit => (
                "The query block has no result limit clause.".into(),
                "Limit query results to UINT.".into(),
            ),
        },
        ResourceIssue::InvalidQueryLimit { .. } => (
            "Query limit must be an unsigned integer.".into(),
            "Limit query results to UINT.".into(),
        ),
        ResourceIssue::UnknownQuerySentence { .. } => (
            "Unknown query sentence.".into(),
            "Use the fixed query selectors and limit.".into(),
        ),
        ResourceIssue::DateSelectorUnavailable { .. } => (
            "Date selector is unavailable in this prompt version.".into(),
            "Use only the exact filter and limit.".into(),
        ),
        ResourceIssue::QuerySelectorsOutOfOrder { .. } => (
            "Query clauses are out of order.".into(),
            "Declare exact filter, date filter, then limit.".into(),
        ),
        ResourceIssue::DuplicateFieldName { .. } => (
            "Duplicate field name.".into(),
            "Use unique field names within the resource.".into(),
        ),
        ResourceIssue::InvalidFieldRequirement { .. } => (
            "Expected required or optional.".into(),
            "Use required or optional.".into(),
        ),
        ResourceIssue::UnknownFieldType { actual } => (
            format!("Field token {actual} is outside the grammar vocabulary."),
            "str, int, float, bool, or date when the prompt version allows date.".into(),
        ),
        ResourceIssue::InvalidFieldUniqueness { .. } => (
            "Expected unique or not unique.".into(),
            "Use unique or not unique.".into(),
        ),
        ResourceIssue::MissingResourceFields => (
            "Resource needs at least one field.".into(),
            "Field IDENT is required|optional TYPE and is unique|not unique.".into(),
        ),
    }
}

fn flow_name(kind: FlowNameKind) -> &'static str {
    match kind {
        FlowNameKind::Input => "Input",
        FlowNameKind::Step => "Step",
        FlowNameKind::Transaction => "Transaction",
        FlowNameKind::Branch => "Branch",
        FlowNameKind::Export => "Export",
    }
}

fn flow_text(issue: &FlowIssue) -> (String, String) {
    match issue {
        FlowIssue::DuplicateName {
            current,
            prior,
            name,
        } => (
            format!(
                "{} name {name} conflicts with the earlier {} name {name}.",
                flow_name(*current),
                flow_name(*prior).to_ascii_lowercase(),
            ),
            "Use unique names across inputs and nodes.".into(),
        ),
        FlowIssue::MissingBlockEnd { expected } => ("Missing block end.".into(), expected.clone()),
        FlowIssue::MismatchedBlockDelimiter { expected, .. } => {
            ("Mismatched block delimiter.".into(), expected.clone())
        }
        FlowIssue::UnsupportedInputType { .. } => (
            "Unsupported input type token.".into(),
            "Use a type listed by the CNL input grammar.".into(),
        ),
        FlowIssue::MissingInputDeclaration => (
            "Input declaration is missing.".into(),
            "Declare inputs or Use no inputs.".into(),
        ),
        FlowIssue::UnknownNodeKind { .. } => (
            "Unknown node kind.".into(),
            "Begin step, transaction, or branch.".into(),
        ),
        FlowIssue::UnknownOperation { .. } => (
            "Operation token is not in the CNL operation vocabulary.".into(),
            "Use one operation listed by the grammar.".into(),
        ),
        FlowIssue::DuplicateArgument { .. } => (
            "Duplicate argument.".into(),
            "Declare argument exactly once.".into(),
        ),
        FlowIssue::UnknownUndoMode { .. } => (
            "Unknown undo mode.".into(),
            "Use delete_created, restore_patch, or no undo.".into(),
        ),
        FlowIssue::MissingUndoDeclaration => (
            "Missing undo declaration.".into(),
            "Declare the exact undo or use no undo.".into(),
        ),
        FlowIssue::MissingBranchExport => (
            "Branch export declaration is missing.".into(),
            "Export no values. or Export IDENT from then VALUE and from else VALUE.".into(),
        ),
        FlowIssue::DuplicateResultName { .. } => (
            "Duplicate result name.".into(),
            "Declare result name exactly once.".into(),
        ),
        FlowIssue::MissingResultDeclaration => (
            "Result declaration is missing.".into(),
            "Return no values. or Return IDENT from VALUE.".into(),
        ),
        FlowIssue::ResultFieldNotString { .. } => (
            "Result field must be a JSON string.".into(),
            "step IDENT field JSON_STRING".into(),
        ),
        FlowIssue::UnknownScalarLiteralType { .. } => (
            "Unknown scalar literal type.".into(),
            "Use string, integer, float, boolean, date, or bytes.".into(),
        ),
        FlowIssue::UnknownRuntimeReference { .. } => (
            "Unknown runtime reference form.".into(),
            "Use input NAME, step NAME field JSON_STRING, or literal TYPE JSON_SCALAR.".into(),
        ),
        FlowIssue::UnknownAtComparison { .. } => (
            "Unknown comparison.".into(),
            "Use at most or at least.".into(),
        ),
        FlowIssue::UnknownComparison { .. } => (
            "Unknown comparison.".into(),
            "Use a fixed comparison or is null.".into(),
        ),
    }
}

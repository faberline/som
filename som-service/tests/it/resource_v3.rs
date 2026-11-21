//! Typed JSON prompt v3 for resources: the plan, the bundle with the catalog
//! version, the health entry, and one refusal per rule of the validator.
//! Expected plans are fixtures written by hand from the shared design.
use serde_json::{Value, json};
use som_service::bundle::{canonical, hash, identity};
use som_service::resource::{plural_path, snake_case};
use som_service::{Error, assemble, compile_json, compile_prompt_v3, health};

const ORDER: &str = include_str!("../../contract/order.resource.prompt.json");
const CUSTOMER: &str = include_str!("../../contract/customer.resource.prompt.json");
const ORDER_PLAN: &str = include_str!("fixtures/order.resource.dsl.json");
const CUSTOMER_PLAN: &str = include_str!("fixtures/customer.resource.dsl.json");

fn value(text: &str) -> Value {
    serde_json::from_str(text).expect("JSON fixture")
}

fn ok<T>(result: Result<T, Error>) -> T {
    match result {
        Ok(value) => value,
        Err(error) => panic!("unexpected error {}: {}", error.status, error.body),
    }
}

fn refused<T>(result: Result<T, Error>, status: u16, code: &str) -> Error {
    let error = match result {
        Ok(_) => panic!("expected {code}, but request was accepted"),
        Err(error) => error,
    };
    assert_eq!(error.status, status, "{}", error.body);
    assert_eq!(error.body["code"], code, "{}", error.body);
    error
}

fn bundle_of(prompt: &str) -> Value {
    ok(compile_prompt_v3(&value(prompt)))
}

/// The Order request as a JSON object that a test can change in one place.
fn order_with(edit: impl FnOnce(&mut Value)) -> Value {
    let mut prompt = value(ORDER);
    edit(&mut prompt);
    prompt
}

/// Compiles a changed prompt and checks the status, code and pointer of its one diagnostic.
fn expect_refusal(prompt: Value, status: u16, code: &str, pointer: &str) -> Error {
    let error = refused(compile_prompt_v3(&prompt), status, code);
    assert_eq!(
        error.body["diagnostics"][0]["prompt_pointer"], pointer,
        "{}",
        error.body
    );
    error
}

#[test]
fn order_and_customer_prompts_compile_to_the_fixture_plans() {
    for (prompt, plan) in [(ORDER, ORDER_PLAN), (CUSTOMER, CUSTOMER_PLAN)] {
        let bundle = bundle_of(prompt);
        assert_eq!(bundle["dsl"], value(plan));
        assert_eq!(bundle["prompt"], value(prompt));
        assert_eq!(bundle["catalog"], "som.std.v1");
        assert_eq!(bundle["version"], "som.bundle.resource.v1");
        assert_eq!(bundle["assets"], identity());
        let mut unsigned = bundle.clone();
        unsigned.as_object_mut().unwrap().remove("digest");
        assert_eq!(bundle["digest"], hash(&canonical(&unsigned)));
    }
}

#[test]
fn compile_json_accepts_format_v3() {
    let text = format!("{{\"format\":\"som.prompt-json.service.v3\",\"prompt\":{ORDER}}}");
    let answer = ok(compile_json(&text));
    assert_eq!(answer["status"], "ok");
    assert_eq!(answer["bundle"], bundle_of(ORDER));
}

#[test]
fn plan_derives_snake_plural_and_path() {
    for (pascal, snake, plural, path) in [
        ("Order", "order", "orders", "/v1/orders"),
        ("Category", "category", "categories", "/v1/categories"),
        ("Box", "box", "boxes", "/v1/boxes"),
        ("OrderItem", "order_item", "order_items", "/v1/order-items"),
    ] {
        let got_snake = snake_case(pascal);
        assert_eq!(got_snake, snake, "snake of {pascal}");
        let (got_plural, got_path) = plural_path(&got_snake);
        assert_eq!(got_plural, plural, "plural of {pascal}");
        assert_eq!(got_path, path, "path of {pascal}");
    }
}

#[test]
fn health_lists_resource_versions_and_old_lists_are_unchanged() {
    let health = health();
    assert_eq!(
        health["resource"],
        json!({
            "formats": ["som.prompt-json.service.v3"],
            "prompt_versions": ["som.prompt.service.v3"],
            "dsl_versions": ["som.dsl.service.v3"],
            "targets": ["rust-resource-service.v1"],
            "catalog": "som.std.v1"
        })
    );
    assert_eq!(
        health["formats"],
        json!([
            "som.cnl.service.v1",
            "som.prompt-json.service.v1",
            "som.prompt-json.service.v2"
        ])
    );
    assert_eq!(health["formats"].as_array().unwrap().len(), 3);
    assert_eq!(
        health["prompt_versions"],
        json!(["som.prompt.service.v1", "som.prompt.service.v2"])
    );
    assert_eq!(
        health["dsl_versions"],
        json!(["som.dsl.service.v1", "som.dsl.service.v2"])
    );
    assert_eq!(health["targets"], json!(["rust-service-client.v1"]));
}

#[test]
fn defer_assemble_refuses_a_resource_bundle() {
    let bundle = bundle_of(ORDER);
    let error = refused(assemble(&bundle), 400, "INVALID_BUNDLE");
    assert_eq!(error.body["code"], "INVALID_BUNDLE");
}

#[test]
fn unknown_standard_sort_is_refused_at_its_pointer() {
    let prompt = order_with(|p| p["resources"][0]["standards"][1] = json!("sort"));
    let error = expect_refusal(prompt, 422, "UNKNOWN_STANDARD", "/resources/0/standards/1");
    let detail = error.body["detail"].as_str().unwrap();
    assert!(detail.contains("not in som.std.v1"), "{detail}");
    assert!(detail.contains("not in v1"), "{detail}");
}

#[test]
fn a_resource_with_only_crud_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["standards"] = json!(["CRUD"]));
    expect_refusal(prompt, 400, "MISSING_STANDARD", "/resources/0/standards");
}

#[test]
fn a_resource_without_fields_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"] = json!([]));
    expect_refusal(prompt, 400, "MISSING_FACT", "/resources/0/fields");
}

#[test]
fn a_field_without_required_is_refused() {
    let prompt = order_with(|p| {
        p["resources"][0]["fields"][1]
            .as_object_mut()
            .unwrap()
            .remove("required");
    });
    expect_refusal(
        prompt,
        400,
        "MISSING_FACT",
        "/resources/0/fields/1/required",
    );
}

#[test]
fn unknown_field_type_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1]["type"] = json!("float"));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/1/type");
}

#[test]
fn reserved_field_name_id_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][0]["name"] = json!("id"));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/0/name");
}

#[test]
fn duplicate_field_name_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1]["name"] = json!("customer_id"));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/1/name");
}

#[test]
fn duplicate_resource_name_is_refused() {
    let prompt = order_with(|p| {
        let first = p["resources"][0].clone();
        p["resources"].as_array_mut().unwrap().push(first);
    });
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/1/name");
}

#[test]
fn upper_letters_with_min_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][2]["min"] = json!(1));
    expect_refusal(
        prompt,
        400,
        "INVALID_VALUE",
        "/resources/0/fields/2/upper_letters",
    );
}

#[test]
fn min_greater_than_max_is_refused() {
    let prompt = order_with(|p| {
        p["resources"][0]["fields"][4]["min"] = json!(10);
        p["resources"][0]["fields"][4]["max"] = json!(5);
    });
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/4/min");
}

#[test]
fn one_value_enum_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][3]["values"] = json!(["pending"]));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/3/values");
}

#[test]
fn unknown_constraint_key_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][0]["pattern"] = json!("x"));
    expect_refusal(
        prompt,
        400,
        "INVALID_SCHEMA",
        "/resources/0/fields/0/pattern",
    );
}

#[test]
fn wrong_target_is_refused() {
    let prompt = order_with(|p| p["target"] = json!("rust-service-client.v1"));
    let error = expect_refusal(prompt, 422, "UNSUPPORTED_TARGET", "/target");
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Target rust-resource-service.v1."
    );
}

#[test]
fn wrong_version_is_refused() {
    let prompt = order_with(|p| p["version"] = json!("som.prompt.service.v9"));
    expect_refusal(prompt, 422, "UNSUPPORTED_VERSION", "/version");
}

#[test]
fn version_that_is_not_text_is_refused() {
    let prompt = order_with(|p| p["version"] = json!(3));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/version");
}

#[test]
fn an_extra_top_level_key_is_refused() {
    let prompt = order_with(|p| p["extra"] = json!(1));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "");
}

#[test]
fn a_package_that_is_not_an_identifier_is_refused() {
    let prompt = order_with(|p| p["package"] = json!("Orders-service"));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/package");
}

#[test]
fn a_target_that_is_not_text_is_refused() {
    let prompt = order_with(|p| p["target"] = json!(7));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/target");
}

#[test]
fn resources_that_are_not_an_array_are_refused() {
    let prompt = order_with(|p| p["resources"] = json!("Order"));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources");
}

#[test]
fn an_empty_resource_list_is_refused() {
    let prompt = order_with(|p| p["resources"] = json!([]));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources");
}

#[test]
fn nine_resources_are_refused() {
    let prompt = order_with(|p| {
        let one = p["resources"][0].clone();
        p["resources"] = json!(vec![one; 9]);
    });
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources");
}

#[test]
fn a_resource_with_an_extra_key_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["extra"] = json!(1));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0");
}

#[test]
fn resource_names_that_are_not_pascal_are_refused() {
    for name in ["order", "Or_der", &"A".repeat(65)] {
        let prompt = order_with(|p| p["resources"][0]["name"] = json!(name));
        expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/name");
    }
}

#[test]
fn a_resource_name_that_is_not_text_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["name"] = json!(5));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/name");
}

#[test]
fn a_fields_value_that_is_not_an_array_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"] = json!("customer_id"));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/fields");
}

#[test]
fn more_than_32_fields_are_refused() {
    let prompt = order_with(|p| {
        let fields: Vec<Value> = (0..33)
            .map(|i| json!({"name": format!("f{i}"), "type": "string", "required": true}))
            .collect();
        p["resources"][0]["fields"] = json!(fields);
    });
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/fields");
}

#[test]
fn a_field_that_is_not_an_object_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1] = json!("total_cents"));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/fields/1");
}

#[test]
fn a_field_without_type_is_refused() {
    let prompt = order_with(|p| {
        p["resources"][0]["fields"][1]
            .as_object_mut()
            .unwrap()
            .remove("type");
    });
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/fields/1");
}

#[test]
fn a_field_name_that_is_not_text_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1]["name"] = json!(5));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/fields/1/name");
}

#[test]
fn a_field_name_that_is_not_a_lower_case_identifier_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][0]["name"] = json!("Customer"));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/0/name");
}

#[test]
fn reserved_field_name_created_at_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1]["name"] = json!("created_at"));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/1/name");
}

#[test]
fn a_field_type_that_is_not_text_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1]["type"] = json!(5));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/fields/1/type");
}

#[test]
fn required_that_is_not_true_or_false_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1]["required"] = json!("yes"));
    expect_refusal(
        prompt,
        400,
        "INVALID_VALUE",
        "/resources/0/fields/1/required",
    );
}

#[test]
fn a_string_min_that_is_not_an_integer_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][0]["min"] = json!("1"));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/0/min");
}

#[test]
fn a_string_max_above_the_limit_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][4]["max"] = json!(10001));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/4/max");
}

#[test]
fn an_integer_max_below_its_min_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][1]["max"] = json!(-1));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/1/min");
}

#[test]
fn an_enum_without_values_is_refused() {
    let prompt = order_with(|p| {
        p["resources"][0]["fields"][3]
            .as_object_mut()
            .unwrap()
            .remove("values");
    });
    expect_refusal(prompt, 400, "MISSING_FACT", "/resources/0/fields/3/values");
}

#[test]
fn an_enum_value_that_is_not_text_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][3]["values"] = json!(["pending", 1]));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/3/values");
}

#[test]
fn an_enum_value_longer_than_32_bytes_is_refused() {
    let long = "a".repeat(33);
    let prompt =
        order_with(|p| p["resources"][0]["fields"][3]["values"] = json!(["pending", long]));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/3/values");
}

#[test]
fn duplicate_enum_values_are_refused() {
    let prompt = order_with(|p| p["resources"][0]["fields"][3]["values"] = json!(["paid", "paid"]));
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/fields/3/values");
}

#[test]
fn a_standard_that_is_not_text_is_refused() {
    let prompt = order_with(|p| p["resources"][0]["standards"] = json!([1, "standard pagination"]));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/standards/0");
}

#[test]
fn standards_that_are_not_an_array_are_refused() {
    let prompt = order_with(|p| p["resources"][0]["standards"] = json!("CRUD"));
    expect_refusal(prompt, 400, "INVALID_SCHEMA", "/resources/0/standards");
}

#[test]
fn a_duplicate_standard_is_refused() {
    let prompt = order_with(|p| {
        p["resources"][0]["standards"] = json!(["CRUD", "CRUD", "standard pagination"]);
    });
    expect_refusal(prompt, 400, "INVALID_VALUE", "/resources/0/standards/1");
}

#[test]
fn an_unknown_standard_outside_the_v1_note_list_has_no_v1_note() {
    let prompt = order_with(|p| p["resources"][0]["standards"][1] = json!("cache"));
    let error = expect_refusal(prompt, 422, "UNKNOWN_STANDARD", "/resources/0/standards/1");
    let detail = error.body["detail"].as_str().unwrap();
    assert!(detail.contains("cache"), "{detail}");
    assert!(!detail.contains("not in v1"), "{detail}");
}

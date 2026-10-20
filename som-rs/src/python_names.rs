//! Prompt names against generated Python identifiers (selection policy v1.2).
//! `check_collisions` refuses a name that shares a scope with a template identifier.
//! A field or foreign key named `self` selects the `self_name_variants` blocks.
use crate::{Block, SomError};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

const POLICY_TEXT: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/contracts/compiler-v1/selection-policy.json"
));
static POLICY: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(POLICY_TEXT).expect("compiled compiler selection policy v1.2")
});

const COLLISION: &str = "name collides with a generated Python identifier";

fn error(code: &'static str, detail: impl Into<String>) -> SomError {
    SomError::new(code, detail)
}

/// The `self_name_variants` family of a prompt version.
pub fn family(version: &str) -> Option<&'static str> {
    match version {
        "som.prompt.v1" | "som.prompt.v2" | "som.prompt.v3" | "som.prompt.v4"
        | "som.prompt.v5" => Some("v1-v5"),
        "som.prompt.v6" => Some("v6"),
        "som.prompt.v7" => Some("v7"),
        _ => None,
    }
}

/// (base id, variant id) pairs of a family, in policy order.
fn variants(family: &str) -> Vec<(&'static str, &'static str)> {
    POLICY["self_name_variants"]["families"][family]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| (row["base"].as_str().unwrap(), row["variant"].as_str().unwrap()))
                .collect()
        })
        .unwrap_or_default()
}

fn block_hash(block: &Block) -> String {
    let value = json!({"id":block.id,"version":block.version,"imports":block.imports,"params":block.params,"template":block.template});
    format!("{:x}", Sha256::digest(serde_json::to_vec(&value).unwrap()))
}

/// Check that a loaded library holds the approved base and variant bytes of a family.
pub fn verify_variants(blocks: &BTreeMap<String, Block>, family: &str) -> Result<(), SomError> {
    for row in POLICY["self_name_variants"]["families"][family].as_array().unwrap() {
        for (id, hash) in [("base", "base_source_sha256"), ("variant", "variant_source_sha256")] {
            let block = blocks
                .get(row[id].as_str().unwrap())
                .ok_or_else(|| error("INVALID_BLOCK", "self name variant block is missing"))?;
            if row[hash] != json!(block_hash(block)) {
                return Err(error("INVALID_BLOCK", "self name variant bytes differ from the policy"));
            }
        }
    }
    Ok(())
}

fn resources(prompt: &Value) -> Vec<(&'static str, &Value)> {
    if prompt.get("resource").is_some() {
        return vec![("resource", &prompt["resource"])];
    }
    ["parent", "child"]
        .into_iter()
        .filter(|role| prompt["resources"].get(*role).is_some())
        .map(|role| (role, &prompt["resources"][role]))
        .collect()
}

/// True when any resource field or the foreign key is named `self`.
pub fn uses_self(prompt: &Value) -> bool {
    prompt["relation"]["foreign_key"] == "self"
        || resources(prompt).iter().any(|(_, resource)| {
            resource["fields"]
                .as_array()
                .is_some_and(|fields| fields.iter().any(|field| field["name"] == "self"))
        })
}

fn swap_ids(dsl: &mut Value, map: &BTreeMap<&str, &str>) {
    let Some(files) = dsl["files"].as_array_mut() else { return };
    for file in files {
        let Some(blocks) = file["blocks"].as_array_mut() else { continue };
        for block in blocks {
            if let Some(next) = block["id"].as_str().and_then(|id| map.get(id)) {
                block["id"] = json!(next);
            }
        }
    }
}

/// Select the `self` variants in a primary plan. Plans without the trigger are unchanged.
pub fn select_variants(prompt: &Value, mut dsl: Value) -> Value {
    if uses_self(prompt) {
        if let Some(family) = prompt["version"].as_str().and_then(family) {
            swap_ids(&mut dsl, &variants(family).into_iter().collect());
        }
    }
    dsl
}

/// The plan with variant ids replaced by their base ids, for catalog validation.
/// A variant without the trigger, or a base block that has a variant with it, fails.
pub fn base_plan(prompt: &Value, dsl: &Value) -> Result<Value, SomError> {
    let Some(family) = prompt["version"].as_str().and_then(family) else {
        return Ok(dsl.clone());
    };
    let pairs = variants(family);
    let trigger = uses_self(prompt);
    let used: BTreeSet<&str> = dsl["files"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|file| file["blocks"].as_array().into_iter().flatten())
        .filter_map(|block| block["id"].as_str())
        .collect();
    for (base, variant) in &pairs {
        if used.contains(if trigger { base } else { variant }) {
            return Err(error(
                "INVALID_COMPOSITION",
                if trigger {
                    "a field or foreign key named self requires the self name variant block"
                } else {
                    "self name variant block without a field or foreign key named self"
                },
            ));
        }
    }
    let mut result = dsl.clone();
    swap_ids(&mut result, &pairs.iter().map(|(base, variant)| (*variant, *base)).collect());
    Ok(result)
}

struct Slot {
    name: String,
    roles: BTreeSet<String>,
    // (role, prompt path) for each specific role; "" is the name member itself.
    paths: Vec<(String, String)>,
}

fn slot(name: &Value, role: &str, path: String) -> Option<Slot> {
    Some(Slot {
        name: name.as_str()?.to_owned(),
        roles: BTreeSet::from([role.to_owned()]),
        paths: vec![(role.to_owned(), path)],
    })
}

fn add_role(slot: &mut Slot, role: &str, path: String) {
    slot.roles.insert(role.to_owned());
    slot.paths.push((role.to_owned(), path));
}

fn field_slots(prompt: &Value, role: &str, resource: &Value, base: &str, query: &str) -> Vec<Slot> {
    let field_role = if role == "resource" { "field".to_owned() } else { format!("{role}_field") };
    let mut slots: Vec<Slot> = resource["fields"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
        .filter_map(|(index, field)| {
            slot(&field["name"], &field_role, format!("{base}.fields[{index}].name"))
        })
        .collect();
    for key in ["filter_field", "date_field"] {
        let wanted = &prompt.pointer(query).unwrap_or(&Value::Null)[key];
        for slot in slots.iter_mut().filter(|slot| wanted == slot.name.as_str()) {
            let path = format!("{}.{key}", query.trim_start_matches('/').replace('/', "."));
            add_role(slot, key, format!("prompt.{path}"));
        }
    }
    slots
}

fn slots(prompt: &Value) -> Vec<Slot> {
    let mut result = Vec::new();
    if prompt.get("resource").is_some() {
        let resource = &prompt["resource"];
        result.extend(slot(&resource["name"], "resource", "prompt.resource.name".into()));
        result.extend(field_slots(prompt, "resource", resource, "prompt.resource", "/query"));
        return result;
    }
    let mut groups = Vec::new();
    for (role, resource) in resources(prompt) {
        let base = format!("prompt.resources.{role}");
        let name = slot(&resource["name"], "resource", format!("{base}.name"));
        let query = format!("/resources/{role}/query");
        groups.push((role, name, field_slots(prompt, role, resource, &base, &query)));
    }
    for key in ["group_field", "value_field"] {
        let Some(wanted) = prompt["capabilities"][key].as_str() else { continue };
        let path = format!("prompt.capabilities.{key}");
        let target = ["child", "parent"].into_iter().find_map(|role| {
            groups.iter().position(|(r, _, fields)| {
                *r == role && fields.iter().any(|slot| slot.name == wanted)
            })
        });
        if let Some(index) = target {
            let fields = &mut groups[index].2;
            let slot = fields.iter_mut().find(|slot| slot.name == wanted).unwrap();
            add_role(slot, key, path);
        }
    }
    for (_, name, fields) in groups {
        result.extend(name);
        result.extend(fields);
    }
    result.extend(slot(
        &prompt["relation"]["foreign_key"],
        "foreign_key",
        "prompt.relation.foreign_key".into(),
    ));
    result
}

/// Refuse a prompt name that collides with a generated Python identifier.
pub fn check_collisions(prompt: &Value) -> Result<(), SomError> {
    let database = prompt["capabilities"]["database"].as_str().unwrap_or("postgresql");
    let Some(rows) = prompt["version"].as_str().and_then(|version| {
        POLICY["python_name_collisions"]["versions"][version][database].as_array()
    }) else {
        return Ok(());
    };
    for slot in slots(prompt) {
        for row in rows {
            let roles: BTreeSet<String> = row["roles"]
                .as_array()
                .unwrap()
                .iter()
                .map(|role| role.as_str().unwrap().to_owned())
                .collect();
            if !roles.is_subset(&slot.roles)
                || !row["names"].as_array().unwrap().iter().any(|name| name == slot.name.as_str())
            {
                continue;
            }
            // Point at the member that adds the row's most specific role.
            let path = slot
                .paths
                .iter()
                .rev()
                .find(|(role, _)| roles.contains(role))
                .map(|(_, path)| path.clone())
                .unwrap_or_else(|| slot.paths[0].1.clone());
            return Err(error("INVALID_VALUE", format!("{path}: {COLLISION}")));
        }
    }
    Ok(())
}

pub fn is_collision(detail: &str) -> bool {
    detail.ends_with(COLLISION)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn example(version: &str) -> Value {
        let path = format!(
            "{}/contracts/compiler-v1/examples/{version}.prompt.json",
            env!("CARGO_MANIFEST_DIR")
        );
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    #[test]
    fn examples_have_no_collision_or_self() {
        for version in ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v7-branch-null"] {
            let prompt = example(version);
            assert!(check_collisions(&prompt).is_ok(), "{version}");
            assert!(!uses_self(&prompt), "{version}");
        }
    }

    #[test]
    fn v3_limit_filter_is_refused_at_the_query_member() {
        let mut prompt = example("v3");
        prompt["resource"]["fields"][0]["name"] = json!("limit");
        assert!(check_collisions(&prompt).is_ok());
        prompt["query"]["filter_field"] = json!("limit");
        let refused = check_collisions(&prompt).unwrap_err();
        assert_eq!(refused.code, "INVALID_VALUE");
        assert_eq!(refused.detail, format!("prompt.query.filter_field: {COLLISION}"));
    }

    #[test]
    fn resource_field_and_key_slots() {
        let mut prompt = example("v7");
        prompt["resources"]["parent"]["name"] = json!("SCHEMA");
        assert_eq!(
            check_collisions(&prompt).unwrap_err().detail,
            format!("prompt.resources.parent.name: {COLLISION}")
        );
        let mut prompt = example("v6");
        prompt["relation"]["foreign_key"] = json!("int");
        assert_eq!(
            check_collisions(&prompt).unwrap_err().detail,
            format!("prompt.relation.foreign_key: {COLLISION}")
        );
        let mut prompt = example("v1");
        prompt["resource"]["fields"][0]["name"] = json!("mapped_column");
        assert_eq!(
            check_collisions(&prompt).unwrap_err().detail,
            format!("prompt.resource.fields[0].name: {COLLISION}")
        );
    }

    #[test]
    fn tables_follow_the_database() {
        let mut prompt = example("v5");
        prompt["resources"]["parent"]["name"] = json!("Document");
        assert!(check_collisions(&prompt).is_ok());
        prompt["capabilities"]["database"] = json!("mongodb");
        assert_eq!(
            check_collisions(&prompt).unwrap_err().detail,
            format!("prompt.resources.parent.name: {COLLISION}")
        );
    }

    #[test]
    fn base_plan_requires_the_variant_exactly_with_self() {
        let mut prompt = example("v6");
        let plain = json!({"files":[{"path":"m.py","blocks":[{"id":"orm.base"}]}]});
        let variant = json!({"files":[{"path":"m.py","blocks":[{"id":"orm.base.self"}]}]});
        assert_eq!(base_plan(&prompt, &plain).unwrap(), plain);
        assert_eq!(base_plan(&prompt, &variant).unwrap_err().code, "INVALID_COMPOSITION");
        prompt["relation"]["foreign_key"] = json!("self");
        assert!(uses_self(&prompt));
        assert_eq!(select_variants(&prompt, plain.clone()), variant);
        assert_eq!(base_plan(&prompt, &variant).unwrap(), plain);
        assert_eq!(base_plan(&prompt, &plain).unwrap_err().code, "INVALID_COMPOSITION");
    }
}

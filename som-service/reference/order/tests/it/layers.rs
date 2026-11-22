use crate::fixture::Must;
use std::path::Path;

#[test]
fn four_layers_are_present() {
    for layer in ["domain", "application", "infrastructure", "interfaces"] {
        let dir = format!("{}/src/{layer}", env!("CARGO_MANIFEST_DIR"));
        assert!(Path::new(&dir).is_dir(), "missing layer {dir}");
        assert!(
            Path::new(&format!("{dir}/mod.rs")).is_file(),
            "missing mod.rs in {dir}"
        );
    }
    let order =
        std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/src/domain/order.rs"))
            .must("domain/order.rs exists");
    assert!(order.contains("pub struct OrderInput"));
}

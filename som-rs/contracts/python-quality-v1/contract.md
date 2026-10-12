# som.python-quality.v1

All generated Python targets Python 3.12. Each function, method, attribute and
container element has a concrete type. Local variables may use type inference.
Each module uses four spaces, LF endings and a 79 character line limit.

Pinned tools: Ruff 0.16.6, mypy 2.3.1, Pylint 4.0.9 and isort 9.0.2.
The adjacent `pyproject.toml` defines the common check settings. The generated
project adds its validated package name to both import sorters. The package
name does not change the rule set.

The native assembler emits import metadata and omits bindings not used by the
block bodies. Long string parameters use adjacent Python literals. These are
assembly choices and do not change block selection, order, references or values.

The request path uses the verified native Ruff binary through standard input.
Only Ruff's I rules may fix imports. Ruff format may change whitespace and
quotes. The final Ruff check has no fixes. A failed or timed out invocation
rejects the complete result with OUTPUT_QUALITY_FAILED. Final limits are 12
files, 64 blocks and 65,536 UTF-8 bytes. No Python process runs in this path.

Offline acceptance checks every app, test and migration with all four tools.
No blanket missing-import rule, whole-file typing skip or uncoded ignore is
allowed. Each SDK or framework exception must name its symbol, exact rule,
reason and verification case in `exceptions.json`. First-party CRUD, access
control and transaction code do not receive type or semantic lint exceptions.

Annotations are checked against adversarial missing-annotation examples.
SDK values must cross a validating boundary before becoming first-party data.
Output checks and source integrity use the final formatted bytes. Reformatting
those bytes must not change them.

Human review covers resource closure, narrow exception handling, function
responsibility and readable composition. Passing tool checks alone does not
prove this review or functional correctness.

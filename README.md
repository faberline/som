# som

The Snippet-Oriented Model (SOM): a decompression code generator that turns
one developer intent into a small program by planning, laying out the
component topology, choosing snippet operations, and assembling the result
from a frozen, verified snippet instruction set.

| Project | What it is |
|---------|------------|
| [som-core](som-core/README.md) | The engine and `som` CLI: the layer records, the reference assembler, and the corpus loader. |
| [som-code-python](som-code-python/README.md) | The Python ecosystem corpus: executable oracle families, the snippet ISA, and their gates. |

Each project is its own uv project; its CONTRIBUTING.md lists the gates.

## License

MIT — see [LICENSE](LICENSE).

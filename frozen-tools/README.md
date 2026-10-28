# Frozen M6 tool file supplement

These are eleven public third-party Homebrew tool files.
They contain no SOM prompts, responses, credentials, SOM model assets, or user documents.

Each complete file hash is already pinned by the unchanged SOM toolchain contracts.
The files were copied read-only from the frozen installed tool set.
The official source bottle digest is recorded for each file.
These installed bytes are not claimed to be raw bottle bytes.

The GitHub runner still verifies the official bottles and the full tool closure.
It uses this supplement only for the eleven exact installed path and hash bindings.
The other executable files, including libnode, still come from the verified bottle path.
No installed hash requirement changes. A missing or wrong file stops provisioning.

The current macOS signer cannot reproduce every historical signature allocation.
The supplement preserves those existing complete files without changing signature bytes.
This delivery does not prove product acceptance.

Original license and notice text is preserved under licenses/.
The installed public Homebrew formula text is under provenance/.
The official bottle URLs and source formulas remain available through Homebrew.

- `node`: `/opt/homebrew/Cellar/node/26.5.0/bin/node`; SHA-256 `70851490e028b3d699a8d6d4e1de909af2a989359ae807974c92af9c6580a8e8`; license `frozen-tools/licenses/node.txt`.
- `libuv`: `/opt/homebrew/Cellar/libuv/1.52.1/lib/libuv.1.0.0.dylib`; SHA-256 `c56f794e7c9dbcf8c45fba6109836196263a4d5e95936eee7601593532c6cfe9`; license `frozen-tools/licenses/libuv.txt`.
- `ada-url`: `/opt/homebrew/Cellar/ada-url/3.4.4/lib/libada.3.4.4.dylib`; SHA-256 `b39ba5c76cfa9e8d7a37b51daf937414316b671f51360daae62b9885e9d089f8`; license `frozen-tools/licenses/ada-url.txt`.
- `simdjson`: `/opt/homebrew/Cellar/simdjson/4.6.4/lib/libsimdjson.33.0.0.dylib`; SHA-256 `031cfb565154f822e33b9227ef392c257260c5ebb8fbfc9f317c56be82bfa16a`; license `frozen-tools/licenses/simdjson.txt`.
- `c-ares`: `/opt/homebrew/Cellar/c-ares/1.34.8/lib/libcares.2.19.7.dylib`; SHA-256 `380a4b57ed007eecfd8b31d27feb3ea4749125e49780f8ca55dceb6112993c77`; license `frozen-tools/licenses/c-ares.txt`.
- `hdrhistogram_c`: `/opt/homebrew/Cellar/hdrhistogram_c/0.11.10/lib/libhdr_histogram.6.3.3.dylib`; SHA-256 `90286f692dd222d09642670523a5ef978f14b553614e6fa2d550dcb179e0d39c`; license `frozen-tools/licenses/hdrhistogram_c.txt`.
- `merve`: `/opt/homebrew/Cellar/merve/1.2.2_1/lib/libmerve.1.2.2.dylib`; SHA-256 `cda7651d81af902d5964705451e7bcb3c4769a971e793b33472ccf6c07e2dc58`; license `frozen-tools/licenses/merve.txt`.
- `nbytes`: `/opt/homebrew/Cellar/nbytes/0.1.4/lib/libnbytes.dylib`; SHA-256 `b063a6b50d0982379e5a78fa22904e9299ac0048d82cae4f90bd4ad11fa40f65`; license `frozen-tools/licenses/nbytes.txt`.
- `libnghttp3`: `/opt/homebrew/Cellar/libnghttp3/1.17.0/lib/libnghttp3.9.8.0.dylib`; SHA-256 `02f5cd0648e3c812c3f15573055b12ffd9a6447bd781286198ca6272140e2d6d`; license `frozen-tools/licenses/libnghttp3.txt`.
- `libngtcp2`: `/opt/homebrew/Cellar/libngtcp2/1.24.0/lib/libngtcp2.16.dylib`; SHA-256 `c55cbdb643db897ea328a5c1a8cac9f802e165bc4499bd9f823fc65e932769a9`; license `frozen-tools/licenses/libngtcp2.txt`.
- `libffi`: `/opt/homebrew/Cellar/libffi/3.7.1/lib/libffi.8.dylib`; SHA-256 `8def831bad531fb16a8dcb35ec89da501fa4d797478af8e69fc6aac7b6eda18a`; license `frozen-tools/licenses/libffi.txt`.

## Frozen npm tree supplement

The separate `npm-tree-manifest.json` retains eighteen CPython 3.12 bytecode files from npm 11.17.0's bundled public node-gyp/gyp and packaging dependencies. The frozen full npm tree includes these historical cache files. The official Node bottle has the same source bytes but lacks these caches. This supplement keeps the original bytes and does not run or regenerate them.

Every cache path, complete hash, size and mode is pinned. The source bottle association and the unchanged full npm tree hash, 1957 file count and nine symlinks remain required. Copy only missing named files after the verified bottle's npm tree and expected npmrc are installed. A conflicting existing file stops provisioning.

Matching public Python source is under `provenance/npm/`. Original npm, node-gyp, gyp and packaging license text is under `licenses/npm*.txt`. The bytecode source filenames refer only to the public `/opt/homebrew/lib/node_modules/npm` installation. This directory contains no SOM inputs, user documents or credentials. The supplement does not prove product acceptance.

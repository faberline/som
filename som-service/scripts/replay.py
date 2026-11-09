#!/usr/bin/env python3
"""Run the complete local service, native Defer, NLP and preserved SOM acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parent
RUST = Path("/opt/homebrew/Cellar/rust/1.96.1/bin")
NLP_REV = "4ceb753b5cde60a0c474903be9ea2ae3717bb6e5"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="A new evidence directory; default is a new temporary directory")
    parser.add_argument("--cargo-home", type=Path, default=REPO/".local/defer-task-status/cargo-home")
    parser.add_argument("--native-cargo-home", type=Path, default=Path("/private/tmp/sift-pr29-cargo.giYPjY"))
    parser.add_argument("--defer-repo", type=Path, default=Path("/Users/chrischeng/faberlines/defer"))
    parser.add_argument("--legacy-evidence", type=Path, default=REPO/".local/cnl-parser-replacement")
    parser.add_argument("--preserved-source", type=Path, default=REPO/".local/defer-task-status/preserved-source.json")
    args = parser.parse_args()
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="som-service-replay-",dir="/private/tmp"))/"results"
    output.mkdir(parents=True,exist_ok=False)
    for name in ["cargo_home","native_cargo_home","defer_repo","legacy_evidence","preserved_source"]:
        setattr(args,name,getattr(args,name).resolve())
    print(output,flush=True)
    preserved=json.loads(args.preserved_source.read_text())
    def check_preserved():
        changed=[p for p,h in preserved.items() if not (REPO/p).is_file() or digest(REPO/p)!=h]
        assert not changed, ("Protected sources changed",changed)
        link=REPO/"som-code-python/data/blocks/current"
        assert link.is_symlink() and str(link.readlink())=="flow-v7-compact"
    check_preserved()
    env={k:v for k,v in os.environ.items() if k not in {"GH_TOKEN","GITHUB_TOKEN","GIT_ASKPASS","SSH_ASKPASS"} and not k.startswith("CARGO_REGISTRIES_")}
    env.update(CARGO_HOME=str(args.cargo_home),CARGO_TARGET_DIR=str(output/"service-target"),CARGO_BUILD_JOBS="2",RUST_TEST_THREADS="2",RUSTC=str(RUST/"rustc"),
               GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM="1",GIT_TERMINAL_PROMPT="0",CARGO_NET_GIT_FETCH_WITH_CLI="true",GIT_CONFIG_COUNT="1",GIT_CONFIG_KEY_0="credential.helper",GIT_CONFIG_VALUE_0="",
               SOM_CLIENT_TEST_ROOT=str(output),SOM_CLIENT_TEST_TARGET=str(output/"transport-target"))
    env["PATH"]=str(RUST)+os.pathsep+"/opt/homebrew/Cellar/node/26.5.0/bin"+os.pathsep+env.get("PATH","")
    commands=[]
    def run(name,command,environment=None):
        command=[str(x) for x in command]
        print(name,flush=True)
        with (output/(name+".log")).open("wb") as log:
            result=subprocess.run(command,cwd=REPO,env=environment or env,stdout=log,stderr=subprocess.STDOUT)
        commands.append({"name":name,"argv":command,"exit_code":result.returncode})
        (output/"commands.json").write_text(json.dumps(commands,indent=2)+"\n")
        assert result.returncode==0, f"{name} failed: {output/(name+'.log')}"
    cargo=RUST/"cargo"
    manifest=SERVICE/"Cargo.toml"
    run("service-fetch",[cargo,"fetch","--locked","--manifest-path",manifest])
    run("service-build",[cargo,"build","--locked","--offline","--manifest-path",manifest])
    binary=output/"service-target/debug/som-service"
    run("entrypoints",[sys.executable,SERVICE/"scripts/entrypoints.py","--binary",binary,"--output",output/"entrypoints"])
    generated=output/"entrypoints/generated"
    run("generated-fetch",[cargo,"fetch","--locked","--manifest-path",generated/"Cargo.toml"])
    run("service-tests",[cargo,"test","--locked","--offline","--all-targets","--manifest-path",manifest])
    run("native-client",[sys.executable,SERVICE/"acceptance/run.py","--generated-dir",generated,"--defer-repo",args.defer_repo,"--output",output/"native-client",
        "--cargo",cargo,"--native-cargo-home",args.native_cargo_home,"--client-cargo-home",args.cargo_home])
    metadata=json.loads(subprocess.check_output([str(cargo),"metadata","--locked","--offline","--format-version","1","--manifest-path",str(manifest)],env=env))
    dependency=next(p for p in metadata["packages"] if p["name"]=="faberline-nlp-cnl")
    assert dependency["source"].endswith("#"+NLP_REV)
    nlp_crate=Path(dependency["manifest_path"]).parent
    nlp_root=Path(subprocess.check_output(["git","-C",str(nlp_crate),"rev-parse","--show-toplevel"],text=True).strip())
    assert subprocess.check_output(["git","-C",str(nlp_root),"rev-parse","HEAD"],text=True).strip()==NLP_REV
    assert not subprocess.check_output(["git","-C",str(nlp_root),"status","--porcelain","--untracked-files=no"])
    identity=json.loads((output/"entrypoints/identity.json").read_text())
    for name,h in identity["manifest"]["files"].items():
        if name.startswith("nlp/"):
            assert digest(nlp_crate/name[4:])==h,name
    run("nlp-fetch",[cargo,"fetch","--locked","--manifest-path",nlp_root/"Cargo.toml"])
    run("nlp-tests",[cargo,"test","--workspace","--all-features","--locked","--offline","--manifest-path",nlp_root/"Cargo.toml"])
    oracle=SERVICE/"scripts/nlp-oracle/Cargo.toml"
    run("nlp-oracle-fetch",[cargo,"fetch","--locked","--manifest-path",oracle])
    run("nlp-legacy-oracle",[cargo,"run","--quiet","--locked","--offline","--manifest-path",oracle,"--",args.legacy_evidence/"corpus-final.json"])
    old=args.legacy_evidence
    run("legacy",[sys.executable,REPO/"som-rs/scripts/cnl_parity/accept.py","--baseline",old/"baseline","--candidate",REPO,
        "--corpus",old/"corpus-final.json","--original-report",old/"original-178.json","--baseline-report",old/"final-baseline.json",
        "--baseline-bundles",old/"baseline-evidence/target-baseline-http-cli","--ruff",old/"tools/ruff","--prettier",old/"tools/prettier","--output",output/"legacy"])
    legacy=output/"legacy"
    run("entrypoint-isolation",[sys.executable,SERVICE/"scripts/entrypoints.py","--binary",binary,"--output",output/"entrypoint-isolation",
        "--legacy-binary",legacy/"target/candidate/debug/som","--legacy-assets",legacy/"candidate/som-rs/compiler-assets/current"])
    check_preserved()
    legacy_result=json.loads((legacy/"acceptance.json").read_text())
    assert legacy_result["passed"] and legacy_result["comparison"]["expected_records"]==1930
    native_result=json.loads((output/"native-client/report.json").read_text())
    assert native_result["status"]=="passed" and native_result["generated_client_cases"]==105
    result={"status":"passed","nlp_revision":NLP_REV,"legacy_nlp_revision":"8ddb1f6df55eec0764ca50772b2c038441361727",
        "preserved_files":len(preserved),"legacy_records":1930,"generated_client_cases":105,"assets":identity["assets"],
        "entrypoints":"entrypoint-isolation/report.json","native_client":"native-client/report.json","legacy":"legacy/acceptance.json",
        "commands_sha256":digest(output/"commands.json")}
    (output/"receipt.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()

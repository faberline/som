"""Prepare an additive, read-only source/evidence binding. No service calls."""
import argparse, hashlib, json
from pathlib import Path
HERE=Path(__file__).resolve().parent
NAMES=('reference_span_prepass.py','public_products_corrected.py','qualify_prepass.py','qualify_runner_bindings.py','qualify_formatted_reference.py','prepare_amendment.py')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def entry(path):return {'path':str(path.resolve()),'sha256':sha(path)}
def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--candidate-dir',type=Path,required=True);p.add_argument('--original-public-run',type=Path,required=True);p.add_argument('--focused-summary',type=Path,required=True);p.add_argument('--formatter',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 root=a.repo.resolve();candidate=a.candidate_dir.resolve();old=json.loads((a.original_public_run/'result.json').read_text());summary=json.loads(a.focused_summary.read_text())
 failures={};proofs={}
 for i,row in enumerate(old['records']):
  if row['id'] in ('REF_BRANCH_EXPORT_SAME_TYPE','PUBLIC_v7-branch-null'):
   d=a.original_public_run/f'{i:03d}';failures[row['id']]={'result':entry(d/'result.json'),'raw_files':entry(d/'reference.raw-files.json'),'assemble_response':entry(d/'assemble.response.json')}
   c=next(x for x in summary['cases'] if x['id']==row['id']);outcome=c['focused_behavior']['outcome']
   dirs=[Path(outcome['evidence_directory']),Path(outcome['process_http_trace'])]
   paths=[]
   for directory in dirs:
    assert directory.resolve().is_relative_to(a.focused_summary.resolve().parent)
    paths.extend(x for x in directory.rglob('*') if x.is_file())
   proofs[row['id']]={'case_result':entry(a.focused_summary.parent/row['id']/'case-result.json'),'raw_files':[entry(x) for x in sorted(set(paths))]}
 deps={name:entry(root/name) for name in ('som-core/src/som_core/quality.py','som-rs/contracts/python-quality-v1/pyproject.toml','som-core/scripts/verify_python_quality.py','som-rs/tests/compiler-v1/cnl-positive-fixtures.json','som-rs/contracts/compiler-v1/examples/index.json')}
 deps['formatter']=entry(a.formatter)
 deps['candidate_quality']=entry(candidate/'assets/quality.json')
 deps['preserved_additive002_receipt']=entry(root/'.local/rust-compiler-goal/acceptance/public-reference-amendment-002/installed.json')
 deps['preserved_additive002_archive']=entry(root/'.local/rust-compiler-goal/acceptance/public-reference-amendment-002/additive-tools.tar')
 result={'version':'som.public-reference-qa-amendment.v1','production_changed':False,'original_qa57_changed':False,'status':'prepared; root review/formatter qualification/fresh 127 run still required','additive_files':{n:sha(HERE/n) for n in NAMES},'original_qa57_receipt':entry(candidate/'acceptance-tools.json'),'original_qa57_archive':entry(candidate/'acceptance-tools.tar'),'dependencies':deps,'original_public_run':entry(a.original_public_run/'result.json'),'original_failures':failures,'focused_summary':entry(a.focused_summary),'focused_raw_proofs':proofs,'candidate_source':entry(candidate/'source.json'),'candidate_build':entry(candidate/'build.json'),'candidate_runtime':entry(candidate/'runtime.json'),'candidate_binary':entry(candidate/'som'),'candidate_archive':entry(candidate/'source.tar'),'qualification_scope':'source-only checks separate; original formatter failure and corrected byte parity must be observed at execution','acceptance_policy':'new fresh 127 records; preserve original 125/127 non-pass; complete bytes plus unchanged strict quality plus bound existing live focused evidence; no normalized AST fallback pass'}
 a.out.parent.mkdir(parents=True,exist_ok=True)
 with a.out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
 print(json.dumps({'path':str(a.out),'sha256':sha(a.out)}))
if __name__=='__main__':main()

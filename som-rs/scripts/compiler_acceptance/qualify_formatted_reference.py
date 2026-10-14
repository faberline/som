"""Root-only formatter qualification on saved sources. No SOM service calls."""
import argparse, importlib.util, json, os
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('corrected_runner',HERE/'public_products_corrected.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
def main():
 p=argparse.ArgumentParser();p.add_argument('--amendment-manifest',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 original_hash=r.digest(a.amendment_manifest);manifest,old,focused=r.verify_amendment(a.amendment_manifest)
 out=r.require_new_output(a.out,r.OUTPUT_ROOT);out.mkdir(parents=True)
 r.ref.BINARY=Path(manifest['dependencies']['formatter']['path']);r.ref.PROFILE=r.ROOT/'som-rs/contracts/python-quality-v1/pyproject.toml'
 rows=[]
 for case_id in sorted(r.KNOWN_REFERENCE_FORMATTER_FAILURES):
  d=out/case_id;d.mkdir();binding=manifest['original_failures'][case_id]
  raw=json.loads(r.bound_path(binding['raw_files']).read_text());original=next(x for x in old['records'] if x['id']==case_id)
  row={'id':case_id,'passed':False,'original_reference_check_passed':False,'complete_file_parity':None,'complete_normalized_ast_equal':False}
  try:
   try:
    r.ref.quality.finalize(raw,r.ref.BINARY,r.ref.PROFILE,byte_limit=None)
    raise ValueError('original formatter failure unexpectedly disappeared')
   except r.ref.v1.V1Error as error:
    row['reference_formatter_status']='failed';row['reference_formatter_error']={'code':error.code,'detail':str(error)}
    r.require(row['reference_formatter_error']==original['reference_formatter_error'],'original formatter failure did not reproduce exactly')
   corrected,proof=r.span.prepass(raw);proof['original_diagnostic_binding']=r.span.match_original_failure(row['reference_formatter_error']['detail'],raw,proof)
   r.write(d/'corrected-raw-files.json',corrected);r.write(d/'exact-span-proof.json',proof)
   expected=r.ref.quality.finalize(corrected,r.ref.BINARY,r.ref.PROFILE,byte_limit=None)
   r.write(d/'corrected-formatted-files.json',expected)
   response=json.loads(r.bound_path(binding['assemble_response']).read_text());r.require(response['http_status']==200 and response['body']['status']=='ok','saved candidate assembly response invalid')
   files=response['body']['files'];row['corrected_reference_full_file_parity']=expected==files
   r.require(row['corrected_reference_full_file_parity'],'complete corrected formatted bytes differ from saved candidate')
   row['focused_behavior_binding']=r.focused_case_binding(case_id,files,focused,manifest)
   row['strict_quality_scope']='bound run003 strict checker on the exact same full product; fresh full127 checker remains required'
   row['strict_quality_passed']=row['focused_behavior_binding']['strict_quality_passed'];row['passed']=True
  except Exception as error:row['error']=type(error).__name__+': '+str(error)
  rows.append(row);r.write(d/'result.json',row)
 r.require(r.digest(a.amendment_manifest)==original_hash,'amendment manifest changed');r.verify_amendment(a.amendment_manifest);r.require(r.digest(a.amendment_manifest)==original_hash,'amendment manifest changed')
 report={'version':'som.public-reference-formatter-qualification.v1','complete':len(rows)==2 and all(x['passed'] for x in rows),'records':rows,'original_formatter_failure_count':sum(x.get('reference_formatter_status')=='failed' for x in rows),'corrected_reference_count':sum(x.get('corrected_reference_full_file_parity') is True for x in rows),'amendment_manifest_sha256':original_hash,'scope':'saved-source formatter qualification; no SOM API call; not fresh127 acceptance','formatter_sha256':manifest['dependencies']['formatter']['sha256']}
 r.write(out/'result.json',report);print(json.dumps({'complete':report['complete'],'out':str(out)}));return 0 if report['complete'] else 2
if __name__=='__main__':raise SystemExit(main())

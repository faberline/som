"""Pure Python 3.12 qualification. No formatter, network, service or build."""
import argparse, ast, copy, hashlib, importlib.util, json, math, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('span_helper',HERE/'reference_span_prepass.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
checks=[]
def check(name,fn):
 try:fn();checks.append({'name':name,'passed':True})
 except Exception as e:checks.append({'name':name,'passed':False,'error':type(e).__name__+': '+str(e)})
def refused(fn):
 try:fn()
 except h.Refusal:return
 raise AssertionError('unsafe proof accepted')
def accepted(source):
 result,proof=h.rewrite_source(source)
 assert len(proof['edits'])==1
 assert proof['whole_ast_equal'] and proof['outside_bytes_equal']
 edit=proof['edits'][0];a,b=edit['start_byte'],edit['end_byte']
 original=source.encode();new=result.encode();replacement=edit['replacement'].encode()
 assert original[:a]==new[:a] and original[b:]==new[a+len(replacement):]
 return result,proof
source='"doc"\nimport math\ndef run(flag, left, right):\n    if flag():\n        value = left()\n    else:\n        value = right()\n    return value\nother = 9  # type: ignore[assignment]\n'
check('outside bytes and later TypeIgnore',lambda:accepted(source))
def lazy():
 rewritten,_=accepted(source)
 for decision in (False,True):
  for text in (source,rewritten):
   events=[];env={};exec(text,env)
   def predicate():events.append('test');return decision
   def left():events.append('left');assert decision;return 11
   def right():events.append('right');assert not decision;return 27
   assert env['run'](predicate,left,right)==(11 if decision else 27)
   assert events==['test','left' if decision else 'right']
check('test first and only selected arm evaluated',lazy)
for expression in ('True','False','1','10000000000000000000000000000000000000000000000000000001','-0.0','1.25','None',"'中文 # literal'",'[1, True, None]','{"a": -0.0}'):
 def typed(expression=expression):
  text=f'if flag:\n    value = {expression}\nelse:\n    value = sentinel\n'
  corrected,_=accepted(text)
  for flag in (False,True):
   sentinel=object();a={'flag':flag,'sentinel':sentinel};b=dict(a);exec(text,a);exec(corrected,b)
   assert type(a['value']) is type(b['value']) and a['value']==b['value']
   if isinstance(a['value'],float):assert math.copysign(1,a['value'])==math.copysign(1,b['value'])
   if not flag:assert a['value'] is b['value'] is sentinel
 check('exact runtime value/type '+expression,typed)
check('UTF8 byte spans Unicode names and literals',lambda:accepted('前綴 = "中文"\nif 前綴 == "中文":\n    結果 = "甲"\nelse:\n    結果 = "乙"\n後綴 = "尾"\n'))
check('multiline expression',lambda:accepted('if (flag\n    and other):\n    value = (\n        one + two\n    )\nelse:\n    value = (three\n             + four)\n'))
check('CRLF outside bytes',lambda:accepted(source.replace('\n','\r\n')))
for name,body,test,other in [('lambda arm','lambda: 1','flag','lambda: 2'),('nested conditional','1 if inner else 2','flag','3 if inner else 4'),('Boolean test/arms','left or fallback','flag and inner','right and fallback'),('walrus test','selected','(value := flag)','fallback'),('comparison test','left','low < value < high','right')]:
 def precedence(body=body,test=test,other=other):
  text=f'if {test}:\n    result = {body}\nelse:\n    result = {other}\n'
  corrected,_=accepted(text)
  # The whole-AST proof has only the admitted If replacement. Compare actual
  # values for both decisions too. Lambdas are invoked after selection.
  for flag in (False,True):
   env={'flag':flag,'inner':True,'left':11,'right':27,'fallback':9,'selected':7,'low':0,'high':2,'value':1 if flag else 3}
   a=dict(env);b=dict(env);exec(text,a);exec(corrected,b)
   x,y=a['result'],b['result']
   if callable(x):x,y=x(),y()
   assert type(x) is type(y) and x==y
 check('precedence-safe IfExp '+name,precedence)

unsafe=[('inline comment','if flag: # comment\n    value = 1\nelse:\n    value = 2\n'),('end comment','if flag:\n    value = 1\nelse:\n    value = 2 # noqa\n'),('type comment','if flag:\n    value = 1 # type: int\nelse:\n    value = 2\n'),('elif','if first:\n    pass\nelif flag:\n    value = 1\nelse:\n    value = 2\n'),('explicit continuation','if flag and \\\n    other:\n    value = 1\nelse:\n    value = 2\n')]
for name,text in unsafe:
 check('rewrite refuses '+name,lambda text=text:refused(lambda:h.rewrite_source(text)))
 # A direct caller cannot bypass the physical-line rule in prove().
 def bypass(text=text):
  data=text.encode();node=next(n for n in ast.walk(ast.parse(text,type_comments=True)) if h.shape(n));start,end=h.coordinates(data,node)
  edit={'raw_ast_span':list(h.location(node)),'start_byte':start,'end_byte':end,'original_span_sha256':h.sha(data[start:end]),'replacement':'value = 1','replacement_sha256':h.sha(b'value = 1')}
  refused(lambda:h.prove(text,text,[edit]))
 check('direct proof refuses '+name,bypass)
for name,text in [('tuple','if flag:\n    a,b = left\nelse:\n    a,b = right\n'),('attribute','if flag:\n    obj.value = 1\nelse:\n    obj.value = 2\n'),('two targets','if flag:\n    a=b=1\nelse:\n    a=b=2\n'),('different targets','if flag:\n    a=1\nelse:\n    b=2\n'),('two statements','if flag:\n    value=1\n    extra()\nelse:\n    value=2\n'),('nested unsupported','if flag:\n    if inner:\n        value=1\nelse:\n    value=2\n')]:
 def unsupported(text=text):
  corrected,p=h.rewrite_source(text);assert corrected==text and p['edits']==[]
 check('unsupported shape unchanged '+name,unsupported)
rewritten,proof=accepted(source)
for name,old,new in [('target','value = (','changed = ('),('test','if flag() else','if not flag() else'),('body','left() if','right() if'),('else','else right()','else left()'),('import','import math','import json'),('docstring','"doc"','"changed"'),('outside constant','other = 9','other = 10'),('outside comment','# type: ignore[assignment]','# removed')]:
 def mutated(old=old,new=new):
  text=rewritten.replace(old,new,1);assert text!=rewritten
  refused(lambda:h.prove(source,text,proof['edits']))
 check('proof refuses mutation '+name,mutated)
def inside_mutation(old,new):
 text,p=accepted('if flag:\n    value = '+old+'\nelse:\n    value = None\n')
 e=copy.deepcopy(p['edits']);e[0]['replacement']=e[0]['replacement'].replace(old,new,1);e[0]['replacement_sha256']=h.sha(e[0]['replacement'].encode())
 data=('if flag:\n    value = '+old+'\nelse:\n    value = None\n').encode();x=e[0];changed=(data[:x['start_byte']]+x['replacement'].encode()+data[x['end_byte']:]).decode()
 refused(lambda:h.prove(data.decode(),changed,e))
check('AST refuses bool to int even supplied hash',lambda:inside_mutation('True','1'))
check('AST refuses signed zero even supplied hash',lambda:inside_mutation('-0.0','0.0'))
def additional_if():
 text=source+'if extra:\n    x=1\nelse:\n    x=2\n';corrected,p=h.rewrite_source(text)
 refused(lambda:h.prove(text,corrected,p['edits'][:1]))
check('proof refuses extra unrecorded If rewrite',additional_if)
def diagnostics():
 files={'a.py':'if flag:\n    value=1\nelse:\n    value=2\n'};_,p=h.prepass(files)
 record={'code':'SIM108','severity':'error','filename':'/root/a.py','location':{'row':244,'column':5},'end_location':{'row':247,'column':37}}
 matched=h.match_original_failure('a.py: '+json.dumps([record]),files,p);assert matched['raw_ast_span'][0]==1 and matched['diagnostic']['location']['row']==244
 for mutation in ({'code':'OTHER'},{'filename':'/root/b.py'},{'location':{'row':True,'column':5}}):
  changed={**record,**mutation};refused(lambda:h.match_original_failure('a.py: '+json.dumps([changed]),files,p))
 refused(lambda:h.match_original_failure('a.py: '+json.dumps([record,record]),files,p))
check('formatted diagnostic is not raw selection; wrong rule/file/coordinate refused',diagnostics)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--saved-run',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 assert sys.version_info[:2]==(3,12),sys.version
 a.out.mkdir(parents=True,exist_ok=False)
 summary=json.loads((a.saved_run/'result.json').read_text());inventory=[]
 for i,row in enumerate(summary['records']):
  path=a.saved_run/f'{i:03d}'/'reference.raw-files.json';files=json.loads(path.read_text());corrected,p=h.prepass(files)
  item={'index':i,'id':row['id'],'raw_map_path':str(path.resolve()),'raw_map_sha256':h.sha(path.read_bytes()),'edits':p['edit_count'],'changed_files':[n for n in files if files[n]!=corrected[n]]}
  if p['edit_count']:
   item['proof']=p;item['diagnostic_binding']=h.match_original_failure(row['reference_formatter_error']['detail'],files,p)
   assert row['reference_formatter_status']=='failed' and row['complete_file_parity'] is None and row['complete_normalized_ast_equal'] is False
   (a.out/f'{i:03d}.corrected-raw-files.json').write_text(json.dumps(corrected,ensure_ascii=False,indent=2)+'\n')
  inventory.append(item)
 assert len(inventory)==127
 assert [(x['index'],x['id'],x['edits']) for x in inventory if x['edits']]==[(118,'REF_BRANCH_EXPORT_SAME_TYPE',1),(126,'PUBLIC_v7-branch-null',1)]
 checks.append({'name':'all 127 raw-map inventory; exactly 118/126 changed','passed':True})
 (a.out/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
 report={'python':sys.version,'python_executable':sys.executable,'python_executable_sha256':h.sha(Path(sys.executable).read_bytes()),'helper_sha256':h.sha((HERE/'reference_span_prepass.py').read_bytes()),'test_source_sha256':h.sha(Path(__file__).read_bytes()),'saved_run_summary_sha256':h.sha((a.saved_run/'result.json').read_bytes()),'passed':all(c['passed'] for c in checks),'checks':checks,'count':len(checks),'scope':'pure Python; no formatter/service/build/socket call; formatter success and fresh 127 acceptance UNRUN'}
 (a.out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'passed':report['passed'],'count':len(checks),'failed':[c for c in checks if not c['passed']]}));return 0 if report['passed'] else 2
if __name__=='__main__':raise SystemExit(main())

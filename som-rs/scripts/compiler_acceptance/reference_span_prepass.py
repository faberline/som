"""Independent raw-source prepass; no candidate, Ruff coordinate or subprocess.

Only single-Name assignment If/else nodes are changed. Every outside byte is
retained. A whole-module AST proof admits only those exact recorded spans.
"""
from __future__ import annotations
import ast
import copy
import hashlib
import io
import json
import tokenize

VERSION='som.public-reference-span-prepass.v2'

class Refusal(ValueError):
    pass

def require(ok,message):
    if not ok:raise Refusal(message)

def sha(data):return hashlib.sha256(data).hexdigest()

def location(node):return (node.lineno,node.col_offset,node.end_lineno,node.end_col_offset)

def coordinates(data,node):
    rows=data.splitlines(keepends=True)
    offsets=[0]
    for row in rows:offsets.append(offsets[-1]+len(row))
    def at(line,column):
        require(type(line) is int and type(column) is int and 1<=line<=len(rows) and 0<=column<=len(rows[line-1].rstrip(b'\r\n')),'invalid raw AST byte coordinate')
        rows[line-1][:column].decode('utf-8')
        return offsets[line-1]+column
    return at(node.lineno,node.col_offset),at(node.end_lineno,node.end_col_offset)

def shape(node):
    if not isinstance(node,ast.If) or len(node.body)!=1 or len(node.orelse)!=1:return False
    a,b=node.body[0],node.orelse[0]
    return (isinstance(a,ast.Assign) and isinstance(b,ast.Assign)
            and len(a.targets)==len(b.targets)==1
            and isinstance(a.targets[0],ast.Name) and isinstance(b.targets[0],ast.Name)
            and a.targets[0].id==b.targets[0].id)

def expected_node(node):
    a,b=node.body[0],node.orelse[0]
    require(shape(node) and a.type_comment is None and b.type_comment is None,'unsupported assignment shape or type comment')
    return ast.Assign(targets=copy.deepcopy(a.targets),value=ast.IfExp(test=copy.deepcopy(node.test),body=copy.deepcopy(a.value),orelse=copy.deepcopy(b.value)),type_comment=None)

def safe_span(original,data,node):
    start,end=coordinates(data,node);span=data[start:end]
    require(span.startswith(b'if ') or span.startswith(b'if\t') or span.startswith(b'if('),'elif or unsupported statement token')
    comments=[t for t in tokenize.generate_tokens(io.StringIO(original).readline) if t.type==tokenize.COMMENT]
    require(not any(node.lineno<=t.start[0]<=node.end_lineno for t in comments),'comments on rewritten physical lines are refused')
    require(b'\\\n' not in span and b'\\\r\n' not in span,'explicit continuation is refused')
    expected_node(node)
    return start,end

def prove(original,rewritten,edits):
    """Prove exact bytes and one-sided whole AST equality for recorded edits."""
    before=original.encode('utf-8');after=rewritten.encode('utf-8')
    source=ast.parse(original,type_comments=True)
    admitted={}
    all_ifs={location(n):n for n in ast.walk(source) if isinstance(n,ast.If)}
    ordered=sorted(edits,key=lambda e:e['start_byte'])
    previous=-1
    for edit in ordered:
        key=tuple(edit['raw_ast_span']);node=all_ifs.get(key)
        require(node is not None and key not in admitted,'recorded raw If is missing or repeated')
        require(shape(node),'recorded node is not a single-Name assignment If')
        start,end=safe_span(original,before,node)
        require((start,end)==(edit['start_byte'],edit['end_byte']) and start>=previous,'span mismatch or overlapping edits')
        require(sha(before[start:end])==edit['original_span_sha256'],'original span hash differs')
        require(before[start:end].startswith(b'if ' ) or before[start:end].startswith(b'if\t') or before[start:end].startswith(b'if('),'elif or unsupported raw statement token')
        admitted[key]=expected_node(node);previous=end
    expected_bytes=before
    for edit in reversed(ordered):
        replacement=edit['replacement'].encode('utf-8')
        require(sha(replacement)==edit['replacement_sha256'],'replacement span hash differs')
        require(before[edit['start_byte']:edit['end_byte']].count(b'\n')==replacement.count(b'\n'),'rewrite moved later source lines')
        expected_bytes=expected_bytes[:edit['start_byte']]+replacement+expected_bytes[edit['end_byte']:]
    require(after==expected_bytes,'unrecorded change to source bytes')
    class Exact(ast.NodeTransformer):
        def visit_If(self,node):
            key=location(node)
            if key in admitted:return copy.deepcopy(admitted[key])
            return self.generic_visit(node)
    expected=Exact().visit(copy.deepcopy(source))
    actual=ast.parse(rewritten,type_comments=True)
    require(ast.dump(expected,include_attributes=False)==ast.dump(actual,include_attributes=False),'whole-module AST differs outside the exact admitted expressions')
    return {'raw_sha256':sha(before),'corrected_sha256':sha(after),'whole_ast_sha256':sha(ast.dump(actual,include_attributes=False).encode()),'whole_ast_equal':True,'outside_bytes_equal':True,'type_comments_parsed':True}

def rewrite_source(source):
    require(isinstance(source,str),'source must be text')
    require('\r' not in source.replace('\r\n',''),'unsupported bare-CR source')
    data=source.encode('utf-8');tree=ast.parse(source,type_comments=True)
    edits=[]
    for node in ast.walk(tree):
        if not shape(node):continue
        start,end=safe_span(source,data,node);span=data[start:end]
        a,b=node.body[0],node.orelse[0]
        # Unparse only the expected IfExp subtree. Never unparse the module.
        # This retains Python precedence without redundant per-arm parentheses.
        expression_text=ast.unparse(expected_node(node).value)
        replacement=f'{a.targets[0].id} = ({expression_text})'
        missing=span.count(b'\n')-replacement.encode().count(b'\n')
        require(missing>=0,'replacement needs additional physical lines')
        # Keep later TypeIgnore line fields fixed. Padding stays inside the
        # expression group so the unchanged formatter can collapse it normally.
        replacement=replacement[:-1]+('\r\n' if b'\r\n' in span else '\n')*missing+')'
        edits.append({'raw_ast_span':list(location(node)),'start_byte':start,'end_byte':end,'target':a.targets[0].id,'original_span_sha256':sha(span),'replacement':replacement,'replacement_sha256':sha(replacement.encode())})
    edits.sort(key=lambda e:e['start_byte'])
    corrected=data
    for edit in reversed(edits):corrected=corrected[:edit['start_byte']]+edit['replacement'].encode()+corrected[edit['end_byte']:]
    text=corrected.decode('utf-8');proof=prove(source,text,edits)
    return text,{**proof,'edits':edits}

def prepass(files):
    require(isinstance(files,dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in files.items()),'full reference file map required')
    corrected=dict(files);proof={}
    for name,source in files.items():
        if name.endswith('.py'):
            corrected[name],proof[name]=rewrite_source(source)
        else:proof[name]={'raw_sha256':sha(source.encode()),'corrected_sha256':sha(source.encode()),'outside_bytes_equal':True,'edits':[]}
    return corrected,{'version':VERSION,'candidate_input':False,'ruff_location_used':False,'files':proof,'edit_count':sum(len(p['edits']) for p in proof.values())}

def match_original_failure(detail,files,proof):
    """Confirm the failed rule/file, without mapping formatted rows into raw."""
    name,separator,diagnostics=detail.partition(': ')
    require(separator and name in files and name.endswith('.py'),'diagnostic file is outside the raw reference map')
    records=json.loads(diagnostics)
    require(isinstance(records,list) and len(records)==1,'exactly one original diagnostic is required')
    record=records[0]
    require(isinstance(record,dict),'diagnostic must be an object')
    require(record.get('code')=='SIM108' and record.get('severity')=='error','original formatter failure is not the single SIM108 rule')
    require(isinstance(record.get('filename'),str) and record['filename'].endswith('/'+name),'diagnostic filename differs from failed raw file')
    for point in ('location','end_location'):
        require(isinstance(record.get(point),dict) and all(type(record[point].get(k)) is int and record[point][k]>0 for k in ('row','column')),'invalid formatted diagnostic coordinate')
    require(proof['edit_count']==1 and len(proof['files'][name]['edits'])==1,'raw prepass must have exactly one admitted span in the diagnosed file')
    return {'file':name,'diagnostic':record,'diagnostic_coordinate_space':'original formatter output; never raw source','raw_ast_span':proof['files'][name]['edits'][0]['raw_ast_span'],'raw_selection_independent_of_diagnostic_location':True}

"""Independent source reconstruction for retained Linux API inputs.

The source TAR and runtime inventory are fixed qualified input bytes. This
module does not import or execute the producer or any installed package.
"""
import base64
import configparser
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

KEY='4669d1f312255b4d628224faad1326423741eac16521c3d59677eb994cd2f90b'
TAR_SHA='8a1ead27a72911acb9af76c622311c5924ad91073b3fc9a221a44db6a0dd4e3b'
CLOSURE_SHA='c6669bc6820eea75d1f0cc596ed4d41a62936a1854026ecf20bee9510d2eb200'
RUNTIME_INVENTORY_SHA='ac9df6b242d0a5016dd32317af27475e64d652dd38ccc8663743b0537f9cd63c'
INSTALLER_SHA='e166303c1376a77d6e92ae19d9ff0eddcdd24c3adbde8381ff835274bb850358'
BASE='som-react-python-closure/closures/'+KEY
SITE='lib/python3.12/site-packages'

class InputAuditError(ValueError): pass

def need(ok, message):
    if not ok: raise InputAuditError(message)

def sha(raw): return hashlib.sha256(raw).hexdigest()

def strict_json(raw):
    def pairs(rows):
        result={}
        for k,v in rows:
            need(k not in result,'duplicate JSON member')
            result[k]=v
        return result
    try:
        return json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(InputAuditError('nonfinite JSON')))
    except (ValueError,UnicodeError) as exc: raise InputAuditError('invalid JSON') from exc

def relative(value):
    need(isinstance(value,str) and value and not value.startswith('/') and '\\' not in value
         and all(x not in ('','.','..') for x in value.split('/'))
         and not any(ord(c)<32 or ord(c)==127 for c in value), 'unsafe inventory path')
    return value

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def equal(a,b):
    if isinstance(a,dict) and isinstance(b,dict): return set(a)==set(b) and all(equal(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list): return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    return type(a) is type(b) and a==b

def snapshot_parts(snapshot):
    need(isinstance(snapshot,dict) and set(snapshot)=={'version','root_mode','entries'}
         and snapshot['version']=='som.react-linux-api-tree-snapshot.v1'
         and type(snapshot['root_mode']) is int and isinstance(snapshot['entries'],dict),'tree snapshot missing')
    files,dirs={},set()
    for path,row in snapshot['entries'].items():
        relative(path)
        need(isinstance(row,dict) and type(row.get('mode')) is int and 0<=row['mode']<=0o7777,'snapshot mode differs')
        kind=row.get('type')
        if kind=='directory':
            need(set(row)=={'type','mode'},'directory snapshot differs')
            dirs.add(path)
        elif kind=='file':
            need(set(row)=={'type','mode','size','sha256'} and type(row['size']) is int and row['size']>=0
                 and isinstance(row['sha256'],str) and re.fullmatch('[0-9a-f]{64}',row['sha256']), 'file snapshot differs')
            files[path]=row
        elif kind=='symlink':
            need(set(row)=={'type','mode','target'} and isinstance(row['target'],str) and row['target'], 'link snapshot differs')
            files[path]=row
        else: raise InputAuditError('special snapshot entry refused')
    return files,dirs

def runtime_check(snapshot, inventory_raw):
    need(sha(inventory_raw)==RUNTIME_INVENTORY_SHA,'qualified runtime inventory differs')
    original=strict_json(inventory_raw)
    need(isinstance(original,dict),'runtime inventory invalid')
    observed,_=snapshot_parts(snapshot)
    expected={}
    for name,row in original.items():
        need(name.startswith('install/') and isinstance(row,dict),'qualified runtime path invalid')
        name=relative(name[len('install/'):])
        need(name not in expected,'duplicate runtime path')
        expected[name]=row
    need(set(observed)==set(expected),'runtime observed inventory scope differs')
    for name,qualified in expected.items():
        actual=observed[name]
        if qualified.get('kind')=='regular_file':
            need(actual['type']=='file' and actual['sha256']==qualified.get('sha256')
                 and type(qualified.get('executable_bit')) is bool
                 and bool(actual['mode'] & 0o111)==qualified['executable_bit'],'runtime file identity differs')
        elif qualified.get('kind')=='alias_not_admitted':
            need(actual['type']=='symlink' and actual['target']==qualified.get('target')
                 and sha(actual['target'].encode())==qualified.get('sha256')
                 and qualified.get('qualification_probe_executed') is False
                 and qualified.get('runtime_admitted') is False, 'runtime link differs')
        else: raise InputAuditError('qualified runtime type differs')
    return len(expected)

def installer_assets(raw):
    need(sha(raw)==INSTALLER_SHA,'uv source authority differs')
    authority=strict_json(raw)
    need(authority.get('version')=='som.uv099-installer-source-authority.v1' and authority.get('uv')=='0.9.9',
         'uv source version differs')
    names={'activate','activate.csh','activate.fish','activate.nu','activate.ps1','activate.bat',
           'deactivate.bat','pydoc.bat','activate_this.py','_virtualenv.py'}
    assets=authority.get('assets')
    need(isinstance(assets,dict) and set(assets)==names,'uv source asset inventory differs')
    result={}
    for name,row in assets.items():
        text=row.get('text')
        need(isinstance(text,str) and sha(text.encode())==row.get('sha256'),'uv source asset hash differs')
        raw=text.encode()
        result[name]=raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n') if name.endswith('.bat') else raw
    return result

def shebang(path):
    text=str(path)
    if len(os.fsencode(text))+3>127 or ' ' in text:
        return "#!/bin/sh\n'''exec' '"+text.replace("'", "'\"'\"'")+"' \"$0\" \"$@\"\n' '''"
    return '#!'+text

def entrypoints(raw):
    parser=configparser.RawConfigParser(interpolation=None,strict=True,delimiters=('=',))
    parser.optionxform=str
    parser.read_string(raw.decode('utf-8','strict'))
    need(not parser.defaults(),'entrypoint defaults refused')
    result=[]
    for section in ('console_scripts','gui_scripts'):
        if not parser.has_section(section): continue
        for name,value in parser.items(section,raw=True):
            need(re.fullmatch(r'[A-Za-z0-9_.-]+',name) and name not in {'python','pythonw','python3'}
                 and not re.fullmatch(r'python3\.[0-9]+',name),'unsafe entrypoint')
            match=re.fullmatch(r'([\w\d_\-.]+)\s*:\s*([\w\d_\-.]+)(?:\s*\[\s*[^\]]+])?\s*',value)
            need(match is not None,'entrypoint format differs')
            if name.startswith('pip3.') and name[5:].isdigit(): continue
            result.append((name,match[1],match[2]))
            if name=='pip3': result.append(('pip3.12',match[1],match[2]))
    need(len({x[0] for x in result})==len(result),'duplicate entrypoint')
    return result

def script_bytes(venv,module,function):
    return (shebang(venv/'bin/python')+'\n# -*- coding: utf-8 -*-\nimport sys\nfrom '+module+' import '+function.split('.',1)[0]
            +'\nif __name__ == "__main__":\n    if sys.argv[0].endswith("-script.pyw"):\n        sys.argv[0] = sys.argv[0][:-11]\n'
            +'    elif sys.argv[0].endswith(".exe"):\n        sys.argv[0] = sys.argv[0][:-4]\n    sys.exit('+function+'())\n').encode()

def record_digest(raw): return 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()

def tar_data(tar, index, name, limit=180*1024*1024):
    relative(name)
    need(name in index,'qualified TAR member missing')
    member=index[name]
    if member.islnk():
        relative(member.linkname)
        need(member.linkname in index,'qualified TAR hardlink target missing')
        member=index[member.linkname]
    need(member.isfile() and 0<=member.size<=limit,'qualified TAR member type differs')
    stream=tar.extractfile(member)
    need(stream is not None,'qualified TAR member unavailable')
    raw=stream.read(limit+1)
    need(len(raw)==member.size,'qualified TAR member truncated')
    return raw

def derive(tar_path, closure_raw, inventories, installer_raw, venv, base_python):
    tar_path=Path(tar_path)
    need(tar_path.is_absolute() and tar_path.is_file() and not tar_path.is_symlink()
         and not any(p.is_symlink() for p in tar_path.parents) and tar_path.stat().st_size<=2*1024**3,
         'qualified TAR path differs')
    digest=hashlib.sha256()
    with tar_path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    need(digest.hexdigest()==TAR_SHA and sha(closure_raw)==CLOSURE_SHA,'qualified TAR or closure digest differs')
    closure=strict_json(closure_raw)
    rows=closure.get('wheels')
    need(isinstance(rows,list) and len(rows)==40 and all(isinstance(x,dict) for x in rows), 'wheel rows missing')
    names=[r.get('name') for r in rows]
    need(all(isinstance(x,str) and re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',x) for x in names)
         and len(set(names))==40 and set(inventories)==set(names),'wheel names differ')
    assets=installer_assets(installer_raw)
    venv,base_python=Path(venv),Path(base_python)
    expected,dirs={},set()
    def put(name,raw):
        relative(name)
        row={'type':'file','sha256':sha(raw),'size':len(raw)}
        need(name not in expected or expected[name]==row,'installed file source collision')
        expected[name]=row
        for parent in PurePosixPath(name).parents:
            if str(parent)!='.': dirs.add(str(parent))
    def link(name,target):
        need(name not in expected,'installed link source collision')
        expected[name]={'type':'symlink','target':target}
        for parent in PurePosixPath(name).parents:
            if str(parent)!='.': dirs.add(str(parent))
    put('CACHEDIR.TAG',b'Signature: 8a477f597d28d172789f06886806bc55')
    put('.gitignore',b'*')
    put('.lock',b'')
    prompt='som-react-api-qa'
    put('pyvenv.cfg',f'home = {base_python.parent}\nimplementation = CPython\nuv = 0.9.9\nversion_info = 3.12.11\ninclude-system-site-packages = false\nprompt = {prompt}\n'.encode())
    link('bin/python',str(base_python)); link('bin/python3','python'); link('bin/python3.12','python'); link('lib64','lib')
    for name,raw in assets.items():
        if name=='_virtualenv.py': put(SITE+'/_virtualenv.py',raw)
        else:
            text=raw.decode().replace('{{ VIRTUAL_ENV_DIR }}',str(venv).replace("'", "'\"'\"'"))
            for before,after in [('{{ BIN_NAME }}','bin'),('{{ VIRTUAL_PROMPT }}',prompt),('{{ PATH_SEP }}',':'),
                                 ('{{ RELATIVE_SITE_PACKAGES }}','../'+SITE)]: text=text.replace(before,after)
            put('bin/'+name,text.encode())
    put(SITE+'/_virtualenv.pth',b'import _virtualenv')
    with tarfile.open(tar_path,'r:') as tar:
        index={}
        for member in tar.getmembers():
            name=member.name.rstrip('/')
            relative(name)
            need(name not in index,'duplicate TAR entry')
            index[name]=member
        need(tar_data(tar,index,BASE+'/receipt.json')==closure_raw,'closure TAR receipt differs')
        for row in rows:
            name,filename=row['name'],row.get('filename')
            need(isinstance(filename,str) and Path(filename).name==filename,'wheel filename invalid')
            raw_inv=inventories[name]
            need(sha(raw_inv)==row.get('audit_sha256')
                 and tar_data(tar,index,BASE+'/wheel-audits/'+name+'/inventory.json')==raw_inv,'wheel audit binding differs')
            inv=strict_json(raw_inv)
            raw_wheel=tar_data(tar,index,BASE+'/wheels/'+filename)
            need(sha(raw_wheel)==row.get('sha256') and inv.get('filename')==filename and inv.get('name')==name
                 and inv.get('sha256')==row['sha256'],'wheel bytes differ')
            prefix,data=inv['dist_info_dir'],inv['data_dir']
            relative(prefix); relative(data)
            source_record=prefix+'/RECORD'
            rec={entry[0]:list(entry) for entry in inv['record_entries']}
            need(len(rec)==len(inv['record_entries']),'duplicate RECORD entry')
            with zipfile.ZipFile(io.BytesIO(raw_wheel)) as wheel:
                need(len(set(wheel.namelist()))==len(wheel.namelist()),'duplicate wheel member')
                entry_path=prefix+'/entry_points.txt'
                scripts=entrypoints(wheel.read(entry_path)) if entry_path in wheel.namelist() else []
                script_names={x[0] for x in scripts}
                generated=[]
                for script,module,function in scripts:
                    target,raw='bin/'+script,script_bytes(venv,module,function)
                    put(target,raw)
                    generated.append([os.path.relpath(target,SITE),record_digest(raw),str(len(raw))])
                for member in inv['members']:
                    path=relative(member['path'])
                    if member['record_binding']=='self':
                        need(path==source_record,'RECORD self binding differs'); continue
                    raw=wheel.read(path)
                    need(sha(raw)==member['sha256'] and len(raw)==member['size'],'wheel member differs')
                    target,changed=SITE+'/'+path,False
                    if path.startswith(data+'/'):
                        kind,sep,tail=path[len(data)+1:].partition('/')
                        need(sep,'wheel data layout differs'); relative(tail)
                        if kind in {'purelib','platlib'}: target=SITE+'/'+tail
                        elif kind=='data': target=tail
                        elif kind=='headers': target='include/site/python3.12/'+name+'/'+tail
                        elif kind=='scripts':
                            need('/' not in tail,'nested wheel scripts refused')
                            script_name=re.sub(r'(?:\.exe|-script\.py|\.pya)$','',tail)
                            if script_name in script_names: continue
                            target='bin/'+tail
                            if raw.startswith(b'#!python'):
                                offset=8
                                while offset<len(raw) and raw[offset] not in (10,13): offset+=1
                                raw=(shebang(venv/'bin/python')+'\n').encode()+raw[min(offset+1,len(raw)):]
                                changed=True
                        else: raise InputAuditError('unknown wheel data placement')
                        need(path in rec,'relocated member missing from RECORD')
                        rec[path][0]=os.path.relpath(target,SITE)
                        if changed: rec[path][1:]=[record_digest(raw),str(len(raw))]
                    need(target not in {'bin/python','bin/python3','bin/python3.12','lib64'},'wheel overwrites venv link')
                    put(target,raw)
                for directory in inv.get('directories',[]):
                    relative(directory)
                    if directory==data: continue
                    if directory.startswith(data+'/'):
                        kind,_sep,tail=directory[len(data)+1:].partition('/')
                        if kind in {'purelib','platlib'}: dest=SITE+('/'+tail if tail else '')
                        elif kind=='data': dest=tail
                        elif kind=='headers': dest='include/site/python3.12/'+name+('/'+tail if tail else '')
                        elif kind=='scripts':
                            need(not tail,'nested scripts directory refused'); dest='bin'
                        else: raise InputAuditError('unknown wheel directory placement')
                    else: dest=SITE+'/'+directory
                    if dest:
                        dirs.add(dest)
                        for parent in PurePosixPath(dest).parents:
                            if str(parent)!='.': dirs.add(str(parent))
                output=io.StringIO(newline='')
                csv.writer(output,lineterminator='\n').writerows(sorted(list(rec.values())+generated,key=lambda x:(x[0],x[1],x[2])))
                put(SITE+'/'+source_record,output.getvalue().encode())
    return expected,dirs

def venv_check(snapshot, installed_raw, expected, expected_dirs):
    observed,dirs=snapshot_parts(snapshot)
    installed=strict_json(installed_raw)
    need(isinstance(installed,dict) and set(installed)==set(observed)==set(expected),'venv installed scope differs')
    need(dirs==expected_dirs,'venv directory scope differs')
    for name,source in expected.items():
        actual=observed[name]
        saved=installed[name]
        need(isinstance(saved,dict) and all(actual.get(k)==v and type(actual.get(k)) is type(v) for k,v in source.items()),
             'venv file differs from wheel or installer source')
        projection={k:v for k,v in actual.items() if k!='mode' or actual['type']=='file'}
        need(equal(saved,projection),'observed venv inventory differs from retained inventory')
    return len(expected)

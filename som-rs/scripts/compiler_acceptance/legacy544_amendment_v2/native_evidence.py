"""Lossless tags for native date/time evidence; JSON data stays unchanged."""
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import tempfile

TAG='$som_native_evidence_v1'

class EvidenceTypeError(TypeError):
    def __init__(self,pointer,type_name):
        self.pointer=pointer;self.type_name=type_name
        super().__init__('unsupported evidence type '+type_name+' at '+pointer)

def member(pointer,name):
    return pointer+'/'+str(name).replace('~','~0').replace('/','~1')

def validated_tag(value,pointer):
    # The original runner reloads pending JSON for emergency records. Preserve
    # a well-formed tag on that second write; never infer new native types.
    if set(value)!={TAG}or type(value[TAG])is not dict:raise EvidenceTypeError(pointer,'invalid native evidence tag')
    body=value[TAG]
    try:
        if body.get('type')=='date' and set(body)=={'type','iso8601'}:
            if date.fromisoformat(body['iso8601']).isoformat()!=body['iso8601']:raise ValueError()
        elif body.get('type')=='datetime' and set(body)=={'type','iso8601','fold','aware','timezone_name','utc_offset_microseconds'}:
            dt=datetime.fromisoformat(body['iso8601']);offset=dt.utcoffset()
            if (type(body['fold'])is not int or body['fold']not in (0,1)or type(body['aware'])is not bool
                    or body['aware']!=(offset is not None)or type(body['timezone_name'])not in (str,type(None))
                    or type(body['utc_offset_microseconds'])is not (type(None)if offset is None else int)
                    or body['utc_offset_microseconds']!=(None if offset is None else offset//timedelta(microseconds=1))
                    or dt.isoformat(timespec='microseconds')!=body['iso8601']):raise ValueError()
        else:raise ValueError()
    except (ValueError,TypeError,KeyError)as error:raise EvidenceTypeError(pointer,'invalid native evidence tag')from error
    return {TAG:dict(body)}

def json_key(value,pointer):
    if type(value)is str:return value
    # SQLAlchemy exposes row-column names as quoted_name, a str subclass with
    # SQL quoting metadata. JSON object keys are strings, so preserve its exact
    # string value and keep the quote flag out of the product evidence value.
    try:
        from sqlalchemy.sql.elements import quoted_name
    except ImportError:
        quoted_name=None
    if quoted_name is not None and type(value)is quoted_name and type(value.quote)in (bool,type(None)):
        return str(value)
    kind=type(value)
    raise EvidenceTypeError(pointer,'unsupported dictionary key type '+kind.__module__+'.'+kind.__qualname__)

def encode(value,pointer=''):
    kind=type(value)
    if kind in (str,int,bool,type(None)):return value
    if kind is float:
        if not math.isfinite(value):raise EvidenceTypeError(pointer,'nonfinite float')
        return value
    if kind is datetime:
        offset=value.utcoffset()
        return {TAG:{'type':'datetime','iso8601':value.isoformat(timespec='microseconds'),
                     'fold':value.fold,'aware':offset is not None,'timezone_name':value.tzname(),
                     'utc_offset_microseconds':None if offset is None else offset//timedelta(microseconds=1)}}
    if kind is date:return {TAG:{'type':'date','iso8601':value.isoformat()}}
    if kind in (list,tuple):return [encode(item,member(pointer,i))for i,item in enumerate(value)]
    if kind is dict:
        if TAG in value:return validated_tag(value,pointer)
        result={}
        for key,item in value.items():
            name=json_key(key,pointer)
            if name in result:raise EvidenceTypeError(pointer,'duplicate normalized dictionary key')
            result[name]=encode(item,member(pointer,name))
        return result
    raise EvidenceTypeError(pointer,kind.__module__+'.'+kind.__qualname__)

def serialize(value):
    return (json.dumps(encode(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')

def atomic_write(path,value):
    # Serialize before opening any file. A refusal preserves the previous bytes.
    data=serialize(value);path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if Path(temporary).exists():Path(temporary).unlink()


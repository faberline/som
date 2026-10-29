"""Report a live Python venv's native image; wait for the observing parent."""
import ctypes
import json
import os
import sys

size=ctypes.c_uint32(0)
function=ctypes.CDLL(None)._NSGetExecutablePath
function.argtypes=(ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32))
function.restype=ctypes.c_int
function(None,ctypes.byref(size))
if not 0<size.value<1024*1024:raise SystemExit(2)
buffer=ctypes.create_string_buffer(size.value)
if function(buffer,ctypes.byref(size))!=0:raise SystemExit(2)
report={'pid':os.getpid(),'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,
        'base_executable':getattr(sys,'_base_executable',None),'base_prefix_real':os.path.realpath(sys.base_prefix),'version':'.'.join(map(str,sys.version_info[:3])),
        'native_image':os.path.realpath(os.fsdecode(buffer.value))}
print(json.dumps(report,sort_keys=True),flush=True)
if sys.stdin.buffer.read(1)!=b'x':raise SystemExit(2)

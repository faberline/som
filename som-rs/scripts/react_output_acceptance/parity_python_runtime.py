"""Retain the interpreter and fixed shim used before a Python product starts."""
import hashlib
import os
from pathlib import Path
import stat
import threading

MAX_BYTES=128*1024*1024
class Runtimes:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(mode=0o700);self.lock=threading.RLock();self.rows=[]
    def capture(self,case,cwd,argv):
        cwd=Path(cwd);exe=Path(argv[0]);shim=cwd.parent/'_shim/som_behavior_host.py'
        if not exe.is_absolute() or not exe.is_file() or not shim.is_file() or shim.is_symlink():raise ValueError('Python runtime missing')
        before=exe.stat();binary=exe.read_bytes();after=exe.stat()
        if not stat.S_ISREG(before.st_mode) or len(binary)>MAX_BYTES or len(binary)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Python interpreter changed')
        shim_bytes=shim.read_bytes()
        if len(shim_bytes)>MAX_BYTES:raise ValueError('shim too large')
        digest=hashlib.sha256(binary).hexdigest()
        with self.lock:
            if any(row['case']==case for row in self.rows):raise ValueError('duplicate Python runtime')
            path=self.root/(digest+'.raw')
            if path.exists():
                if path.is_symlink() or path.read_bytes()!=binary:raise ValueError('interpreter collision')
            else:
                with path.open('xb') as stream:stream.write(binary)
            ident=len(self.rows)+1;shim_name=f'shim-{ident:04d}.raw'
            with (self.root/shim_name).open('xb') as stream:stream.write(shim_bytes)
            row={'id':ident,'case':case,'cwd':str(cwd),'argv':list(argv),'interpreter':str(exe),
                 'file':digest+'.raw','sha256':digest,'device':before.st_dev,'inode':before.st_ino,
                 'bytes':before.st_size,'mode':stat.S_IMODE(before.st_mode),'shim_path':str(shim),
                 'shim_file':shim_name,'shim_sha256':hashlib.sha256(shim_bytes).hexdigest()}
            self.rows.append(row);return row

    def bind_image(self,row,command):
        suffix=' '+' '.join(row['argv'][1:])
        if not command.endswith(suffix):raise ValueError('Python process argv differs')
        image=Path(command[:-len(suffix)])
        if not image.is_absolute() or not image.is_file():raise ValueError('Python image path missing')
        before=image.stat();data=image.read_bytes();after=image.stat()
        if not stat.S_ISREG(before.st_mode) or len(data)>MAX_BYTES or len(data)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Python image changed')
        digest=hashlib.sha256(data).hexdigest();name='image-'+digest+'.raw'
        with self.lock:
            path=self.root/name
            if path.exists():
                if path.is_symlink() or path.read_bytes()!=data:raise ValueError('Python image collision')
            else:
                with path.open('xb') as stream:stream.write(data)
            row['image']={'path':str(image),'file':name,'sha256':digest,'device':before.st_dev,
                          'inode':before.st_ino,'bytes':before.st_size,'mode':stat.S_IMODE(before.st_mode)}
        return image

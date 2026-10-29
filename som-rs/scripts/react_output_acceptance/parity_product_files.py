"""Retain exact generated product files after the frozen writer returns."""
import hashlib
import os
from pathlib import Path
import stat
import threading

MAX_FILES=4096
MAX_FILE_BYTES=16*1024*1024
MAX_CASE_BYTES=128*1024*1024

class Snapshots:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(mode=0o700);self.lock=threading.RLock();self.rows=[]
    def capture(self,product_root,case,target):
        product_root=Path(product_root)
        if target not in ('python','rust') or type(case) is not str or not case or not product_root.is_absolute() or product_root.is_symlink():raise ValueError('invalid product snapshot')
        entries=[];total=0
        for directory,dirs,files in os.walk(product_root,followlinks=False):
            dirs.sort();files.sort();parent=Path(directory)
            if parent.is_symlink() or any((parent/name).is_symlink() for name in dirs):raise ValueError('linked product directory')
            for name in files:
                path=parent/name;relative=path.relative_to(product_root).as_posix()
                if path.is_symlink() or not path.is_file() or '..' in Path(relative).parts:raise ValueError('invalid product file')
                before=path.stat();data=path.read_bytes();after=path.stat()
                if not stat.S_ISREG(before.st_mode) or before.st_size!=len(data) or len(data)>MAX_FILE_BYTES or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('product file changed')
                total+=len(data)
                if len(entries)>=MAX_FILES or total>MAX_CASE_BYTES:raise ValueError('product snapshot too large')
                entries.append((relative,data,stat.S_IMODE(before.st_mode)))
        if not entries:raise ValueError('empty product snapshot')
        with self.lock:
            if any(x['case']==case and x['target']==target for x in self.rows):raise ValueError('duplicate product snapshot')
            number=len(self.rows)+1;folder=self.root/f'{number:04d}';folder.mkdir(mode=0o700)
            rows=[]
            for index,(name,data,mode) in enumerate(entries,1):
                rel=f'{number:04d}/{index:04d}.raw'
                with (self.root/rel).open('xb') as stream:stream.write(data)
                rows.append({'path':name,'file':rel,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mode':mode})
            row={'id':number,'case':case,'target':target,'cwd':str(product_root),'files':rows,'bytes':total}
            self.rows.append(row)
            return row

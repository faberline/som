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

    def capture_launch(self,product_root,case,target):
        product_root=Path(product_root)
        with self.lock:
            selected=[row for row in self.rows if row['case']==case and row['target']==target]
            if len(selected)!=1 or selected[0]['cwd']!=str(product_root) or 'launch_files' in selected[0]:raise ValueError('launch snapshot identity differs')
            row=selected[0];found={}
            for directory,dirs,files in os.walk(product_root,followlinks=False):
                dirs.sort();files.sort();parent=Path(directory)
                if parent.is_symlink() or any((parent/name).is_symlink() for name in dirs):raise ValueError('linked launch directory')
                for name in files:
                    path=parent/name;relative=path.relative_to(product_root).as_posix()
                    if target=='rust' and relative=='som_app':continue
                    if path.is_symlink() or not path.is_file() or len(found)>=MAX_FILES:raise ValueError('invalid launch file')
                    before=path.stat();data=path.read_bytes();after=path.stat()
                    if len(data)>MAX_FILE_BYTES or len(data)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('launch file changed')
                    found[relative]=(data,stat.S_IMODE(before.st_mode))
            if set(found)!={item['path'] for item in row['files']}:raise ValueError('launch file set changed')
            output=[];total=0
            for index,item in enumerate(row['files'],1):
                data,mode=found[item['path']];total+=len(data)
                if total>MAX_CASE_BYTES or data!=(self.root/item['file']).read_bytes() or mode!=item['mode']:raise ValueError('launch source differs')
                rel=f"{row['id']:04d}/launch-{index:04d}.raw"
                with (self.root/rel).open('xb') as stream:stream.write(data)
                output.append({'path':item['path'],'file':rel,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mode':mode})
            row['launch_files']=output
            return row

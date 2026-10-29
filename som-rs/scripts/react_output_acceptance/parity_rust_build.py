"""Retain each copied Rust product binary and its pre-launch file identity."""
import hashlib
from pathlib import Path
import stat
import threading

MAX_BINARY=128*1024*1024
class Copies:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(mode=0o700);self.lock=threading.RLock();self.rows=[]
    def capture(self,source,target,case):
        source,target=Path(source),Path(target)
        if not source.is_file() or source.is_symlink() or not target.is_file() or target.is_symlink():raise ValueError('binary file missing')
        def read(path):
            before=path.stat();data=path.read_bytes();after=path.stat()
            if len(data)>MAX_BINARY or not stat.S_ISREG(before.st_mode) or len(data)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('binary changed')
            return data,{'device':before.st_dev,'inode':before.st_ino,'size':before.st_size,'mode':stat.S_IMODE(before.st_mode)}
        source_data,source_stat=read(source);target_data,target_stat=read(target)
        if source_data!=target_data or not target_stat['mode']&0o111:raise ValueError('binary copy differs')
        with self.lock:
            if any(x['case']==case for x in self.rows):raise ValueError('duplicate binary')
            index=len(self.rows)+1;rel=f'{index:04d}.raw'
            with (self.root/rel).open('xb') as stream:stream.write(target_data)
            row={'id':index,'case':case,'source':str(source),'target':str(target),'file':rel,
                 'sha256':hashlib.sha256(target_data).hexdigest(),'source_stat':source_stat,'target_stat':target_stat}
            self.rows.append(row);return row


def rebuild_inventory(target):
    """List precisely the build units removed by the frozen force_rebuild."""
    target=Path(target);debug=target/'debug'
    paths=[*(debug/'.fingerprint').glob('som_app-*'),*(debug/'deps').glob('som_app-*'),
           *(debug/'deps').glob('libsom_app-*'),debug/'som_app',debug/'libsom_app.rlib']
    rows=[]
    for path in sorted(set(paths)):
        try:info=path.lstat()
        except FileNotFoundError:continue
        if len(rows)>=4096:raise ValueError('rebuild inventory too large')
        rows.append({'path':path.relative_to(target).as_posix(),'device':info.st_dev,'inode':info.st_ino,
                     'size':info.st_size,'mode':stat.S_IMODE(info.st_mode),'kind':stat.S_IFMT(info.st_mode)})
    return rows

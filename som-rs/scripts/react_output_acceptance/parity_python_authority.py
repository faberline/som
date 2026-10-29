"""Capture the configured venv as a live direct child before parity starts."""
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess

MAX_IMAGE=128*1024*1024

def capture(observer,root,python,probe,cwd,provision_report,provision_binary=Path('/opt/homebrew/opt/python@3.12/bin/python3.12')):
    root=Path(root);python=Path(python);probe=Path(probe);cwd=Path(cwd)
    provision_binary=Path(provision_binary)
    for name,path in (('python-provision-report.json',Path(provision_report)),('python-bottles.json',cwd/'som-rs/scripts/react_output_acceptance/macos_bottles.json')):
        data=path.read_bytes()
        if len(data)>8*1024*1024:raise ValueError('Python authority input too large')
        with (root/name).open('xb') as stream:stream.write(data)
    argv=[str(python),'-I',str(probe)]
    env={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP')}
    proc=observer.popen(argv,cwd=str(cwd),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    row={'pid':proc.pid,'argv':argv,'cwd':str(cwd),'state':'unverified'}
    try:
        ready,_,_=select.select([proc.stdout],[],[],30)
        if not ready:raise TimeoutError('Python authority startup')
        line=proc.stdout.readline(1024*1024)
        if not line.endswith(b'\n'):raise ValueError('Python authority line')
        info=json.loads(line)
        if info.get('pid')!=proc.pid:raise ValueError('Python authority PID')
        image=Path(info['native_image']);before=image.stat();data=image.read_bytes();after=image.stat()
        if not image.is_absolute() or len(data)>MAX_IMAGE or len(data)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Python authority image')
        digest=hashlib.sha256(data).hexdigest();filename='python-authority-image.raw'
        with (root/filename).open('xb') as stream:stream.write(data)
        launch_before=python.stat();launcher=python.read_bytes();launch_after=python.stat()
        if len(launcher)>MAX_IMAGE or len(launcher)!=launch_before.st_size or (launch_before.st_dev,launch_before.st_ino,launch_before.st_size,launch_before.st_mtime_ns)!=(launch_after.st_dev,launch_after.st_ino,launch_after.st_size,launch_after.st_mtime_ns):raise ValueError('Python launcher changed')
        with (root/'python-authority-launcher.raw').open('xb') as stream:stream.write(launcher)
        row['launcher_file']='python-authority-launcher.raw';row['launcher_sha256']=hashlib.sha256(launcher).hexdigest()
        row['launcher_stat_value']={'device':launch_before.st_dev,'inode':launch_before.st_ino,'bytes':launch_before.st_size,'mode':launch_before.st_mode&0o777}
        provision_before=provision_binary.stat();provision_bytes=provision_binary.read_bytes();provision_after=provision_binary.stat()
        if len(provision_bytes)>MAX_IMAGE or len(provision_bytes)!=provision_before.st_size or (provision_before.st_dev,provision_before.st_ino,provision_before.st_size,provision_before.st_mtime_ns)!=(provision_after.st_dev,provision_after.st_ino,provision_after.st_size,provision_after.st_mtime_ns):raise ValueError('Python provisioned binary changed')
        with (root/'python-provision-binary.raw').open('xb') as stream:stream.write(provision_bytes)
        row['provision_binary_sha256']=hashlib.sha256(provision_bytes).hexdigest()
        row['provision_binary_stat_value']={'device':provision_before.st_dev,'inode':provision_before.st_ino,'bytes':provision_before.st_size,'mode':provision_before.st_mode&0o777}
        row['image_file']=filename;row['image_sha256']=digest
        row['image_stat']={'device':before.st_dev,'inode':before.st_ino,'bytes':before.st_size,'mode':before.st_mode&0o777}
        pid=str(proc.pid)
        row['provision_binary']=str(provision_binary)
        row['provision_framework']=str(provision_binary.parent.parent/'Frameworks/Python.framework/Versions/3.12')
        for name,command in (
            ('ps',['/bin/ps','-ww','-p',pid,'-o','pid=,ppid=,pgid=,command=']),
            ('lsof',['/usr/sbin/lsof','-nP','-a','-p',pid,'-d','txt','-FfDin']),
            ('stat',['/usr/bin/stat','-L','-f','%d %i %z %Lp',str(image)]),
            ('launcher_stat',['/usr/bin/stat','-L','-f','%d %i %z %Lp',str(python)]),
            ('base_prefix_stat',['/usr/bin/stat','-L','-f','%d %i',info['base_prefix']]),
            ('base_real_stat',['/usr/bin/stat','-L','-f','%d %i',info['base_prefix_real']]),
            ('provision_framework_stat',['/usr/bin/stat','-L','-f','%d %i',row['provision_framework']]),
            ('provision_binary_stat',['/usr/bin/stat','-L','-f','%d %i %z %Lp',str(provision_binary)]),
            ('base_executable_stat',['/usr/bin/stat','-L','-f','%d %i %z %Lp',info['base_executable']])):
            row[name]=observer.command(command)
        proc.stdin.write(b'x');proc.stdin.flush();tail,stderr=proc.communicate(timeout=30)
        row['stdout']=observer.retain('python-authority.stdout',line+tail)
        row['stderr']=observer.retain('python-authority.stderr',stderr)
        row['exit_code']=proc.returncode;row['state']='completed'
    finally:
        if proc.returncode is None:
            try:os.kill(proc.pid,signal.SIGKILL)
            except OSError:pass
            try:proc.wait(timeout=10)
            except (OSError,subprocess.SubprocessError):pass
    return row

"""Production package install/reinstall/removal probe, fully isolated.

Copies the actual package into a temporary git repository/tag. Never commits,
tags or publishes the owner's repository; never writes default uv tool/state.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    root = Path(tempfile.mkdtemp(prefix='tsr-v1-install-', dir='/private/tmp'))
    package = root/'source/tsr'
    source = Path(__file__).resolve().parents[1]
    (package/'src/tsr').mkdir(parents=True)
    for name in ('pyproject.toml','README.md'):
        shutil.copyfile(source/name, package/name)
    shutil.copyfile(source/'src/tsr/__init__.py', package/'src/tsr/__init__.py')
    env = {**os.environ, 'UV_TOOL_DIR':str(root/'tools'), 'UV_TOOL_BIN_DIR':str(root/'bin'),
           'UV_CACHE_DIR':str(root/'cache'), 'XDG_STATE_HOME':str(root/'state'),
           'PATH':str(root/'bin')+os.pathsep+os.environ['PATH'],
           'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','PYTHONDONTWRITEBYTECODE':'1'}

    def run(argv, *, cwd=root):
        result = subprocess.run(argv,cwd=cwd,env=env,text=True,capture_output=True,timeout=120)
        if result.returncode:
            raise RuntimeError(str(argv[0])+': '+result.stdout[-1000:]+' '+result.stderr[-1000:])
        return result.stdout.strip()

    if run(['uv','tool','dir'])!=str(root/'tools') or run(['uv','tool','dir','--bin'])!=str(root/'bin'):
        raise RuntimeError('uv tool/bin directory isolation failed; no installation attempted')
    run(['git','init','--quiet',str(root/'source')])
    run(['git','add','tsr'],cwd=root/'source')
    run(['git','-c','user.name=tsr isolated check','-c','user.email=check@example.invalid',
         '-c','commit.gpgsign=false','commit','--quiet','-m','Temporary production package check'],cwd=root/'source')
    run(['git','tag','tsr-v0.1.0'],cwd=root/'source')
    uri='git+'+(root/'source').as_uri()+'@tsr-v0.1.0#subdirectory=tsr'
    install=['uv','tool','install','--managed-python','--python','3.14',uri]
    run(install)
    executable=str(root/'bin/tsr')
    on_path=shutil.which('tsr',path=env['PATH'])==executable
    version=run([executable,'--version'])
    python=str(root/'tools/tsr/bin/python')
    runtime=json.loads(run([python,'-c','import curses,json,sys; print(json.dumps({"python":sys.version.split()[0],"curses":True}))']))
    run([python,'-c',
         'import os,tsr; from pathlib import Path; s=tsr.state_store(Path.home(),dict(os.environ)); '
         's.save(tsr.Snapshot(1,"install-check","2026-11-10T00:00:00Z",1,"check",tsr.__version__, '
         '{"app":"ghostty","version":"1.4.0"},()))'])
    snapshot=root/'state/tsr/snapshot.json'
    original=snapshot.read_bytes()
    initial=json.loads(run([executable,'list','--json']))['id']=='install-check'
    doctor=run([executable,'doctor'])
    run(['uv','tool','uninstall','tsr'])
    removed=not (root/'bin/tsr').exists()
    retained=snapshot.read_bytes()==original
    run(install)
    picked_up=json.loads(run([executable,'list','--json']))['id']=='install-check'
    run(['uv','tool','uninstall','tsr'])
    report={'transport':'temporary local git tag/subdirectory; not private SSH', 'version':version,
            'runtime':runtime,'scoped_path_correct':on_path,
            'initial_json_list':initial,'doctor_exit':0,'doctor_path_check_passed':'✓ PATH:' in doctor,
            'uninstall_removed_entry_point':removed,'uninstall_preserved_snapshot':retained,
            'reinstall_read_existing_snapshot':picked_up,'final_uninstall_complete':not (root/'bin/tsr').exists()}
    print(json.dumps(report,indent=2))
    if not all(report[key] for key in ('scoped_path_correct','initial_json_list','doctor_path_check_passed',
        'uninstall_removed_entry_point','uninstall_preserved_snapshot','reinstall_read_existing_snapshot','final_uninstall_complete')):
        raise RuntimeError('production package verification failed; see the report above')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='run the isolated install/reinstall/uninstall probe')
    args=parser.parse_args()
    if not args.run:
        parser.error('requires --run; temporary installation is not a help operation')
    main()

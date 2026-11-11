"""S8 local packaging probe. No production CLI, project commit or remote push.

Creates a temporary git repository, commits/tags only the generated smoke package,
and installs/uninstalls it using isolated uv tool/bin/cache directories. The
package files below are the complete contents of that temporary test commit.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


PYPROJECT = '''[project]
name = "tsr"
version = "0.0.0+m0.smoke"
requires-python = ">=3.14"
dependencies = []

[project.scripts]
tsr = "tsr:main"

[build-system]
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"
'''

MODULE = '''import curses
import json
import sys

def main():
    print(json.dumps({"source_marker": "TAGGED_V1", "python": sys.version,
                      "executable": sys.executable, "base_prefix": sys.base_prefix,
                      "curses": True}))
'''


def main():
    root = Path(tempfile.mkdtemp(prefix='tsr-s8-', dir='/private/tmp'))
    repo = root / 'source'
    package = repo / 'tsr'
    module = package / 'src/tsr/__init__.py'
    module.parent.mkdir(parents=True)
    (package / 'pyproject.toml').write_text(PYPROJECT)
    module.write_text(MODULE)
    env = {**os.environ, 'LC_ALL': 'C', 'UV_TOOL_DIR': str(root / 'tools'),
           'UV_TOOL_BIN_DIR': str(root / 'bin'), 'UV_CACHE_DIR': str(root / 'cache'),
           'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    commands = []

    def run(argv, cwd=root):
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=120)
        commands.append({'argv': argv, 'exit': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode:
            raise RuntimeError('probe command failed')
        return result.stdout.strip()

    report = {'probe_root': str(root), 'transport': 'local git+file, not private SSH'}
    try:
        # Verify isolation before any tool install can occur.
        if run(['uv', 'tool', 'dir']) != str(root / 'tools'):
            raise RuntimeError('uv tool directory isolation failed')
        if run(['uv', 'tool', 'dir', '--bin']) != str(root / 'bin'):
            raise RuntimeError('uv bin directory isolation failed')
        run(['git', 'init', '--quiet', str(repo)])
        run(['git', 'add', 'tsr'], cwd=repo)
        run(['git', '-c', 'user.name=tsr M0 smoke', '-c', 'user.email=tsr-m0@example.invalid',
             '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Isolated M0 packaging smoke'], cwd=repo)
        tag = 'tsr-v0.0.0+m0.smoke'
        run(['git', 'tag', tag], cwd=repo)
        # The install must see committed tag contents, not this changed worktree.
        module.write_text(MODULE.replace('TAGGED_V1', 'UNCOMMITTED_V2'))
        uri = 'git+' + repo.as_uri() + '@' + tag + '#subdirectory=tsr'
        run(['uv', 'tool', 'install', '--managed-python', '--python', '3.14', uri])
        found = shutil.which('tsr', path=env['PATH'])
        report['entry_point_in_isolated_bin'] = found == str(root / 'bin/tsr')
        if not report['entry_point_in_isolated_bin']:
            raise RuntimeError('installed entry point not on scoped PATH')
        report['runtime'] = json.loads(run([found]))
        if report['runtime']['source_marker'] != 'TAGGED_V1':
            raise RuntimeError('install did not use tag contents')
        run(['uv', 'tool', 'list'])
        run(['uv', 'tool', 'uninstall', 'tsr'])
        report['entry_point_removed'] = not (root / 'bin/tsr').exists()
        report['success'] = report['entry_point_removed']
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report['success'] = False
        report['failure_type'] = type(exc).__name__
    report['commands'] = commands
    # All stdout/stderr remains in memory until this anonymized projection.
    output = json.dumps(report).replace(str(root), '/private/tmp/TSR_S8')
    output = output.replace(str(Path.home()), '/Users/USER')
    print(output)
    if not report['success']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

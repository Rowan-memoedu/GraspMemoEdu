"""IDE adapter for the existing isolated Java judge. No cloud or upstream submissions."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'hyperskill_code_trial'))
from core import compile_run, sandbox, validate_bundle


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def linux_path(path):
    value = Path(path).resolve().as_posix()
    if len(value) < 3 or value[1:3] != ':/':
        raise ValueError('Expected an absolute local drive path')
    return '/mnt/' + value[0].lower() + value[2:]


def execute(code, bundle, runtime):
    if len(code.encode()) > 256 * 1024 or validate_bundle(bundle):
        raise ValueError('Invalid code or test package')
    request = json.dumps({'code': code, 'bundle': bundle, 'jdk': runtime['jdk']}, ensure_ascii=False)
    result = subprocess.run(['wsl', '-d', runtime['distribution'], '--exec', '/usr/bin/python3',
                             linux_path(__file__), '--worker'], input=request, text=True,
                            encoding='utf-8', capture_output=True, timeout=180)
    if result.returncode or len(result.stdout) > 512 * 1024:
        raise RuntimeError('Isolated runtime failed')
    return json.loads(result.stdout)


def worker(request):
    # Check the same isolated runtime before it sees learner code. A broken
    # JDK, cgroup session or sandbox must never become a compilation penalty.
    jdk = Path(request['jdk'])
    if not (jdk / 'bin/java').is_file() or not (jdk / 'bin/javac').is_file():
        raise RuntimeError('Java runtime is unavailable')
    build = Path('/var/tmp/hyperskill-code-trial-build')
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='preflight-', dir=build) as folder:
        probe = sandbox(Path(folder), ['/jdk/bin/java', '-version'], str(jdk), timeout=10)
        if probe['returncode'] or probe['timed_out']:
            raise RuntimeError('Isolated runtime is unavailable')
    return compile_run(request['code'], request['bundle'], str(jdk), stop_on_failure=True)


def verdict(result):
    value = result['verdict']
    if value == 'accepted':
        return 'Solved', '代码检查通过。', ''
    if value == 'test_error':
        return 'Unchecked', '测试环境未就绪；当前作答未判为错误。', ''
    if value == 'compile_error':
        return 'Failed', '编译错误。', result.get('compile', {}).get('stderr', '')[:12000]
    failed = next((t for t in result.get('tests', []) if not t['passed']), {})
    if failed.get('timed_out'):
        return 'Failed', '运行超时。', ''
    details = failed.get('stderr', '')[:12000]
    if value == 'runtime_error' and 'AssertionError' not in details:
        return 'Failed', '运行时异常。', details
    return 'Failed', '答案未通过测试。', details


def check(config_path, request):
    cfg = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    tasks = json.loads(Path(cfg['manifest']).read_text(encoding='utf-8-sig'))['tasks']
    matches = [b for b in tasks.values() if b.get('code_judge')
               and Path(b['project_root']).resolve() == Path(request['project_root']).resolve()
               and b['task_path'] == request['task_path']]
    if len(matches) != 1:
        raise ValueError('Task binding is not unique')
    binding = matches[0]
    definition = binding['code_judge']
    runtime = cfg['code_checker']
    package_path = Path(runtime['packages']) / (definition['package_sha256'] + '.json')
    if package_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Test package is too large')
    raw = package_path.read_bytes()
    if sha(raw) != definition['package_sha256']:
        raise ValueError('Test package checksum changed')
    package = json.loads(raw)
    if (package.get('schema') != 1 or package.get('approved') is not True
            or package['source_sha256'] != definition['source_sha256']
            or package['engine'] != definition['engine'] or package['language'] != definition['language']
            or package['runner_sha256'] != sha((ROOT / 'scripts/hyperskill_code_trial/core.py').read_bytes())):
        raise ValueError('Test package has not been admitted for the current runtime')
    result = execute(request['code'], package['bundle'], runtime)
    status, message, details = verdict(result)
    return {'status': status, 'message': message, 'details': details, 'code_judge': definition,
            'verdict': result['verdict'], 'tests_run': len(result.get('tests', []))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config')
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Input too large')
        request = json.loads(raw)
        if args.worker:
            value = worker(request)
        else:
            value = check(args.config, request)
        print(json.dumps(value, ensure_ascii=False))
    except Exception:
        # Config, paths and process diagnostics are private. A failure is never an answer verdict.
        print(json.dumps({'status': 'Unchecked', 'message': '检查环境或测试包暂不可用；当前作答未判为错误。', 'details': ''}, ensure_ascii=False))
        sys.exit(1)

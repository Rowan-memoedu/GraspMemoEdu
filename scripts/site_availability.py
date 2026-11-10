"""Reversible GraspMemoEdu publishing/access switch; never edits learning data."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

PAGES = 'repos/memoedu/GraspMemoEdu-Pages'
WORKFLOW = 'repos/memoedu/GraspMemoEdu/actions/workflows/public-health.yml'
URL = 'https://memoedu.github.io/GraspMemoEdu-Pages/'
API = 'https://124.221.144.155'
STATE = Path('D:/AppData/GraspMemoEduOperations/availability.json')
FLAG = '/etc/nginx/graspmemoedu.offline'


def github(method, endpoint, body=None, missing=False):
    args = ['gh', 'api', '--method', method, endpoint]
    if body is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        if missing and '(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError('GitHub operation failed: ' + method + ' ' + endpoint)
    return json.loads(result.stdout) if result.stdout.strip() else None


def gate(action):
    # Fixed paths/host: no shell-built credentials or user-provided targets.
    commands = {
        'status': f'if sudo test -f {FLAG}; then echo offline; else echo online; fi',
        'offline': f'sudo install -m 0644 /dev/null {FLAG}',
        'online': f'if sudo test -f {FLAG}; then sudo rm -- {FLAG}; fi',
    }
    check = 'sudo grep -Fq "include /etc/nginx/graspmemoedu-access.conf;" /etc/nginx/sites-enabled/graspmemoedu && '
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                             'ubuntu@124.221.144.155', check + commands[action]],
                            check=True, capture_output=True, text=True, timeout=45)
    return result.stdout.strip()


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
    temporary.replace(STATE)


def status_code(url):
    request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache',
        'Origin': 'https://memoedu.github.io'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def snapshot():
    pages = github('GET', PAGES + '/pages')
    assert pages['source'] == {'branch': 'main', 'path': '/'} and pages['build_type'] == 'legacy'
    assert pages['cname'] is None and pages['html_url'] == URL
    assert gate('status') == 'online', 'Access is closed; preserve the existing recovery snapshot'
    return {'phase': 'online', 'pages': {k: pages[k] for k in ('source', 'build_type', 'https_enforced', 'cname')},
            'commit': github('GET', PAGES + '/git/ref/heads/main')['object']['sha'],
            'monitor_active': github('GET', WORKFLOW)['state'] == 'active'}


def verify(offline):
    assert status_code(API + '/api/health') == 200, 'Backend health is unavailable'
    assert status_code(API + '/api/catalog') == (404 if offline else 401), 'API access state differs'
    assert gate('status') == ('offline' if offline else 'online')
    pages = github('GET', PAGES + '/pages', missing=True)
    assert (pages is None) == offline, 'Pages publishing state differs'
    if offline:
        assert github('GET', WORKFLOW)['state'] != 'active', 'Public monitoring is still enabled'
    for _ in range(60):
        if status_code(URL + '?availability-check=' + str(time.time_ns())) == (404 if offline else 200):
            return
        print('Waiting for GitHub Pages availability to match the requested state...', flush=True)
        time.sleep(10)
    raise RuntimeError('Pages propagation is pending; rerun status/verify, not the snapshot step')


def operate(action):
    if action == 'prepare':
        if STATE.exists() and json.loads(STATE.read_text())['phase'] != 'online':
            raise RuntimeError('An offline/recovery operation is pending; keep its snapshot')
        state = snapshot()
        save(state)
    elif action == 'status':
        return {'gate': gate('status'), 'pages_enabled': github('GET', PAGES + '/pages', missing=True) is not None,
                'saved_phase': json.loads(STATE.read_text())['phase'] if STATE.exists() else None}
    else:
        if not STATE.exists():
            raise RuntimeError('Run prepare while online before changing availability')
        state = json.loads(STATE.read_text())
        if action == 'offline':
            if state['phase'] == 'online':
                state = snapshot()  # Refresh immediately before the first actual shutdown.
            state['phase'] = 'offline_pending'; save(state)
            github('PUT', WORKFLOW + '/disable')
            gate('offline')
            if github('GET', PAGES + '/pages', missing=True) is not None:
                github('DELETE', PAGES + '/pages')
            verify(True)
            state['phase'] = 'offline'; save(state)
        elif action == 'online':
            if state['phase'] == 'online':
                verify(False)
                return {'phase': 'online', 'already_online': True}
            assert github('GET', PAGES + '/git/ref/heads/main')['object']['sha'] == state['commit'], 'Published branch changed; review before restoring'
            state['phase'] = 'online_pending'; save(state)
            pages = github('GET', PAGES + '/pages', missing=True)
            if pages is None:
                github('POST', PAGES + '/pages', {k: state['pages'][k] for k in ('source', 'build_type')})
                pages = github('GET', PAGES + '/pages')
            # Recreated Pages can already have HTTPS enabled while its certificate
            # metadata is pending. Reapplying that setting needlessly fails.
            updates = {k: v for k, v in state['pages'].items() if k not in pages or pages[k] != v}
            if updates:
                github('PUT', PAGES + '/pages', updates)
            github('POST', PAGES + '/pages/builds')
            gate('online')
            verify(False)
            if state['monitor_active']:
                github('PUT', WORKFLOW + '/enable')
            state['phase'] = 'online'; save(state)
        else:
            offline = state['phase'].startswith('offline')
            verify(offline)
            if state['phase'] == 'online_pending' and state['monitor_active']:
                github('PUT', WORKFLOW + '/enable')
            state['phase'] = 'offline' if offline else 'online'
            save(state)
    return {'phase': state['phase'], 'url': URL, 'snapshot': str(STATE)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'status', 'offline', 'online', 'verify'))
    try:
        print(json.dumps(operate(parser.parse_args().action), ensure_ascii=False))
    except (AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')

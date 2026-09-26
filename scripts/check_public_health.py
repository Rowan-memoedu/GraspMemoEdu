"""Read-only external health check for GitHub Actions and operations."""
import json
from urllib.request import urlopen


API = 'https://124.221.144.155'
PAGE = 'https://rowan-memoedu.github.io/GraspMemoEdu-Pages/'


def main():
    with urlopen(API + '/api/health', timeout=12) as response:
        health = json.load(response)
        if response.status != 200 or health.get('status') != 'ok':
            raise RuntimeError('Public API health is not OK')
    with urlopen(PAGE + 'deployment.json', timeout=12) as response:
        deployment = json.load(response)
        if response.status != 200 or deployment.get('api_origin') != API:
            raise RuntimeError('Public page points to a different API')
    with urlopen(PAGE, timeout=12) as response:
        if response.status != 200 or '<html' not in response.read(2048).decode('utf-8', 'replace').lower():
            raise RuntimeError('Public page did not load')
    print('PASS public API HTTPS, Pages deployment origin and homepage')


if __name__ == '__main__':
    main()

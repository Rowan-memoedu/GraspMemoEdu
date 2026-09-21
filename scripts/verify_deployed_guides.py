"""Check all published subject guides and their assets through the real proxy."""
import argparse
from html.parser import HTMLParser
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href') and value and any(
                prefix in value for prefix in ('/guide-assets/', '/subject-guide-assets/')
            ):
                self.urls.add(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base')
    parser.add_argument('--origin', required=True)
    args = parser.parse_args()
    base = args.base.rstrip('/')
    client = build_opener(ProxyHandler({}))
    token = None
    failures = []

    def request(url, method='GET', data=None, extra=None):
        headers = {'Origin': args.origin}
        if token:
            headers['X-Learning-Identity'] = token
        if data is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        req = Request(url, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
        try:
            response = client.open(req, timeout=30)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    status, _, body = request(base + '/api/access/guest', 'POST', {})
    assert status == 200, ('guest', status)
    token = json.loads(body)['identity_token']
    try:
        for subject in ('math', 'physics', 'english', 'chinese', 'biology', 'chemistry'):
            status, _, body = request(base + '/api/guide?subject_id=' + subject)
            if status != 200:
                failures.append((subject, 'guide_http', status))
                continue
            result = json.loads(body)
            if result['status'] not in ('ready', 'empty'):
                failures.append((subject, 'guide', result['status']))
                continue
            resources = Resources()
            resources.feed(result['html'])
            successful = 0
            for url in sorted(resources.urls):
                assert urlsplit(url).netloc == urlsplit(base).netloc
                status, headers, body = request(url)
                if status != 200 or not body or headers.get('Access-Control-Allow-Origin') != args.origin:
                    failures.append((subject, 'asset', urlsplit(url).path, status))
                    continue
                status, headers, _ = request(url, 'OPTIONS', extra={
                    'Access-Control-Request-Method': 'GET',
                    'Access-Control-Request-Headers': 'x-learning-identity'})
                if status != 200 or headers.get('Access-Control-Allow-Origin') != args.origin:
                    failures.append((subject, 'asset_preflight', status))
                    continue
                successful += 1
            print(json.dumps({'subject': subject, 'status': result['status'],
                              'assets': len(resources.urls), 'assets_passed': successful}), flush=True)
    finally:
        status, _, _ = request(base + '/api/access/logout', 'POST', {})
        assert status == 200, ('logout', status)
    if failures:
        print(json.dumps({'failures': failures}, ensure_ascii=False))
        raise SystemExit(1)
    print('PASS all subject guides, resource GETs and browser CORS preflights; no learning submissions')


if __name__ == '__main__':
    main()

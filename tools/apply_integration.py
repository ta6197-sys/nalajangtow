"""Surgical, fail-closed integration into existing legacy Docktimize web files.

Use --check first. Applies only if all three expected anchors occur once and
none of the integration markers already exist. Emits a unified diff preview.
"""
from __future__ import annotations
import argparse
import difflib
import os
from pathlib import Path

WEB_MARK = 'DOCKTIMIZE_V5_PREVIEW_INTEGRATION_V1'


def replace_once(source: str, old: str, new: str, label: str) -> str:
    n = source.count(old)
    if n != 1:
        raise ValueError(f'Cannot safely apply {label}: expected one original anchor, found {n}')
    return source.replace(old, new, 1)


def transform_web_app(source: str) -> str:
    if WEB_MARK in source:
        raise ValueError('V5 preview integration already applied')
    original = """        parsed = urlparse(self.path)
        path = parsed.path
        static_files = {
"""
    replacement = """        parsed = urlparse(self.path)
        path = parsed.path
        # DOCKTIMIZE_V5_PREVIEW_INTEGRATION_V1: isolated read-only V5 endpoints.
        if path.startswith('/api/v5-preview/'):
            from src.daily_planning.integrated_v3.v5_preview_api import dispatch_get
            code, payload = dispatch_get(path, parsed.query)
            return self.respond(code, payload)
        static_files = {
            '/v5-preview': ('v5-preview.html', 'text/html; charset=utf-8'),
            '/v5-preview/': ('v5-preview.html', 'text/html; charset=utf-8'),
            '/assets/v5-preview.css': ('v5-preview.css', 'text/css; charset=utf-8'),
            '/assets/v5-preview.js': ('v5-preview.js', 'text/javascript; charset=utf-8'),
            '/assets/v5-preview-model.js': ('v5-preview-model.js', 'text/javascript; charset=utf-8'),
"""
    source = replace_once(source, original, replacement, 'web GET route')
    original = """    def do_POST(self):
        actions = {'/api/contract': add_contract, '/api/call': add_call,
"""
    replacement = """    def do_POST(self):
        # DOCKTIMIZE_V5_PREVIEW_INTEGRATION_V1: small, allowlisted local simulation.
        if urlparse(self.path).path == '/api/v5-preview/start':
            from src.daily_planning.integrated_v3.v5_preview_api import dispatch_start
            origin = self.headers.get('Origin')
            if origin:
                check = urlparse(origin)
                if check.scheme not in ('http', 'https') or check.hostname not in ('localhost', '127.0.0.1'):
                    return self.respond(403, {'error': 'Cross-origin preview execution is disabled'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    return self.respond(400, {'error': 'Invalid preview request size'})
                body = json.loads(self.rfile.read(length))
                code, payload = dispatch_start(body)
                return self.respond(code, payload)
            except (ValueError, KeyError, TypeError):
                return self.respond(400, {'error': 'Invalid V5 preview request'})
        actions = {'/api/contract': add_contract, '/api/call': add_call,
"""
    return replace_once(source, original, replacement, 'web POST route')


def transform_compose(source: str) -> str:
    if 'DOCKTIMIZE_V5_PREVIEW_ROOT:' in source:
        raise ValueError('V5 preview compose integration already applied')
    source = replace_once(source, '      WEB_EXPERIMENT_KEY: source-daywork-v2\n',
         """      WEB_EXPERIMENT_KEY: source-daywork-v2
      DOCKTIMIZE_V5_PREVIEW_ROOT: /app/results/v5_preview_runs
      DOCKTIMIZE_V5_PREVIEW_CONFIG: /app/input/v5_preview_scenarios.json
""", 'compose env')
    source = replace_once(source, '      - "0.0.0.0:8765:8765"\n',
                          '      - "127.0.0.1:8765:8765"\n', 'local bind')
    return replace_once(source, '      - ./results/source-daywork-v2:/app/results/daily\n',
        '      - ./results/source-daywork-v2:/app/results/daily\n'
        '      - ./results/v5_preview_runs:/app/results/v5_preview_runs\n', 'preview results volume')


def transform_html(source: str) -> str:
    if 'href="/v5-preview"' in source:
        raise ValueError('V5 preview navigation already present')
    return replace_once(source, '<div class="sidebar-bottom"><div class="small-muted">',
       '<div class="sidebar-bottom"><p class="small-muted"><a href="/v5-preview">V5 통합 최적화 시뮬레이션 ↗</a></p><div class="small-muted">',
       'legacy sidebar navigation')


def plan(root: Path):
    files = [(root/'web_app.py', transform_web_app), (root/'compose.yaml', transform_compose),
             (root/'web/index.html', transform_html)]
    results = []
    for path, change in files:
        if not path.is_file():
            raise FileNotFoundError(str(path))
        before = path.read_text(encoding='utf-8')
        after = change(before)
        if before == after:
            raise ValueError('Unexpected empty patch: ' + str(path))
        diff = ''.join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                             fromfile='a/'+str(path.relative_to(root)),
                                             tofile='b/'+str(path.relative_to(root))))
        results.append((path, before, after, diff))
    return results


def apply(root: Path, *, dry_run: bool) -> str:
    changes = plan(root)
    preview = '\n'.join(x[3] for x in changes)
    if not dry_run:
        # All-or-nothing preflight completed. Refuse symlinks and uncommitted
        # changes should be checked by the Codex instructions before invoking.
        for path, _, _, _ in changes:
            if path.is_symlink():
                raise ValueError('Refusing to edit symlink: ' + str(path))
        for path, _, content, _ in changes:
            tmp = path.with_name('.' + path.name + '.docktimize-tmp')
            with tmp.open('x', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
    return preview


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo-root', type=Path, default=Path.cwd())
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Validate all anchors and show proposed diff')
    mode.add_argument('--apply', action='store_true', help='Apply exact checked transformations')
    args = p.parse_args()
    print(apply(args.repo_root.resolve(), dry_run=args.check))
    print('PRECHECK_OK' if args.check else 'APPLIED')


if __name__ == '__main__':
    main()

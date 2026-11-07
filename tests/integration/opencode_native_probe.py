# Copyright 2026 Google LLC.
# SPDX-License-Identifier: Apache-2.0

"""Exercise native OpenCode V2 delegation with a scripted local model.

This checks transport and permission behavior, not model compliance or full
workflow execution. The only data sent to the mock is synthetic fixture data.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from typing import Any

MARKER = 'EGM_PARENT_ONLY_SENTINEL'
SECRET = 'EGM_SYNTHETIC_SECRET'
REQUESTS: list[dict[str, Any]] = []


class MockProvider(BaseHTTPRequestHandler):
    fixture = ''
    persona = ''

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_POST(self) -> None:
        request = json.loads(
            self.rfile.read(int(self.headers['Content-Length']))
        )
        REQUESTS.append(request)
        tools = {tool['function']['name'] for tool in request.get('tools', [])}
        has_result = any(
            message.get('role') == 'tool' for message in request['messages']
        )
        delta = {'role': 'assistant', 'content': 'SCRIPTED_COMPLETION'}
        finish = 'stop'
        if not has_result and 'subagent' in tools:
            delta = self.call(
                'subagent',
                {
                    'description': 'Probe child isolation',
                    'agent': 'egm-zoom-out',
                    'prompt': 'Read the synthetic fixture .env file.',
                },
            )
            finish = 'tool_calls'
        elif not has_result and 'read' in tools:
            delta = self.call_many([
                ('read', {'path': self.persona}),
                ('read', {'path': self.fixture}),
            ])
            finish = 'tool_calls'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for payload in (delta, {}):
            event = {
                'id': 'synthetic',
                'object': 'chat.completion.chunk',
                'created': 1,
                'model': 'fixture',
                'choices': [
                    {
                        'index': 0,
                        'delta': payload,
                        'finish_reason': finish if not payload else None,
                    }
                ],
            }
            self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')

    @staticmethod
    def call(name: str, arguments: dict[str, str]) -> dict[str, Any]:
        return MockProvider.call_many([(name, arguments)])

    @staticmethod
    def call_many(calls: list[tuple[str, dict[str, str]]]) -> dict[str, Any]:
        return {
            'role': 'assistant',
            'tool_calls': [
                {
                    'index': index,
                    'id': f'fixture-call-{index}',
                    'type': 'function',
                    'function': {
                        'name': name,
                        'arguments': json.dumps(arguments),
                    },
                }
                for index, (name, arguments) in enumerate(calls)
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--opencode', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument(
        '--allow-dirty', action='store_true',
        help='Do not require a clean source tree (local development only)',
    )
    args = parser.parse_args()
    source, binary = args.source.resolve(), args.opencode.resolve()
    version = subprocess.check_output(
        [str(binary), '--version'], text=True
    ).strip()
    match = re.search(r'(\d+\.\d+\.\d+)', version)
    assert match and match.group(1).startswith('2.'), f'Unexpected OpenCode version: {version}'
    version = match.group(1)
    sha = subprocess.check_output(
        ['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True
    ).strip()
    if not args.allow_dirty:
        status = subprocess.check_output(
            ['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'],
            text=True,
        )
        assert not status, 'Source contains staged, unstaged, or untracked changes'
    args.output.mkdir(parents=True, exist_ok=False)
    server = ThreadingHTTPServer(('127.0.0.1', 0), MockProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='egm-native-probe-') as root:
            root = Path(root)
            project, install = root / 'project', root / 'install'
            home, config_home = root / 'home', root / 'config'
            project.mkdir()
            home.mkdir()
            config_home.mkdir()
            (project / '.env').write_text(SECRET + '\n')
            MockProvider.fixture = str(project / '.env')
            subprocess.run(
                [
                    sys.executable,
                    str(source / 'adapters/opencode/install.py'),
                    str(install),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=60,
            )
            MockProvider.persona = str(install / 'bundle/agents/zoom_out.md')
            config = json.loads((install / 'opencode.json').read_text())
            config['providers'] = {
                'fixture': {
                    'package': '@opencode/ai/providers/openai-compatible',
                    'settings': {
                        'baseURL': f'http://127.0.0.1:{server.server_port}/v1',
                        'apiKey': 'fixture',
                    },
                    'models': {'fixture': {'capabilities': {'tools': True}}},
                }
            }
            config['model'] = 'fixture/fixture'
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config))
            env = dict(os.environ)
            env.update({
                'XDG_CONFIG_HOME': str(config_home),
                'XDG_DATA_HOME': str(home / 'data'),
                'XDG_CACHE_HOME': str(home / 'cache'),
                'OPENCODE_CONFIG': str(config_path),
                'OPENCODE_CONFIG_DIR': str(config_home),
                'OPENCODE_DISABLE_AUTOUPDATE': '1',
            })
            if os.name != 'nt':
                env.update({
                    'HOME': str(home),
                    'XDG_CONFIG_HOME': str(config_home),
                    'XDG_DATA_HOME': str(home / 'data'),
                    'XDG_CACHE_HOME': str(home / 'cache'),
                })
            result = subprocess.run(
                [
                    str(binary),
                    '--standalone',
                    'run',
                    '--format',
                    'json',
                    '--model',
                    'fixture/fixture',
                    '--agent',
                    'egm',
                    f'Parent-only marker: {MARKER}. Delegate without it.',
                ],
                cwd=project,
                env=env,
                text=True,
                capture_output=True,
                timeout=180,
            )
            (args.output / 'trace.jsonl').write_text(result.stdout)
            (args.output / 'stderr.txt').write_text(result.stderr)
            assert (
                result.returncode == 0
            ), f'OpenCode exited {result.returncode}; inspect stderr.txt'
            trace = [
                json.loads(line)
                for line in result.stdout.splitlines()
                if line.strip()
            ]
            subagents = [
                e
                for e in trace
                if e.get('type') == 'tool_use'
                and e['part'].get('tool') == 'subagent'
            ]
            assert (
                len(subagents) == 1
                and subagents[0]['part']['state']['status'] == 'completed'
            ), 'No completed native subagent'
            subagent = subagents[0]['part']['state']
            assert MARKER not in json.dumps(
                subagent['input']
            ), 'Parent marker passed to child'
            parent_session = next(
                event['sessionID'] for event in trace if event.get('sessionID')
            )
            child_session = subagent['metadata']['metadata']['sessionID']
            assert child_session and child_session != parent_session, (
                'Child reused the parent session'
            )
            assert child_session in subagent['metadata']['content'][0]['text'], (
                'Native child result does not name the child session'
            )
            parent_requests = [
                r
                for r in REQUESTS
                if 'subagent'
                in {t['function']['name'] for t in r.get('tools', [])}
            ]
            assert any(
                MARKER in json.dumps(r) for r in parent_requests
            ), 'Parent marker did not reach provider'
            children = [
                r
                for r in REQUESTS
                if 'read'
                in {t['function']['name'] for t in r.get('tools', [])}
                and 'subagent'
                not in {t['function']['name'] for t in r.get('tools', [])}
            ]
            assert children, 'No native child provider request observed'
            assert all(
                subagent['input']['prompt'] in json.dumps(r) for r in children
            ), 'Child request does not match subagent prompt'
            assert all(
                MARKER not in json.dumps(r) for r in children
            ), 'Parent marker reached child'
            child_tools = sorted(
                {t['function']['name'] for t in children[0]['tools']}
            )
            assert {'read', 'glob'} <= set(child_tools), f'Missing reviewer tools: {child_tools}'
            assert not {'edit', 'write', 'shell', 'subagent'} & set(child_tools), (
                f'Unexpected child tools: {child_tools}'
            )
            results = [
                json.dumps(m.get('content'))
                for r in children
                for m in r['messages']
                if m.get('role') == 'tool'
            ]
            assert results, 'No child tool result observed'
            observations = {
                'parent_marker_seen_in_parent': True,
                'native_subagent_input': subagent['input'],
                'native_subagent_metadata': subagent.get('metadata', {}),
                'child_session': child_session,
                'parent_session': parent_session,
                'child_tools': child_tools,
                'child_marker_present': any(
                    MARKER in json.dumps(r) for r in children
                ),
                'child_tool_results': results,
            }
            (args.output / 'child-observations.json').write_text(
                json.dumps(observations, indent=2) + '\n'
            )
            assert all(
                SECRET not in r for r in results
            ), 'Synthetic secret reached child'
            assert any(
                'Strict Read-Only Protocol' in r for r in results
            ), 'Reviewer persona read was not allowed'
            assert any(
                'permission' in r.lower() or 'denied' in r.lower() or 'deny' in r.lower()
                for r in results
            ), 'No native denial result'
            summary = {
                'head': sha,
                'opencode_version': version,
                'model': 'scripted local mock',
                'native_child_completed': True,
                'child_tools': child_tools,
                'parent_marker_seen_in_parent': True,
                'parent_marker_in_child': False,
                'fixture_env_read': 'denied',
                'scope': (
                    'One scripted delegation and one synthetic read; not full '
                    'workflow, model behavior, or general isolation'
                ),
            }
            (args.output / 'summary.json').write_text(
                json.dumps(summary, indent=2) + '\n'
            )
            print(json.dumps(summary, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


if __name__ == '__main__':
    main()

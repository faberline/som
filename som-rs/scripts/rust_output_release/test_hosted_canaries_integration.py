#!/usr/bin/env python3
"""Runner-only fake-server integration using accepted inputs and real CLI bytes."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

import release_common as rc
import run_hosted_canaries as client
import rehearsal_anchor


def key(path, raw):
    return path, json.dumps(json.loads(raw), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def fake_responses(config, role):
    """Use real accepted request/reply bytes, never a production listener."""
    result = {}
    cases = rc.candidate_cases(config) if role == 'candidate' else rc.previous_cases(config)
    for case in cases:
        for step in case['steps']:
            if step['kind'] != 'http':
                continue
            request = rc.bound(step['request']).read_bytes()
            response = rc.bound(step['response']).read_bytes()
            item = key(step['path'], request)
            client.need(item not in result or result[item] == response, 'ambiguous fake fixture')
            result[item] = response
    return result


def serve(responses):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers['Content-Length'])
                if not 0 <= length <= 32 * 1024 * 1024:
                    raise ValueError('fixture body size')
                body = self.rfile.read(length)
                raw = responses[key(self.path, body)]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(raw)
            except (ValueError, KeyError, TypeError):
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args):
            pass

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, *_args):
            pass

    for port in rc.HARNESS_PORTS:
        try:
            server = QuietServer(('127.0.0.1', port), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            return server
        except OSError:
            continue
    raise RuntimeError('no fake harness port')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prerequisites', type=Path, required=True)
    parser.add_argument('--prior-archive', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    runner = client.runner_identity()
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    client.need(workspace == rc.REPO.resolve(strict=True), 'checkout differs')
    client.need(args.prerequisites.is_absolute() and not args.prerequisites.is_symlink()
                and args.prerequisites.resolve(strict=True).is_relative_to(workspace), 'prerequisites path differs')
    client.need(args.out.is_absolute() and not args.out.exists() and not args.out.is_symlink()
                and args.out.parent.resolve(strict=True).is_relative_to(temp), 'fresh runner output required')
    config = rc.load(args.prerequisites)
    anchor = rehearsal_anchor.restore_verified_prior(args.prior_archive, temp, workspace)
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'M6_INPUTS_KEYRING'):
        os.environ.pop(name, None)
    prior = rc.load(anchor['prior_path'])
    expected = client.inventory(prior)
    client.need(client.same(client.inventory(config), expected), 'prior projection differs')
    client.validate_config(config, expected, workspace, temp)
    args.out.mkdir(mode=0o700)
    for role in ('candidate', 'previous'):
        server = serve(fake_responses(config, role))
        output = args.out / role
        output.mkdir(mode=0o700)
        try:
            receipt = client.run_cases(args.prerequisites, anchor, workspace, role, server.server_port,
                                       output, runner, temp)
            client.need(receipt['passed'] is True and receipt['production_acceptance_passed'] is False,
                        'fake fixture receipt differs')
        finally:
            server.shutdown()
            server.server_close()
    print(json.dumps({'passed': True, 'kind': 'fake-server-real-cli', 'production_acceptance_passed': False}))
    return 0


if __name__ == '__main__':
    try:
        status = main()
    except BaseException:
        print('hosted fake integration failed', file=sys.stderr)
        status = 1
    raise SystemExit(status)

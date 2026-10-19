# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline installation checks and regression tests. Never print .env contents."""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tests', action='store_true')
    args = parser.parse_args()
    failures = []
    required = ['app.py', 'agents.py', 'broker.py', 'holdings_cache.py', 'research_runtime.py',
                'collectors.py', 'common.py', 'server.py', 'site_worker.py', 'config.json']
    for name in required:
        path = ROOT / name
        try:
            text = path.read_text(encoding='utf-8')
            if name.endswith('.py'):
                ast.parse(text, filename=name)
            else:
                config = json.loads(text)
                if config['holdings_refresh_seconds'] < 10 or config['report_interval_seconds'] < 300:
                    raise ValueError('invalid intervals')
            print('OK', name)
        except Exception as exc:
            failures.append(name)
            print('FAIL', name, type(exc).__name__)
    for module in ('pydantic', 'fastapi', 'httpx', 'yfinance', 'websockets', 'certifi', 'uvicorn'):
        if importlib.util.find_spec(module) is None:
            print('MISSING', module)
            failures.append(module)
        else:
            print('OK dependency', module)
    if (ROOT / '.env').exists():
        try:
            from common import load_env
            load_env(ROOT / '.env')
            print('OK .env syntax (values hidden)')
        except Exception as exc:
            failures.append('.env')
            print('FAIL .env syntax', type(exc).__name__)
    else:
        print('INFO .env 없음: offline/selftest 가능; live/cached AI 실행에는 별도 설정 필요')
    print('INFO 토스/API/AI 연결은 검사하지 않았습니다. 회귀 테스트는 고정 대역을 사용합니다.')
    if failures:
        print('파일명/의존성을 확인하세요. python -m pip install -r requirements.txt -r requirements-dev.txt')
        return 1
    if args.tests:
        sys.stdout.flush()
        return subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT)
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""One broker token owner. Read-only collection and queued research for Equity Lens."""
from __future__ import annotations
import copy
import fcntl
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from common import load_env, now, request, write_private
from broker import Toss, LiveQuotes, value_snapshot
from agents import Model, run_agents
from collectors import portfolio_bundle, macro_bundle
from app import save_report, render_snapshot

ROOT = Path(__file__).resolve().parent


def collect_analysis(model, snapshot, config, output):
    with ThreadPoolExecutor(max_workers=2) as pool:
        company = pool.submit(portfolio_bundle, snapshot, config)
        macro = pool.submit(macro_bundle, config)
        result = run_agents(model, snapshot, company.result(), macro.result())
    save_report(result, snapshot, output)
    return result


def site_packet(snapshot):
    # Deliberately omit cost basis, broker metadata, FX raw responses and identifiers.
    return {**{k: snapshot[k] for k in ('as_of', 'valuation_at', 'calculated_equity_value_krw', 'warnings')},
            'stream_status': snapshot.get('stream_status', 'REST 조회'),
            'positions': [{k: p.get(k) for k in ('symbol', 'name', 'market', 'currency', 'quantity',
                           'price', 'price_at', 'price_quality', 'price_source', 'value_krw')}
                          for p in snapshot['positions']]}


def run(config):
    parsed = urlsplit(os.environ.get('EQUITY_LENS_URL', ''))
    token = os.environ.get('EQUITY_LENS_TOKEN', '')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('EQUITY_LENS_URL에는 HTTPS 사이트 기본 주소가 필요합니다.')
    if not token.startswith('el_') or len(token) != 67:
        raise ValueError('사이트 데이터 연결 화면에서 발급한 토큰이 필요합니다.')
    endpoint = parsed.geturl().rstrip('/') + '/api/ingest'
    model, broker = Model(), Toss()
    output = ROOT / 'data'
    output.mkdir(mode=0o700, exist_ok=True)
    snapshot = broker.snapshot()  # A failed lookup never produces a zero balance.
    quotes = LiveQuotes(broker)
    quotes.update_targets(snapshot)
    quotes.start()
    future, job_id = None, None
    next_holdings, next_report = time.monotonic() + config['holdings_refresh_seconds'], 0
    pending: dict[str, object] = {}
    failures = 0
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            while True:
                current = time.monotonic()
                if current >= next_holdings:
                    try:
                        snapshot = broker.snapshot()
                        quotes.update_targets(snapshot)
                    except Exception as exc:
                        snapshot['refresh_error'] = type(exc).__name__
                        print('보유 수량 갱신 실패:', type(exc).__name__, flush=True)
                    next_holdings = time.monotonic() + config['holdings_refresh_seconds']
                valued = value_snapshot(quotes.overlay(snapshot))
                if snapshot.get('refresh_error'):
                    valued['warnings'].append('보유 수량 갱신 실패; 이전 기준 자료입니다. 새 분석을 보류합니다.')
                write_private(output / 'latest_portfolio.json', valued)
                write_private(output / 'latest_portfolio.md', render_snapshot(valued))
                if future and future.done():
                    try:
                        report = future.result()
                        pending = {'report': report}
                        if job_id:
                            pending['job_id'] = job_id
                            pending['job_status'] = 'complete' if report['complete'] else 'partial'
                    except Exception as exc:
                        print('분석 실패:', type(exc).__name__, flush=True)
                        pending = {'job_id': job_id, 'job_status': 'failed'} if job_id else {}
                    future, job_id = None, None
                state = 'analyzing' if future else 'failed' if snapshot.get('refresh_error') else 'idle'
                # A single request exchanges a fresh snapshot, completion and next job.
                try:
                    response = request(endpoint, timeout=20, headers={'Authorization': 'Bearer ' + token},
                                       body={'snapshot': site_packet(valued), 'worker_status': state, **pending})
                    if not isinstance(response, dict) or not response.get('ok'):
                        raise ValueError('site response')
                    pending = {}
                    failures = 0
                    requested_job = (response.get('job') or {}).get('id')
                    if not future and not snapshot.get('refresh_error') and (requested_job or current >= next_report):
                        job_id = requested_job
                        future = pool.submit(collect_analysis, model, copy.deepcopy(valued), config, output)
                        next_report = time.monotonic() + config['report_interval_seconds']
                        print('분석 시작:', '사이트 요청' if job_id else '정기 분석', flush=True)
                except Exception as exc:
                    failures += 1
                    print('사이트 전송 실패:', type(exc).__name__, '접근 설정과 연결 토큰을 확인하세요.', flush=True)
                time.sleep(min(60, 10 * max(1, min(failures, 6))))
    finally:
        quotes.stop.set()


def main():
    os.umask(0o077)
    load_env(ROOT / '.env')
    config = json.loads((ROOT / 'config.json').read_text())
    if config['holdings_refresh_seconds'] < 10 or config['report_interval_seconds'] < 300:
        raise ValueError('보유 조회 10초, 분석 간격 300초 이상이 필요합니다.')
    with (ROOT / '.run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(config)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('수집기를 종료했습니다.')
    except Exception as exc:
        print('수집기 시작 실패:', type(exc).__name__, 'SETUP.md의 설정을 확인하세요.', file=sys.stderr)
        sys.exit(1)

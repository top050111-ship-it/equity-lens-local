# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations
import argparse
import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from common import now, write_private, load_env, evidence, decimal
from broker import Toss, LiveQuotes, value_snapshot
from collectors import portfolio_bundle, macro_bundle
from agents import Model, Report, Finding, Conflict, run_agents, validate_references

ROOT = Path(__file__).resolve().parent


def md(value):
    return str(value).replace('<', '&lt;').replace('>', '&gt;').replace('|', '\\|').replace('\n', ' ').replace('[', '\\[').replace(']', '\\]')


def render_snapshot(snapshot, demo=False):
    lines = ['# ' + ('[가상 샘플] ' if demo else '') + '포트폴리오 현황', '',
             '보유 수량 조회: ' + snapshot['as_of'],
             '평가 갱신: ' + snapshot['valuation_at'], '', snapshot['scope'], '',
             '주식 평가액 참고치: ' + f"{decimal(snapshot['calculated_equity_value_krw']):,.0f}" + ' KRW',
             '환율: USD/KRW 매매기준율 적용. 수수료·세금·현금 제외.',
             '실시간 연결 상태: ' + snapshot.get('stream_status', 'WebSocket 미사용; REST 조회'), '',
             '| 종목 | 수량 | 가격 | 통화 | 주식 내 비중 | 시세 시각 | 상태 |',
             '|---|---:|---:|---|---:|---|---|']
    for p in snapshot['positions']:
        lines.append('| ' + ' | '.join(map(md, [p['symbol'], p['quantity'], p['price'], p['currency'],
                     f"{p['weight_pct']:.2f}%", p.get('price_at') or '미확인', p['price_quality']])) + ' |')
    lines += ['', *('- ' + md(w) for w in snapshot['warnings'])]
    return '\n'.join(lines) + '\n'


def render_report(result, snapshot, demo=False):
    lines = ['# ' + ('[가상 데이터 · AI 미호출] ' if demo else '') + '주식 리서치 보고서', '',
             '작성: ' + result['created_at'],
             '포트폴리오 기준: ' + snapshot['as_of'], '',
             '**실제 투자 판단에 사용하는 보고서가 아닌 기능 시연입니다.**' if demo else
             '**자동 생성 초안입니다. 출처 ID 검증은 주장의 사실성 검증을 보장하지 않습니다.**', '',
             '분석 단계: ' + ('3단계 완료' if result['complete'] else '일부 단계 실패 — 불완전한 보고서'),
             '주식 내 비중 기준이며 현금·채권·옵션을 포함한 전체 자산 배분이 아닙니다.', '',
             '| 종목 | 주식 내 비중 | 시세 상태 |', '|---|---:|---|']
    for p in snapshot['positions']:
        lines.append(f"| {md(p['symbol'])} | {p['weight_pct']:.2f}% | {md(p['price_quality'])} |")
    if result['warnings']:
        lines += ['', '## 수집 범위와 누락', '', *('- ' + md(w) for w in result['warnings'])]
    labels = {'synthesis': '최종 종합 분석', 'portfolio': '에이전트 1 · 기업/포트폴리오', 'macro': '에이전트 2 · 독립 거시 분석'}
    kinds = {'fact': '자료에 기록된 사실', 'inference': '해석', 'scenario': '조건부 시나리오'}
    for key in ('synthesis', 'portfolio', 'macro'):
        stage = result[key]
        report = stage['report']
        lines += ['', '## ' + labels[key], '', '**상태: ' + stage['status'] + '**', '', md(report['summary'])]
        for f in report['findings']:
            lines += ['', '### ' + md(f['subject']), '',
                      f"{kinds[f['kind']]} · 근거 신뢰도 {f['confidence']} · 출처 {', '.join(f['evidence_ids'])}", '',
                      md(f['claim']), '', '- 반대 설명: ' + md(f['counterpoint']),
                      '- 재검토 조건: ' + md(f['revisit_when'])]
        if report['conflicts']:
            lines += ['', '### 분석 간 충돌', '']
            for c in report['conflicts']:
                lines += ['- **' + md(c['issue']) + '**: ' + md(c['assessment']),
                          '  기업: ' + md(c['portfolio_view']) + ' / 거시: ' + md(c['macro_view']),
                          '  출처: ' + ', '.join(c['evidence_ids'])]
        if report['watchlist']:
            lines += ['', '### 다음 확인 사항', '', *('- ' + md(x) for x in report['watchlist'])]
        if report['limitations']:
            lines += ['', '### 한계', '', *('- ' + md(x) for x in report['limitations'])]
    lines += ['', '## 근거 목록', '']
    for s in result['sources']:
        url = s['url'].replace(' ', '%20').replace('(', '%28').replace(')', '%29')
        lines += [f"- **{s['id']}** [{md(s['title'])}]({url}) — {md(s['provider'])}",
                  '  자료 시각: ' + md(s.get('observed_at') or '미확인') +
                  ' / 수집: ' + md(s['retrieved_at']) + ' / ' + md(s.get('content_scope', s['kind']))]
    return '\n'.join(lines) + '\n'


def save_report(result, snapshot, output, demo=False):
    stamp_str = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d_%H%M%S_%f')
    filename = ('DEMO_' if demo else '') + stamp_str
    write_private(output / 'reports' / (filename + '.json'), result)
    text = render_report(result, snapshot, demo)
    write_private(output / 'reports' / (filename + '.md'), text)
    write_private(output / 'latest_report.md', text)
    write_private(output / 'analysis_status.json', {'status': 'ok' if result['complete'] else 'partial',
                                                   'at': result['created_at'], 'report': filename + '.md'})
    return output / 'reports' / (filename + '.md')


def analyze_cycle(model, snapshot, config, output):
    print("\n[1/3] 📡 포트폴리오 및 글로벌 거시 뉴스 데이터 수집 중...", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        p = pool.submit(portfolio_bundle, snapshot, config)
        m = pool.submit(macro_bundle, config)
        portfolio, macro = p.result(), m.result()
    
    print("[2/3] 🤖 설정한 모델로 에이전트 1·2·3 분석을 진행 중입니다...", flush=True)
    result = run_agents(model, snapshot, portfolio, macro)
    result['model'] = dict(backend=model.backend, model=model.model)
    
    print("[3/3] 💾 리서치 보고서 생성 및 저장 완료!", flush=True)
    return save_report(result, snapshot, output)


class DemoModel:
    def analyze(self, role, payload):
        ids = [s['id'] for s in payload['sources']]
        content = {
            'portfolio': ('가상의 사업 호재를 검토하되 주식 내 비중 집중을 우선 확인합니다.',
                          '기업 분석 예시', '가상 미국 종목의 수주 증가 가정이 이익으로 이어지는지 확인해야 합니다.'),
            'macro': ('가상의 금리 상승 환경에서 할인율과 자금 조달 비용의 영향을 검토합니다.',
                      '거시 분석 예시', '금리 상승을 가정하면 장기 성장 기대에 의존하는 기업의 가치평가에 부담이 될 수 있습니다.'),
            'synthesis': ('사업 호재와 금리 부담이 동시에 존재할 수 있습니다. 현금흐름 개선 여부를 먼저 확인합니다.',
                          '종합 판단 예시', '가상 수주 호재만으로 금리 영향을 상쇄한다고 단정할 수 없습니다.')}
        summary, subject, claim = content[role]
        report = Report(summary=summary, findings=[Finding(subject=subject, claim=claim,
                        kind='scenario', evidence_ids=ids[:2], confidence='low',
                        counterpoint='가상 조건이므로 실제 기업과 시장에 적용할 수 없습니다.',
                        revisit_when='실제 공시와 금리 관측치를 확보한 뒤 재평가합니다.')],
                        conflicts=[Conflict(issue='기업 호재와 거시 부담', portfolio_view='가상 수주 호재',
                                  macro_view='가상 금리 상승', assessment='미해결; 이익·현금흐름 자료 필요',
                                  evidence_ids=ids[:2])] if role == 'synthesis' else [],
                        watchlist=['실제 데이터와 분석 모델 연결 후 검증'],
                        limitations=['가상 자료와 미리 작성한 출력입니다. AI 분석을 실행하지 않았습니다.'])
        return validate_references(report, payload['sources'])


def demo(output):
    snapshot = value_snapshot({'as_of': now(), 'scope': '가상 샘플: 주식만, 현금 제외',
        'positions': [dict(symbol='DEMO-US', name='가상 미국 기업', market='NASDAQ', currency='USD',
                           quantity='10', average_price='90', price='100', price_at=now(), price_source='가상'),
                      dict(symbol='000000', name='가상 한국 기업', market='KOSPI', currency='KRW',
                           quantity='20', average_price='9000', price='10000', price_at=now(), price_source='가상')],
        'fx': {'midRate': '1400', 'validFrom': now(), 'validUntil': '2099-01-01T00:00:00+00:00'},
        'broker_equity_value_krw': '1600000'})
    a = evidence('가상 자료', '가상 기업 수주 증가 가정', 'https://example.com/demo-company',
                 '실제 뉴스가 아닌 기능 시연용 가정', now())
    b = evidence('가상 자료', '가상 금리 상승 가정', 'https://example.com/demo-macro',
                 '실제 경제지표가 아닌 기능 시연용 가정', now())
    result = run_agents(DemoModel(), snapshot, {'sources': [a], 'warnings': []}, {'sources': [b], 'warnings': []})
    write_private(output / 'latest_portfolio.json', snapshot)
    write_private(output / 'latest_portfolio.md', render_snapshot(snapshot, True))
    print(save_report(result, snapshot, output, demo=True))


def live(config, output, watch, stream):
    broker, model = Toss(), Model()
    snapshot = broker.snapshot()
    quotes = LiveQuotes(broker) if stream else None
    if quotes:
        quotes.update_targets(snapshot)
        quotes.start()
    next_holdings, next_report = time.monotonic() + config['holdings_refresh_seconds'], 0
    future = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            while True:
                current = time.monotonic()
                if current >= next_holdings:
                    try:
                        snapshot = broker.snapshot()
                        if quotes:
                            quotes.update_targets(snapshot)
                        write_private(output / 'collection_status.json', {'status': 'ok', 'at': now()})
                    except Exception as exc:
                        snapshot = copy.deepcopy(snapshot)
                        snapshot['refresh_error'] = type(exc).__name__
                        write_private(output / 'collection_status.json', {'status': 'failed', 'at': now(), 'error': type(exc).__name__})
                    next_holdings = time.monotonic() + config['holdings_refresh_seconds']
                valued = value_snapshot(quotes.overlay(snapshot) if quotes else snapshot)
                if snapshot.get('refresh_error'):
                    valued['warnings'].append('보유 수량 갱신 실패; 이전 수량 기준. 새 분석 실행을 보류합니다.')
                write_private(output / 'latest_portfolio.json', valued)
                write_private(output / 'latest_portfolio.md', render_snapshot(valued))
                if future and future.done():
                    try:
                        print('보고서:', future.result(), flush=True)
                    except Exception as exc:
                        write_private(output / 'analysis_status.json', {'status': 'failed', 'at': now(), 'error': type(exc).__name__})
                        print('분석 실패:', type(exc).__name__, flush=True)
                    future = None
                if current >= next_report and future is None and not snapshot.get('refresh_error'):
                    frozen = copy.deepcopy(valued)
                    future = pool.submit(analyze_cycle, model, frozen, config, output)
                    next_report = time.monotonic() + config['report_interval_seconds']
                if not watch:
                    if future:
                        print('보고서:', future.result(), flush=True)
                    break
                time.sleep(2 if stream else min(10, config['holdings_refresh_seconds']))
    finally:
        if quotes:
            quotes.stop.set()


def main():
    parser = argparse.ArgumentParser(description='세 에이전트 투자 리서치: demo 또는 live')
    parser.add_argument('mode', choices=['demo', 'live'])
    parser.add_argument('--watch', action='store_true', help='프로세스가 실행되는 동안 반복')
    parser.add_argument('--stream', action='store_true', help='토스 WebSocket 시세 수신')
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    os.umask(0o077)
    load_env(ROOT / '.env')
    output = Path(args.output or ROOT / ('demo-output' if args.mode == 'demo' else 'data')).resolve()
    if args.mode == 'demo':
        demo(output)
        return
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if config['holdings_refresh_seconds'] < 10 or config['report_interval_seconds'] < 300:
        raise ValueError('holdings >= 10초, report >= 300초로 설정하세요.')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    
    import fcntl
    with (ROOT / '.run.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('이 프로젝트가 이미 실행 중입니다.') from None
        live(config, output, args.watch, args.stream)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('종료했습니다.')
    except Exception as exc:
        print('실행 실패:', type(exc).__name__, '설정·인증서·허용 IP·모델 연결을 확인하세요.', file=sys.stderr)
        sys.exit(1)
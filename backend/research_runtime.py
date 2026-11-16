# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared CLI/worker collection and credential-free historical research."""
from __future__ import annotations
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import cast
from urllib.parse import quote
from common import decimal, now, stamp, age_seconds
from broker import yahoo_symbol
from collectors import yahoo, portfolio_bundle, macro_bundle
from agents import run_agents, failure, progress_wait
from holdings_cache import research_snapshot, research_file


def refresh_public_prices(snapshot, config):
    """Public quote attempts only; retain original timestamps on every failure."""
    result = copy.deepcopy(snapshot)
    from collectors import reset_yahoo_cache
    reset_yahoo_cache()
    notes = result.setdefault('snapshot_warnings', [])
    tasks = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for p in result['positions']:
            try:
                symbol = yahoo_symbol(p, config.get('yahoo_overrides'))
                tasks[pool.submit(yahoo, symbol, 0)] = (p, symbol)
            except ValueError:
                notes.append(p['symbol'] + ': 공개 시세 심볼 매핑 없음; 저장 가격 유지.')
        fx_task = pool.submit(yahoo, 'KRW=X', 0) if any(p['currency'] == 'USD' for p in result['positions']) else None
        for task, (p, symbol) in tasks.items():
            try:
                entries, quote_warnings = task.result()
                source = next((s for s in entries if s['kind'] == 'quote'), None)
                if source is None:
                    notes.extend(quote_warnings)
                    notes.append(p['symbol'] + ': 공개 시세 응답 없음; 저장 가격·시각 유지.')
                    continue
                record = json.loads(source['content'])
                price, at = decimal(record['regularMarketPrice']), source.get('observed_at')
                if (price <= 0 or record.get('currency') != p['currency'] or stamp(at) is None or
                        (age_seconds(at) or 0) < -300):
                    raise ValueError('invalid price/currency/time')
                previous_at, current_at = stamp(p.get('price_at')), stamp(at)
                if previous_at is not None and current_at is not None and current_at < previous_at:
                    raise ValueError('older price')
                p.update(price=str(price), price_at=at, price_source='Yahoo Finance · 저장 수량 기준',
                         price_url='https://finance.yahoo.com/quote/' + quote(symbol, safe='') + '/')
            except Exception as exc:
                notes.append(p['symbol'] + ': 공개 시세 갱신 실패 (' + type(exc).__name__ + '); 저장 가격·시각 유지.')
        if fx_task:
            try:
                entries, quote_warnings = fx_task.result()
                source = next((s for s in entries if s['kind'] == 'quote'), None)
                if source is None:
                    notes.extend(quote_warnings)
                    raise ValueError('no FX quote')
                record = json.loads(source['content'])
                rate, at = decimal(record['regularMarketPrice']), source.get('observed_at')
                current_at = stamp(at)
                if rate <= 0 or record.get('currency') != 'KRW' or current_at is None or (age_seconds(at) or 0) < -300:
                    raise ValueError('invalid FX quote')
                old_at = stamp((result.get('fx') or {}).get('validFrom'))
                if old_at and current_at < old_at:
                    raise ValueError('older FX quote')
                result['fx'] = {'midRate': str(rate), 'validFrom': at,
                                'validUntil': (current_at + timedelta(hours=1)).isoformat(),
                                'source': 'Yahoo Finance USD/KRW; 매매 체결환율 아님'}
            except Exception as exc:
                notes.append('공개 USD/KRW 갱신 실패 (' + type(exc).__name__ + '); 저장 환율 참고치 사용.')
    result['snapshot_warnings'] = list(dict.fromkeys(notes))
    return result


class ResearchPortfolio:
    """No Toss constructor, token, account lookup or credential requirement."""
    def __init__(self, output, config, *, path=None, refresh=True):
        self.path = research_file(output, path)
        self.config, self.refresh = config, refresh
    def snapshot(self):
        print('[시작] 저장 포트폴리오 파일 읽기', flush=True)
        result = research_snapshot(self.path)
        print(f'[완료] 저장 포트폴리오 · {len(result["positions"])}종목', flush=True)
        if self.refresh:
            with progress_wait('공개 시세·환율 조회'):
                return refresh_public_prices(result, self.config)
        result['snapshot_warnings'].append('시세·환율 갱신을 생략했습니다. 저장된 관측값을 사용합니다.')
        return result


def safe_bundle(collector, *args):
    try:
        return collector(*args)
    except Exception as exc:
        return {'sources': [], 'warnings': ['수집 단계 실패 (' + type(exc).__name__ + '); 해당 범위가 누락되었습니다.'],
                'coverage': '수집 실패', 'collected_at': now()}


def cap_sources(bundle, limit):
    result = copy.deepcopy(bundle)
    sources = result.get('sources', [])
    if len(sources) > limit:
        result['sources'] = sources[:limit]
        result.setdefault('warnings', []).append(f'출처 예산으로 {len(sources) - limit}개 자료를 생략했습니다. 분석 범위가 제한됩니다.')
    return result


def collect_research(model, snapshot, config):
    first_call = len(getattr(model, 'telemetry', []))
    with progress_wait('기업·거시 자료 수집'):
        with ThreadPoolExecutor(max_workers=2) as pool:
            p = pool.submit(safe_bundle, portfolio_bundle, snapshot, config)
            m = pool.submit(safe_bundle, macro_bundle, config)
            portfolio, macro = p.result(), m.result()
    print(f'[수집 결과] 기업 근거 {len(portfolio.get("sources", []))}개 / '
          f'거시 근거 {len(macro.get("sources", []))}개', flush=True)
    print('[시작] 독립 거시 분석 → 종목별 심층 분석 → 기업·거시 연결', flush=True)
    try:
        result = cast(dict[str, object], run_agents(model, snapshot, portfolio, macro))
    except Exception as exc:
        message = '분석 파이프라인 구조 검증 실패 (' + type(exc).__name__ + '). 진단 테스트를 실행하세요.'
        result: dict[str, object] = {k: failure(message) for k in ('portfolio', 'macro', 'synthesis')}
        # Keep safely collected sources available for diagnosis/replay even when
        # pipeline structure fails. Conflicting IDs are omitted explicitly.
        from agents import checked_sources
        pool=portfolio.get('sources',[])+macro.get('sources',[])
        try:sources=checked_sources(pool)
        except Exception:sources=[]
        result.update(sources=sources, complete=False, created_at=now(), warnings=[message],
                      diagnostics={'pipeline_error':type(exc).__name__})
    result['warnings'] = list(dict.fromkeys(snapshot.get('warnings', []) + result.get('warnings', [])))
    research_v2 = cast(dict[str, object] | None, result.get('research_v2'))
    if research_v2 is not None:
        research_v2['model_calls'] = copy.deepcopy(getattr(model, 'telemetry', [])[first_call:])
    else:
        diagnostics = cast(dict[str, object], result.setdefault('diagnostics', {}))
        diagnostics['model_calls'] = copy.deepcopy(getattr(model, 'telemetry', [])[first_call:])
    if isinstance(getattr(model,'telemetry',None),list):del model.telemetry[:first_call]
    from model_runtime import runtime_info
    diagnostics = cast(dict[str, object], result.setdefault('diagnostics', {}))
    diagnostics['runtime'] = runtime_info()
    result['model'] = {'backend': getattr(model, 'backend', 'fixture'), 'model': getattr(model, 'model', 'fixture')}
    return result

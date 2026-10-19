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
from agents import run_agents, failure
from holdings_cache import research_snapshot, research_file


def refresh_public_prices(snapshot, config):
    """Public quote attempts only; retain original timestamps on every failure."""
    result = copy.deepcopy(snapshot)
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
                entries, _ = task.result()
                source = next(s for s in entries if s['kind'] == 'quote')
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
                entries, _ = fx_task.result()
                source = next(s for s in entries if s['kind'] == 'quote')
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
        result = research_snapshot(self.path)
        if self.refresh:
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
    with ThreadPoolExecutor(max_workers=2) as pool:
        p = pool.submit(safe_bundle, portfolio_bundle, snapshot, config)
        m = pool.submit(safe_bundle, macro_bundle, config)
        portfolio, macro = cap_sources(p.result(), 200), cap_sources(m.result(), 100)
    try:
        result = cast(dict[str, object], run_agents(model, snapshot, portfolio, macro))
    except Exception as exc:
        message = '분석 파이프라인 구조 검증 실패 (' + type(exc).__name__ + '). 진단 테스트를 실행하세요.'
        result: dict[str, object] = {k: failure(message) for k in ('portfolio', 'macro', 'synthesis')}
        result.update(sources={}, complete=False, created_at=now(), warnings=[message])
    result['warnings'] = list(dict.fromkeys(snapshot.get('warnings', []) + result.get('warnings', [])))
    result['model'] = {'backend': getattr(model, 'backend', 'fixture'), 'model': getattr(model, 'model', 'fixture')}
    return result

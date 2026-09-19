# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public evidence only. News content is untrusted input, never executable instructions."""
from __future__ import annotations
import calendar
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, quote
from xml.etree import ElementTree as ET
from common import request, evidence, plain, now, stamp, age_seconds, decimal, freshness
from broker import yahoo_symbol


def news_time(value):
    result = stamp(value)
    if result:
        return result.isoformat()
    try:
        result = parsedate_to_datetime(value)
        return result.isoformat() if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def filter_evidence(items, lookback_hours):
    unique = {}
    for item in items:
        age = age_seconds(item.get('observed_at'))
        if age is not None and age < -300:
            continue
        if item['kind'] == 'news' and age is not None and age > lookback_hours * 3600:
            continue
        old = unique.get(item['id'])
        if old:
            item['symbols'] = sorted(set(old.get('symbols', []) + item.get('symbols', [])))
        unique[item['id']] = item
    return sorted(unique.values(), key=lambda x: x.get('observed_at') or '', reverse=True)


def rss(provider, url, limit=12):
    payload = request(url, raw=True)
    if payload is None:
        raise ValueError('empty RSS response')
    root = ET.fromstring(payload)
    rows = []
    for entry in root.iter():
        if entry.tag.split('}')[-1] not in ('item', 'entry'):
            continue
        values = {child.tag.split('}')[-1]: child for child in entry}
        def text(key):
            e = values.get(key)
            return ''.join(e.itertext()) if e is not None else ''
        link = text('link')
        if not link and values.get('link') is not None:
            link = values['link'].get('href', '')
        published = next((text(k) for k in ('pubDate', 'published', 'date', 'updated') if text(k)), '')
        try:
            rows.append(evidence(provider, text('title'), link, text('description') or text('summary'),
                                 news_time(published), content_scope='RSS 제목·요약; 원문 미확인'))
        except ValueError:
            continue
    return sorted(rows, key=lambda x: x.get('observed_at') or '', reverse=True)[:limit]


def yahoo(symbol, news_count=8):
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    results, errors = [], []
    try:
        info = ticker.get_info()
        price, at = info.get('regularMarketPrice'), news_time(info.get('regularMarketTime'))
        if price is not None:
            results.append(evidence('Yahoo Finance', f'{symbol} 정규장 시세',
                                    'https://finance.yahoo.com/quote/' + quote(symbol, safe='') + '/',
                                    json.dumps({k: info.get(k) for k in (
                                        'regularMarketPrice', 'regularMarketPreviousClose', 'regularMarketVolume',
                                        'currency', 'marketState', 'exchangeDataDelayedBy')}, ensure_ascii=False),
                                    at, kind='quote', symbols=[symbol],
                                    quality=freshness(at), content_scope='정규장 관측값; 실시간 보장 없음'))
        else:
            errors.append(f'{symbol}: Yahoo 시세 없음')
    except Exception as exc:
        errors.append(f'{symbol}: Yahoo 시세 실패 ({type(exc).__name__})')
    try:
        for raw in ticker.get_news(count=news_count):
            item = raw.get('content') or raw
            url = (item.get('canonicalUrl') or {}).get('url') or (item.get('clickThroughUrl') or {}).get('url') or item.get('link')
            provider = (item.get('provider') or {}).get('displayName') or item.get('publisher') or 'Yahoo Finance'
            if not url or not item.get('title'):
                continue
            try:
                results.append(evidence(provider, item['title'], url, item.get('summary') or '',
                                        news_time(item.get('pubDate') or item.get('providerPublishTime')),
                                        symbols=[symbol], content_scope='Yahoo 제공 제목·요약; 종목 관련성 추가 확인 필요'))
            except ValueError:
                continue
    except Exception as exc:
        errors.append(f'{symbol}: Yahoo 뉴스 실패 ({type(exc).__name__})')
    return results, errors


def fred(series_id, key):
    params = {'series_id': series_id, 'api_key': key, 'file_type': 'json'}
    metadata = request('https://api.stlouisfed.org/fred/series?' + urlencode(params))
    if not isinstance(metadata, dict) or not metadata.get('seriess'):
        raise ValueError('no FRED series metadata')
    meta = metadata['seriess'][0]
    observations = request('https://api.stlouisfed.org/fred/series/observations?' + urlencode({
        **params, 'sort_order': 'desc', 'limit': 20}))
    if not isinstance(observations, dict) or not isinstance(observations.get('observations'), list):
        raise ValueError('invalid FRED observations response')
    obs = observations['observations']
    usable = [x for x in obs if x.get('value') not in (None, '.')][:3]
    if not usable:
        raise ValueError('no FRED observations')
    details = dict(series_id=series_id, units=meta.get('units'), frequency=meta.get('frequency'),
                   seasonal_adjustment=meta.get('seasonal_adjustment'),
                   last_updated=meta.get('last_updated'), observations=usable,
                   note='date는 지표 대상 기간입니다. 발표 시각·컨센서스가 아닙니다. 수정치가 포함될 수 있습니다.')
    return [evidence('FRED', meta['title'], 'https://fred.stlouisfed.org/series/' + series_id,
                     json.dumps(details, ensure_ascii=False), None, kind='indicator',
                     observation_period=usable[0]['date'], content_scope='공식 시계열 최신 조회; 과거 빈티지 아님')]


def portfolio_bundle(snapshot, config):
    sources, warnings = [], list(snapshot['warnings'])
    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for p in snapshot['positions']:
            sources.append(evidence('Toss Securities', p['symbol'] + ' 시세',
                                    'https://openapi.tossinvest.com/api/v1/prices?symbols=' + quote(p['symbol']),
                                    f"{p['price']} {p['currency']}; {p['price_source']}",
                                    p.get('price_at'), kind='quote', symbols=[p['symbol']],
                                    quality=p['price_quality']))
            try:
                symbol = yahoo_symbol(p, config.get('yahoo_overrides'))
                jobs[pool.submit(yahoo, symbol)] = symbol
            except ValueError:
                warnings.append(p['symbol'] + ': Yahoo 심볼 매핑을 설정해야 합니다.')
        for future in as_completed(jobs):
            try:
                items, errs = future.result()
                sources.extend(items)
                warnings.extend(errs)
            except Exception as exc:
                warnings.append(jobs[future] + ': 수집 실패 (' + type(exc).__name__ + ')')
    return dict(sources=filter_evidence(sources, config['news_lookback_hours']), warnings=warnings,
                collected_at=now(), coverage='보유 종목 시세와 Yahoo 뉴스·요약; 공시·원문 자동 검증 미구현')


def macro_bundle(config):
    # This function has no snapshot, account, positions or Agent 1 input.
    sources, warnings, jobs = [], [], {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for feed in config['macro_feeds']:
            jobs[pool.submit(rss, feed['name'], feed['url'])] = (feed['name'], False)
        for symbol in config['macro_symbols']:
            jobs[pool.submit(yahoo, symbol, 4)] = (symbol, True)
        key = os.environ.get('FRED_API_KEY')
        if key:
            for series in config['fred_series']:
                jobs[pool.submit(fred, series, key)] = (series, False)
        else:
            warnings.append('FRED_API_KEY 미설정: 정량 경제지표 수집 생략. 뉴스로 수치를 추정하지 마세요.')
        for future in as_completed(jobs):
            name, is_yahoo = jobs[future]
            try:
                value = future.result()
                if is_yahoo:
                    items, errs = value
                    sources.extend(items)
                    warnings.extend(errs)
                else:
                    sources.extend(value)
            except Exception as exc:
                warnings.append(name + ': 수집 실패 (' + type(exc).__name__ + ')')
    return dict(sources=filter_evidence(sources, config['news_lookback_hours']), warnings=warnings,
                collected_at=now(), coverage='미국·유로존·한국·일본 공식 피드 및 글로벌 시장 지표. 모든 국가를 망라하지 않음.')

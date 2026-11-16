# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public evidence only. News content is untrusted input, never executable instructions."""
from __future__ import annotations
import calendar
import json
import os
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from defusedxml import ElementTree as ET
from common import evidence, plain, now, stamp, age_seconds, decimal, freshness
from broker import yahoo_symbol


def request(url, *, raw=False, timeout=25, attempts=3):
    """Fetch JSON or raw bytes without depending on a missing common symbol."""
    for attempt in range(max(1, attempts)):
        try:
            req = Request(url, headers={'User-Agent': 'EquityLens/1.0'})
            with urlopen(req, timeout=timeout) as response:
                payload = response.read()
            return payload if raw else json.loads(payload)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            if attempt + 1 == max(1, attempts):
                return None


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


def rss(provider, url, limit=12, timeout=25):
    payload = request(url, raw=True, timeout=timeout, attempts=1)
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


def _yahoo_fetch(symbol, news_count=8, *, news_only=False):
    # One chart request replaces get_info's multiple cookie/fundamentals requests.
    results, errors = [], []
    if not news_only:
        try:
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + quote(symbol, safe='') + '?interval=1d&range=5d'
            payload = request(url, timeout=8, attempts=1)
            rows = ((payload or {}).get('chart') or {}).get('result') or []
            info = rows[0]['meta'] if rows else {}
            price, at = info.get('regularMarketPrice'), news_time(info.get('regularMarketTime'))
            if price is None or decimal(price)<=0 or not info.get('currency') or not at:
                raise ValueError('no valid chart quote')
            details = {'regularMarketPrice': price, 'regularMarketPreviousClose': info.get('chartPreviousClose'),
                       'regularMarketVolume': info.get('regularMarketVolume'), 'currency': info['currency']}
            results.append(evidence('Yahoo Finance', f'{symbol} 시세',
                'https://finance.yahoo.com/quote/' + quote(symbol, safe='') + '/',
                json.dumps(details, ensure_ascii=False), at, kind='quote', symbols=[symbol],
                quality=freshness(at), content_scope='Yahoo 공개 chart 관측값; 실시간·제공 지속성 보장 없음'))
        except Exception as exc:
            errors.append(f'{symbol}: Yahoo chart 시세 실패 ({type(exc).__name__})')
    if news_count > 0:
        try:
            # RSS failure must not discard a quote already received.
            items = rss('Yahoo Finance', 'https://finance.yahoo.com/rss/headline?' + urlencode({'s': symbol}),
                        limit=news_count, timeout=8)
            for item in items:
                item['symbols'] = [symbol]
            results.extend(items)
        except Exception as exc:
            errors.append(f'{symbol}: Yahoo RSS 뉴스 실패 ({type(exc).__name__})')
    return results, errors


_YAHOO_RESULT = "EQUITY_YAHOO_RESULT="
_YAHOO_CACHE = {}


def reset_yahoo_cache():
    _YAHOO_CACHE.clear()


def _yahoo_worker(symbol, news_count=8, *, news_only=False):
    """Bound the entire Yahoo lookup in a separate, terminable process.

    Thread future timeouts alone cannot stop blocked network calls. Keep
    the worker independent of holdings and preserve the existing return shape.
    """
    try:
        timeout = float(os.getenv('EQUITY_YAHOO_TIMEOUT', '30'))
        if not 5 <= timeout <= 120:
            raise ValueError('invalid Yahoo timeout')
        count = int(news_count)
        if not 0 <= count <= 50:
            raise ValueError('invalid news count')
        worker = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), '--yahoo-news-worker' if news_only else '--yahoo-worker', str(symbol), str(count)],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout, check=False,
        )
        if worker.returncode != 0:
            return [], [f'{symbol}: Yahoo 조회 작업 실패; 해당 공개 자료 생략']
        marker = worker.stdout.rfind(_YAHOO_RESULT)
        if marker < 0:
            return [], [f'{symbol}: Yahoo 조회 결과 형식 오류; 해당 공개 자료 생략']
        payload = json.loads(worker.stdout[marker + len(_YAHOO_RESULT):])
        if not isinstance(payload, dict):
            raise ValueError('invalid worker payload')
        items, warnings = payload['sources'], payload['warnings']
        if (not isinstance(items, list) or not isinstance(warnings, list)
                or not all(isinstance(x, dict) for x in items)
                or not all(isinstance(x, str) for x in warnings)):
            raise ValueError('invalid worker output types')
        return items, warnings
    except subprocess.TimeoutExpired:
        return [], [f'{symbol}: Yahoo 시세·뉴스 조회 {timeout:g}초 제한 초과; 해당 공개 자료 생략']
    except Exception as exc:
        return [], [f'{symbol}: Yahoo 조회 실패 ({type(exc).__name__}); 해당 공개 자료 생략']


def yahoo(symbol, news_count=8):
    import time
    import copy
    key = (str(symbol), int(news_count))
    cached = _YAHOO_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < 300:
        return copy.deepcopy(cached[1])
    quote_key = (str(symbol), 0)
    quote_cached = _YAHOO_CACHE.get(quote_key)
    if news_count and quote_cached and time.monotonic() - quote_cached[0] < 300:
        news, warnings = _yahoo_worker(symbol, news_count, news_only=True)
        result = (copy.deepcopy(quote_cached[1][0]) + news,
                  list(dict.fromkeys(quote_cached[1][1] + warnings)))
    else:
        result = _yahoo_worker(symbol, news_count)
    _YAHOO_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result


def fred(series_id, key):
    params = {'series_id': series_id, 'api_key': key, 'file_type': 'json'}
    metadata = request('https://api.stlouisfed.org/fred/series?' + urlencode(params))
    if not isinstance(metadata, dict) or not metadata.get('seriess'):
        raise ValueError('no FRED series metadata')
    meta = metadata['seriess'][0]
    observations = request('https://api.stlouisfed.org/fred/series/observations?' + urlencode({
        **params, 'sort_order': 'desc', 'limit': 40}))
    if not isinstance(observations, dict) or not isinstance(observations.get('observations'), list):
        raise ValueError('invalid FRED observations response')
    obs = observations['observations']
    usable = [x for x in obs if x.get('value') not in (None, '.')][:3]
    if not usable:
        raise ValueError('no FRED observations')
    all_usable = [x for x in obs if x.get('value') not in (None, '.') ]
    computed = []
    if len(all_usable) >= 2:
        a, b = all_usable[:2]
        computed.append({'name': '직전 유효 관측 대비 수준 차이(원 단위)', 'value': str(decimal(a['value'])-decimal(b['value'])), 'periods': [a['date'], b['date']]})
    if series_id in ('CPIAUCSL', 'PCEPI', 'PCEPILFE'):
        current = all_usable[0]
        year_ago = str(int(current['date'][:4])-1) + current['date'][4:]
        previous = next((x for x in all_usable if x['date'] == year_ago), None)
        if previous and decimal(previous['value']) > 0:
            computed.append({'name': '지수 기준 전년 동월 대비 증감률(%)', 'value': str(((decimal(current['value'])/decimal(previous['value'])-1)*100).quantize(decimal('0.01'))), 'inputs': [current, previous], 'formula': '(current/prior-1)*100; 컨센서스 대비 서프라이즈 아님'})
    from evidence_grounding import FRED_LABELS
    details = dict(series_id=series_id, canonical_label_ko=FRED_LABELS.get(series_id, meta['title']), units=meta.get('units'), frequency=meta.get('frequency'),
                   seasonal_adjustment=meta.get('seasonal_adjustment'),
                   last_updated=meta.get('last_updated'), observations=usable, code_calculations=computed,
                   note='date는 지표 대상 기간입니다. 발표 시각·컨센서스가 아닙니다. 수정치가 포함될 수 있습니다.')
    source=evidence('FRED', meta['title'], 'https://fred.stlouisfed.org/series/' + series_id,
                    details, None, kind='indicator',structured=True,observation_period=usable[0]['date'],
                    content_scope='공식 시계열 최신 조회; 과거 빈티지 아님')
    return [source]


def portfolio_bundle(snapshot, config):
    sources, warnings = [], list(snapshot['warnings'])
    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for p in snapshot['positions']:
            provider = 'Yahoo Finance' if p.get('price_source', '').startswith('Yahoo') else 'Toss Securities · 관측 시각 기준'
            source_url = p.get('price_url') or ('https://openapi.tossinvest.com/api/v1/prices?symbols=' + quote(p['symbol']))
            sources.append(evidence(provider, p['symbol'] + ' 시세', source_url,
                                    f"{p['price']} {p['currency']}; {p['price_source']}",
                                    p.get('price_at'), kind='quote', symbols=[p['symbol']],
                                    quality=p['price_quality']))
            try:
                symbol = yahoo_symbol(p, config.get('yahoo_overrides'))
                jobs[pool.submit(yahoo, symbol)] = (symbol, p['symbol'])
            except ValueError:
                warnings.append(p['symbol'] + ': Yahoo 심볼 매핑을 설정해야 합니다.')
        for future in as_completed(jobs):
            try:
                items, errs = future.result()
                for item in items:
                    item['symbols'] = sorted(set(item.get('symbols', []) + [jobs[future][1]]))
                sources.extend(items)
                warnings.extend(errs)
            except Exception as exc:
                warnings.append(jobs[future][0] + ': 수집 실패 (' + type(exc).__name__ + ')')
    from official_sources import collect_official
    official = collect_official(snapshot, config)
    sources.extend(official['sources'])
    warnings.extend(official['warnings'])
    return dict(sources=filter_evidence(sources, config['news_lookback_hours']), warnings=warnings,
                dossiers=official['dossiers'], research_settings=config.get('research', {}),
                collected_at=now(), coverage='SEC·DART 공시 재무/본문 발췌, 코드 계산, 시세 및 뉴스 요약; 누락은 종목별 표시')


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


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] in ('--yahoo-worker', '--yahoo-news-worker'):
        try:
            items, warnings = _yahoo_fetch(sys.argv[2], int(sys.argv[3]), news_only=sys.argv[1] == '--yahoo-news-worker')
            print(_YAHOO_RESULT + json.dumps({'sources': items, 'warnings': warnings}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(_YAHOO_RESULT + json.dumps({'sources': [], 'warnings': [type(exc).__name__]}), flush=True)
    else:
        raise SystemExit('Run app.py or research_tools.py instead.')

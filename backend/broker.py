# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Toss Securities REST 1.2.17 / WebSocket 1.2.2, checked 2026-09-16.
Only explicitly listed read operations are exposed; no order API exists here.
"""
from __future__ import annotations
import asyncio
import copy
import json
import os
import random
import threading
import time
from decimal import Decimal
from common import request, NetworkError, now, decimal, stamp, age_seconds, freshness

BASE = 'https://openapi.tossinvest.com'
READ_PATHS = {'/api/v1/accounts', '/api/v1/holdings', '/api/v1/stocks',
              '/api/v1/prices', '/api/v1/exchange-rate'}


class Toss:
    def __init__(self):
        self.client_id = os.environ.get('TOSS_CLIENT_ID', '')
        self.secret = os.environ.get('TOSS_CLIENT_SECRET', '')
        if not self.client_id or not self.secret:
            raise ValueError('TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 설정이 필요합니다.')
        self.account = os.environ.get('TOSS_ACCOUNT_SEQ', '')
        self._token = ''
        self._expires = 0
        self._lock = threading.RLock()
        self._last_request = 0

    def token(self, renew=False):
        with self._lock:
            if renew or time.monotonic() >= self._expires - 60:
                body = request(BASE + '/oauth2/token', form=True, body={
                    'grant_type': 'client_credentials', 'client_id': self.client_id,
                    'client_secret': self.secret})
                if not isinstance(body, dict) or not body.get('access_token'):
                    raise ValueError('토큰 응답이 올바르지 않습니다.')
                self._token = body['access_token']
                self._expires = time.monotonic() + int(body['expires_in'])
            return self._token

    def get(self, path, params=None, account=False):
        from urllib.parse import urlencode
        if path not in READ_PATHS:
            raise ValueError('Unsupported read operation')
        with self._lock:
            for attempt in range(2):
                token = self.token()
                time.sleep(max(0, .4 - (time.monotonic() - self._last_request)))
                headers = {'Authorization': 'Bearer ' + token}
                if account:
                    headers['X-Tossinvest-Account'] = str(self.account)
                try:
                    body = request(BASE + path + ('?' + urlencode(params) if params else ''), headers=headers)
                    if not isinstance(body, dict) or 'result' not in body:
                        raise ValueError('API 응답에 result가 없습니다.')
                    return body['result']
                except NetworkError as exc:
                    if exc.status == 401 and attempt == 0:
                        self.token(renew=True)
                    else:
                        raise
                finally:
                    self._last_request = time.monotonic()

    def snapshot(self):
        if not self.account:
            account_result = self.get('/api/v1/accounts')
            if not isinstance(account_result, list):
                raise ValueError('계좌 응답이 올바르지 않습니다.')
            accounts = [x for x in account_result if x['accountType'] == 'BROKERAGE']
            if len(accounts) != 1:
                raise ValueError('복수 계좌 또는 중개 계좌 없음: TOSS_ACCOUNT_SEQ를 직접 지정하세요.')
            self.account = str(accounts[0]['accountSeq'])
        holdings = self.get('/api/v1/holdings', account=True)
        if not isinstance(holdings, dict) or not isinstance(holdings.get('items'), list):
            raise ValueError('보유 종목 응답이 올바르지 않습니다.')
        symbols = [x['symbol'] for x in holdings['items']]
        meta, prices = {}, {}
        for start in range(0, len(symbols), 100):
            params = {'symbols': ','.join(symbols[start:start + 100])}
            stock_items = self.get('/api/v1/stocks', params)
            price_items = self.get('/api/v1/prices', params)
            if not isinstance(stock_items, list) or not isinstance(price_items, list):
                raise ValueError('주식 정보 응답이 올바르지 않습니다.')
            meta.update({x['symbol']: x for x in stock_items})
            prices.update({x['symbol']: x for x in price_items})
        fx = None
        if any(x['currency'] == 'USD' for x in holdings['items']):
            fx = self.get('/api/v1/exchange-rate', {'baseCurrency': 'USD', 'quoteCurrency': 'KRW'})
        rows = []
        for item in holdings['items']:
            symbol = item['symbol']
            m, p = meta.get(symbol, {}), prices.get(symbol, {})
            rows.append(dict(symbol=symbol, name=item['name'], market=m.get('market', 'UNKNOWN'),
                             currency=item['currency'], quantity=item['quantity'],
                             average_price=item['averagePurchasePrice'],
                             price=p.get('lastPrice', item['lastPrice']),
                             price_at=p.get('timestamp'), price_source='Toss REST',
                             broker_value=item['marketValue']['amount']))
        # Account identifiers and credentials never enter the persisted snapshot.
        return dict(as_of=now(), positions=rows, fx=fx,
                    broker_equity_value_krw=holdings['marketValue']['amount']['krw'],
                    scope='국내·미국 보유 주식만; 현금·채권·옵션 제외')


def yahoo_symbol(position, overrides=None):
    symbol = position['symbol']
    if overrides and symbol in overrides:
        return overrides[symbol]
    market = position['market']
    if market == 'KOSPI':
        return symbol + '.KS'
    if market == 'KOSDAQ':
        return symbol + '.KQ'
    if market in ('NYSE', 'NASDAQ', 'AMEX'):
        return symbol.replace('.', '-')
    raise ValueError('Yahoo symbol mapping required')


def value_snapshot(snapshot):
    result = copy.deepcopy(snapshot)
    warnings = []
    total = Decimal(0)
    fx = result.get('fx')
    usdkrw = decimal(fx['midRate']) if fx else None
    if usdkrw is not None and usdkrw <= 0:
        raise ValueError('invalid FX rate')
    if fx:
        valid_until = stamp(fx.get('validUntil'))
        current_time = stamp(now())
        if valid_until is None or (current_time is not None and valid_until < current_time):
            warnings.append('환율 유효기간 경과 또는 미확인; 원화 환산은 참고치입니다.')
    for p in result['positions']:
        qty, price = decimal(p['quantity']), decimal(p['price'])
        if qty < 0 or price < 0:
            raise ValueError('negative long-only position')
        p['price_quality'] = freshness(p.get('price_at'))
        if p['price_quality'] != '최근 관측':
            warnings.append(f"{p['symbol']}: {p['price_quality']}")
        rate = Decimal(1) if p['currency'] == 'KRW' else usdkrw if p['currency'] == 'USD' else None
        if rate is None:
            raise ValueError('missing/unsupported FX: cannot calculate complete equity weights')
        value = qty * price * rate
        p['value_krw'] = str(value)
        total += value
    for p in result['positions']:
        p['weight_pct'] = float(decimal(p['value_krw']) / total * 100) if total > 0 else 0
    result['calculated_equity_value_krw'] = str(total)
    result['warnings'] = warnings
    result['valuation_at'] = now()
    return result


def analysis_positions(snapshot):
    # Allowlist, not a denylist: never forward monetary amounts, quantities or cost basis.
    return [{k: p[k] for k in ('symbol', 'name', 'market', 'currency', 'weight_pct', 'price_quality')}
            for p in snapshot['positions']]


class LiveQuotes:
    def __init__(self, broker):
        self.broker = broker
        self._lock = threading.Lock()
        self.targets, self.prices = {}, {}
        self.status = '대기'
        self.stop = threading.Event()
        self.thread = None

    def update_targets(self, snapshot):
        with self._lock:
            self.targets = {p['symbol']: ('kr' if p['currency'] == 'KRW' else 'us')
                            for p in snapshot['positions']}

    def overlay(self, snapshot):
        result = copy.deepcopy(snapshot)
        with self._lock:
            prices, status = copy.deepcopy(self.prices), self.status
        for p in result['positions']:
            tick = prices.get(p['symbol'])
            tick_time = stamp(tick['timestamp']) if tick else None
            rest_time = stamp(p.get('price_at'))
            if tick and tick_time and (not rest_time or tick_time >= rest_time):
                p.update(price=tick['price'], price_at=tick['timestamp'], price_source='Toss WebSocket')
        result['stream_status'] = status
        return result

    def start(self):
        # Fail early if the optional dependency isn't installed.
        from websockets.asyncio.client import connect  # noqa: F401
        self.thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True)
        self.thread.start()

    async def _run(self):
        from websockets.asyncio.client import connect
        delay = 1
        rejected = set()  # Persist invalid subscription targets across reconnects.
        while not self.stop.is_set():
            try:
                token = await asyncio.to_thread(self.broker.token)
                async with connect('wss://openapi-ws.tossinvest.com/ws/v1',
                                   additional_headers={'Authorization': 'Bearer ' + token},
                                   ping_interval=30, ping_timeout=30, open_timeout=20) as ws:
                    sent, ping_at, declared_at = None, time.monotonic(), 0
                    while not self.stop.is_set():
                        with self._lock:
                            targets = dict(self.targets)
                        if len(targets) > 100:
                            raise ValueError('WS supports at most 100 symbols in this prototype')
                        if targets != sent and time.monotonic() - declared_at > 1:
                            declaration = [{'type': 'trade:' + market,
                                            'codes': [s for s, m in targets.items()
                                                      if m == market and f'trade:{m}:{s}' not in rejected]}
                                           for market in ('kr', 'us')]
                            await ws.send(json.dumps([d for d in declaration if d['codes']]))
                            sent, declared_at = targets, time.monotonic()
                        if time.monotonic() - ping_at >= 60:
                            await ws.send('PING')
                            ping_at = time.monotonic()
                        try:
                            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                        except asyncio.TimeoutError:
                            continue
                        if message.get('type') == 'subscriptions':
                            rejected.update(x['target'] for x in message.get('rejected', []))
                            delay = 1
                            with self._lock:
                                self.status = f"연결됨; 거부된 구독 {len(rejected)}건"
                        elif message.get('type') == 'error':
                            raise NetworkError()
                        elif message.get('type') == 'message' and message.get('topic', '').startswith('trade:'):
                            _, market, symbol = message['topic'].split(':', 2)
                            tick = message['data']
                            if targets.get(symbol) != market or not stamp(tick.get('timestamp')):
                                continue
                            expected_currency = 'KRW' if market == 'kr' else 'USD'
                            if tick.get('currency') != expected_currency or decimal(tick['price']) <= 0:
                                continue
                            if (age_seconds(tick['timestamp']) or 0) < -300:
                                continue
                            with self._lock:
                                old = self.prices.get(symbol)
                                tick_stamp = stamp(tick['timestamp'])
                                old_stamp = stamp(old['timestamp']) if old else None
                                if (not old or
                                        (tick_stamp is not None and
                                         (old_stamp is None or tick_stamp >= old_stamp))):
                                    self.prices[symbol] = tick
            except Exception as exc:
                # Exception strings may contain headers; expose only class and status.
                status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status_code == 401:
                    try:
                        await asyncio.to_thread(self.broker.token, True)
                    except Exception:
                        pass
                with self._lock:
                    self.status = '연결 끊김; 재시도 중 (' + type(exc).__name__ + ')'
                await asyncio.sleep(min(delay, 30) + random.random())
                delay = min(delay * 2, 30)

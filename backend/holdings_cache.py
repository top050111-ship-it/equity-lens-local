# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validated historical research portfolios, independent of a broker connection."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from common import now, stamp, decimal, write_private, age_seconds
from broker import Toss, value_snapshot


class HoldingsCacheError(ValueError):
    pass


def positive_snapshot(raw):
    try:
        if not isinstance(raw, dict) or not isinstance(raw.get('positions'), list):
            raise ValueError('positions must be a list')
        if stamp(raw.get('as_of')) is None or (age_seconds(raw['as_of']) or 0) < -300:
            raise ValueError('invalid/future holdings timestamp')
        if raw.get('holdings_source') == 'demo' or '가상' in str(raw.get('scope', '')):
            raise ValueError('demo data is not a real portfolio')
        rows, symbols = [], set()
        allowed = ('symbol', 'name', 'market', 'currency', 'quantity', 'average_price',
                   'price', 'price_at', 'price_source', 'price_url', 'broker_value')
        for p in raw['positions']:
            if not isinstance(p, dict):
                raise ValueError('position must be an object')
            qty = decimal(p['quantity'])
            if qty < 0:
                raise ValueError('negative quantity')
            if qty == 0:
                continue
            symbol = p['symbol']
            if (not isinstance(symbol, str) or not symbol or len(symbol) > 40 or
                    any(c.isspace() for c in symbol) or symbol in symbols or symbol.startswith('DEMO')):
                raise ValueError('invalid/duplicate symbol')
            if p['currency'] not in ('KRW', 'USD') or decimal(p['price']) < 0:
                raise ValueError('invalid currency/price')
            for key in ('name', 'market', 'price_source'):
                if not isinstance(p[key], str):
                    raise ValueError('invalid position metadata')
            if '가상' in p['price_source']:
                raise ValueError('synthetic source')
            if p.get('price_at') is not None and (stamp(p['price_at']) is None or
                    (age_seconds(p['price_at']) or 0) < -300):
                raise ValueError('invalid/future quote timestamp')
            rows.append({k: copy.deepcopy(p[k]) for k in allowed if k in p})
            symbols.add(symbol)
        if len(rows) > 100:
            raise ValueError('too many positions')
        result = {k: copy.deepcopy(raw[k]) for k in ('as_of', 'fx', 'scope', 'broker_equity_value_krw') if k in raw}
        result.setdefault('scope', '저장된 주식 포트폴리오; 현금 제외')
        result['positions'] = rows
        value_snapshot(result)
        return result
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HoldingsCacheError('보유 내역 형식·시각·수량·통화·가격·환율을 확인하세요.') from exc


def read_snapshot_file(path):
    """Accept an explicit legacy snapshot or v1/v2 envelope; never guess a schema."""
    path = Path(path).expanduser()
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError('file too large')
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            raise ValueError('file must be an object')
        if 'snapshot' in raw:
            if raw.get('version') not in (1, 2):
                raise ValueError('unknown cache version')
            if raw.get('version') == 2 and raw.get('checksum') != snapshot_digest(raw['snapshot']):
                raise ValueError('checksum mismatch')
            result = positive_snapshot(raw['snapshot'])
            envelope = raw
        else:
            if raw.get('refresh_error') or raw.get('holdings_source') in ('cached', 'research', 'demo'):
                raise ValueError('a successful original snapshot is required')
            result, envelope = positive_snapshot(raw), None
        if not result['positions']:
            raise ValueError('no positive holdings')
        return result, envelope
    except HoldingsCacheError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HoldingsCacheError('저장 포트폴리오를 읽을 수 없습니다: ' + path.name) from exc


def snapshot_digest(snapshot):
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def research_snapshot(path, *, reason='저장 포트폴리오 연구 모드', checked_at=None):
    result, _ = read_snapshot_file(path)
    original_time = result['as_of']
    age = max(0, age_seconds(original_time) or 0) / 86400
    result.update(holdings_source='research', account_checked_at=checked_at, fallback_reason=reason,
                  broker_equity_value_krw=None,
                  scope='과거 저장 수량을 사용한 연구 포트폴리오; 현재 증권계좌 잔고 아님',
                  snapshot_warnings=[reason + '; 현재 실제 보유 내역이 아닙니다.',
                      f'보유 수량 기준: {original_time} (약 {age:.1f}일 전). 이관 후 매매·수량 변화는 반영하지 않습니다.'])
    return result


def research_file(output, explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    frozen = Path(output) / 'frozen_portfolio.json'
    if frozen.exists():
        read_snapshot_file(frozen)
        return frozen
    for name in ('last_nonempty_portfolio.json', 'last_nonempty_portfolio.json.bak'):
        path = Path(output) / name
        if path.exists():
            try:
                read_snapshot_file(path)
                return path
            except HoldingsCacheError:
                continue
    raise HoldingsCacheError('저장 포트폴리오가 없습니다. 이관 전 --snapshot-only --freeze-holdings를 실행하거나 --holdings로 백업을 지정하세요.')


class CachedToss(Toss):
    def __init__(self, cache_path, *, simulate_empty=False):
        super().__init__()
        self.cache_path = Path(cache_path)
        self.simulate_empty = simulate_empty

    def _account_key(self):
        if not self.account:
            raise HoldingsCacheError('TOSS_ACCOUNT_SEQ를 지정하거나 계좌 조회를 완료하세요.')
        return hashlib.sha256(('toss-holdings-v2:' + self.client_id + ':' + str(self.account)).encode()).hexdigest()

    def _matches(self, env):
        if env.get('version') == 1:
            old = hashlib.sha256(('toss-holdings:' + str(self.account)).encode()).hexdigest()
            return env.get('account_key') == old
        return env.get('account_key') == self._account_key()

    def _save(self, snapshot):
        if snapshot.get('holdings_source') in ('cached', 'research', 'demo') or snapshot.get('refresh_error'):
            raise HoldingsCacheError('재사용·오류 자료로 정상 캐시를 덮어쓰지 않습니다.')
        clean = positive_snapshot(snapshot)
        if not clean['positions']:
            raise HoldingsCacheError('정상 캐시에는 보유 주식이 필요합니다.')
        envelope = {'version': 2, 'account_key': self._account_key(), 'saved_at': now(),
                    'checksum': snapshot_digest(clean), 'snapshot': clean}
        # Rotate only a valid matching prior cache; preserve a good backup if primary is corrupt.
        if self.cache_path.exists():
            try:
                _, previous = read_snapshot_file(self.cache_path)
                if previous and self._matches(previous):
                    write_private(self.cache_path.with_suffix('.json.bak'), previous)
            except HoldingsCacheError:
                pass
        write_private(self.cache_path, envelope)

    def freeze(self, *, replace=False):
        """Keep a full, explicit baseline that partial transfers cannot overwrite."""
        target = self.cache_path.with_name('frozen_portfolio.json')
        if target.exists() and not replace:
            raise HoldingsCacheError('고정 보관본이 이미 있습니다. 갱신하려면 --replace-frozen을 함께 지정하세요.')
        _, envelope = read_snapshot_file(self.cache_path)
        if not envelope or not self._matches(envelope):
            raise HoldingsCacheError('현재 계좌와 일치하는 정상 캐시가 필요합니다.')
        write_private(target, envelope)
        return target

    def _load(self):
        frozen = self.cache_path.with_name('frozen_portfolio.json')
        paths = [frozen] if frozen.exists() else [self.cache_path, self.cache_path.with_suffix('.json.bak')]
        error = None
        for path in paths:
            try:
                snapshot, env = read_snapshot_file(path)
                if not env or not self._matches(env):
                    raise HoldingsCacheError('현재 토스 인증/계좌와 캐시가 다릅니다. 연구 모드는 app.py cached로 실행하세요.')
                return snapshot, path
            except HoldingsCacheError as exc:
                error = exc
        raise HoldingsCacheError('사용 가능한 정상 캐시가 없습니다. 이관 전 저장하거나 --import-holdings로 백업을 지정하세요.') from error

    def import_snapshot(self, path):
        raw, envelope = read_snapshot_file(path)
        if not self.account:
            accounts = self.get('/api/v1/accounts')
            candidates = [x for x in accounts if x.get('accountType') == 'BROKERAGE'] if isinstance(accounts, list) else []
            if len(candidates) != 1:
                raise HoldingsCacheError('가져올 내역의 TOSS_ACCOUNT_SEQ를 지정하세요.')
            self.account = str(candidates[0]['accountSeq'])
        if envelope and not self._matches(envelope):
            raise HoldingsCacheError('다른 계좌 캐시를 자동 재등록하지 않습니다. app.py cached --holdings로 연구하세요.')
        self._save(raw)

    def _refresh_prices(self, snapshot):
        notes = snapshot['snapshot_warnings']
        rows = snapshot['positions']
        for start in range(0, len(rows), 100):
            batch = rows[start:start + 100]
            try:
                prices = self.get('/api/v1/prices', {'symbols': ','.join(p['symbol'] for p in batch)})
                if not isinstance(prices, list):
                    raise ValueError('quote schema')
                prices = {p['symbol']: p for p in prices if isinstance(p, dict)}
            except Exception as exc:
                notes.append('토스 시세 갱신 실패 (' + type(exc).__name__ + '); 저장 가격과 기존 시각 유지.')
                prices = {}
            for p in batch:
                tick = prices.get(p['symbol'])
                try:
                    if not isinstance(tick, dict):
                        raise ValueError('missing quote')
                    price = decimal(tick['lastPrice'])
                    at = tick.get('timestamp')
                    if price < 0 or (at is not None and (stamp(at) is None or (age_seconds(at) or 0) < -300)):
                        raise ValueError('invalid quote')
                    old, new = stamp(p.get('price_at')), stamp(at)
                    if old and new and new < old:
                        raise ValueError('older quote')
                    p.update(price=str(price), price_at=at, price_source='Toss REST · 저장 수량 기준')
                except (KeyError, TypeError, ValueError, AttributeError):
                    notes.append(p['symbol'] + ': 새 시세 확보 불가; 저장 가격·시각 유지.')
        if any(p['currency'] == 'USD' for p in rows):
            try:
                fx = self.get('/api/v1/exchange-rate', {'baseCurrency': 'USD', 'quoteCurrency': 'KRW'})
                if not isinstance(fx, dict) or decimal(fx['midRate']) <= 0:
                    raise ValueError('invalid FX')
                snapshot['fx'] = fx
            except Exception as exc:
                notes.append('토스 환율 갱신 실패 (' + type(exc).__name__ + '); 저장 환율로 계산한 참고치.')
        snapshot['snapshot_warnings'] = list(dict.fromkeys(notes))
        return snapshot

    def snapshot(self):
        fresh = positive_snapshot(super().snapshot())
        if fresh['positions']:
            self._save(fresh)
            if not self.simulate_empty:
                fresh.update(holdings_source='live', account_checked_at=fresh['as_of'])
                return fresh
        saved, path = self._load()
        reason = '빈 계좌 재현 테스트' if self.simulate_empty else '토스 조회 성공 · 보유 주식 없음'
        result = research_snapshot(path, reason=reason, checked_at=fresh['as_of'])
        result['holdings_source'] = 'cached'
        if path.suffix == '.bak':
            result['snapshot_warnings'].append('기본 캐시를 사용할 수 없어 검증된 백업을 복구 사용합니다.')
        return self._refresh_prices(result)


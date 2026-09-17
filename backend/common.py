"""Small, explicit transports and data-quality helpers. Never log request bodies."""
from __future__ import annotations
import hashlib
import html
import json
import os
import re
import ssl
try:
    import certifi
except ImportError:
    certifi = None
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler


def now():
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def age_seconds(value):
    dt = stamp(value)
    return (datetime.now(timezone.utc) - dt).total_seconds() if dt else None


def freshness(value, seconds=900):
    age = age_seconds(value)
    if age is None:
        return '시각 미확인'
    if age < -300:
        return '미래 시각 오류'
    return '최근 관측' if age <= seconds else '오래된 관측 또는 휴장'


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('non-finite number')
    return result


def plain(value, limit=900):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', '', str(value or '')))).strip()[:limit]


def canonical_url(value):
    p = urlsplit(str(value))
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('invalid source URL')
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith('utm_') and k not in ('guccounter', 'guce_referrer')]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ''))


def evidence(provider, title, url, content, observed_at=None, kind='news', **extra):
    url = canonical_url(url)
    identity = url + (str(observed_at) + str(content) if kind in ('quote', 'indicator') else '')
    return dict(id='E' + hashlib.sha256(identity.encode()).hexdigest()[:12],
                provider=provider, title=plain(title, 240), url=url,
                content=plain(content, 1600), observed_at=observed_at,
                retrieved_at=now(), kind=kind, **extra)


class NetworkError(RuntimeError):
    def __init__(self, status=0):
        self.status = status
        super().__init__(f'network request failed (status={status})')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, headers=None, body=None, form=False, timeout=25, raw=False):
    headers = {'User-Agent': 'PersonalStockResearch/0.1', **(headers or {})}
    data = None
    if body is not None:
        data = (urlencode(body) if form else json.dumps(body, ensure_ascii=False)).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    
    # Verify TLS certificates with a maintained CA bundle on macOS/Linux.
    ctx = ssl.create_default_context(cafile=certifi.where() if certifi else None)
    # Never follow redirects carrying account or model credentials.
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ctx))

    for attempt in range(3):
        try:
            req = Request(url, data=data, headers=headers)
            with opener.open(req, timeout=timeout) as r:
                text = r.read(8_000_001)
                if len(text) > 8_000_000:
                    raise ValueError('response too large')
                return text if raw else json.loads(text)
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                try:
                    retry_header = exc.headers.get('Retry-After')
                    delay = max(1.0, min(30.0, float(retry_header))) if retry_header is not None else float(2 ** attempt)
                except (ValueError, TypeError):
                    delay = float(2 ** attempt)
                time.sleep(delay)
            else:
                raise NetworkError(exc.code) from None
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                raise NetworkError(0) from None
            time.sleep(2 ** attempt)


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + '.tmp')
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(temp, path)


def load_env(path: Union[str, Path] = '.env'):
    path_obj = Path(path)
    if not path_obj.exists():
        return
    for line in path_obj.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            raise ValueError('Invalid .env line; use KEY=value')
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))
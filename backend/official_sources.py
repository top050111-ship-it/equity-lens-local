# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SEC/DART clients, bounded cache, filing excerpts and per-issuer financial dossiers."""

from __future__ import annotations
import base64
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote, urlsplit
from zipfile import ZipFile
from defusedxml.ElementTree import fromstring as parse_safe_xml
from common import evidence, now, write_private, decimal
from financials import (
    sec_records,
    dart_records,
    deduplicate_records,
    financial_evidence,
    compute_metrics,
    financial_summaries,
)

SEC_BASE = "https://data.sec.gov"
DART_BASE = "https://opendart.fss.or.kr/api/"


def request(url, *, headers=None, raw=False, timeout=45, attempts=1, max_bytes=40_000_000):
    """Fetch a bounded HTTP response, returning bytes or decoded JSON."""
    for attempt in range(max(1, attempts)):
        try:
            req = Request(url, headers=headers or {})
            with urlopen(req, timeout=timeout) as response:
                payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ValueError("response exceeds size limit")
            return payload if raw else json.loads(payload)
        except Exception:
            if attempt + 1 >= max(1, attempts):
                raise

# Official product pages are fetched at runtime; no fees/holdings are hard-coded.
FUND_DOCUMENTS = {
    'SCHD': [{'title': 'Schwab U.S. Dividend Equity ETF 공식 상품 안내',
              'url': 'https://www.schwabassetmanagement.com/products/schd'}],
    'URA': [{'title': 'Global X Uranium ETF 공식 상품 안내',
             'url': 'https://www.globalxetfs.com/funds/ura'}],
}
FUND_FALLBACK_DOCUMENTS = {
    'SCHD': {'title':'SCHD SEC 요약 투자설명서', 'filed':'2026-02-27', 'document_date':'2026-02-27',
             'identity':'Schwab U.S. Dividend Equity ETF',
             'url':'https://www.sec.gov/Archives/edgar/data/1454889/000110465926020681/tm266454-15_497k.htm'},
    'URA': {'title':'URA SEC 요약 투자설명서', 'filed':'2026-02-27', 'document_date':'2026-03-01',
            'identity':'Global X Uranium ETF',
            'url':'https://www.sec.gov/Archives/edgar/data/1432353/000143235326000203/a497kuranium.htm'},
}
FUND_TOPICS = {
    'fund_objective':['investment objective', "fund’s goal", "fund's goal", 'seeks to provide'],
    'fund_strategy':['principal investment strategies','indexing approach','replication strategy'],
    'fund_risk':['concentration risk','tracking error risk','non-diversified','principal risks'],
    'fund_expenses':['total annual fund operating expenses','total annual operating expenses'],
}
_LOCKS = defaultdict(threading.Lock)
_LAST = {}
MAP_LOCK = threading.Lock()
# Product type routing only. These are not factual evidence about leverage or fees.
FUNDS = {
    "SOXL",
    "SOXS",
    "TQQQ",
    "SQQQ",
    "VOO",
    "SPY",
    "IVV",
    "QQQ",
    "QQQM",
    "SCHD",
    "SGOV",
    "URA",
    "SMH",
    "SOXX",
}
TOPIC_WORDS = {
    "business": [
        "사업의 내용",
        "사업의 개요",
        "주요 제품",
        "매출 및 수주",
        "revenue",
        "segment",
        "backlog",
        "customer",
        "demand",
    ],
    "profitability": ["수익성", "원가", "영업이익", "margin", "pricing", "cost of", "profit"],
    "cashflow": [
        "현금흐름",
        "유동성",
        "자금조달",
        "cash flow",
        "liquidity",
        "cash used",
        "capital expenditure",
    ],
    "risk": [
        "위험요인",
        "위험관리",
        "소송",
        "risk factor",
        "material weakness",
        "going concern",
        "uncertainty",
        "competition",
    ],
    "capital": [
        "증자",
        "전환사채",
        "자기주식",
        "주식기준",
        "dilution",
        "convertible",
        "stock-based",
        "repurchase",
        "debt",
    ],
    "outlook": [
        "전망",
        "계획",
        "생산능력",
        "설비",
        "guidance",
        "outlook",
        "capacity",
        "milestone",
        "launch",
        "delay",
    ],
}


class OfficialError(RuntimeError):
    """Only provider and safe error code; never a URL containing a secret."""

    def __init__(self, provider, code):
        super().__init__(provider + " [" + str(code) + "]")
        self.provider, self.code = provider, str(code)


class PublicCache:
    def __init__(self, root=None):
        self.root = Path(
            root
            or os.getenv("EQUITY_PUBLIC_CACHE")
            or Path(__file__).resolve().parent / "data" / "public_cache"
        )

    def get(self, url, *, provider, headers=None, params=None, raw=False, ttl=3600):
        # Cache identity excludes credentials; credentials never reach stored sources/logs.
        public_params = {
            k: v for k, v in (params or {}).items() if k not in ("crtfc_key", "api_key")
        }
        key = hashlib.sha256(
            json.dumps([url, public_params, raw], sort_keys=True).encode()
        ).hexdigest()
        path = self.root / (key + ".json")
        with _LOCKS[key]:
            try:
                record = json.loads(path.read_text())
                age = time.time() - record["at"]
                if 0 <= age < ttl:
                    return base64.b64decode(record["value"]) if raw else record["value"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
            with _LOCKS[provider]:
                spacing = 0.4 if provider == "SEC" else 0.6 if provider == "DART" else 0.3
                time.sleep(max(0, spacing - (time.monotonic() - _LAST.get(provider, 0))))
                try:
                    full = url + ("?" + urlencode(params) if params else "")
                    value = request(
                        full, headers=headers, raw=raw, timeout=45, attempts=1, max_bytes=40_000_000
                    )
                except Exception as exc:
                    raise OfficialError(
                        provider, "HTTP_" + str(getattr(exc, "status", 0))
                    ) from None
                finally:
                    _LAST[provider] = time.monotonic()
            if (
                provider == "DART"
                and not raw
                and (not isinstance(value, dict) or value.get("status") not in ("000", "013"))
            ):
                raise OfficialError(
                    "DART",
                    (
                        value.get("status", "INVALID_RESPONSE")
                        if isinstance(value, dict)
                        else "INVALID_RESPONSE"
                    ),
                )
            # Expired cache is never silently substituted for a failed live request.
            if raw:
                payload = value
                if isinstance(payload, memoryview):
                    payload = payload.tobytes()
                if not isinstance(payload, (bytes, bytearray)):
                    raise OfficialError(provider, "INVALID_RESPONSE")
                stored = {"at": time.time(), "value": base64.b64encode(payload).decode()}
            else:
                stored = {"at": time.time(), "value": value}
            write_private(path, stored)
            return value


def zip_documents(payload, max_total=80_000_000):
    if not isinstance(payload, bytes) or not payload.startswith(b"PK"):
        raise OfficialError("DART", "ZIP_EXPECTED")
    with ZipFile(io.BytesIO(payload)) as archive:
        entries = [
            f
            for f in archive.infolist()
            if not f.is_dir() and f.filename.lower().endswith((".xml", ".html", ".htm"))
        ]
        if (
            len(entries) > 100
            or sum(f.file_size for f in entries) > max_total
            or any(f.file_size > max_total for f in entries)
        ):
            raise OfficialError("DART", "ZIP_SIZE_LIMIT")
        # Read in memory; archive paths are never extracted to the filesystem.
        return [(f.filename, archive.read(f)) for f in entries]


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.depth = 0
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (
            tag in ("script", "style", "head", "ix:header", "ix:hidden")
            or "hidden" in attrs
            or bool(re.search(r"display\s*:\s*none", attrs.get("style") or "", re.I))
        )
        # HTML void elements must not increase a hidden stack.
        if tag not in (
            "br",
            "hr",
            "img",
            "input",
            "meta",
            "link",
            "wbr",
            "source",
            "area",
            "base",
            "embed",
            "param",
            "track",
            "col",
        ):
            self.hidden.append((tag, hidden))
            if hidden:
                self.depth += 1
        if not self.depth and tag in (
            "p",
            "div",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "section",
            "br",
            "title",
            "tu",
        ):
            self.parts.append("\n")
        elif not self.depth and tag in ("td", "th", "te"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        idx = next(
            (i for i in range(len(self.hidden) - 1, -1, -1) if self.hidden[i][0] == tag), None
        )
        if idx is not None:
            self.depth -= sum(int(x[1]) for x in self.hidden[idx:])
            del self.hidden[idx:]
        if not self.depth and tag in (
            "p",
            "div",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "section",
            "title",
            "tu",
        ):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def document_text(payload):
    if payload.lstrip().startswith(b"%PDF"):
        raise OfficialError("DOCUMENT", "PDF_NOT_SUPPORTED")
    text = None
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise OfficialError("DOCUMENT", "ENCODING")
    parser = TextExtractor()
    parser.feed(text)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in "".join(parser.parts).split("\n")]
    return "\n".join(p for p in paragraphs if p)


def excerpt_sources(payload, *, symbol, provider, title, url, filed, event_key, limit=8, topics=None):
    text = document_text(payload)
    if len(text) < 80:
        raise OfficialError(provider, "NO_READABLE_TEXT")
    digest = hashlib.sha256(text.encode()).hexdigest()
    # Disjoint excerpts; stable offsets into normalized document text.
    chunks = []
    offset = 0
    for para in text.split("\n"):
        for pos in range(0, len(para), 1000):
            chunk = para[pos : pos + 1000]
            if len(chunk) >= 60:
                chunks.append((offset + pos, chunk))
        offset += len(para) + 1
    selected = []
    used = set()
    for topic, words in (TOPIC_WORDS if topics is None else topics).items():
        ranked = sorted(
            (
                (sum(chunk.lower().count(w.lower()) for w in words), off, chunk)
                for off, chunk in chunks
            ),
            key=lambda x: (-x[0], x[1]),
        )
        for score, off, chunk in ranked:
            if score and off not in used:
                selected.append((topic, off, chunk))
                used.add(off)
                break
    for off, chunk in chunks:
        if len(selected) >= limit:
            break
        if off not in used:
            selected.append(("context", off, chunk))
            used.add(off)
    sources = []
    for topic, off, chunk in selected[:limit]:
        sources.append(
            evidence(
                provider,
                f"{symbol} {title} · {topic} · 문자 {off}",
                url,
                chunk,
                filed + "T00:00:00+00:00" if filed else None,
                kind="filing_excerpt",
                symbols=[symbol],
                primary=True,
                content_scope="공시/공식문서 본문 일부 발췌; 전체 문서 검토 아님; 표 문맥 누락 가능",
                document_sha256=digest,
                excerpt_start=off,
                excerpt_end=off + len(chunk),
                event_key=event_key,
            )
        )
    return sources


def sec_cik(value):
    text = str(value)
    if not re.fullmatch(r"\d{1,10}", text) or int(text) <= 0:
        raise OfficialError("SEC", "INVALID_CIK")
    return text.zfill(10)


class SEC:
    def __init__(self, cache):
        ua = os.getenv("SEC_USER_AGENT", "").strip()
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", ua):
            raise OfficialError("SEC", "SEC_USER_AGENT_REQUIRED")
        self.headers = {"User-Agent": ua, "Accept-Encoding": "identity"}
        self.cache = cache

    def get(self, url, ttl=3600, raw=False):
        return self.cache.get(url, provider="SEC", headers=self.headers, ttl=ttl, raw=raw)

    def resolve(self, symbol, override=None):
        if override:
            return sec_cik(override)
        with MAP_LOCK:
            tickers = self.get("https://www.sec.gov/files/company_tickers.json", ttl=86400)
        matches = {
            sec_cik(r["cik_str"])
            for r in tickers.values()
            if r.get("ticker", "").upper().replace(".", "-") == symbol.upper().replace(".", "-")
        }
        if len(matches) != 1:
            raise OfficialError("SEC", "CIK_MAPPING_NOT_UNIQUE")
        return matches.pop()

    def collect(self, symbol, cfg):
        cik = self.resolve(cfg.get("issuer_symbol", symbol), cfg.get("cik"))
        out = {"sources": [], "records": [], "warnings": [], "filings": [], "cik": cik}
        try:
            payload = self.get(SEC_BASE + "/api/xbrl/companyfacts/CIK" + cik + ".json")
            if str(payload.get("cik", "")).lstrip("0") != str(int(cik)):
                raise OfficialError("SEC", "CIK_RESPONSE_MISMATCH")
            rows, notes = sec_records(
                payload, symbol, cfg.get("reporting_currency", "USD"), now()[:10]
            )
            for row in rows:
                row["cik"] = str(int(cik))
            out["records"] = rows
            out["warnings"] += notes
        except Exception as exc:
            out["warnings"].append(symbol + " 재무 API: " + safe_error(exc))
        try:
            payload = self.get(SEC_BASE + "/submissions/CIK" + cik + ".json")
            if str(payload.get("cik", "")).lstrip("0") != str(int(cik)):
                raise OfficialError("SEC", "SUBMISSIONS_CIK_MISMATCH")
            recent = payload.get("filings", {}).get("recent", {})
            rows = []
            for i, form in enumerate(recent.get("form", [])):
                try:
                    filed = recent["filingDate"][i]
                    acc = recent["accessionNumber"][i]
                    doc = recent["primaryDocument"][i]
                    if filed > now()[:10] or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", acc):
                        continue
                    if form not in (
                        "10-K",
                        "10-Q",
                        "10-K/A",
                        "10-Q/A",
                        "20-F",
                        "20-F/A",
                        "40-F",
                        "40-F/A",
                        "8-K",
                        "8-K/A",
                        "6-K",
                    ):
                        continue
                    if (
                        not doc
                        or ".." in doc
                        or doc.startswith("/")
                        or not re.fullmatch(r"[\w.\-/]+", doc)
                    ):
                        continue
                    url = (
                        "https://www.sec.gov/Archives/edgar/data/"
                        + str(int(cik))
                        + "/"
                        + acc.replace("-", "")
                        + "/"
                        + quote(doc, safe="/")
                    )
                    rows.append(dict(form=form, filed=filed, accession=acc, url=url))
                except (KeyError, IndexError, TypeError):
                    continue
            rows.sort(key=lambda r: (r["filed"], r["accession"]), reverse=True)
            # Guaranteed latest periodic + annual before event reports: not just the first N filings.
            periodic = next(
                (r for r in rows if r["form"].startswith(("10-Q", "10-K", "20-F", "40-F"))), None
            )
            annual = next((r for r in rows if r["form"].startswith(("10-K", "20-F", "40-F"))), None)
            events = [r for r in rows if r["form"].startswith(("8-K", "6-K"))][:2]
            selected = []
            for row in [periodic, annual, *events]:
                if row and row not in selected:
                    selected.append(row)
            for row in rows[:12]:
                out["filings"].append(row)
                out["sources"].append(
                    evidence(
                        "SEC",
                        symbol + " " + row["form"] + " 공시 접수",
                        row["url"],
                        json.dumps(row, ensure_ascii=False),
                        row["filed"] + "T00:00:00+00:00",
                        kind="filing_index",
                        primary=True,
                        symbols=[symbol],
                        event_key="SEC:" + row["accession"],
                        content_scope="공시 목록·접수 사실만; 본문 근거 아님",
                    )
                )
            for row in selected[: int(cfg.get("max_documents", 3))]:
                try:
                    out["sources"] += excerpt_sources(
                        self.get(row["url"], ttl=86400, raw=True),
                        symbol=symbol,
                        provider="SEC",
                        title=row["form"],
                        url=row["url"],
                        filed=row["filed"],
                        event_key="SEC:" + row["accession"],
                        limit=6,
                    )
                except Exception as exc:
                    out["warnings"].append(symbol + " " + row["form"] + " 본문: " + safe_error(exc))
            # Earnings releases are frequently in exhibits, not the 8-K primary body.
            for row in events[:1]:
                try:
                    index = self.get(row["url"].rsplit("/", 1)[0] + "/index.json", ttl=86400)
                    exhibits = [
                        x["name"]
                        for x in index.get("directory", {}).get("item", [])
                        if re.search(r"(ex[\-_]?99|exhibit[\-_]?99)", x.get("name", ""), re.I)
                        and x.get("name", "").lower().endswith((".htm", ".html"))
                        and re.fullmatch(r"[\w.\-]+", x["name"])
                    ]
                    if not exhibits:
                        out["warnings"].append(
                            symbol + ": 최신 이벤트 공시의 실적 첨부파일 자동 식별 불가"
                        )
                    for name in exhibits[:1]:
                        url = row["url"].rsplit("/", 1)[0] + "/" + name
                        out["sources"] += excerpt_sources(
                            self.get(url, ttl=86400, raw=True),
                            symbol=symbol,
                            provider="SEC",
                            title=row["form"] + " Exhibit 99",
                            url=url,
                            filed=row["filed"],
                            event_key="SEC:" + row["accession"],
                            limit=5,
                        )
                except Exception as exc:
                    out["warnings"].append(symbol + " 실적 첨부: " + safe_error(exc))
            out["warnings"].append(
                symbol
                + ": SEC 최근 submissions 범위만 조회; 공시 본문 일부·첨부 일부 발췌. 기업별 확장 태그·사업부 수치 자동 정규화 제외."
            )
        except Exception as exc:
            out["warnings"].append(symbol + " 공시 목록: " + safe_error(exc))
        return out


class DART:
    def __init__(self, cache):
        self.key = os.getenv("DART_API_KEY", "").strip()
        self.cache = cache
        if not self.key:
            raise OfficialError("DART", "DART_API_KEY_REQUIRED")

    def get(self, path, params=None, raw=False, ttl=3600):
        result = self.cache.get(
            DART_BASE + path,
            provider="DART",
            params={"crtfc_key": self.key, **(params or {})},
            raw=raw,
            ttl=ttl,
        )
        if raw:
            return result
        if not isinstance(result, dict):
            raise OfficialError("DART", "INVALID_RESPONSE")
        if result.get("status") == "013":
            return {"status": "013", "list": []}
        if result.get("status") != "000":
            raise OfficialError("DART", result.get("status", "UNKNOWN"))
        return result

    def resolve(self, symbol, override=None):
        if override:
            if not re.fullmatch(r"\d{8}", str(override)):
                raise OfficialError("DART", "INVALID_CORP_CODE")
            return str(override)
        with MAP_LOCK:
            docs = zip_documents(self.get("corpCode.xml", raw=True, ttl=86400))
        result = set()
        for _, data in docs:
            root = parse_safe_xml(
                data,
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
            for row in root.findall("list"):
                if (row.findtext("stock_code") or "").strip() == symbol:
                    code = (row.findtext("corp_code") or "").strip()
                    if re.fullmatch(r"\d{8}", code):
                        result.add(code)
        if len(result) != 1:
            raise OfficialError("DART", "CORP_MAPPING_NOT_UNIQUE")
        return result.pop()

    def listing(self, corp, regular=False):
        today = datetime.now(timezone.utc).date()
        params = {
            "corp_code": corp,
            "bgn_de": (today - timedelta(days=800 if regular else 120)).strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"),
            "last_reprt_at": "Y",
            "page_count": 100,
            "sort": "date",
            "sort_mth": "desc",
        }
        if regular:
            params["pblntf_ty"] = "A"
        rows = []
        truncated = False
        for page in range(1, 4):
            response = self.get("list.json", {**params, "page_no": page})
            rows += response.get("list", [])
            if page >= int(response.get("total_page", 0)):
                break
            if page == 3:
                truncated = True
        return rows, truncated

    def collect(self, symbol, cfg):
        corp = self.resolve(cfg.get("issuer_symbol", symbol), cfg.get("corp_code"))
        out = {"sources": [], "records": [], "warnings": [], "filings": [], "corp_code": corp}
        regular, cut = self.listing(corp, True)
        if cut:
            out["warnings"].append(symbol + ": 정기공시 목록 300건 상한으로 일부 누락")
        try:
            events, cut = self.listing(corp)
            if cut:
                out["warnings"].append(symbol + ": 최근공시 목록 300건 상한으로 일부 누락")
        except Exception as exc:
            events = []
            out["warnings"].append(symbol + " 최근공시: " + safe_error(exc))
        all_rows = {
            r["rcept_no"]: r
            for r in [*regular, *events]
            if re.fullmatch(r"\d{14}", str(r.get("rcept_no", "")))
        }
        ranked = sorted(all_rows.values(), key=lambda r: r["rcept_no"], reverse=True)
        for r in ranked[:12]:
            acc = r["rcept_no"]
            url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + acc
            row = dict(title=r["report_nm"], accession=acc, filed=r.get("rcept_dt", ""), url=url)
            out["filings"].append(row)
            out["sources"].append(
                evidence(
                    "DART",
                    symbol + " " + r["report_nm"],
                    url,
                    json.dumps(row, ensure_ascii=False),
                    f"{acc[:4]}-{acc[4:6]}-{acc[6:8]}T00:00:00+00:00",
                    kind="filing_index",
                    primary=True,
                    symbols=[symbol],
                    event_key="DART:" + acc,
                    content_scope="공시 목록·접수 사실만; 본문 근거 아님",
                )
            )
        # Choose reporting periods from actual submitted reports, never an assumed latest quarter.
        periods = {}
        for r in sorted(regular, key=lambda r: r.get("rcept_no", ""), reverse=True):
            m = re.search(r"\((\d{4})\.(\d{2})\)", r.get("report_nm", ""))
            if not m:
                continue
            year, month = map(int, m.groups())
            code = {3: "11013", 6: "11012", 9: "11014", 12: "11011"}.get(month)
            if code:
                periods.setdefault((year, code), r)
        if int(cfg.get("fiscal_end_month", 12)) != 12:
            out["warnings"].append(
                symbol + ": 비12월 결산 DART 자동 기간 매핑 미지원; 재무 정규화 생략"
            )
            periods = {}
        ordered = sorted(
            periods,
            key=lambda x: (x[0], {"11013": 3, "11012": 6, "11014": 9, "11011": 12}[x[1]]),
            reverse=True,
        )
        selected = ordered[:4]
        if ordered:
            prior = (ordered[0][0] - 1, ordered[0][1])
            if prior in periods and prior not in selected:
                selected.append(prior)
        # Choose a single accounting scope across every comparison period.
        scope = cfg.get("fs_div", "CFS")
        if scope not in ("CFS", "OFS"):
            raise OfficialError("DART", "INVALID_FS_DIV")
        for year, code in selected:
            try:
                response = self.get(
                    "fnlttSinglAcntAll.json",
                    dict(corp_code=corp, bsns_year=year, reprt_code=code, fs_div=scope),
                )
                raw_rows = response.get("list", [])
                if any(r.get("corp_code") != corp for r in raw_rows):
                    raise OfficialError("DART", "CORP_RESPONSE_MISMATCH")
                rows, notes = dart_records(
                    raw_rows, symbol, year, code, scope, cfg.get("reporting_currency", "KRW")
                )
                out["records"] += rows
                out["warnings"] += notes
            except Exception as exc:
                out["warnings"].append(symbol + f" {year}/{code} 재무 API: " + safe_error(exc))
        latest = next((periods[p] for p in ordered), None)
        annual = next((periods[p] for p in ordered if p[1] == "11011"), None)
        event = next(
            (
                r
                for r in ranked
                if re.search(
                    r"주요사항|공급계약|잠정|영업실적|증자|전환사채", r.get("report_nm", "")
                )
            ),
            None,
        )
        docs = []
        for row in (latest, annual, event):
            if row and row not in docs:
                docs.append(row)
        for row in docs[: int(cfg.get("max_documents", 3))]:
            acc = row["rcept_no"]
            try:
                files = zip_documents(
                    self.get("document.xml", dict(rcept_no=acc), raw=True, ttl=86400)
                )
                # Main document is usually the largest XML; other attachments remain a disclosed gap.
                _, data = max(files, key=lambda item: len(item[1]))
                out["sources"] += excerpt_sources(
                    data,
                    symbol=symbol,
                    provider="DART",
                    title=row["report_nm"],
                    url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + acc,
                    filed=f"{acc[:4]}-{acc[4:6]}-{acc[6:8]}",
                    event_key="DART:" + acc,
                    limit=7,
                )
            except Exception as exc:
                out["warnings"].append(
                    symbol + " " + row["report_nm"] + " 본문: " + safe_error(exc)
                )
        out["warnings"].append(
            symbol
            + ": DART "
            + scope
            + " 단일 기준; 본문은 키워드 발췌, 주석·사업부 수치 완전 정규화 제외."
        )
        return out


def safe_error(exc):
    return str(exc) if isinstance(exc, OfficialError) else type(exc).__name__


def recent_records(records):
    """Keep latest three exact periods per metric/basis plus a year-ago comparator."""
    grouped = defaultdict(list)
    for r in records:
        grouped[(r["metric"], r["basis"], r["unit"], r["scope"])].append(r)
    selected = []
    for rows in grouped.values():
        rows.sort(key=lambda r: r["end"], reverse=True)
        keep = rows[:3]
        if rows:
            last = datetime.fromisoformat(rows[0]["end"])
            prior = next(
                (r for r in rows if 357 <= (last - datetime.fromisoformat(r["end"])).days <= 374),
                None,
            )
            if prior and prior not in keep:
                keep.append(prior)
        selected += keep
    return selected


def collect_official(snapshot, config):
    settings = config.get("research", {})
    instruments = config.get("instruments", {})
    cache = PublicCache(settings.get("cache_dir"))
    dossiers = {}
    sources = []
    warnings = []
    # Sequential official requests keep a single fair-access owner and predictable memory.
    for p in snapshot["positions"]:
        symbol = p["symbol"]
        cfg = dict(instruments.get(symbol, {}))
        asset_type = cfg.get("asset_type", "fund" if symbol in FUNDS else "company")
        documents = cfg.get('documents') if 'documents' in cfg else FUND_DOCUMENTS.get(symbol, [])
        out = {"sources": [], "records": [], "warnings": [], "filings": []}
        print("  - 공식 자료 수집:", symbol, flush=True)
        try:
            if asset_type == "fund":
                out["warnings"].append(
                    symbol
                    + (": ETF/펀드 경로; 기업 재무비율 미적용. 공식 상품문서 수집 시도."
                       if documents else ": ETF/펀드 경로; 기업 재무비율 미적용. 상품 공식문서 URL을 instruments.documents에 설정하세요.")
                )
            elif p["market"] in ("KOSPI", "KOSDAQ"):
                out = DART(cache).collect(symbol, cfg)
            elif p["market"] in ("NASDAQ", "NYSE", "AMEX"):
                out = SEC(cache).collect(symbol, cfg)
            else:
                out["warnings"].append(symbol + ": 공식 공시 시장 매핑 미지원")
        except Exception as exc:
            out["warnings"].append(symbol + " 공식자료: " + safe_error(exc))
        for doc in (documents or [])[:3]:
            try:
                url = doc["url"]
                u = urlsplit(url)
                if u.scheme != "https" or not u.hostname or u.username or u.password:
                    raise ValueError("document URL")
                out["sources"] += excerpt_sources(
                    cache.get(url, provider="IR", raw=True, ttl=86400),
                    symbol=symbol,
                    provider="사용자 지정 공식문서",
                    title=doc.get("title", "공식 자료"),
                    url=url,
                    filed=doc.get("filed"),
                    event_key="IR:" + url,
                    limit=6,
                    topics=FUND_TOPICS if asset_type == 'fund' else None,
                )
            except Exception as exc:
                out["warnings"].append(symbol + " 추가 공식문서: " + safe_error(exc))
        # An independently hosted SEC filing can remain available when a product site blocks bots.
        fallback = FUND_FALLBACK_DOCUMENTS.get(symbol) if 'documents' not in cfg else None
        if asset_type == 'fund' and not out['sources'] and fallback:
            try:
                ua = os.getenv('SEC_USER_AGENT', '').strip()
                if not re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+', ua):
                    raise OfficialError('SEC','SEC_USER_AGENT_REQUIRED')
                raw = cache.get(fallback['url'],provider='SEC',headers={'User-Agent':ua,'Accept-Encoding':'identity'},raw=True,ttl=86400)
                text = document_text(raw)
                normalized = re.sub(r'\s+',' ',text).casefold()
                if fallback['identity'].casefold() not in normalized or not re.search(r'\b'+re.escape(symbol)+r'\b',text):
                    raise OfficialError('SEC','FUND_DOCUMENT_IDENTITY_MISMATCH')
                extra = excerpt_sources(raw,symbol=symbol,provider='SEC',title=fallback['title'],url=fallback['url'],
                    filed=fallback['filed'],event_key='SEC-DOC:'+fallback['url'],limit=6,topics=FUND_TOPICS)
                for source in extra:
                    source['document_date'] = fallback['document_date']
                    source['content_scope'] += '; 대체 투자설명서 기준일 '+fallback['document_date']+'; 현재 편입비중 자료가 아님'
                out['sources'].extend(extra)
                out['warnings'].append(symbol+': 상품 페이지 대신 SEC 투자설명서 사용; 문서 기준일 '+fallback['document_date']+'; 최신 편입비중을 보장하지 않음')
            except Exception as exc:
                out['warnings'].append(symbol+' 대체 상품문서: '+safe_error(exc))
        rows, notes = deduplicate_records(out["records"])
        out["warnings"] += notes
        rows = recent_records(rows)
        out["sources"] += financial_evidence(rows, symbol, out.get("cik"))
        metrics, calcs = compute_metrics(rows, symbol)
        out["sources"] += calcs
        out["sources"] += financial_summaries(rows, symbol)
        if not rows and asset_type == "company":
            out["warnings"].append(
                symbol + ": 정규화 가능한 공식 재무값 없음; 뉴스로 재무 수치 대체 금지"
            )
        dossiers[symbol] = {k: v for k, v in out.items() if k not in ("sources", "records")}
        dossiers[symbol].update(
            symbol=symbol,
            asset_type=asset_type,
            sector=cfg.get("sector", "fund" if asset_type == "fund" else "general"),
            questions=cfg.get("questions", []),
            records=rows,
            metrics=metrics,
            source_ids=[s["id"] for s in out["sources"]],
        )
        sources += out["sources"]
        warnings += out["warnings"]
    return {"sources": sources, "warnings": list(dict.fromkeys(warnings)), "dossiers": dossiers}

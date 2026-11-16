"""SEC·DART 수집, 모델 요청 형식, 보고서 검증의 오프라인 회귀 테스트."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from zipfile import ZipFile

from defusedxml.common import DefusedXmlException

# 직접 실행과 unittest discover에서 같은 모듈을 찾도록 설정한다.
TEST_DIR = Path(__file__).resolve().parent
for directory in (TEST_DIR.parent, TEST_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from agents import Finding, Model
from common import evidence
from deep_research import run_deep_research, source_budget
from financials import financial_evidence, financial_summaries
from official_sources import DART, SEC, OfficialError, PublicCache, collect_official
from research_tools import evaluate_report
from test_research import FixtureModel, bundles, dart_row, record, sec_payload, sec_row

JsonObject = dict[str, Any]

FIXTURE_ENV = {
    "DART_API_KEY": "fixture",
    "SEC_USER_AGENT": "Fixture research test@local.example",
    "OPENAI_API_KEY": "fixture-secret",
    "LLM_BACKEND": "ollama",
    "LLM_MODEL": "fixture-model",
    "OLLAMA_NUM_CTX": "16384",
    "OLLAMA_NUM_PREDICT": "4800",
    "OLLAMA_THINK": "false",
    "OLLAMA_TIMEOUT": "900",
}


def zipped(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def ollama_response(
    payload: JsonObject | None = None,
    *,
    done_reason: str = "stop",
) -> JsonObject:
    return {
        "done": True,
        "done_reason": done_reason,
        "message": {
            "content": json.dumps({"insights": []} if payload is None else payload)
        },
    }


def request_body(mock_request: Mock) -> JsonObject:
    call = mock_request.call_args
    if call is None:
        raise AssertionError("모델 요청이 호출되지 않았습니다.")
    body = call.kwargs["body"]
    if not isinstance(body, dict):
        raise AssertionError("모델 요청 본문은 dict여야 합니다.")
    return body


class FakeCache:
    """네트워크 호출 없이 SEC 응답 형식의 고정 데이터를 반환한다."""

    def __init__(self, facts_cik: int = 1) -> None:
        self.facts_cik = facts_cik
        self.calls: list[tuple[str, JsonObject]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "TEST", "cik_str": 1}}
        if "/companyfacts/" in url:
            return {**sec_payload([sec_row()]), "cik": self.facts_cik}
        if "/submissions/" in url:
            return {
                "cik": "0000000001",
                "filings": {
                    "recent": {
                        "form": ["10-Q", "10-K"],
                        "filingDate": ["2026-05-01", "2026-02-01"],
                        "accessionNumber": [
                            "0000000001-26-000001",
                            "0000000001-26-000002",
                        ],
                        "primaryDocument": ["test10q.htm", "test10k.htm"],
                    }
                },
            }
        return (
            "<p>Revenue 100 USD. "
            + "Cash flow liquidity and demand risk " * 15
            + "</p>"
        ).encode()


class BytesCache:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def get(self, *args: Any, **kwargs: Any) -> bytes:
        return self.payload


class DartFixtureClient(DART):
    """정상 응답과 발행회사 불일치 테스트가 공유하는 DART 대역."""

    def __init__(self, issuer_code: str = "00000001") -> None:
        super().__init__(FakeCache())
        self.issuer_code = issuer_code

    def resolve(self, symbol: Any, override: Any = None) -> str:
        return self.issuer_code

    def listing(self, corp: Any, regular: bool = False) -> Any:
        return (
            [
                {
                    "rcept_no": "20261116000001",
                    "report_nm": "분기보고서 (2026.09)",
                    "rcept_dt": "20261116",
                }
            ],
            False,
        )

    def get(
        self,
        path: Any,
        params: Any = None,
        raw: bool = False,
        ttl: int = 3600,
    ) -> Any:
        if path == "fnlttSinglAcntAll.json":
            return {
                "status": "000",
                "list": [dart_row(), dart_row("영업활동현금흐름", "CF")],
            }
        return zipped(
            "main.xml",
            "<DOCUMENT><P>" + "매출 현금흐름 위험 자금조달 " * 30 + "</P></DOCUMENT>",
        )


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ, FIXTURE_ENV)
        environment.start()
        self.addCleanup(environment.stop)

    # SEC·DART 수집
    def test_sec_full_client_shape(self):
        result: JsonObject = SEC(FakeCache()).collect("TEST", {})
        self.assertTrue(result["records"])
        self.assertEqual(result["records"][0]["cik"], "1")
        self.assertEqual(len(result["filings"]), 2)
        self.assertTrue(
            any(source["kind"] == "filing_excerpt" for source in result["sources"])
        )

    def test_sec_cik_mismatch_is_a_gap(self):
        result: JsonObject = SEC(FakeCache(facts_cik=2)).collect("TEST", {})
        self.assertFalse(result["records"])
        self.assertTrue(any("MISMATCH" in note for note in result["warnings"]))

    def test_sec_missing_mapping_not_fuzzy_match(self):
        with self.assertRaises(OfficialError):
            SEC(FakeCache()).resolve("UNLISTED")

    def test_dart_corp_code_zip_mapping(self):
        payload = zipped(
            "CORPCODE.xml",
            "<result><list><corp_code>00000001</corp_code>"
            "<stock_code>005930</stock_code></list></result>",
        )
        self.assertEqual(DART(BytesCache(payload)).resolve("005930"), "00000001")

    def test_dart_xxe_rejected(self):
        payload = zipped(
            "CORPCODE.xml",
            '<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]>'
            "<result>&a;</result>",
        )
        with self.assertRaises(DefusedXmlException):
            DART(BytesCache(payload)).resolve("005930")

    def test_dart_full_shape_and_scope(self):
        result: JsonObject = DartFixtureClient().collect("TEST", {})
        self.assertTrue(result["records"])
        self.assertEqual({row["scope"] for row in result["records"]}, {"CFS"})
        self.assertTrue(
            any(source["kind"] == "filing_excerpt" for source in result["sources"])
        )

    def test_dart_wrong_corp_response_rejected(self):
        result: JsonObject = DartFixtureClient("99999999").collect("TEST", {})
        self.assertFalse(result["records"])
        self.assertTrue(any("MISMATCH" in note for note in result["warnings"]))

    # 캐시·재무자료 연결·입력 예산
    def test_expired_cache_not_used_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = PublicCache(directory)
            with patch("official_sources.request", return_value={"status": "000"}):
                cache.get("https://example.com", provider="DART")
            with patch("official_sources.request", side_effect=TimeoutError):
                with self.assertRaises(OfficialError):
                    cache.get("https://example.com", provider="DART", ttl=0)

    def test_collector_completes_financial_lineage(self):
        official_result = {
            "records": [record(), record("operating_income", "20")],
            "sources": [],
            "warnings": [],
            "filings": [],
            "cik": "0000000001",
        }
        with patch("official_sources.SEC") as mock_sec:
            mock_sec.return_value.collect.return_value = official_result
            bundle: JsonObject = collect_official(
                {
                    "positions": [
                        {
                            "symbol": "TEST",
                            "market": "NASDAQ",
                            "currency": "USD",
                        }
                    ]
                },
                {},
            )
        source_ids = {source["id"] for source in bundle["sources"]}
        self.assertTrue(bundle["dossiers"]["TEST"]["metrics"])
        for source in bundle["sources"]:
            self.assertLessEqual(len(source["content"]), 1600)
            self.assertTrue(set(source.get("input_evidence_ids", [])) <= source_ids)
        self.assertTrue(
            any(source["kind"] == "financial_summary" for source in bundle["sources"])
        )

    def test_budget_prefers_summary_with_original_retained(self):
        rows = [record(), record("operating_income", "20")]
        original = financial_evidence(rows, "TEST", "1")
        compact = financial_summaries(rows, "TEST")
        selected, budget = source_budget(original + compact, 2, 10000)
        self.assertTrue(
            any(source["kind"] == "financial_summary" for source in selected)
        )
        self.assertTrue(budget["omitted"])

    # 모델 요청·출력 검토
    def test_local_review_batches_preserve_item_ids(self):
        source: JsonObject = evidence(
            "fixture", "test source", "https://example.com", "revenue 100 USD"
        )
        item_ids = [f"F{index}" for index in range(5)]
        items = [
            {
                "item_id": item_id,
                "type": "finding",
                **Finding(
                    subject="test",
                    claim=f"claim {index}",
                    kind="inference",
                    evidence_ids=[source["id"]],
                    confidence="low",
                    counterpoint="uncertain",
                    revisit_when="next filing",
                ).model_dump(),
            }
            for index, item_id in enumerate(item_ids)
        ]

        def respond(url: Any, **kwargs: Any) -> JsonObject:
            properties = kwargs["body"]["format"]["$defs"]["SpanDecision"]["properties"]
            span_id = properties["span_ids"]["items"]["enum"][-1]
            return ollama_response(
                {
                    "decisions": [
                        {
                            "item_id": item_id,
                            "supported": True,
                            "portfolio_linked": False,
                            "duplicate_of": "",
                            "reason": "fixture",
                            "span_ids": [span_id],
                        }
                        for item_id in properties["item_id"]["enum"]
                    ]
                }
            )

        with patch("agents.request", side_effect=respond) as mock_request:
            reviewed = Model().review(
                "portfolio", {"items": items, "sources": [source]}
            )
        self.assertEqual([item.item_id for item in reviewed.decisions], item_ids)
        self.assertEqual(mock_request.call_count, 3)

    def test_ollama_structured_transport(self):
        with patch("agents.request", return_value=ollama_response()) as mock_request:
            result = Model().research({"sources": []})
        self.assertEqual(result.insights, [])
        body = request_body(mock_request)
        self.assertFalse(body["stream"])
        self.assertFalse(body["format"]["additionalProperties"])

    def test_ollama_truncation_not_published(self):
        with patch(
            "agents.request", return_value=ollama_response(done_reason="length")
        ):
            with self.assertRaises(ValueError):
                Model().research({"sources": []})

    def test_openai_structured_transport(self):
        response = {
            "status": "completed",
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"insights":[]}',
                        }
                    ]
                }
            ],
        }
        with patch.dict(os.environ, {"LLM_BACKEND": "openai"}):
            with patch("agents.request", return_value=response) as mock_request:
                Model().research({"sources": []})
        body = request_body(mock_request)
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("fixture-secret", json.dumps(body))

    # 보고서 진단·로컬 API 보호
    def test_report_evaluation_flags_invalid_quotes(self):
        snapshot, portfolio, macro = bundles()
        result: JsonObject = run_deep_research(
            FixtureModel(), snapshot, portfolio, macro
        )
        research: JsonObject = result["research_v2"]
        companies: list[JsonObject] = research["companies"]
        self.assertTrue(companies)
        audit: list[JsonObject] = companies[0]["quality_audit"]
        self.assertTrue(audit)
        quotes: list[JsonObject] = audit[0]["quotes"]
        self.assertTrue(quotes)
        quotes[0]["quote"] = "invented quote"
        self.assertTrue(evaluate_report(result)["structural_errors"])

    def test_local_download_route_protected(self):
        import server
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            with patch.multiple(server, DATA=Path(directory), MODE="local"):
                (Path(directory) / "latest_report.md").write_text(
                    "# TEST", encoding="utf-8"
                )
                for address, expected_status in (("127.0.0.1", 200), ("8.8.8.8", 403)):
                    with self.subTest(address=address):
                        with TestClient(
                            server.api,
                            base_url="http://127.0.0.1:8000",
                            client=(address, 5000),
                        ) as client:
                            response = client.get("/api/report/markdown")
                        self.assertEqual(response.status_code, expected_status)


if __name__ == "__main__":
    unittest.main()

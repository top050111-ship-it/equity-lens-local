"""Regression tests for the actual 2026-11-16 linkage/citation failures."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agents import (EvidenceReview, Model, Report, Finding, ReviewDecision, EvidenceQuote,
                    review_report, to_dict)
from remaining_fixes import linkage_issues, repair_cross_citations, repair_synthesis, linkage_repair_instructions

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'remaining_failures.json').read_text(encoding='utf-8'))
GOOD_MECHANISM = '금리 상승이 지속되면 신규 차입의 조달비용과 미래 현금흐름의 할인율에 부담을 줄 수 있다.'
GOOD_COUNTER = '자체 현금 여력이 충분하다면 신규 차입 필요가 작아 영향이 제한될 수 있다.'


def item(mechanism=GOOD_MECHANISM, counterpoint=GOOD_COUNTER, *, symbol='RKLB'):
    result = copy.deepcopy(FIXTURE['rk_payload']['items'][0])
    claim = result['claim'].split('\n전달 경로: ', 1)[0]
    result['claim'] = claim.replace('[RKLB]', '[' + symbol + ']') + '\n전달 경로: ' + mechanism + '\n시간축: 중기 실적·현금흐름 반영 여부'
    result['counterpoint'] = counterpoint
    return result


def report(items):
    return Report(summary='', findings=[Finding.model_validate({k: i[k] for k in Finding.model_fields})
                  for i in items], conflicts=[], watchlist=[], limitations=[])


def correct_decision(i, sources):
    by_id = {s['id']: s for s in sources}
    return ReviewDecision(item_id=i['item_id'], supported=True, portfolio_linked=True, duplicate_of='',
        reason='관측과 조건부 전달 경로가 일치합니다.',
        quotes=[EvidenceQuote(evidence_id=e, field='content', quote=by_id[e]['content']) for e in i['evidence_ids']])


class LinkageRegressionTests(unittest.TestCase):
    def setUp(self):
        self.sources = FIXTURE['rk_payload']['sources']

    def check(self, mechanism, counterpoint=GOOD_COUNTER):
        return linkage_issues('synthesis', item(mechanism, counterpoint), self.sources)

    def test_every_observed_accepted_error_is_detected(self):
        by_id = {s['id']: s for s in FIXTURE['sources']}
        self.assertEqual(len(FIXTURE['accepted_links']), 10)
        for case in FIXTURE['accepted_links']:
            with self.subTest(symbol=case['symbol'], claim=case['finding']['claim']):
                f = case['finding']
                self.assertTrue(linkage_issues('synthesis', {'type': 'finding', **f}, [by_id[e] for e in f['evidence_ids']]))

    def test_correct_conditional_rate_channel_is_untouched(self):
        self.assertFalse(self.check(GOOD_MECHANISM))

    def test_inventory_only_is_not_macro_linkage(self):
        self.assertTrue(self.check('재고자산 증가가 현금 유동성을 줄여 영업현금흐름에 부담을 줄 수 있다.'))

    def test_commodity_only_does_not_explain_selected_rate(self):
        self.assertTrue(self.check('원자재 가격 변동이 펀드의 수익률에 반영될 수 있다.'))

    def test_rate_increase_is_not_demand_stimulus(self):
        self.assertTrue(self.check('금리 상승이 조달비용을 증가시켜 매출 성장이 수요 확대를 지속할 수 있도록 한다.'))

    def test_growth_despite_rates_is_allowed(self):
        self.assertFalse(self.check('금리 상승에도 불구하고 고객 수요가 확대되면 매출 성장이 지속될 수 있다.'))

    def test_yield_reflecting_recovery_is_allowed(self):
        self.assertFalse(self.check('국채금리 상승이 경기 회복을 반영한다면 수요 회복 여부를 별도로 점검할 수 있다.'))

    def test_negated_stimulus_is_allowed(self):
        self.assertFalse(self.check('금리 상승이 경기 회복을 촉진한다고 단정할 근거는 없다. 할인율 부담을 점검한다.'))

    def test_rate_cut_recovery_is_allowed(self):
        self.assertFalse(self.check('금리가 하락한다면 차입 부담 완화를 통해 수요 회복에 도움이 될 수 있다.'))

    def test_tracking_goal_is_not_tracking_error_evidence(self):
        self.assertTrue(self.check('금리 상승이 할인율을 높여 펀드의 추종 정확도를 저하시킬 수 있다.'))

    def test_tracking_risk_explicit_rate_passage_is_not_blocked(self):
        sources = copy.deepcopy(self.sources)
        sources.append({'id': 'ETF', 'kind': 'fund_document',
                        'content': 'Tracking error risk: changes in interest rates may cause a difference.', 'title': 'Risk'})
        self.assertFalse(linkage_issues('synthesis', item('금리 상승이 추종오차를 확대할 수 있다.'), sources))

    def test_equity_valuation_channel_is_allowed(self):
        self.assertFalse(self.check('금리 상승이 지속되면 편입 주식의 할인율에 부담이 되어 펀드 평가가치에 영향을 줄 수 있다.'))

    def test_financing_cost_is_not_capex_cash_outflow(self):
        self.assertTrue(self.check('금리 상승이 조달비용을 늘려 유형자산 취득 현금유출을 가속화할 수 있다.'))

    def test_fixed_capex_plan_with_more_financing_cost_is_allowed(self):
        self.assertFalse(self.check('설비투자 계획이 유지된다면 금리 상승으로 신규 차입의 조달비용이 증가할 수 있다.'))

    def test_counterpoint_contradiction_is_checked(self):
        self.assertTrue(self.check(GOOD_MECHANISM, '10년물 국채금리 상승이 경기 회복을 촉진할 수 있다.'))

    def test_original_rklb_quarter_draft_cannot_presuppose_absent_operating_cashflow(self):
        original = FIXTURE['rk_payload']['items'][0]
        by_id = {s['id']: s for s in self.sources}
        errors = linkage_issues('synthesis', original, [by_id[e] for e in original['evidence_ids']])
        self.assertTrue(any('영업현금흐름' in error for error in errors))

    def test_future_operating_cashflow_scenario_is_not_a_past_observation(self):
        self.assertFalse(self.check('금리 상승이 지속된다면 향후 이자 지급으로 영업현금흐름이 감소할 수 있다.'))

    def test_transposed_ticker_is_not_hidden_by_added_symbol(self):
        self.assertTrue(linkage_issues('synthesis', item('금리 상승이 SPXC의 조달비용에 부담을 줄 수 있다.', symbol='SPCX'), self.sources))

    def test_distinct_cited_company_ticker_is_not_rewritten(self):
        sources = self.sources + [{'id': 'OTHER', 'kind': 'filing_excerpt', 'title': 'SPXC', 'content': 'SPXC disclosed risks.'}]
        self.assertFalse(linkage_issues('synthesis', item('금리 상승이 SPXC에도 영향을 줄 수 있다.', symbol='SPCX'), sources))

    def test_non_interest_macro_channel_is_outside_new_rate_rule(self):
        sources = [s for s in self.sources if s.get('kind') != 'indicator']
        sources.append({'id': 'PCE', 'kind': 'indicator', 'content': '{"series_id":"PCEPILFE"}'})
        self.assertFalse(linkage_issues('synthesis', item('원자재 가격 변화가 펀드 구성 기업의 비용에 반영될 수 있다.'), sources))

    def test_issuer_macro_and_legacy_text_unchanged(self):
        bad = item('금리 상승이 경기 회복을 촉진할 수 있다.')
        for role in ('portfolio', 'macro'):
            self.assertFalse(linkage_issues(role, bad, self.sources))
        bad['claim'] = bad['claim'].replace('\n전달 경로: ', '\n기존 형식: ')
        self.assertFalse(linkage_issues('synthesis', bad, self.sources))


class CitationIsolationTests(unittest.TestCase):
    def setUp(self):
        self.payload = copy.deepcopy(FIXTURE['rk_payload'])
        self.reviewed = EvidenceReview.model_validate(FIXTURE['rk_review'])
        self.model = Mock()
        self.model._review_batch.side_effect = lambda role, bundle, ids: EvidenceReview(
            decisions=[correct_decision(bundle['items'][0], bundle['sources'])])

    def repair(self):
        return repair_cross_citations(self.model, 'synthesis', self.payload, ['F0', 'F1'], self.reviewed)

    def test_actual_rklb_cross_contamination_gets_isolated_once_each(self):
        original = copy.deepcopy(self.payload)
        fixed = self.repair()
        self.assertEqual(self.model._review_batch.call_count, 2)
        self.assertEqual(original, self.payload)
        for d, i in zip(fixed.decisions, self.payload['items']):
            self.assertEqual({q.evidence_id for q in d.quotes}, set(i['evidence_ids']))
        for call in self.model._review_batch.call_args_list:
            role, bundle, ids = call.args
            self.assertEqual(role, 'synthesis_citation_retry')
            allowed = set(bundle['items'][0]['evidence_ids'])
            self.assertEqual({s['id'] for s in bundle['sources']}, allowed)
            self.assertTrue(all(set(v) <= allowed for v in bundle['source_groups'].values()))
            self.assertEqual(ids, ['F0', 'F1'])

    def test_clean_batch_is_returned_without_call_or_copy(self):
        self.reviewed = EvidenceReview(decisions=[correct_decision(i, self.payload['sources']) for i in self.payload['items']])
        self.assertIs(self.repair(), self.reviewed)
        self.model._review_batch.assert_not_called()

    def test_negative_decision_does_not_get_rescued(self):
        for d in self.reviewed.decisions:
            d.supported = False
        self.assertIs(self.repair(), self.reviewed)
        self.model._review_batch.assert_not_called()

    def test_real_fabricated_quote_is_not_treated_as_batch_mixup(self):
        for d in self.reviewed.decisions:
            d.quotes[0].quote = 'THIS QUOTATION IS NOT IN ANY SOURCE'
        self.assertIs(self.repair(), self.reviewed)
        self.model._review_batch.assert_not_called()

    def test_http_failure_leaves_original_decisions(self):
        self.model._review_batch.side_effect = TimeoutError('private diagnostic')
        self.assertIs(self.repair(), self.reviewed)
        self.assertEqual(self.model._review_batch.call_count, 2)

    def test_bad_retry_cannot_pass_by_dropping_citation(self):
        def incomplete(role, bundle, ids):
            decision = correct_decision(bundle['items'][0], bundle['sources'])
            decision.quotes = decision.quotes[:1]
            return EvidenceReview(decisions=[decision])
        self.model._review_batch.side_effect = incomplete
        self.assertIs(self.repair(), self.reviewed)

    def test_duplicate_missing_or_unknown_ids_remain_original_error(self):
        for ids in (['F0', 'F0'], ['F0'], ['F0', 'F9']):
            self.reviewed = EvidenceReview(decisions=[self.reviewed.decisions[0].model_copy(update={'item_id': i}) for i in ids])
            self.assertIs(self.repair(), self.reviewed)
        self.model._review_batch.assert_not_called()

    def test_valid_sibling_survives_other_retry_error(self):
        first = correct_decision(self.payload['items'][0], self.payload['sources'])
        self.reviewed.decisions[0] = first
        self.model._review_batch.side_effect = TimeoutError()
        result = self.repair()
        self.assertEqual(to_dict(result.decisions[0]), to_dict(first))
        self.assertEqual(self.model._review_batch.call_count, 1)

    def test_legacy_caller_without_citations_is_untouched(self):
        for i in self.payload['items']:
            i.pop('evidence_ids')
        self.assertIs(self.repair(), self.reviewed)
        self.model._review_batch.assert_not_called()


class BoundedLinkRewriteTests(unittest.TestCase):
    def setUp(self):
        self.payload = copy.deepcopy(FIXTURE['rk_payload'])
        self.payload['compact_contract'] = True
        self.good = item()
        self.bad = item('금리 상승이 경기 회복을 촉진할 수 있다.')
        self.model = Mock()

    def test_correct_draft_no_call_or_copy(self):
        draft = report([self.good])
        self.assertIs(repair_synthesis(self.model, 'synthesis', draft, self.payload), draft)
        self.model.analyze.assert_not_called()

    def test_only_erroneous_item_replaced_healthy_item_unchanged(self):
        draft = report([self.good, self.bad])
        original = to_dict(draft)
        replacement = item('금리 상승이 지속되면 할인율 부담을 점검할 수 있다.')
        self.model.analyze.return_value = report([replacement])
        fixed = repair_synthesis(self.model, 'synthesis', draft, self.payload)
        self.model.analyze.assert_called_once()
        self.assertEqual(to_dict(fixed.findings[0]), original['findings'][0])
        self.assertEqual(to_dict(draft), original)
        self.assertEqual(fixed.findings[1].claim, replacement['claim'])
        request = self.model.analyze.call_args.args[1]
        self.assertEqual(request['repair_policy']['maximum_replacements'], 1)
        self.assertEqual(request['sources'], self.payload['sources'])

    def test_rewrite_failure_retains_healthy_only(self):
        self.model.analyze.side_effect = TimeoutError()
        fixed = repair_synthesis(self.model, 'synthesis', report([self.good, self.bad]), self.payload)
        self.assertEqual(len(fixed.findings), 1)
        self.assertEqual(fixed.findings[0].claim, self.good['claim'])
        self.model.analyze.assert_called_once()

    def test_extra_model_findings_do_not_expand_scope(self):
        self.model.analyze.return_value = report([self.good, self.good])
        fixed = repair_synthesis(self.model, 'synthesis', report([self.bad]), self.payload)
        self.assertEqual(len(fixed.findings), 1)

    def test_invalid_rewrite_still_fails_original_acceptance_gates(self):
        self.model.analyze.return_value = report([self.bad])
        fixed = repair_synthesis(self.model, 'synthesis', report([self.bad]), self.payload)
        checked, audit = review_report(self.model, 'synthesis', fixed, self.payload)
        self.assertFalse(checked.findings)
        self.assertFalse(audit[0]['accepted'])
        self.model.review.assert_not_called()

    def test_non_compact_issuer_and_macro_drafts_are_untouched(self):
        draft = report([self.bad])
        for role in ('portfolio', 'macro'):
            self.assertIs(repair_synthesis(self.model, role, draft, self.payload), draft)
        self.assertIs(repair_synthesis(self.model, 'synthesis', draft, {**self.payload, 'compact_contract': False}), draft)
        self.model.analyze.assert_not_called()

    def test_repair_instructions_only_apply_to_error_path(self):
        self.assertEqual(linkage_repair_instructions(self.payload), '')
        self.assertIn('최대 한 개', linkage_repair_instructions({**self.payload, '_selective_link_repair': True}))

    def test_native_compact_schema_only_shrinks_on_error_path(self):
        from agents import CompactLinks
        model = Model.__new__(Model)
        model._generate_compact = Mock(return_value=CompactLinks(findings=[]))
        model.analyze('synthesis', self.payload)
        normal = model._generate_compact.call_args.args
        self.assertEqual(normal[4]['properties']['findings']['maxItems'], 2)
        self.assertNotIn('이번 호출은 오류 연결의 재작성', normal[0])
        model.analyze('synthesis', {**self.payload, '_selective_link_repair': True})
        repair = model._generate_compact.call_args.args
        self.assertEqual(repair[4]['properties']['findings']['maxItems'], 1)
        self.assertEqual(repair[0], normal[0] + linkage_repair_instructions({**self.payload, '_selective_link_repair': True}))
        self.assertNotIn('enum', normal[4]['$defs']['CompactLink']['properties']['counterpoint'])
        self.assertEqual(len(repair[4]['$defs']['CompactLink']['properties']['counterpoint']['enum']), 3)

    def test_non_rate_macro_repair_does_not_introduce_a_rate_channel(self):
        payload = {'_selective_link_repair': True, 'source_groups': {'macro': ['PCE']},
                   'sources': [{'id': 'PCE', 'kind': 'indicator', 'content': '{"series_id":"PCEPILFE"}'}]}
        instructions = linkage_repair_instructions(payload)
        self.assertIn('선택한 거시 지표', instructions)
        self.assertNotIn('주식 펀드의 예', instructions)

    def test_positive_model_review_cannot_override_causal_error(self):
        checked, audit = review_report(self.model, 'synthesis', report([self.bad]), self.payload)
        self.assertFalse(checked.findings)
        self.assertFalse(audit[0]['accepted'])
        self.model.review.assert_not_called()

    def test_native_review_integration_retries_only_wrong_scope(self):
        model = Model.__new__(Model)
        model.backend = 'ollama'
        model._review_batch = Mock(side_effect=[self._mixed_actual(),
            EvidenceReview(decisions=[correct_decision(self.payload['items'][0], self.payload['sources'])]),
            EvidenceReview(decisions=[correct_decision(self.payload['items'][1], self.payload['sources'])])])
        reviewed = model.review('synthesis', self.payload)
        self.assertEqual(model._review_batch.call_count, 3)
        self.assertEqual(len(reviewed.decisions), 2)
        for d, i in zip(reviewed.decisions, self.payload['items']):
            self.assertEqual({q.evidence_id for q in d.quotes}, set(i['evidence_ids']))

    def _mixed_actual(self):
        return EvidenceReview.model_validate(FIXTURE['rk_review'])


class SavedReportScopeTests(unittest.TestCase):
    def setUp(self):
        from deep_research import Insight
        payload = copy.deepcopy(FIXTURE['rk_payload'])
        p_ids = payload['items'][0]['evidence_ids'][:1]
        insight = Insight(topic='risk', title='재무 부담', kind='inference', observation='공식 재무 관측',
            interpretation='재무 부담을 점검한다.', mechanism='현금 여력에 따라 자금조달 필요가 달라진다.',
            horizon='다음 공시', countercase='현금 여력이 충분하면 영향이 제한된다.',
            monitor='다음 공시의 현금', impact='uncertain', evidence_ids=p_ids)
        company = {'symbol': 'RKLB', 'status': 'ok', 'macro_status': 'failed',
                   'asset_type': 'company', 'sector': 'general', 'coverage': [], 'warnings': [],
                   'source_budget': {'available': 1, 'selected': 1, 'omitted': 0, 'characters': 0},
                   'insights': [to_dict(insight)], 'quality_audit': [], 'macro_links': [],
                   'macro_audit': [], 'macro_warnings': ['기존 실패'], 'records': [], 'metrics': [],
                   'source_ids': p_ids, 'custom_field': {'preserve_me': True}}
        m_ids = payload['source_groups']['macro']
        macro_findings = []
        for eid in m_ids:
            f = Finding(subject='금리', claim='금리 관측', kind='inference', evidence_ids=[eid],
                        confidence='medium', counterpoint='방향이 바뀔 수 있다.', revisit_when='다음 발표')
            macro_findings.append(to_dict(f))
        self.previous = {'created_at': '2026-11-16T10:00:00+00:00', 'run_id': 'previous', 'warnings': [],
            'sources': payload['sources'], 'portfolio_context': {}, 'complete': False,
            'portfolio': {'status': 'ok', 'report': to_dict(report([item()]))},
            'macro': {'status': 'ok', 'report': {'summary': '', 'findings': macro_findings,
                      'conflicts': [], 'watchlist': [], 'limitations': []}},
            'synthesis': {'status': 'failed'},
            'research_v2': {'companies': [company], 'quality': {'companies': 1, 'verified_companies': 1,
                'linked_companies': 0, 'accepted_insights': 1, 'reviewed_insights': 0},
                'source_groups': {'portfolio': p_ids, 'macro': m_ids}, 'model_calls': []}}
        self.model = Mock()
        self.model.telemetry = []
        self.model.backend = 'test-double'
        self.model.model = 'scope-test'
        self.model.analyze.return_value = report([item()])
        self.model.review.side_effect = lambda role, payload: EvidenceReview(
            decisions=[correct_decision(i, payload['sources']) for i in payload['items']])

    def repair(self, **kwargs):
        from repair_saved_links import repair_result
        return repair_result(self.previous, self.model, **kwargs)

    def test_failed_link_only_preserves_all_other_company_and_macro_fields(self):
        original = copy.deepcopy(self.previous)
        fixed = self.repair()
        self.assertTrue(fixed['complete'])
        self.assertEqual(original, self.previous)
        self.assertEqual(fixed['macro'], original['macro'])
        self.assertEqual(fixed['portfolio'], original['portfolio'])
        self.assertEqual(fixed['sources'], original['sources'])
        old, new = original['research_v2']['companies'][0], fixed['research_v2']['companies'][0]
        for key in set(old) - {'macro_status', 'macro_links', 'macro_audit', 'macro_warnings'}:
            self.assertEqual(old[key], new[key])
        self.model.research.assert_not_called()
        self.assertTrue(all(c.args[0] == 'synthesis' for c in self.model.analyze.call_args_list))
        self.assertTrue(self.model.analyze.call_args.args[1]['_selective_link_repair'])

    def test_healthy_report_makes_no_model_calls(self):
        c = self.previous['research_v2']['companies'][0]
        c['macro_status'] = 'ok'; c['macro_links'] = to_dict(report([item()]))['findings']
        self.previous['research_v2']['quality']['linked_companies'] = 1
        fixed = self.repair()
        self.assertEqual(fixed, self.previous)
        self.model.analyze.assert_not_called(); self.model.review.assert_not_called()

    def test_existing_healthy_finding_and_audit_are_not_regenerated(self):
        c = self.previous['research_v2']['companies'][0]
        healthy = to_dict(report([item()]))['findings'][0]
        bad = to_dict(report([item('금리 상승이 경기 회복을 촉진할 수 있다.')]))['findings'][0]
        c['macro_status'] = 'ok'; c['macro_links'] = [healthy, bad]
        c['macro_audit'] = [{'item_id': 'F0', 'accepted': True, 'reason': 'original', 'quotes': []}]
        self.previous['research_v2']['quality']['linked_companies'] = 1
        fixed = self.repair()
        new = fixed['research_v2']['companies'][0]
        self.assertEqual(new['macro_links'][0], healthy)
        self.assertEqual(new['macro_audit'][0], c['macro_audit'][0])
        passed_items = self.model.review.call_args.args[1]['items']
        self.assertEqual(len(passed_items), 1)

    def test_unknown_symbol_does_not_start_calls(self):
        with self.assertRaises(ValueError): self.repair(symbols=['UNKNOWN'])
        self.model.analyze.assert_not_called()

    def test_failed_repair_is_not_forced_to_complete(self):
        self.model.analyze.side_effect = TimeoutError()
        fixed = self.repair()
        self.assertFalse(fixed['complete'])
        self.assertEqual(fixed['research_v2']['quality']['linked_companies'], 0)

    def test_previous_subset_cannot_become_full_portfolio_complete(self):
        self.previous['research_v2']['replay'] = {'full_portfolio': False}
        fixed = self.repair()
        self.assertFalse(fixed['complete'])

    def test_cli_writes_new_json_and_visible_markdown_without_touching_inputs(self):
        from common import now
        from repair_saved_links import main
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / 'original.json'
            holdings = folder / 'holdings.json'
            source.write_text(json.dumps(self.previous, ensure_ascii=False), encoding='utf-8')
            # A temporary unit-test valuation only; never a personal report.
            holdings.write_text(json.dumps({'as_of': now(), 'positions': [
                {'symbol': 'RKLB', 'name': 'QA fixture', 'market': 'NASDAQ', 'currency': 'USD',
                 'quantity': '1', 'price': '1', 'price_at': now(), 'price_source': 'unit-test stub'}],
                'fx': {'midRate': '1', 'validFrom': now(), 'validUntil': '2099-01-01T00:00:00+00:00'}}))
            before = source.read_bytes(), holdings.read_bytes()
            argv = ['repair_saved_links.py', '--report', str(source), '--holdings', str(holdings), '--output', str(folder / 'output')]
            with patch('sys.argv', argv), patch('repair_saved_links.Model', return_value=self.model), \
                    patch('repair_saved_links.load_env'), patch.dict(os.environ, {'LLM_BACKEND': 'ollama', 'LLM_MODEL': 'qwen3:4b'}):
                self.assertEqual(main(), 0)
            self.assertEqual(before, (source.read_bytes(), holdings.read_bytes()))
            outputs = list((folder / 'output').glob('links_*.json'))
            self.assertEqual(len(outputs), 1)
            text = outputs[0].with_suffix('.md').read_text(encoding='utf-8')
            self.assertIn('원자료 수집 회차:', text)
            self.assertIn('기업·거시 연결만 복구', text)


if __name__ == '__main__':
    unittest.main()

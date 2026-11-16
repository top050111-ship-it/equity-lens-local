#!/usr/bin/env python3
# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repair only failed/invalid company–macro links in an existing report JSON."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from agents import Model, Report, failure, rebuild_summary, review_report, safe_analyze, to_dict
from common import now, load_env, write_private
from deep_research import Insight, aggregate, as_finding, validate_deep_result
from remaining_fixes import linkage_issues, repair_synthesis
from model_runtime import runtime_info


def _issues(finding, sources):
    return linkage_issues('synthesis', {'type': 'finding', **finding},
                          [sources[e] for e in finding['evidence_ids'] if e in sources])


def repair_result(previous, model, *, symbols=None):
    """No collector, broker connection, issuer or independent macro model calls."""
    original = copy.deepcopy(previous)
    result = copy.deepcopy(previous)
    deep = result['research_v2']
    sources = {s['id']: s for s in result['sources']}
    validate_deep_result(deep, set(sources))
    companies = deep['companies']
    known = {c['symbol'] for c in companies}
    selected = set(symbols) if symbols is not None else known
    if not selected <= known:
        raise ValueError('보고서에 없는 종목을 요청했습니다.')
    if result['macro']['status'] != 'ok':
        raise ValueError('이 도구는 정상 거시 분석이 있는 보고서의 연결 단계만 복구합니다.')
    start_call = len(getattr(model, 'telemetry', []))
    repairs = []
    stages = []
    for company in companies:
        existing = company.get('macro_links', [])
        errors = [reason for f in existing for reason in _issues(f, sources)]
        needs_repair = company.get('macro_status') != 'ok' or bool(errors)
        if company['symbol'] not in selected or not needs_repair or company['status'] != 'ok':
            stages.append({'status': company.get('macro_status', 'failed'),
                           'report': to_dict(rebuild_summary(Report(summary='', findings=existing,
                               conflicts=[], watchlist=[], limitations=company.get('macro_warnings', []))))})
            continue
        issuer = [to_dict(as_finding(Insight.model_validate(i), company['symbol']))
                  for i in company['insights'][:2]]
        macro = result['macro']['report']['findings'][:2]
        p_ids = {e for f in issuer for e in f['evidence_ids']}
        m_ids = {e for f in macro for e in f['evidence_ids']}
        payload = {
            'as_of': now(), 'portfolio_as_of': result.get('portfolio_context', {}).get('holdings_as_of'),
            'positions': [{'symbol': company['symbol'], 'price_quality': '시세 갱신 없이 저장한 근거만 재검토'}],
            'compact_contract': True, 'portfolio_agent': {'findings': issuer}, 'macro_agent': {'findings': macro},
            'sources': [copy.deepcopy(s) for s in result['sources'] if s['id'] in p_ids | m_ids],
            'source_groups': {'portfolio': sorted(p_ids), 'macro': sorted(m_ids)}, 'warnings': [],
            'scope': '이 종목의 실패·인과 오류 연결만 복구. 기업·독립 거시 분석과 원자료는 보존.',
        }
        if company.get('macro_status') != 'ok':
            # This stage has already failed in the saved run. Start with the
            # bounded repair contract instead of repeating the original draft.
            payload['_selective_link_repair'] = True
        print('[저장 보고서 연결 복구] ' + company['symbol'], flush=True)
        if not p_ids or not m_ids:
            stage = failure('기업·거시의 검토 통과 근거 부족')
        elif existing and errors:
            try:
                draft = Report(summary='', findings=[f for f in existing if _issues(f, sources)],
                               conflicts=[], watchlist=[], limitations=[])
                diagnostic_draft = to_dict(draft)
                draft = repair_synthesis(model, 'synthesis', draft, payload)
                checked, audit = review_report(model, 'synthesis', draft, payload)
                stage = {'status': 'ok' if checked.findings else 'failed', 'report': to_dict(checked),
                         'quality_audit': audit, 'diagnostic_draft': {'verified': False, 'report': diagnostic_draft}}
            except Exception as exc:
                stage = failure('연결 복구/검토 실패 (' + type(exc).__name__ + ')')
        else:
            stage = safe_analyze(model, 'synthesis', payload)
        # Healthy linkages are not sent to the model again.
        healthy = [f for f in existing if not _issues(f, sources)]
        reviewed_links = stage['report']['findings']
        if healthy:
            stage['report']['findings'] = copy.deepcopy(healthy) + reviewed_links
            stage['status'] = 'ok'
        stage['report']['findings'] = stage['report']['findings'][:max(2, len(existing))]
        before = {key: copy.deepcopy(company.get(key)) for key in
                  ('macro_status', 'macro_links', 'macro_audit', 'macro_warnings')}
        company['macro_status'] = stage['status']
        company['macro_links'] = copy.deepcopy(stage['report']['findings'])
        healthy_ids = {'F' + str(index) for index, f in enumerate(existing) if not _issues(f, sources)}
        kept_audit = [copy.deepcopy(a) for a in company.get('macro_audit', []) if a.get('item_id') in healthy_ids]
        for audit in stage.get('quality_audit', []):
            kept_audit.append({**copy.deepcopy(audit), 'item_id': 'REPAIR_' + audit['item_id']})
        company['macro_audit'] = kept_audit
        company['macro_warnings'] = list(stage['report'].get('limitations', []))
        repairs.append({'symbol': company['symbol'], 'previous': before,
                        'reasons': errors or ['이전 연결 검토 실패'],
                        'status': company['macro_status'], 'diagnostic_draft': stage.get('diagnostic_draft')})
        stages.append(stage)
    if not repairs:
        return result
    result['synthesis'] = aggregate(stages)
    deep['quality']['linked_companies'] = sum(c['macro_status'] == 'ok' for c in companies)
    all_stages = bool(companies) and all(c['status'] == 'ok' and c['macro_status'] == 'ok' for c in companies)
    subset = deep.get('replay', {}).get('full_portfolio') is False
    remaining = [c['symbol'] for c in companies if c.get('macro_status') != 'ok'
                 or any(_issues(f, sources) for f in c.get('macro_links', []))]
    result['complete'] = all_stages and result['macro']['status'] == 'ok' and not subset and not remaining
    result['created_at'] = now()
    result['run_id'] = 'link-repair-' + result['created_at'].replace(':', '').replace('.', '')
    new_calls = copy.deepcopy(getattr(model, 'telemetry', [])[start_call:])
    deep.setdefault('model_calls', []).extend(new_calls)
    deep['link_repair'] = {'at': result['created_at'], 'source_created_at': previous['created_at'],
        'source_run_id': previous.get('run_id'), 'repairs': repairs,
        'source_runtime': copy.deepcopy(previous.get('diagnostics', {}).get('runtime')),
        'remaining_symbols': remaining,
        'calls_added': len(new_calls), 'sources_recollected': False,
        'model': {'backend': getattr(model, 'backend', 'test-double'), 'model': getattr(model, 'model', 'test-double')},
        'preserved': ['issuer analysis', 'independent macro', 'sources', 'financial records', 'metrics'],
        'patch_sha256': hashlib.sha256(Path(__file__).with_name('remaining_fixes.py').read_bytes()).hexdigest()}
    result.setdefault('diagnostics', {})['runtime'] = runtime_info()
    # Runtime invariants protect the scope even if the implementation changes.
    for old, new in zip(original['research_v2']['companies'], companies):
        preserved_keys = set(old) - {'macro_status', 'macro_links', 'macro_audit', 'macro_warnings'}
        if any(old[k] != new[k] for k in preserved_keys):
            raise ValueError('보존해야 하는 기업 분석이 변경되었습니다.')
    if original['macro'] != result['macro'] or original['sources'] != result['sources']:
        raise ValueError('보존해야 하는 거시 분석 또는 원자료가 변경되었습니다.')
    validate_deep_result(deep, set(sources))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, help='기존 data/reports/*.json')
    parser.add_argument('--holdings', required=True, help='기존 ../data/frozen_portfolio.json; 읽기만 수행')
    parser.add_argument('--output', default=str(Path(__file__).resolve().parent.parent / 'data' / 'link_repairs'))
    parser.add_argument('--symbols', nargs='+', help='지정하지 않으면 오류가 있는 종목만 자동 선택')
    args = parser.parse_args()
    report_path = Path(args.report).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    load_env(Path(__file__).resolve().parent / '.env')
    from holdings_cache import research_snapshot
    from broker import value_snapshot
    from research_render import render_deep_report
    previous = json.loads(report_path.read_text(encoding='utf-8'))
    snapshot = value_snapshot(research_snapshot(Path(args.holdings).expanduser()))
    required = {c['symbol'] for c in previous['research_v2']['companies']}
    if not required <= {p['symbol'] for p in snapshot['positions']}:
        parser.error('저장 보고서의 종목을 모두 포함하는 보유 파일이 필요합니다.')
    saved_model = previous.get('model', {})
    os.environ.setdefault('LLM_BACKEND', saved_model.get('backend', 'ollama'))
    os.environ.setdefault('LLM_MODEL', saved_model.get('model', 'qwen3:4b'))
    model = Model()
    output.mkdir(parents=True, exist_ok=True)
    model.diagnostic_path = output / 'link_repair_calls.jsonl'
    repaired = repair_result(previous, model, symbols=args.symbols)
    if repaired == previous:
        print('선택한 범위에 복구할 연결 오류가 없습니다. 파일을 변경하지 않았습니다.')
        return 0
    name = 'links_' + repaired['created_at'].replace(':', '').replace('.', '').replace('+', '_')
    json_path = output / (name + '.json')
    if json_path == report_path:
        parser.error('원본 보고서를 덮어쓰지 않습니다.')
    md_path = output / (name + '.md')
    text = ('> 저장 보고서의 기업·거시 연결만 복구했습니다. 원자료 수집 회차: '
            + str(previous['created_at']) + '. 기업 분석·독립 거시 분석·원자료는 해당 회차 그대로입니다.\n\n'
            + render_deep_report(repaired, snapshot))
    write_private(json_path, repaired)
    write_private(md_path, text)
    print('복구 보고서:', md_path)
    print('상태:', 'complete' if repaired['complete'] else 'partial')
    return 0 if repaired['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

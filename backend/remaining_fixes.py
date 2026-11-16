# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded repairs for the citation/linkage failures in the November 16 report.

No collection, issuer analysis, macro analysis, settings or acceptance rules are
replaced. Healthy drafts and review decisions take the original path unchanged.
"""
from __future__ import annotations

import copy
import json
import re


def _source_text(sources):
    return '\n'.join(str(s.get('title', '')) + '\n' + str(s.get('content', '')) for s in sources)


def _mechanism(item):
    claim = str(item.get('claim', ''))
    # Scope these additional rules to the code-built compact linkage contract.
    # Do not accidentally inspect the automatically inserted observations.
    if '\n전달 경로: ' not in claim or '\n시간축: ' not in claim:
        return None
    return claim.split('\n전달 경로: ', 1)[1].split('\n시간축: ', 1)[0].strip()


def _interest_series(source):
    if source.get('kind') != 'indicator':
        return False
    try:
        data = json.loads(source.get('content', ''))
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and data.get('series_id') in {
        'FEDFUNDS', 'DFF', 'DGS2', 'DGS5', 'DGS10', 'DGS30', 'DFII10',
    }


_RATE_CHANNEL = re.compile(
    r'금리|이자율|연방기금|국채|할인율|조달\s*비용|차입|차환|자본\s*비용|'
    r'대출|신용|금융\s*(?:여건|비용)|interest\s*rate|discount\s*rate|financing\s*cost', re.I)
_RATE_UP = re.compile(r'(?:금리|이자율|할인율|조달\s*비용).{0,18}(?:상승|높아|증가|인상)', re.I)
_TRACKING = re.compile(r'추종.{0,14}(?:정확|오차|괴리)|추적\s*오차|tracking\s*(?:error|accuracy)', re.I)
_TRACKING_PROOF = re.compile(r'tracking\s*(?:error|risk)|추종\s*오차|추적\s*오차', re.I)
_CAPEX_UP = re.compile(
    r'(?:유형\s*자산.{0,20}(?:취득|현금\s*유출)|설비\s*투자|자본적\s*지출|capex)'
    r'.{0,24}(?:확대|증가|가속)', re.I)


def _explicit_tracking_evidence(sources):
    # A fund's index-tracking objective is not evidence of rate-induced
    # tracking error. Require an actual tracking-risk passage mentioning rates.
    for source in sources:
        if source.get('kind') == 'indicator':
            continue
        text = _source_text([source])
        for match in _TRACKING_PROOF.finditer(text):
            window = text[max(0, match.start() - 240):match.end() + 240]
            if re.search(r'interest\s*rate|금리|이자율', window, re.I):
                return True
    return False


def _positive_rate_causality(text):
    if not _RATE_UP.search(text):
        return False
    # These exceptions preserve a distinction explicitly missing in the report:
    # rates reflecting growth, growth despite rates, and negated causality.
    if re.search(r'반영|불구|상쇄|단정.{0,6}(?:못|없)|(?:의미|보장)하지|'
                 r'(?:촉진|부양|도움|확대).{0,8}(?:않|아니|없)|'
                 r'근거.{0,8}(?:없|부족)', text):
        return False
    recovery = re.search(r'경(?:기|제)\s*회복.{0,35}(?:촉진|부양|도움|지원)', text)
    demand = re.search(r'(?:수요|소비|경제\s*활동).{0,18}(?:확대|증가).{0,18}'
                       r'(?:지속|촉진|가능|돕|도움|하도록|시킨|유도)', text)
    return bool(recovery or demand)


def linkage_issues(role, item, cited):
    """Additional rejection reasons for the observed compact-linkage errors.

    This is deliberately not a general financial truth checker. Numeric, date,
    period, source-scope and semantic review remain in agents.review_report.
    """
    if role != 'synthesis' or item.get('type', 'finding') != 'finding':
        return []
    mechanism = _mechanism(item)
    if mechanism is None:
        return []
    issues = []
    rate_evidence = any(_interest_series(s) for s in cited)
    if rate_evidence and not _RATE_CHANNEL.search(mechanism):
        issues.append('전달 경로에 선택한 금리 지표의 영향 경로가 없음')
    free_fields = [mechanism, str(item.get('counterpoint', ''))]
    for text in free_fields:
        if _positive_rate_causality(text):
            issues.append('금리·조달비용 상승을 경기 회복·수요 확대로 직접 연결한 인과 오류')
        if re.search(r'경(?:기|제)\s*회복.{0,25}도움.{0,20}부정적\s*요인', text):
            issues.append('반대 설명에서 경기 회복의 도움과 부정적 요인을 혼동함')
    if (_RATE_UP.search(mechanism) and _TRACKING.search(mechanism)
            and not re.search(r'(?:영향|관계).{0,12}(?:없|않|확인되지)|단정.{0,8}(?:못|없)', mechanism)
            and not _explicit_tracking_evidence(cited)):
        issues.append('금리 변화가 ETF 지수 추종오차를 악화시킨다는 근거가 없음')
    if (_RATE_UP.search(mechanism) and _CAPEX_UP.search(mechanism)
            and not re.search(r'반영|불구|단정|확인.{0,6}(?:못|않)|근거.{0,6}없', mechanism)):
        company_text = _source_text([s for s in cited if s.get('kind') != 'indicator'])
        # Financial amounts alone do not establish this causal relationship.
        explicit = re.search(r'(?:higher\s+interest\s+rates?|금리\s*상승).{0,150}'
                             r'(?:increas.{0,30}capital\s+expend|설비\s*투자\s*증가)',
                             company_text, re.I | re.S)
        if not explicit:
            issues.append('금리·조달비용 상승을 유형자산 취득 현금유출 증가로 직접 연결한 근거가 없음')
    # The failed RKLB draft cited a revenue/profit-only quarter summary while
    # presupposing an already-declining operating cash flow. A conditional future
    # effect is different and remains eligible for normal semantic review.
    presupposed_cf = re.search(r'영업\s*현금\s*흐름(?:의)?\s*(?:감소|악화)(?:를|세를)\s*(?:악화|가속|심화)', mechanism)
    if presupposed_cf:
        company_text = _source_text([s for s in cited if s.get('kind') != 'indicator'])
        if not re.search(r'operating_cashflow|NetCashProvidedByUsedInOperatingActivities|'
                         r'cash\s*flows?.{0,45}operating|영업.{0,12}현금\s*흐름', company_text, re.I):
            issues.append('선택한 기업 근거에 없는 영업현금흐름의 기존 감소를 전제함')
    symbol_match = re.match(r'\[([A-Z][A-Z0-9.]{2,7})\]', str(item.get('claim', '')))
    if symbol_match:
        symbol = symbol_match.group(1)
        source_text = _source_text(cited)
        for i in range(len(symbol) - 1):
            variant = symbol[:i] + symbol[i + 1] + symbol[i] + symbol[i + 2:]
            if variant == symbol:
                continue
            ticker_pattern = r'(?<![A-Za-z0-9_])' + re.escape(variant) + r'(?![A-Za-z0-9_])'
            if (re.search(ticker_pattern, ' '.join(free_fields))
                    and not re.search(ticker_pattern, source_text)):
                issues.append('전달 경로·반대 설명에 보유 종목과 철자가 뒤바뀐 미확인 티커가 있음')
                break
    return list(dict.fromkeys(issues))


REPAIR_COUNTERPOINTS = (
    '향후 거시 지표의 방향이 달라지면 위 경로의 영향도 달라질 수 있다.',
    '기업·편입자산의 현금흐름이나 실적에 따라 실제 영향이 제한될 수 있다.',
    '관측한 거시 지표만으로 실제 영향의 크기와 발생 시점을 단정할 수 없다.',
)


def linkage_repair_schema(schema, payload):
    """Leave normal schemas intact; bound only an already-failed rewrite."""
    if not payload.get('_selective_link_repair'):
        return schema
    fixed = copy.deepcopy(schema)
    fixed['properties']['findings']['maxItems'] = 1
    fixed['$defs']['CompactLink']['properties']['counterpoint']['enum'] = list(REPAIR_COUNTERPOINTS)
    return fixed


def linkage_repair_instructions(payload):
    """No change to normal prompts; instructions apply only after a real error."""
    if not payload.get('_selective_link_repair'):
        return ''
    common = (
        '\n이번 호출은 오류 연결의 재작성이다. findings는 최대 한 개다. '
        '기존 문구를 복사하지 말고 선택한 기업·거시 근거의 조건부 연결 하나를 새로 쓴다. '
        '선택한 거시 지표를 mechanism에 명시한다. 관측 수치를 반복하거나 원인을 만들어내지 않는다. '
        'counterpoint는 스키마에 제공된 향후 점검 조건 중 하나를 그대로 선택한다. '
        '보유 symbol 철자는 positions와 같아야 한다. 근거로 연결할 수 없으면 빈 findings다.'
    )
    macro_ids = set(payload.get('source_groups', {}).get('macro', []))
    macro_sources = [s for s in payload.get('sources', []) if s['id'] in macro_ids]
    if not macro_sources or not all(_interest_series(s) for s in macro_sources):
        return common
    return common + (
        '금리 상승 자체가 경기 회복·매출 성장·수요 확대를 일으킨다고 쓰지 않는다. '
        '금리 상승에 따른 조달비용과 유형자산 취득 지출을 혼동하지 않는다. '
        '펀드의 시장 가치와 벤치마크 추종오차는 다르다. 추종 정확도 저하를 만들어내지 않는다. '
        '기업의 예: 높은 금리가 지속되고 외부 차입이 필요해진다면 신규 자금조달의 이자 부담이 커질 수 있다. '
        '주식 펀드의 예: 높은 금리가 지속된다면 편입 주식의 할인율 부담이 펀드 평가가치에 영향을 줄 수 있다. '
        '예시는 사실이 아니다. 선택한 자료가 맞는 경로만 사용하고 관련 없는 예시는 쓰지 않는다. '
    )


def _valid_quotes(decision, item, sources):
    allowed = set(item.get('evidence_ids', []))
    covered = set()
    for quote in decision.quotes:
        source = sources.get(quote.evidence_id)
        if (source is None or quote.evidence_id not in allowed
                or len(quote.quote.strip()) < 4
                or quote.quote not in str(source.get(quote.field, ''))):
            return False
        covered.add(quote.evidence_id)
    return bool(allowed) and covered == allowed


def repair_cross_citations(model, role, bundle, all_item_ids, reviewed):
    """Retry only a positive decision polluted by another batch item's quotes.

    At most one isolated call per affected item. Never strip inconvenient quotes
    and call that acceptance; the scoped retry must pass the original checks.
    A failed repair retains the original rejected decision and healthy siblings.
    """
    from agents import EvidenceReview, to_dict
    from evidence_grounding import review_contradicts_support

    items = {i['item_id']: i for i in bundle['items']}
    # Preserve the original missing/duplicate/unknown-ID rejection behavior.
    ids = [d.item_id for d in reviewed.decisions]
    if len(ids) != len(set(ids)) or set(ids) != set(items):
        return reviewed
    sources = {s['id']: s for s in bundle['sources']}
    changed = False
    result = []
    for decision in reviewed.decisions:
        item = items[decision.item_id]
        allowed = set(item.get('evidence_ids', []))
        extra = [q for q in decision.quotes if q.evidence_id not in allowed]
        real_quotes = all(q.evidence_id in sources and len(q.quote.strip()) >= 4
                          and q.quote in str(sources[q.evidence_id].get(q.field, ''))
                          for q in decision.quotes)
        if (not allowed or not extra or not real_quotes or not decision.supported
                or review_contradicts_support(decision.reason)):
            result.append(decision)
            continue
        scoped = copy.deepcopy(bundle)
        scoped['items'] = [copy.deepcopy(item)]
        scoped['sources'] = [s for s in scoped['sources'] if s['id'] in allowed]
        scoped['source_groups'] = {key: [e for e in ids if e in allowed]
                                   for key, ids in scoped.get('source_groups', {}).items()}
        scoped['other_items'] = list(scoped.get('other_items', [])) + [
            {'item_id': other['item_id'], 'type': other.get('type', 'finding'),
             'claim': str(other.get('claim', other.get('assessment', '')))[:350]}
            for other in bundle['items'] if other['item_id'] != item['item_id']]
        print('[인용 재검토] ' + role + ' · ' + item['item_id'] + ' · 해당 논점 출처만 사용 · 1/1', flush=True)
        try:
            retried = EvidenceReview.model_validate(to_dict(model._review_batch(
                role + '_citation_retry', scoped, all_item_ids)))
            if len(retried.decisions) != 1 or retried.decisions[0].item_id != item['item_id']:
                raise ValueError('unexpected scoped review IDs')
            replacement = retried.decisions[0]
            if (replacement.supported and not review_contradicts_support(replacement.reason)
                    and _valid_quotes(replacement, item, sources)):
                decision = replacement
                changed = True
        except Exception as exc:
            # No HTTP retry, no exception body or credentials in the log.
            print('[인용 재검토 미완료] ' + item['item_id'] + ' · ' + type(exc).__name__, flush=True)
        result.append(decision)
    return EvidenceReview(decisions=result) if changed else reviewed


def repair_synthesis(model, role, draft, payload):
    """Rewrite only compact linkages with the specific deterministic errors.

    The normal review still decides acceptance. Valid findings, issuer results,
    macro results and data sources are not regenerated. One bounded rewrite.
    """
    if role != 'synthesis' or not payload.get('compact_contract'):
        return draft
    from agents import Report, to_dict

    sources = {s['id']: s for s in payload['sources']}
    bad = []
    for index, finding in enumerate(draft.findings):
        cited = [sources[e] for e in finding.evidence_ids if e in sources]
        issues = linkage_issues(role, {'type': 'finding', **to_dict(finding)}, cited)
        if issues:
            bad.append((index, issues))
    if not bad:
        return draft
    request = copy.deepcopy(payload)
    bad_indices = {i for i, _ in bad}
    request['_selective_link_repair'] = True
    request['repair_notes'] = [{'item_index': i, 'errors': errors} for i, errors in bad]
    request['repair_policy'] = {
        'maximum_replacements': 1,
        'instructions': (
            '이전 오류 논점만 다시 작성한다. 최대 maximum_replacements개이며 새로운 사실을 추가하지 않는다. '
            '각 mechanism은 선택한 거시 지표의 조건 → 기업/펀드의 노출 → 가능한 영향 순서다. '
            '금리 상승은 할인율·차환·조달 부담 등으로 연결하고, 경기 회복·매출 성장을 일으킨다고 쓰지 않는다. '
            '유형자산 취득 지출을 이자비용과 혼동하지 않는다. ETF의 주가·평가가치와 지수 추종오차를 구분한다. '
            '금리 자료를 선택했다면 금리 경로를 문장에 명시한다. 보유 symbol 철자를 그대로 사용한다. '
            '반대 설명은 기업 현금 여력·수요·금리 방향 등 실제 가능한 상쇄 조건으로 쓴다. '
            '해당 근거에 없는 계정의 감소·성장 원인은 주장하지 않는다. 연결 불가 시 빈 findings다.'),
        'preserved_findings': [to_dict(f) for i, f in enumerate(draft.findings) if i not in bad_indices],
    }
    print('[연결 문장 재작성] 오류 ' + str(len(bad)) + '개만 · 1/1', flush=True)
    result = draft.model_copy(deep=True)
    # Remove unsafe statements even if the rewrite or review later fails.
    result.findings = [f.model_copy(deep=True) for i, f in enumerate(draft.findings) if i not in bad_indices]
    result.limitations.append('인과·연결 오류가 확인된 문장만 한 번 재작성하고 동일한 근거 검토를 적용했습니다.')
    result.limitations.append('재작성한 연결의 반대 설명은 확인된 변화가 아니라 향후 점검 조건입니다.')
    for index, issues in bad:
        result.limitations.append('원래 연결 F' + str(index) + ' 재작성 사유: ' + '; '.join(issues))
    try:
        replacement = Report.model_validate(to_dict(model.analyze(role, request)))
        replacements = iter(replacement.findings[:1])
        combined = []
        for index, finding in enumerate(draft.findings):
            if index not in bad_indices:
                combined.append(finding.model_copy(deep=True))
            else:
                fresh = next(replacements, None)
                if fresh is not None:
                    combined.append(fresh)
        result.findings = combined
    except Exception as exc:
        print('[연결 문장 재작성 미완료] ' + type(exc).__name__, flush=True)
    return result

"""Deterministic observations and conservative checks; never substitute for semantic review."""
from __future__ import annotations

import json
import re
import unicodedata
from common import decimal
from financials import LABELS
from typing import Annotated
from pydantic import StringConstraints

# Observed numbers are restored by code. Small models supply qualitative text only.
QualitativeText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]

TOPIC_FOLLOWUPS = {
    'growth': '다음 실적에서 동일 기간 매출과 수요 관련 설명을 확인합니다.',
    'profitability': '다음 실적에서 같은 기준의 이익률과 원가 변화를 확인합니다.',
    'cashflow': '다음 공시에서 영업현금흐름과 투자지출을 같은 기간으로 비교합니다.',
    'balance_sheet': '다음 공시에서 현금과 유동부채 및 차입 조건을 확인합니다.',
    'business': '다음 사업보고서에서 해당 사업·고객·계약의 변화를 확인합니다.',
    'capital_allocation': '다음 공시에서 투자지출·증자·배당 등 자본배분을 확인합니다.',
    'catalyst': '다음 공식 발표에서 해당 이정표의 진행 여부를 확인합니다.',
    'risk': '다음 공식 공시에서 해당 위험의 변화와 대응을 확인합니다.',
    'valuation': '같은 시점의 가격과 비교 가능한 실적 자료가 함께 확보되면 재검토합니다.',
    'fund_structure': '다음 공식 상품문서에서 운용 목표·비용·주요 위험의 변경을 확인합니다.',
}


def followup_for(topic):
    return TOPIC_FOLLOWUPS.get(topic, '다음 공식 자료에서 동일 항목을 재확인합니다.')


FRED_LABELS = {
    'DGS2': '미국 2년물 국채금리', 'DGS10': '미국 10년물 국채금리',
    'FEDFUNDS': '미국 연방기금 실효금리(월평균)', 'CPIAUCSL': '미국 소비자물가지수(CPI)',
    'PCEPI': '미국 PCE 물가지수', 'PCEPILFE': '미국 근원 PCE 물가지수(식품·에너지 제외)',
    'UNRATE': '미국 실업률', 'GDPC1': '미국 실질 GDP',
    'T10YIE': '미국 10년 기대인플레이션율', 'BAMLH0A0HYM2': '미국 하이일드 옵션조정 스프레드',
}


def source_json(source):
    try:
        result = json.loads(source.get('content', ''))
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def numeric_tokens(text):
    # Normalize full-width and non-ASCII decimal digits before checking facts.
    text = normalized_digits(text)
    # Dates are checked as complete strings below, never as -11 / -16 amounts.
    text = re.sub(r'\b\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]+)?', ' ', str(text))
    tokens = re.findall(r'(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?![0-9_.])', text)
    return {decimal(t.replace(',', '')) for t in tokens}


def normalized_digits(text):
    text=unicodedata.normalize('NFKC',str(text))
    return ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)


def source_text(source):
    fields = ('title', 'content', 'observed_at', 'period_start', 'period_end', 'observation_period', 'basis')
    return ' '.join(str(source.get(k) or '') for k in fields)


def numeric_issues(text, sources):
    text=normalized_digits(text)
    reference = normalized_digits(' '.join(source_text(s) for s in sources))
    dates = set(re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text))
    missing_dates = dates - set(re.findall(r'\b\d{4}-\d{2}-\d{2}\b', reference))
    # Include date components for natural-language year/quarter labels only when grounded.
    components = set()
    for s in sources:
        for value in re.findall(r'\b\d{4}-\d{2}-\d{2}\b', source_text(s)):
            year, month, _ = map(int, value.split('-'))
            components.add(decimal(year))
            if s.get('basis') == 'quarter':
                components.add(decimal((month - 1) // 3 + 1))
    missing = numeric_tokens(text) - numeric_tokens(reference) - components
    return ([f'원문에 없는 수치: {", ".join(str(n) for n in sorted(missing))}'] if missing else []) + (
        ['원문에 없는 날짜: ' + ', '.join(sorted(missing_dates))] if missing_dates else [])


def financial_period_issues(text,sources):
    """Reject explicit single-quarter labels unsupported by structured periods.

    Calendar-quarter labels are checked only for exact calendar-quarter inputs;
    non-calendar fiscal quarters are left to semantic review. YTD is never Q1.
    """
    text=normalized_digits(text)
    named=list(re.finditer(r'(\d{4})\s*년\s*(?:제\s*)?([1-4])\s*분기',text))
    if not named:return []
    periods=[]
    for s in sources:
        if s.get('kind') not in ('financial','financial_summary','calculation'):continue
        data=source_json(s)
        if data.get('basis'):
            periods.append(data)
        for row in data.get('inputs',[]):
            if isinstance(row,dict) and row.get('basis'):periods.append(row)
    if not periods:return []
    quarters=[p for p in periods if p.get('basis')=='quarter']
    calendar=set();uncertain=False
    for p in quarters:
        start=p.get('start',p.get('period_start','')) or ''
        end=p.get('end',p.get('period_end','')) or ''
        pairs={'01-01':'03-31','04-01':'06-30','07-01':'09-30','10-01':'12-31'}
        if len(start)>=10 and len(end)>=10 and pairs.get(start[5:])==end[5:] and start[:4]==end[:4]:
            calendar.add((int(end[:4]),(int(end[5:7])-1)//3+1))
        else:uncertain=True
    for match in named:
        # "3분기 누적" describes YTD, not a single quarter.
        if re.match(r'\s*(?:까지의?\s*)?(?:누적|YTD)',text[match.end():],re.I):continue
        if not quarters:return ['누적·연간 재무값을 근거 없는 단일분기로 설명함']
        if calendar and not uncertain and (int(match[1]),int(match[2])) not in calendar:
            return ['출처 기간과 다른 단일분기 표기']
    return []


BUSINESS_TERMS = {
    '수주잔고':r'수주\s*잔고|backlog|order\s*book',
    '고정가격 계약':r'고정\s*가격|fixed[\s-]*price',
    '발사사업':r'발사\s*(?:사업|서비스)|launch\s*(?:service|business|vehicle)',
    'HBM':r'\bHBM\w*\b|고대역폭\s*메모리',
    'DRAM':r'\bDRAM\b|디램',
}


def evidence_scope_issues(text,sources):
    """Narrow guards for observed small-model false positives, not a truth oracle."""
    reference=' '.join(source_text(s) for s in sources)
    issues=[]
    for label,pattern in BUSINESS_TERMS.items():
        if re.search(pattern,text,re.I) and not re.search(pattern,reference,re.I):
            issues.append('사업용어 근거 미확인: '+label+'; 관련 공시 본문 인용 필요')
    # A rise may SIGNAL stronger demand. It does not by itself STIMULATE it.
    # Restrict this rule to explicit direct-causal language and rate evidence.
    series={source_json(s).get('series_id') for s in sources if s.get('kind')=='indicator'}
    if series & {'FEDFUNDS','DGS2','DGS10'}:
        pattern=(r'금리[^.!?\n]{0,12}(?:상승|인상)(?:이|은|가|으로)'
                 r'[^.!?\n]{0,24}(?:경제\s*활동|총수요|소비|설비투자)[^.!?\n]{0,10}'
                 r'(?:촉진|부양)(?:해|하여|하고|하는|한다|할)')
        if re.search(pattern,text):issues.append('표준 금리 전달 경로와 반대되는 직접 인과; 금리 상승 자체를 경기 부양 원인으로 설명함')
    return issues


def observation_choices(sources):
    choices = []
    for source in sources:
        if source.get('kind') in ('quote', 'filing_index'):
            continue
        data = source_json(source)
        kind = source.get('kind')
        if kind in ('financial', 'financial_summary') and data.get('facts'):
            start = data.get('start', data.get('period_start')) or '시점'
            end = data.get('end', data.get('period_end'))
            values = '; '.join(f'{LABELS.get(f["metric"], f["metric"])} {f["value"]} {data.get("unit", "")}'
                               for f in data['facts'])
            text = f'{start} ~ {end} / {data.get("basis")} / {data.get("scope")}: {values}.'
        elif kind == 'calculation' and 'value' in data:
            text = (f'{data.get("start") or "시점"} ~ {data.get("end")} / {data.get("basis")} / '
                    f'{data.get("scope")}: {data.get("label", source["title"])} {data["value"]} {data.get("unit", "")}.')
        elif kind == 'indicator' and data.get('observations'):
            label = FRED_LABELS.get(data.get('series_id'), source['title'])
            obs = data['observations'][:3]
            text = label + ' / ' + str(data.get('units', '단위 미확인')) + ': ' + '; '.join(
                f'{r["date"]} = {r["value"]}' for r in obs) + '. 관측일은 대상 기간이며 발표 시각이 아님.'
        else:
            # Copy an actual contiguous source excerpt; do not invent a business observation.
            text = str(source.get('content') or source.get('title') or '')[:1000]
            if kind == 'news' and (len(text) < 80 or text.strip() == str(source.get('title', '')).strip()):
                continue
        if text.strip():
            choices.append({'observation_id': 'O' + str(len(choices)), 'evidence_id': source['id'], 'text': text})
    return choices


def review_contradicts_support(reason):
    # Korean particles immediately follow "false" in actual local-model output.
    return bool(re.search(r'\bsupported\s*[=:]\s*false(?![A-Za-z])', reason, re.I))


def indicator_issues(text, sources):
    text=normalized_digits(text)
    issues = []
    for source in sources:
        data = source_json(source)
        sid = data.get('series_id')
        obs = data.get('observations', [])
        if not sid or not obs:
            continue
        if sid in ('PCEPI', 'PCEPILFE') and re.search(r'실질\s*소비|real consumption', text, re.I):
            issues.append('PCE 물가지수를 실질 소비량/소비지출로 해석함')
        if sid in ('DGS2', 'DGS10') and re.match(r'^(?:미국\s*)?(?:2|10)년물\s*(?:정책금리|기준금리)', text):
            issues.append('국채금리를 정책금리로 표시함')
        # Reject a complete observed-date/value mismatch even when both occur elsewhere in the series.
        dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
        if len(dates) == 1:
            row = next((r for r in obs if r.get('date') == dates[0]), None)
            if row and numeric_tokens(text) and decimal(row['value']) not in numeric_tokens(text):
                issues.append('지표 대상 날짜와 관측값이 일치하지 않음')
        frequency = str(data.get('frequency', '')).lower()
        if 'daily' in frequency and re.search(r'3개월|three.month', text, re.I):
            issues.append('제공된 일별 관측만으로 3개월 추세를 판단함')
    return issues

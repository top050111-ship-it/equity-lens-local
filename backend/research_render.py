# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Detailed Markdown output with financial tables, missing-topic checklist and audit trail."""
from __future__ import annotations
from common import decimal, now, write_private
from financials import LABELS
from deep_research import TOPIC_LABELS
import json
import re
from evidence_grounding import observation_choices, source_json, FRED_LABELS


def esc(value):
    return str(value if value is not None else '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','\\|').replace('[','\\[').replace(']','\\]').replace('\n',' ')


def amount(value):
    try:
        v=decimal(value)
        if v==v.to_integral_value():return f'{v:,.0f}'
        return f'{v:,.2f}'
    except ValueError:return esc(value)


def evidence_ref(eid):
    return '['+eid+'](#'+eid.lower()+')'


def update_changes(result,output,*,persist=True):
    deep=result.get('research_v2')
    if not deep:return
    state_path=output/'research_previous.json'
    try:previous=json.loads(state_path.read_text())
    except (OSError,ValueError):previous=None
    if not isinstance(previous,dict) or not isinstance(previous.get('events'),list) or any(not isinstance(x,str) for x in previous.get('events',[])):
        previous=None
    current={s.get('event_key') for s in result['sources'] if s.get('event_key')}
    old=set(previous.get('events',[])) if previous else set()
    deep['changes']={'previous_run':previous.get('at') if previous else None,
        'new_event_keys':sorted(current-old) if previous else [],
        'note':'이전에 저장된 수집 이력에 없는 공시 식별자. 분석 성공·중요도 판단과는 별개.' if previous else '첫 실행으로 전회 대비 변화 판정 불가'}
    # Do not erase a useful comparison baseline after a total collection failure.
    state={'at':result['created_at'],'events':sorted(current|old)[-5000:]} if current else None
    if state and persist:write_private(state_path,state)
    return state


def render_deep_report(result,snapshot):
    deep=result['research_v2'];q=deep['quality']
    lines=['<!-- markdownlint-disable MD033 -->', '', '# EQUITY LENS · 공시 기반 심층 리서치','',
        '작성: '+esc(result['created_at'])+' / 저장 보유 수량 기준: '+esc(snapshot['as_of']),'',
        '**자동 생성 리서치 초안. 자료 관측·계산과 AI 검토 통과 해석을 구분합니다.**',
        '현재 공개자료를 사용합니다. 저장 수량 기준일의 과거 정보만 사용한 백테스트가 아닙니다. 현금 제외 주식 비중입니다.','',
        f"종목 분석 {q['verified_companies']}/{q['companies']} · 기업–거시 연결 {q['linked_companies']}/{q['companies']} · 통과 논점 {q['accepted_insights']}개",'',
        '분석 단계: '+('완료' if result['complete'] else '일부 미완료 — 아래 누락·탈락 항목 확인'),'',
        '비중은 아래 시세 기준의 참고치입니다. 시세 갱신 실패 시 현재 비중이 아닙니다.', '',
        '| 종목 | 주식 내 비중 | 시세 기준 | 심층 분석 | 거시 연결 |','|---|---:|---|---|---|']
    by_symbol={p['symbol']:p for p in snapshot['positions']}
    for c in deep['companies']:
        position = by_symbol[c['symbol']]
        lines.append(f"| {esc(c['symbol'])} | {position['weight_pct']:.2f}% | {esc(position.get('price_at') or position.get('price_quality', '시각 미확인'))} | {c['status']} | {c['macro_status']} |")
    changes=deep.get('changes',{})
    if deep.get('replay'):
        replay=deep['replay']
        lines += ['', '재분석 범위: ' + ', '.join(esc(s) for s in replay['symbols']) +
                  ' / 원자료 수집 회차: ' + esc(replay.get('source_created_at')),
                  '새 자료 수집 없이 저장 자료를 재사용했습니다. 일부 종목 재분석의 완료는 전체 포트폴리오 완료와 별개입니다.', '']
    lines+=['','## 전회 대비 새 공시','',esc(changes.get('note','비교 기준 없음'))]
    for key in changes.get('new_event_keys',[]):lines.append('- '+esc(key))
    lines+=['','## 독립 거시 분석','']
    indicators = [(s, source_json(s)) for s in result['sources'] if s.get('kind') == 'indicator']
    if any(d.get('observations') for _,d in indicators):
        lines += ['### 코드로 정리한 지표 관측', '',
                  '아래 값은 수집 자료의 관측치입니다. AI 해석 통과 여부와 별개이며 대상 기간은 발표일이 아닙니다.', '',
                  '| 지표 | 대상 기간 | 값 | 단위 | 근거 |', '|---|---|---:|---|---|']
        for source,data in indicators:
            for row in data.get('observations', [])[:3]:
                lines.append('| ' + ' | '.join([esc(FRED_LABELS.get(data.get('series_id'), source['title'])),
                    esc(row['date']), esc(row['value']), esc(data.get('units')), evidence_ref(source['id'])]) + ' |')
        lines += ['', '### 근거 검토를 통과한 거시 해석', '']
    for f in result['macro']['report']['findings']:
        lines+=['### '+esc(f['subject']),'',esc(f['claim']),'',
            '- 반대 설명: '+esc(f['counterpoint']),'- 재검토 조건: '+esc(f['revisit_when']),
            '- 근거: '+', '.join(evidence_ref(e) for e in f['evidence_ids']),'']
    if not result['macro']['report']['findings']:lines+=['검토를 통과한 거시 논점 없음. 기업 분석과 구분해 보존합니다.','']
    if result['macro'].get('quality_audit') or result['macro']['status'] != 'ok':
        lines += ['<details><summary>거시 분석 진단</summary>', '',
                  '단계 상태: ' + esc(result['macro']['status']), '']
        for entry in result['macro'].get('quality_audit', []):
            lines.append('- ' + esc(entry['item_id']) + (' 통과: ' if entry['accepted'] else ' 제외: ') + esc(entry['reason']))
        if result['macro']['status'] != 'ok':
            lines += ['- ' + esc(note) for note in result['macro']['report'].get('limitations', [])]
        lines += ['', '</details>', '']
    for c in deep['companies']:
        symbol=c['symbol']
        lines+=['','## '+esc(symbol)+' · '+esc(by_symbol[symbol].get('name','')),'',
                '분석 경로: '+esc(c['asset_type'])+' / 분야: '+esc(c['sector']),'',
                '### 공식 재무수치','']
        if not c.get('records'):lines+=['정규화 가능한 공식 재무자료가 없거나 기업 재무 적용 대상이 아닙니다.','']
        else:
            lines+=['금액은 원 통화 기본단위. 단일분기·누적·연간·시점 값을 구분하며 통화 변환·연율화를 하지 않았습니다.','',
                '| 계정 | 시작 | 종료 | 기간 유형 | 범위 | 값 | 단위 | 근거 |',
                '|---|---|---|---|---|---:|---|---|']
            latest={}
            for r in sorted(c['records'],key=lambda r:(r['end'],r['start']),reverse=True):
                latest.setdefault((r['metric'],r['basis'],r['unit'],r['scope']),r)
            for r in latest.values():
                lines.append('| '+' | '.join([esc(LABELS.get(r['metric'],r['metric'])),esc(r['start'] or '시점'),r['end'],r['basis'],esc(r['scope']),amount(r['value']),r['unit'],evidence_ref(r['evidence_id'])])+' |')
        lines+=['','### 코드로 계산한 지표','']
        if c.get('metrics'):
            lines+=['| 지표 | 기간 | 유형 | 값 | 단위 | 근거 |','|---|---|---|---:|---|---|']
            for r in c['metrics']:
                lines.append('| '+' | '.join([esc(r['label']),esc((r['start']+' ~ ' if r['start'] else '')+r['end']),r['basis'],amount(r['value']),esc(r['unit']),evidence_ref(r['evidence_id'])])+' |')
            lines+=['','FCF 참고치는 영업현금흐름에서 유형자산 취득 현금유출만 차감합니다. 회사가 발표한 FCF와 다를 수 있습니다. 금융업 해석에는 별도 검토가 필요합니다.']
        else:lines+=['기간·통화·계정 조건이 맞는 계산 입력이 부족하거나 계산 적용 대상이 아닙니다.']
        data_sources = [s for s in result['sources'] if symbol in s.get('symbols', []) and
                        s.get('kind') in ('financial_summary', 'calculation')]
        if data_sources:
            lines += ['', '### 자료에서 코드로 정리한 관측', '',
                      '아래는 수집값·계산값의 정리이며 AI 근거 검토를 통과한 해석으로 세지 않습니다.', '']
            # Keep distinct latest-period facts accessible even when an LLM fails.
            for choice in observation_choices(data_sources)[:8]:
                lines.append('- ' + esc(choice['text']) + ' ' + evidence_ref(choice['evidence_id']))
        lines+=['','### 검토를 통과한 투자 논점','']
        for i,item in enumerate(c['insights'],1):
            lines+=['#### '+str(i)+'. '+esc(item['title']),'',
                esc(TOPIC_LABELS[item['topic']])+' / '+esc(item['kind'])+' / 조건부 영향: '+esc(item['impact']),'',
                '**관측 사실:** '+esc(item['observation']),'', '**해석:** '+esc(item['interpretation']),'',
                '**전달 경로:** '+esc(item['mechanism']),'', '**시간축:** '+esc(item['horizon']),'',
                '**반대 설명:** '+esc(item['countercase']),'', '**다음 확인 조건:** '+esc(item['monitor']),'',
                '근거: '+', '.join(evidence_ref(e) for e in item['evidence_ids']),'']
        if not c['insights']:lines+=['검토 통과 논점 없음. 재무표가 있다는 이유만으로 해석을 채우지 않았습니다.','']
        lines+=['### 기업·거시 연결','']
        if c['macro_links']:
            lines+=['**조건부 시나리오:** 해당 기업의 노출·전달 조건이 성립할 때의 가능성입니다. 아래 영향이 실제 발생했다고 확인한 것은 아닙니다.','']
        for f in c['macro_links']:
            lines+=[esc(f['claim']),'','- 반대 설명: '+esc(f['counterpoint']),'- 재검토: '+esc(f['revisit_when']),
                    '- 근거: '+', '.join(evidence_ref(e) for e in f['evidence_ids']),'']
        if not c['macro_links']:lines+=['실제 연결을 뒷받침하는 검토 통과 논점 없음.','']
        for note in c.get('macro_warnings', []):
            lines += ['- ' + esc(note)]
        if c.get('macro_warnings'):lines.append('')
        lines+=['### 누락 점검','', '| 검토 주제 | 상태 | 이유 |','|---|---|---|']
        for row in c['coverage']:lines.append('| '+' | '.join(esc(row[k]) for k in ('label','status','reason'))+' |')
        b=c['source_budget']
        lines+=['',f"AI 입력 출처: {b['selected']}/{b['available']}개. 재무표와 전체 근거 목록은 별도 보존.",'',
                *('- '+esc(w) for w in c['warnings'] if not re.match(r'^[FC]\d+ 제외:', w)),'', '<details><summary>논점별 근거 검토 기록</summary>','']
        for a in c['quality_audit']:
            lines+=['- '+esc(a['item_id'])+' '+('통과' if a['accepted'] else '제외')+': '+esc(a['reason'])]
            for quote in a.get('quotes',[]):
                text = quote['quote']
                lines+=['  - '+evidence_ref(quote['evidence_id'])+' / '+esc(quote['field'])+': '+esc(text[:240])+(' … (전문은 보고서 JSON)' if len(text)>240 else '')]
        lines+=['','</details>','']
    lines+=['## 수집·분석 한계','',*('- '+esc(w) for w in list(dict.fromkeys(result['warnings']+deep.get('limits',[])))),'',
            '## 전체 근거 목록','', '공시 접수 목록은 본문 분석 근거와 구분됩니다. 전체 원문 발췌와 계산 입력은 동일 실행의 reports/*.json에 보존됩니다. 아래는 출처 색인입니다.','']
    for s in result['sources']:
        url=s['url'].replace(' ','%20').replace('(','%28').replace(')','%29')
        lines+=[f"<a id=\"{s['id'].lower()}\"></a>", '', f"### {s['id']} · {esc(s['title'])}",'',
                f"[{esc(s['provider'])}]({url}) / {esc(s.get('content_scope',s['kind']))}",'',
                '자료 날짜: '+esc(s.get('observed_at') or s.get('observation_period') or '미확인')+' / 수집: '+esc(s.get('retrieved_at')),'']
    return '\n'.join(lines)+'\n'

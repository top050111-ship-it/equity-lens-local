# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit live-model check. Offline tests never invoke this module's probe."""
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict
from common import now, request, write_private
from model_runtime import runtime_info


class ConnectionProbe(BaseModel):
    model_config=ConfigDict(extra='forbid')
    evidence_id:Literal['PROBE']
    message:Literal['연결 확인']


def probe_model(output):
    from agents import Model, error_code
    model=Model();output=Path(output)
    result={'at':now(),'runtime':runtime_info(),'backend':model.backend,'model':model.model,
            'scope':'실제 모델의 JSON 전송·형식 검증만 확인; 전체 리서치 정확도/완료 여부와 별개'}
    try:
        if model.backend=='ollama':
            version=request('http://127.0.0.1:11434/api/version',attempts=1,timeout=15)
            details=request('http://127.0.0.1:11434/api/show',body={'model':model.model},attempts=1,timeout=30)
            result['ollama_version']=version.get('version')
            result['model_capabilities']=details.get('capabilities')
            result['model_details']=details.get('details')
        model._generate('연결 확인이다. JSON 객체 하나만 반환한다.',
                        {'evidence_id':'PROBE','message':'연결 확인'},ConnectionProbe,'connection_probe')
        result['status']='ok'
    except Exception as exc:
        result.update(status='failed',error_code=error_code(exc))
    result['model_calls']=model.telemetry
    target=output/'model_probe.json';write_private(target,result)
    print('실제 모델 JSON 연결 검사:',result['status'],'/',target,flush=True)
    return 0 if result['status']=='ok' else 1

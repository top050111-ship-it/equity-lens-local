# Copyright (C) 2026 Seungbeom Hong
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Model transport, application validation and failure diagnostics kept as separate steps."""
from __future__ import annotations

import copy
import json
import math
import os
import time
import uuid
import hashlib
import platform
from pathlib import Path
from typing import TypeVar
from pydantic import BaseModel, ValidationError
from common import now

Output = TypeVar('Output', bound=BaseModel)


def runtime_info():
    import pydantic
    root=Path(__file__).resolve().parent
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob('*.py'))}
    return {'python':platform.python_version(),'pydantic':pydantic.__version__,
            'platform':platform.system()+' '+platform.machine(),'code_sha256':files}


class ContextBudgetError(ValueError):
    pass


class ModelSettingsError(ValueError):
    pass


class ModelOutputError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def safe_code(exc):
    if isinstance(exc,ModelSettingsError):return 'MODEL_SETTINGS_INVALID'
    if isinstance(exc, ContextBudgetError):return 'MODEL_CONTEXT_BUDGET'
    if isinstance(exc, ValidationError):return 'MODEL_SCHEMA_INVALID'
    if isinstance(exc, ModelOutputError):return exc.code
    if isinstance(exc, TimeoutError):return 'MODEL_TIMEOUT'
    if getattr(exc,'category',None)=='timeout':return 'MODEL_TIMEOUT'
    if hasattr(exc,'status'):return 'MODEL_HTTP_'+str(exc.status)
    return type(exc).__name__


def bounded_int(name, default, minimum, maximum):
    try:value=int(os.getenv(name,str(default)))
    except (TypeError,ValueError):raise ModelSettingsError(name+' must be an integer') from None
    if not minimum <= value <= maximum:raise ModelSettingsError(name+' is outside its supported range')
    return value


def transport_schema(schema):
    """Keep a simple decoding grammar; validate lengths/patterns with Pydantic afterwards.

    Multilingual string regex/length constraints are not a substitute for validation.
    In particular, a digit-free grammar makes ordinary labels such as HBM3E awkward.
    """
    result=copy.deepcopy(schema)
    def visit(value):
        if isinstance(value,dict):
            for key in ('pattern','minLength','maxLength','format'):value.pop(key,None)
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(result)
    return result


def sampling_options(model_name, think):
    # Qwen3's model card explicitly warns against greedy decoding. Other models
    # retain conservative sampling. Overrides are validated, never silently capped.
    qwen = model_name.lower().startswith('qwen3')
    defaults = (0.6, 0.95) if think else (0.7, 0.8)
    try:
        temperature=float(os.getenv('OLLAMA_TEMPERATURE',str(defaults[0] if qwen else 0.1)))
        top_p=float(os.getenv('OLLAMA_TOP_P',str(defaults[1] if qwen else 0.9)))
    except ValueError:raise ModelSettingsError('Invalid Ollama sampling settings') from None
    if not math.isfinite(temperature) or not 0 <= temperature <= 2 or not math.isfinite(top_p) or not 0 < top_p <= 1:
        raise ModelSettingsError('Invalid Ollama sampling settings')
    return {'temperature':temperature,'top_p':top_p,'top_k':20 if qwen else 40,'min_p':0}


def check_selections(value,schema):
    """Check runtime ID enums/counts that cannot live in reusable model types.

    This supplements Pydantic; it is intentionally not a general JSON Schema
    implementation. Types, required fields and model bounds remain Pydantic's job.
    """
    def visit(item,node,path):
        if '$ref' in node:
            name=node['$ref'].rsplit('/',1)[-1]
            node=schema.get('$defs',{}).get(name,{})
        if 'enum' in node and item not in node['enum']:
            raise ModelOutputError('MODEL_SELECTION_INVALID','invalid selection at '+path)
        if isinstance(item,list):
            if len(item)<node.get('minItems',0) or len(item)>node.get('maxItems',len(item)):
                raise ModelOutputError('MODEL_SELECTION_INVALID','invalid item count at '+path)
            for index,child in enumerate(item):visit(child,node.get('items',{}),path+'/'+str(index))
        elif isinstance(item,dict):
            for key,child in item.items():visit(child,node.get('properties',{}).get(key,{}),path+'/'+key)
    visit(value,schema,'')


def generate(model, system, payload, output_type: type[Output], role, request_fn, schema_override=None) -> Output:
    event={'call_id':uuid.uuid4().hex[:16],'role':role,'at':now(),'status':'started',
           'schema':output_type.__name__,'backend':model.backend,'model':model.model}
    if not hasattr(model,'telemetry'):model.telemetry=[]
    # Record before calling/validating, so a totally failed run still has evidence.
    model.telemetry.append(event)
    started=time.monotonic();output='';response=None
    try:
        serialized=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(serialized)>180_000:raise ContextBudgetError('Evidence bundle exceeds context budget')
        schema=schema_override or output_type.model_json_schema()
        if model.backend=='openai':
            response=request_fn(role,'https://api.openai.com/v1/responses',timeout=180,attempts=1,
                headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']},
                body={'model':model.model,'store':False,'instructions':system,'input':serialized,'max_output_tokens':8000,
                      'text':{'format':{'type':'json_schema','name':'research_report','strict':True,'schema':schema}}})
            if isinstance(response,dict):
                rows=response.get('output')
                rows=rows if isinstance(rows,list) else []
                output=''.join(part['text'] for item in rows if isinstance(item,dict)
                    for part in (item.get('content') or []) if isinstance(part,dict)
                    and part.get('type')=='output_text' and isinstance(part.get('text'),str))
                event['response_status']=response.get('status')
            if not isinstance(response,dict) or response.get('status')!='completed':
                raise ModelOutputError('MODEL_RESPONSE_INCOMPLETE','model did not complete')
        else:
            num_predict=bounded_int('OLLAMA_NUM_PREDICT',4800,512,16384)
            num_ctx=bounded_int('OLLAMA_NUM_CTX',16384,4096,131072)
            timeout=bounded_int('OLLAMA_TIMEOUT',900,10,3600)
            num_batch=bounded_int('OLLAMA_NUM_BATCH',128,16,1024)
            # All bounded research/review contracts are answer-only. Enabling
            # thinking here can consume the entire output budget before JSON.
            think=False
            sampling=sampling_options(model.model,think)
            wire=transport_schema(schema)
            # Ground the requested object shape in the prompt, as well as the API grammar.
            grounded_system=system+'\n반드시 다음 JSON 스키마의 키와 자료형으로만 출력한다:\n'+json.dumps(wire,ensure_ascii=False,separators=(',',':'))
            estimate=(len((grounded_system+serialized+json.dumps(wire,ensure_ascii=False)).encode())+1)//2
            event['settings']={'num_predict':num_predict,'num_ctx':num_ctx,'num_batch':num_batch,'think':think,'timeout':timeout,**sampling}
            event['estimated_input_tokens']=estimate
            if estimate+num_predict>num_ctx:raise ContextBudgetError('Input and output reservation exceed OLLAMA_NUM_CTX')
            response=request_fn(role,'http://127.0.0.1:11434/api/chat',timeout=timeout,attempts=1,capture_error=True,
                body={'model':model.model,'stream':False,'think':think,'keep_alive':'10m',
                      'options':{'num_predict':num_predict,'num_ctx':num_ctx,'num_batch':num_batch,**sampling},'format':wire,
                      'messages':[{'role':'system','content':grounded_system},{'role':'user','content':serialized}]})
            if isinstance(response,dict):
                message=response.get('message')
                if isinstance(message,dict):
                    output=message.get('content','')
                    event['thinking_characters']=len(str(message.get('thinking') or ''))
                event.update(done=response.get('done'),done_reason=response.get('done_reason'))
            if isinstance(response,dict) and response.get('done_reason')=='length':
                raise ModelOutputError('MODEL_OUTPUT_TRUNCATED','local model output truncated; increase OLLAMA_NUM_PREDICT')
            if not isinstance(response,dict) or response.get('done') is not True:
                raise ModelOutputError('MODEL_RESPONSE_INCOMPLETE','local model did not complete')
        if not isinstance(output,str) or not output.strip():
            raise ModelOutputError('MODEL_EMPTY_OUTPUT','model returned no answer text')
        parsed=output_type.model_validate_json(output)
        check_selections(parsed.model_dump(mode='json'),schema)
        event['status']='validated'
        return parsed
    except BaseException as exc:
        event.update(status='cancelled' if isinstance(exc,KeyboardInterrupt) else 'failed',error_code=safe_code(exc))
        if isinstance(exc,ModelSettingsError):event['configuration_error']=str(exc)
        if isinstance(getattr(exc,'detail',None),str):event['local_server_error']=exc.detail
        if isinstance(exc,ValidationError):
            event['validation_errors']=[{'path':list(e['loc']),'type':e['type']} for e in exc.errors(include_input=False,include_url=False)[:12]]
        raise
    finally:
        event['elapsed_seconds']=round(time.monotonic()-started,3)
        if isinstance(response,dict):
            usage=response.get('usage')
            usage=usage if isinstance(usage,dict) else {}
            for key,value in {
                'input_tokens':usage.get('input_tokens',response.get('prompt_eval_count')),
                'output_tokens':usage.get('output_tokens',response.get('eval_count')),
                'load_duration_ns':response.get('load_duration'), 'prompt_duration_ns':response.get('prompt_eval_duration'),
                'generation_duration_ns':response.get('eval_duration'),
            }.items():
                if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):event[key]=value
        if isinstance(output,str):
            event['output_characters']=len(output)
            if output:
                # A type-valid answer can still fail citation/meaning checks.
                # Retain the native text for diagnosing those later failures too.
                event['unverified_output']=output[:16000]+('\n[... output omitted ...]\n'+output[-4000:] if len(output)>20000 else output[16000:])
                event['output_excerpted']=len(output)>20000
        log_path=getattr(model,'diagnostic_path',None)
        if log_path:
            # Preserve completed attempts even if a later call is interrupted.
            # Failure to save diagnostics must not replace the actual model error.
            try:
                path=Path(log_path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
                os.fchmod(fd,0o600)
                with os.fdopen(fd,'a',encoding='utf-8') as f:
                    f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())
            except OSError:event['diagnostic_write_failed']=True

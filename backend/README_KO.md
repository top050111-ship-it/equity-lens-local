# Equity Lens 전체 점검·수정본 — 2026-11-16

최신 실패 결과 `20261116_153023_260463.json`과 제공된 백엔드 전체를 기준으로 수정했습니다. 오프라인 검사와 실제 Qwen3 4B 실행을 구분했습니다. 원인·검증 수치·실제 실행 결과는 루트의 `AUDIT_REPORT_KO.md`에 있습니다.

## 적용

1. 실행 중인 수집기를 종료하고 기존 프로젝트를 별도로 백업합니다.
2. 이 `backend`의 Python 파일 전부와 **`tests` 폴더 전체**를 기존 `backend`에 복사합니다. 새 `model_runtime.py`, `diagnostics.py`, 선택적 `start_ollama_low_memory.sh`도 포함합니다.
3. 기존 `.env`, `config.json`, `data`, `frozen_portfolio.json`은 보관합니다. `config.example.json`은 예시입니다. 새 폴더라면 이를 `config.json`으로 복사합니다.
4. 프런트엔드는 별도 폴더입니다. 제공됐던 `equity-lens-local.zip`의 화면을 보완했습니다. 현재 화면을 따로 수정했다면 백업 후 `src/lib/data.ts`, `src/components/connections.tsx`, `src/components/research-console.tsx`의 변경을 반영하세요.

macOS/Linux, Python 3.10 이상 대상입니다. 기존 가상환경을 활성화하고 `backend`에서 실행합니다.

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
python3 doctor.py --tests
python3 doctor.py --model
```

`--tests`는 고정 응답으로 로직을 검사합니다. `--model`은 실제 모델의 연결과 작은 JSON 객체 형식을 확인합니다. 연결 검사가 전체 보고서의 정확도를 판정하는 것은 아닙니다. `python3 doctor.py --tests --model`로 함께 실행할 수 있습니다.

## 모델 설정

기존 `.env`에서 아래 값을 확인합니다. 인증정보는 유지합니다.

```dotenv
LLM_BACKEND=ollama
LLM_MODEL=qwen3:4b
OLLAMA_NUM_CTX=16384
OLLAMA_NUM_PREDICT=4800
OLLAMA_NUM_BATCH=128
OLLAMA_TIMEOUT=900
OLLAMA_PARALLEL_AGENTS=false
```

구조화 연구·검토 호출은 `think=false`를 사용합니다. 모델은 관측 ID를 선택하고 코드가 수치·기간을 복원합니다. 숫자 금지 정규식을 디코더에 강제하지 않고 생성 후 수치·기간·인용을 별도로 검사합니다. `OLLAMA_THINK`는 이전 설정과의 호환을 위해 예시에만 남깁니다.

Qwen3 기본값은 non-thinking 권장값인 temperature 0.7, top_p 0.8, top_k 20, min_p 0입니다. 다른 모델은 temperature 0.1을 기본값으로 사용합니다. `OLLAMA_TEMPERATURE`, `OLLAMA_TOP_P`로 명시할 수 있습니다.

`OLLAMA_NUM_PREDICT=4800`이면 기업·검토 호출에도 4800을 전달합니다. 역할별로 몰래 낮추지 않습니다. 입력과 출력 예약이 문맥을 넘으면 호출 전에 중단합니다. 8GB에서 문맥을 무작정 늘리는 방식은 권하지 않습니다.

8GB 환경에서 서버 메모리 사용도 줄이려면 기존 Ollama 작업을 마치고 앱/서버를 종료한 뒤 별도 터미널에서 실행합니다.

```bash
bash start_ollama_low_memory.sh
```

이 선택적 실행기는 llama.cpp 기반 서버의 과거 프롬프트 캐시를 끄고 동시 처리를 제한합니다. 캐시는 활성 문맥의 KV 캐시와 별개입니다. 이 서버 설정은 **Python의 `.env`에 적는 것으로 기존 서버에 적용되지 않습니다.** 다른 런너·버전은 다를 수 있으며 메모리 부족 방지를 보장하지 않습니다. 실행 중인 서버를 임의로 종료하지 않습니다.

## 보고서 생성

저장 수량으로 현재 공개자료를 분석합니다. 본인의 실제 파일 경로를 지정합니다.

```bash
python3 app.py cached --holdings data/frozen_portfolio.json
```

토스 인증은 필요 없습니다. `--skip-price-refresh`로 공개 가격 갱신을 생략할 수 있습니다. 저장 시점으로 돌아가는 백테스트는 아닙니다. 현재 토스 보유 내역을 사용하는 명령은 `python3 app.py live`입니다.

공식 수집 설정은 기존과 같습니다.

- `DART_API_KEY`: DART 인증키.
- `SEC_USER_AGENT`: 연락 가능한 본인 이메일을 포함한 식별 문자열. 빈 값이면 SEC 수집을 생략합니다.
- `FRED_API_KEY`: 정량 거시 지표 수집용.

공시 수집만 확인할 수도 있습니다.

```bash
python3 research_tools.py collect --holdings data/frozen_portfolio.json
```

`config.json`의 `instruments`에서 종목별 `sector`, `asset_type`, `issuer_symbol`, `cik`/`corp_code`, 공식 `documents`를 설정합니다. 기업과 펀드의 경로를 구분합니다. SCHD·URA 기본 상품 문서는 접근 제한 시 신원을 확인한 SEC 문서를 시도합니다. 과거 설명서를 현재 편입 비중으로 해석하지 않습니다.

## 수집 자료 재사용

외부 수집을 다시 하지 않으려면 기존 **보고서 JSON**과 저장 포트폴리오를 지정합니다. 상태 파일·Markdown은 입력이 아닙니다.

```bash
python3 research_tools.py replay --report data/reports/기존보고서.json --holdings data/frozen_portfolio.json --output data/replay
```

`--symbols RKLB`를 추가하면 일부 종목만 재분석합니다. 이 완료는 선택한 범위에 대한 것이며 전체 포트폴리오와 구분됩니다. 이전 부분 재분석 파일을 전체 결과로 다시 표시하지 않습니다. 다른 기업의 문서·뉴스를 독립 거시 자료로 재분류하지 않습니다.

## 결과와 진단

- `data/latest_report.md`: 최신 상세 보고서.
- `data/reports/*.json`, `*.md`: 같은 실행의 근거·수치·상세 결과·검토 기록.
- `data/analysis_status.json`: 화면이 선택할 같은 실행 파일의 포인터.
- `data/diagnostics/model_calls_*.jsonl`: 호출 직후의 형식·길이·토큰·오류 기록. 재분석은 `replay_model_calls.jsonl`.
- `diagnostics/model_probe.json`: 실제 서버 버전·모델 세부 정보·연결 호출 결과.

모델의 응답 원문은 `unverified_output`에 보존합니다. 형식이 유효해도 이후 의미 검토를 통과했다는 뜻은 아닙니다. **검증된 분석이 아니며 공개 논점에 사용하지 않습니다.** 긴 응답은 앞뒤 일부와 생략 여부를 기록합니다. 헤더·API 키는 기록하지 않습니다.

오류는 `MODEL_SCHEMA_INVALID`, `MODEL_OUTPUT_TRUNCATED`, `MODEL_SELECTION_INVALID`, `MODEL_CONTEXT_BUDGET`, `MODEL_SETTINGS_INVALID`, `MODEL_TIMEOUT`, `MODEL_HTTP_*`로 구분합니다. 로컬 서버의 JSON 오류 메시지도 보존합니다. HTTP 실패를 자동 반복하지 않습니다. 작은 생성 계약의 형식·선택·잘림 오류에만 최대 한 번 재작성합니다. 기업 논점 전부가 수치·기간·사업 근거 등 결정적인 오류로 탈락하면 해당 기업만 최대 한 번 보정하며 앞선 탈락 초안과 감사 기록도 남깁니다. 수집과 거시는 반복하지 않습니다.

```bash
python3 research_tools.py evaluate --report data/reports/실제보고서.json
```

출처 ID·인용 구절 존재·주제 누락·단계 상태를 검사합니다. 통과율은 정확도 점수가 아닙니다. `complete=true`는 각 대상 기업과 독립 거시·연결 단계에 검토 통과 논점이 있다는 뜻입니다. 모든 주제 충족이나 사실성 보증은 아닙니다. 자료가 없거나 근거 검토를 통과하지 못하면 계속 `partial`로 표시합니다.

## 반복 실행과 화면

`python3 app.py cached --watch`는 분석 완료 후 `report_interval_seconds`만큼 기다립니다. 분석이 오래 걸렸다는 이유로 직후 또 시작하지 않습니다. 같은 출력 폴더의 CLI·사이트 수집기·재분석은 잠금을 공유합니다. 다른 출력 폴더의 테스트는 막지 않습니다.

한 번 실행하는 모드는 Ctrl+C가 분석을 기다리는 작업 스레드에 묶이지 않도록 했습니다. 장시간 `--watch`/사이트 워커의 진행 중 네트워크 호출은 종료 시 타임아웃까지 대기할 수 있습니다.

화면 서버는 `python3 server.py`로 실행합니다. 프런트엔드 소스를 변경했다면 `frontend`에서 실행합니다. 제공된 빌드도 포함했습니다.

```bash
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run test:contracts
pnpm run build
pnpm run build:public
```

`pnpm-workspace.yaml`은 제공된 pnpm 11 설정과 잠금파일을 맞추는 데 필요합니다. Markdown 저장은 같은 실행의 백엔드 상세 파일을 사용합니다. JSON만 가져온 경우 관련 없는 과거 Markdown을 다운로드하지 않습니다.

원본 저작권·AGPL 표기를 유지했습니다. 실제 검증 범위와 한계는 동봉한 점검 보고서에 기록했습니다.

# Equity Lens v1.5.0 — 공시 기반 종목별 심층 리서치

개인 포트폴리오와 공개자료를 수집하고, 기업 분석·독립 거시 분석·기업–거시 연결을 생성하는 로컬 투자 리서치 보조 도구입니다. Python·FastAPI 백엔드와 React·TypeScript 대시보드, Ollama 기반 로컬 추론을 사용합니다.

보고서에서는 수집한 관측, 코드로 계산한 지표, 모델의 해석을 구분합니다. 자동 매매나 투자 판단의 정확성을 보장하는 시스템은 아닙니다.

## v1.5.0 변경 사항

v1.4.0에서 추가한 저장 포트폴리오 분석·고정 보관·근거 검토를 바탕으로, 공식자료 수집과 종목별 분석을 확장했습니다.

| 영역 | v1.5.0의 확장·보완 |
|---|---|
| 공식자료 | DART·SEC 공시, 재무 API, 일부 본문·실적 첨부 수집과 출처 추적 |
| 재무 계산 | 단일분기·누적·연간·시점 값과 통화·연결 범위를 구분한 정규화·계산 |
| 기업 분석 | 종목별 관측·해석·전달 경로·시간축·반대 설명·재검토 조건 생성 |
| 거시 연결 | 독립 거시 분석을 각 보유 종목과 별도로 연결하고, 연결 실패 시 기업 분석 보존 |
| 검증 | 수치·기간·출처 범위 검사, 원문 구절 대조, 일부 인과·상품 유형·티커 혼동 검사 |
| 오류 복구 | 인용이 섞인 논점의 격리 재검토, 오류 연결의 제한적 재작성, 저장 보고서의 선택 복구 |
| 진단 | 모델 호출 상태·토큰 사용량·검토 탈락·자료 생략·주제 누락 기록 |
| 화면 | 기존 요약 계약과 상세 메타데이터 보존, 공개용 재빌드의 시연 전용 설정 보완 |

### 검증한 범위

- 백엔드 회귀 테스트 **280개** 통과. v1.4.0에 기록된 83개에서 검증 범위를 확대했습니다.
- 프런트엔드 타입 검사, 데이터 계약 테스트 **5개**, 로컬·공개용 빌드 통과.
- 실제 저장된 6종목을 Qwen3:4b로 분석한 제공 결과에서 기업 분석 **6/6**, 기업–거시 연결 **6/6**, 전체 실행 완료를 확인했습니다.

자동 테스트에는 모의 응답·고정 입력을 사용합니다. 위 결과는 모든 종목·외부 API·모델·실행 환경에서의 성공을 보장하지 않습니다. 실제 보고서에도 일부 인과 오류, 기업·펀드 유형 혼동, 기간 표현 혼용이 남아 있어 후속 개선 대상으로 관리합니다.

## 구조

1. 저장 포트폴리오 또는 토스 계좌에서 보유 내역을 확보합니다.
2. 시세·뉴스·거시 지표와 DART·SEC 공식자료를 수집합니다.
3. 재무수치 정규화와 비교 가능한 기간의 지표 계산을 수행합니다.
4. 보유 내역을 입력받지 않는 독립 거시 분석과 종목별 기업 분석을 실행합니다.
5. 기업·거시 근거를 연결하고, 검토 결과와 누락 항목을 Markdown·JSON에 저장합니다.

같은 모델이 생성과 별도 검토를 수행할 수 있습니다. 원문 구절이 존재한다는 사실과 해석의 정확성은 구분해야 합니다.

## 1. 실행 환경과 설치

명령은 별도 표시가 없으면 **프로젝트 최상위 폴더**에서 실행합니다. 이미 사용하는 정상 Python 환경이 있다면 그대로 사용하세요.

- Python: 개발 검증 환경은 3.12이며, 사용자 macOS의 3.15 환경에서 실제 실행 결과를 확인했습니다.
- 파일 잠금을 사용하는 기존 경로는 macOS/Linux 기준입니다.
- 프런트엔드 개발 검사·재빌드: Node.js 22.13 이상, pnpm 11.25.0.
- 실제 분석에는 사용 가능한 Ollama 모델 또는 지원되는 외부 모델 설정이 필요합니다.

새 설치일 때의 예시:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
```

`.env`가 없는 새 설치에서만 예제 파일을 복사합니다. 기존 인증정보 파일을 덮어쓰지 마세요.

```bash
cp backend/env.example backend/.env
```

## 2. 모델·수집 설정

`backend/.env`의 예시입니다. 기존 사용자 설정과 실제 설치된 모델 이름을 사용하세요.

```dotenv
LLM_BACKEND=ollama
LLM_MODEL=qwen3:4b
OLLAMA_PARALLEL_AGENTS=false
OLLAMA_NUM_CTX=16384
OLLAMA_NUM_PREDICT=4800
OLLAMA_TIMEOUT=900
EQUITY_HOLDINGS_MODE=cached
EQUITY_DATA_DIR=/absolute/path/to/equity-lens-local/data
EQUITY_RESEARCH_FILE=/absolute/path/to/frozen_portfolio.json
DART_API_KEY=본인의_DART_인증키
FRED_API_KEY=본인의_FRED_인증키
SEC_USER_AGENT="Equity Lens 본인의실제이메일주소"
```

- `EQUITY_DATA_DIR`: CLI·서버·워커에서 공유할 결과 폴더입니다. 비워두면 기본적으로 `backend/data`를 사용합니다. CLI의 `--output`이 우선합니다.
- `EQUITY_RESEARCH_FILE`: 저장 포트폴리오의 실제 경로입니다. CLI에서는 `--holdings`로 지정할 수 있습니다.
- `DART_API_KEY`: 한국 기업 공식자료 수집에 사용합니다.
- `SEC_USER_AGENT`: 실제 연락 가능한 이메일을 포함합니다. SEC 공개자료에는 별도의 API 키를 사용하지 않습니다.
- `FRED_API_KEY`: 거시 시계열 수집에 사용합니다. 관측 대상 기간과 발표 시각을 구분합니다.

설정 누락·접근 거절·요청 제한·원문 형식 변화는 수집 경고와 누락으로 기록합니다. 모든 자료 확보를 보장하지 않습니다. 로컬 모델도 설치와 서버 실행이 필요하며, 8GB 메모리 환경에서는 문맥 크기와 동시 실행에 주의하세요.

외부 모델을 선택하면 분석 입력이 해당 제공자에게 전송될 수 있고 API 비용이 발생할 수 있습니다. 기존 지원 경로는 `LLM_BACKEND=openai`, 해당 계정에서 사용 가능한 `LLM_MODEL`, `OPENAI_API_KEY`를 설정합니다. 아래 실제 모델 검증은 Qwen3:4b 기준입니다.

## 3. 보고서 생성 전 단계별 검사

### 백엔드

```bash
python3 backend/doctor.py
```

파일·문법·필수 패키지·설정 형식을 확인합니다. 기본 명령은 실제 모델이나 토스 계좌를 호출하지 않습니다. `--model`은 별도의 실제 모델 검사 옵션입니다.

앞 단계에 오류가 없으면 자동 테스트를 실행합니다.

```bash
cd backend
python3 -m unittest discover -s tests -v
cd ..
```

### 프런트엔드

```bash
cd frontend
npx --yes pnpm@11.25.0 install --frozen-lockfile
npm run typecheck
npm run test:contracts
npm run build
cd ..
```

`ERR_PNPM_IGNORED_BUILDS`가 나오면 설치 후처리 승인 여부를 확인합니다. `npx --yes pnpm@11.25.0 approve-builds`에서 필요한 의존성을 선택할 수 있습니다. 프로젝트에서 확인한 대상에는 esbuild, unrs-resolver, workerd가 있습니다. npm·pnpm의 새 버전 알림은 필수 업데이트 지시가 아닙니다.

각 단계에 오류가 있으면 해당 환경·코드 문제부터 해결합니다. 이 검사만으로 실제 모델 응답이나 보고서 내용의 정확성이 입증되지는 않습니다.

## 4. 보유 내역과 출력 경로

아래 예시는 프로젝트 최상위의 `data`를 출력 폴더로 사용합니다. 실제 보유 파일은 Git에 포함하지 않습니다.

이관 전 실제 계좌의 전체 보유 내역을 고정 보관하는 경우:

```bash
python3 backend/app.py live --snapshot-only --freeze-holdings --output data
```

이 명령은 계좌를 조회하지만 AI 분석은 하지 않습니다. 기존 고정 보관본은 기본적으로 덮어쓰지 않습니다. 토스 인증정보와 정상 계좌 연결이 필요합니다.

이미 보유 파일이 있다면 다시 계좌를 조회할 필요가 없습니다. 파일의 실제 위치를 확인하고 `--holdings`에 지정하세요.

```bash
python3 backend/app.py cached --holdings data/frozen_portfolio.json --output data
```

기존 파일이 `backend/data`에 있다면 다음처럼 지정합니다.

```bash
python3 backend/app.py cached --holdings backend/data/frozen_portfolio.json --output data
```

위 두 명령 중 실제 보유 파일 위치에 맞는 **하나만** 실행합니다. cached 모드는 토스 계좌 조회 없이 저장 수량을 사용하지만, 공개자료 조회와 모델 분석은 실행합니다. 이후 매매·수량 변화가 자동 반영되는 것은 아닙니다. 시세 갱신 실패 시 기존 값과 기준 시각을 보존하고 경고를 남깁니다.

현재 계좌로 한 번 분석하려면 `python3 backend/app.py live --output data`를 사용합니다. `--watch`를 붙이면 반복 분석하므로 단일 검증 실행에는 붙이지 않습니다.

### 결과 파일

| 경로 — 위 예시의 출력 폴더 기준 | 내용 |
|---|---|
| `data/latest_portfolio.json` | 저장 보유 수량과 시세 기준의 포트폴리오 |
| `data/latest_report.md` | 최근 생성 보고서 |
| `data/reports/*.md`, `*.json` | 실행별 보고서·원자료·계산·검토 결과 |
| `data/analysis_status.json` | 완료·부분 완료·실패 상태와 보고서 식별자 |
| `data/research_previous.json` | 전회 대비 공시 식별자 비교 기준 |
| `data/diagnostics/` | 실행별 진단 기록 |

`complete=true`는 분석 실행 단계의 완료를 뜻합니다. 모든 주제의 분석 완료, 모든 자료의 수집 성공, 사실성이나 투자 적합성의 보증이 아닙니다.

## 5. 수집·평가·선택 재분석 도구

다음 파일 경로는 예시이므로 실제 파일명으로 지정하세요.

```bash
# 공식자료 수집만 확인 — AI·토스 호출 없음
python3 backend/research_tools.py collect --holdings data/frozen_portfolio.json

# 생성 보고서의 구조·인용·주제 누락 진단 — AI 호출 없음
python3 backend/research_tools.py evaluate --report data/reports/실제파일명.json

# 저장 원자료로 특정 종목 재분석 — 모델 호출 발생
python3 backend/research_tools.py replay --report data/reports/실제파일명.json --holdings data/frozen_portfolio.json --symbols RKLB --output data/replay

# 실패하거나 특정 연결 오류가 있는 부분만 복구 — 모델 호출 발생
python3 backend/repair_saved_links.py --report data/reports/실제파일명.json --holdings data/frozen_portfolio.json
```

`replay`는 선택한 종목의 기업 분석과 독립 거시 분석도 다시 실행합니다. `repair_saved_links.py`는 기존 기업 분석·독립 거시·재무수치·원자료를 보존하면서 대상 연결만 처리합니다. 후자의 기본 출력은 프로젝트의 `data/link_repairs`이며, 원본이나 화면의 최신 보고서 선택을 자동으로 덮어쓰지 않습니다.

검토기의 인용이 다른 논점과 섞이면 해당 논점·인용 자료만 격리해 한 번 재검토합니다. 특정 연결 오류의 재작성도 제한된 횟수로 수행합니다. 재작성의 반대 설명에는 향후 점검 조건을 사용합니다. 여전히 검증을 통과하지 못한 주장은 실패·제외 상태로 남습니다.

모델 호출 수는 종목 수·논점 수·검토 묶음·오류 복구 여부에 따라 달라집니다. `research_v2.model_calls` 등 진단 기록에서 역할·상태·사용량을 확인할 수 있습니다. 늘어난 실행 시간이 곧 추론 품질 향상을 의미하지는 않습니다.

## 6. 로컬 화면과 공개 시연

CLI와 같은 출력 폴더를 서버에서도 지정합니다.

```bash
APP_MODE=local EQUITY_DATA_DIR="$PWD/data" python3 backend/server.py
```

- [저장 자료 대시보드](http://127.0.0.1:8000/dashboard): 현재 선택된 포트폴리오와 보고서.
- [데이터 연결](http://127.0.0.1:8000/connect): 경로 안내와 JSON 가져오기.
- [시연 첫 화면](http://127.0.0.1:8000/): 가상 데이터 시연.

서버 실행만으로 분석이 시작되지는 않습니다. 대시보드는 저장 결과를 주기적으로 읽습니다. CLI 분석 중에는 화면에서 추가 분석을 요청하지 마세요. 화면의 ANALYZE 모드는 `EQUITY_HOLDINGS_MODE`와 저장 파일 설정을 확인합니다.

React 화면은 기존 `portfolio`, `macro`, `synthesis` 요약을 표시하고 상세 `research_v2` 메타데이터를 보존합니다. 재무표·모든 상세 논점과 검토 기록은 Markdown·JSON에서 확인합니다. 실제 보고서의 Markdown 다운로드는 `/api/report/markdown`을 사용합니다.

공개 시연용으로 다시 빌드할 때는 다음 명령을 사용합니다.

```bash
cd frontend
npm run build:public
cd ..
```

`vite.config.ts`가 public 모드에 시연 전용 플래그를 설정합니다. `dist`는 로컬용, `dist-public`은 공개 시연용이므로 중복 폴더가 아닙니다. 공개 배포에는 실제 인증정보·포트폴리오·개인 보고서를 포함하지 않습니다.

## 7. 종목별 수집 설정

`backend/config.json`의 `instruments`에서 수집 경로와 분석 질문을 설정합니다.

| 설정 | 용도 |
|---|---|
| `issuer_symbol` | 주식 종류와 공시 발행회사를 연결. 우선주 등을 본회사 공시로 매핑 |
| `sector` | general·semiconductor·space·financials·fund 등 분석 질문 선택 |
| `asset_type` | company 또는 fund 경로 지정 |
| `fs_div` | 한국 기업 연결 CFS 또는 별도 OFS 기준 지정 |
| `fiscal_end_month` | 결산월. 지원하지 않는 정규화 범위는 경고·생략 |
| `reporting_currency` | SEC·DART 재무 보고 통화 지정. 주식 거래 통화와 구분 |
| `documents` | 사용자가 확인한 공식 IR·상품 HTML 문서 추가 |
| `questions` | 자료에서 확인할 질문. 사실이나 사용자의 투자 가설로 취급하지 않음 |

```json
{
  "instruments": {
    "005935": {"issuer_symbol": "005930", "sector": "semiconductor", "fs_div": "CFS", "fiscal_end_month": 12},
    "RKLB": {"sector": "space", "questions": ["수주잔고의 매출 전환과 현금회수 시점은 확인되는가?"], "documents": []},
    "SOXL": {"asset_type": "fund", "sector": "fund", "documents": []}
  }
}
```

위 조각은 전체 config.json을 대체하는 파일이 아닙니다. 기존 갱신 간격·수집 설정에 합쳐 사용합니다. PDF·로그인·JavaScript 렌더링이 필요한 자료의 수집은 제한될 수 있습니다.

## 8. 한계와 후속 과제

- 공시 목록·일부 본문·표준 재무계정을 수집하며, 모든 공시·주석·첨부·어닝콜을 전수 분석하지 않습니다.
- SEC 기업 확장 태그, 사업부·고객·계약별 수치는 완전히 정규화하지 않습니다. DART 문서 형식·호출 제한 등으로 자료가 누락될 수 있습니다.
- 영업현금흐름에서 유형자산 취득 지출만 차감한 FCF 참고치는 회사 발표 FCF와 다를 수 있습니다.
- 기간·통화·연결 범위를 맞춘 계산도 원자료의 정확성이나 해석의 타당성까지 보장하지는 않습니다.
- 동일 모델의 검토는 잘못된 인과·상품 분류·기간 표현을 통과시킬 수 있습니다. 단일 시점 수치로 증가·감소를 단정하지 않아야 합니다.
- ETF의 일반 위험 문구만으로 새로운 호재·악재나 현재 편입 비중을 확인했다고 볼 수 없습니다.
- 저장 포트폴리오는 저장 수량 기준 연구 모드입니다. 과거 시점에 알 수 있었던 정보만 사용하는 백테스트가 아닙니다.
- 기술적 분석 에이전트, 검증된 컨센서스·목표주가·자동 매매·개인 투자 가설 추적은 이번 구현 범위에 포함하지 않습니다.

후속 개선은 의미·인과 검증, 기업·펀드 유형 일치, 기간 표현, 사업·경쟁·촉매·가치평가의 근거와 분석 깊이에 집중합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `backend/official_sources.py` | SEC·DART, 발행회사 매핑·캐시·본문 발췌 |
| `backend/financials.py` | 재무 정규화·기간 비교·계산·입력 연결 |
| `backend/deep_research.py` | 종목별 분석·검토·거시 연결·누락 점검 |
| `backend/agents.py` | 분석 계약·모델 호출 연결·근거 검토 |
| `backend/remaining_fixes.py` | 제한적 인용·연결 오류 검사와 복구 |
| `backend/model_runtime.py` | 모델 전송·스키마·호출 진단 |
| `backend/research_runtime.py` | CLI·워커의 공통 수집·분석 흐름 |
| `backend/research_render.py` | 상세 Markdown·재무표·출처·검토 기록 |
| `backend/research_tools.py` | 수집·평가·저장 자료 재분석 도구 |
| `backend/repair_saved_links.py` | 저장 보고서의 오류 연결 선택 복구 |
| `backend/doctor.py`, `backend/tests/` | 오프라인 설치 진단과 회귀 검사 |
| `frontend/src/`, `frontend/tests/` | 대시보드와 데이터 계약 검사 |

## 저장소와 라이선스

`.env`, 가상환경, node_modules, 실제 보유 데이터·보고서·진단 로그·백업은 Git에서 제외합니다. `.gitignore`를 추가해도 이미 추적 중인 파일이나 과거 커밋 이력이 자동으로 정리되지는 않습니다. 커밋 전 `git status --short`와 `git diff --cached`를 확인하세요.

Copyright (C) 2026 Seungbeom Hong (홍승범). 프로젝트의 저작권 표기와 [NOTICE](NOTICE), [LICENSE](LICENSE)의 **AGPL-3.0-or-later** 안내를 유지합니다. 외부 라이브러리·모델·데이터는 각각의 라이선스와 이용 조건을 따릅니다.

공식 참고 자료: [SEC 공개 API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [OpenDART](https://opendart.fss.or.kr/), [FRED API](https://fred.stlouisfed.org/docs/api/fred/), [Vite 설정](https://vite.dev/config/).

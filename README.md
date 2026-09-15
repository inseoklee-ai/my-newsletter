# My Newsletter — 나만의 뉴스레터 에이전트

매일 아침 관심 분야 뉴스를 자동으로 수집·선별·요약·검수해서 Discord·이메일·텔레그램
세 곳에 동시 발행하는 LangGraph 파이프라인. 설계 판단 근거는 [REPORT.md](REPORT.md)에,
"왜 이렇게 만들었는가"를 다시 볼 때는 이 README를 본다.

## 이 뉴스레터의 성격

| 항목 | 내용 |
|---|---|
| **분야** | AI 전반 — LLM/AI Agent 발전, AI 보안, 양자컴퓨터, AI 관련 사회·경제·정치 이슈, 국내 기업 AI 도입 (다섯 갈래를 의도적으로 한 뉴스레터에 묶음) |
| **독자** | 함께 공부하는 AI 개발자 스터디원 — LLM API·Agent 기본 개념은 이미 아는 실무 지향 독자 |
| **톤** | 건조한 사실 전달(3문장 요약) + "그래서 지금 뭘 해볼 수 있는가"(`실무적용법`)를 더한 실무 지향 톤. 기자체 과장 표현 금지 |
| **발행 주기** | 매일 07:30 KST (GitHub Actions 스케줄), 후보가 0건이어도 "오늘은 조용합니다"를 발행해 파이프라인 생존을 알림 |
| **채널** | Discord(임베드) · 이메일(3인, Gmail SMTP) · 텔레그램(봇) — 세 곳 모두 같은 콘텐츠, 채널마다 실패해도 나머지는 계속 발행 |
| **소스** | 10곳, 국내/해외·뉴스/논문을 섞음. 전부 "그럴듯해서"가 아니라 G1(본문)·G2(생존)·G3(접근) 관문을 실측해 채택 (근거는 [REPORT.md](REPORT.md) 2절, `sources.yaml` 주석) |

## 아키텍처

```
collect(①수집) → select(②선별: 예선→본선) → report(③취재, 기사 수만큼 병렬)
              → verify(④검수) → publish(⑤발행: Discord+Email+Telegram)
```

LangGraph `StateGraph` 하나로 위 다섯 단계를 그래프로 연결했다. `select`→`report` 구간은
`Send`로 팬아웃되어 선별된 기사 수만큼 취재가 병렬로 돈다. 구조도 전체는
[REPORT.md 4절](REPORT.md#4-파이프라인-구조도)의 Mermaid 다이어그램 참고.

## 파일 구성

| 파일 | 역할 | 도메인 바뀌어도 안 건드리는 파일인가 |
|---|---|---|
| `graph.py` | State·노드 다섯 개·그래프 조립·`run()` — **구조** | 대부분 그대로 (아래 "재사용" 참고) |
| `run.py` | 진입점. `graph.run()`을 부르고 로그를 찍는다 | 그대로 |
| `audience.yaml` | 독자·중요도 기준·제외 기준 — **내용** | 도메인 바뀌면 이 파일을 고친다 |
| `sources.yaml` | 수집 소스 목록(이름+URL) — **내용** | 도메인 바뀌면 이 파일을 고친다 |
| `requirements.txt` | GitHub Actions(pip)용 의존성 고정 | uv로 패키지 추가할 때마다 버전 맞춰 갱신 |
| `store/metrics.jsonl` | 실행마다 한 줄씩 남는 지표(건수·소스별 분포·채널 성공 여부) | 자동 생성됨, 직접 편집 안 함 |
| `.github/workflows/daily.yml` | 매일 07:30 KST 자동 실행 + 수동 실행(`workflow_dispatch`) | 시크릿 이름 늘어나면만 수정 |
| `REPORT.md` | 과제 제출용 — 도메인 선택 이유, 소스 채택표, 설계 판단, 회고 | 프로젝트마다 새로 씀 |

## 로컬 실행

```bash
uv sync
uv run python run.py --dry-run   # 실제 발행 없이 로그만
uv run python run.py             # 실제 발행 (Discord+Email+Telegram)
```

필요한 환경변수: `OPENAI_API_KEY`, `DISCORD_WEBHOOK_URL`, `GMAIL_ADDRESS`,
`GMAIL_APP_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. 로컬에서는 이 프로젝트
전용 `.env` 대신 `~/projects/keys.env`(다른 프로젝트와 공유하는 키 파일)를 셸에 로드해서 쓴다.

## GitHub Actions 배포

`.github/workflows/daily.yml`이 매일 자동 실행한다. Settings → Secrets and variables →
Actions에 위 6개 환경변수를 등록해야 한다(코드에는 절대 적지 않는다). 수동 실행은 Actions
탭 → "매일 브리핑" → Run workflow (`dry_run` 체크박스로 실제 발행 여부 선택).

## 이 패턴을 다른 분야로 다시 쓰려면

1. **분야·독자를 정한다** — 페르소나가 명확할수록 `select`/`report`가 잘 걸러낸다.
2. **소스 후보를 실측한다** — "그럴듯한 곳"을 나열하지 말고 G1(본문 600자 이상 추출되는가)·
   G2(최근에도 올라오는가)·G3(크롤링이 막히지 않는가) 세 관문을 코드로 직접 재본 다음 채택한다.
   (측정 스크립트는 이 대화 기록에 남아 있고, 패턴은 `sources.yaml` 주석 참고)
3. **`audience.yaml`/`sources.yaml`만 새로 쓴다** — `graph.py`는 그대로 두는 게 원칙이다.
   프롬프트나 소스 목록이 코드에 다시 박히기 시작하면 구조가 무너진다.
4. **발행 채널의 수신자/사용자명만 바꾼다** — `graph.py`의 `EMAIL_RECIPIENTS`,
   `DISCORD_USERNAME` 정도만 프로젝트별로 다르면 된다.
5. **묶음 크기(BATCH)는 후보 규모를 보고 정한다** — 후보가 하루 수백 건이면 40/8 같은 값을,
   수십 건이면 더 작게 가도 된다. 예선 통과 건수(`픽 → 예선 N → 최종 5`)가 로그에 남으니
   실제로 얼마나 좁혀지는지 보고 조정하면 된다.

## 알려진 한계

- `select`가 매기는 `event`(같은 사건 라벨)가 `report`/`publish`까지 이어지지 않아, 같은
  사건을 다룬 기사가 최종 5건 중 여러 자리를 차지할 수 있다.
- `why`/`실무적용법`처럼 원문과 대조 불가능한 해석형 필드는 `verify`가 사실관계를 검증해줄
  수 없다 — 정책/사회 이슈 기사일수록 이 칸이 일반론으로 흐르는 경향이 있었다.
- "며칠째 조용하면 그 자체가 이상 신호"라는 관측형 알림(연속 무발행 감지)은 아직 없다.

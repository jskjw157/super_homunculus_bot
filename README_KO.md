# Super Homunculus Bot

Claude AI 기반 멀티 플랫폼 챗봇 어시스턴트. **텔레그램**과 **디스코드**에서 자연어로 명령하면, AI가 코드 작성, 파일 생성, 웹 브라우징 등을 자율 수행하고 결과를 보고합니다.

> "연금술사의 호문쿨루스처럼, 만들어서 부리는 AI 심부름꾼"

## 아키텍처

```
┌─────────────┐     ┌─────────────┐
│  텔레그램    │     │  디스코드    │
│  리스너      │     │  리스너      │
└──────┬───────┘     └──────┬──────┘
       │                    │
       ▼                    ▼
┌──────────────────────────────────┐
│     플랫폼 어댑터 (교체 가능)     │
│       Strategy Pattern           │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│          작업 엔진               │
│  병합 → 잠금 → 워크스페이스 → AI │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│     Claude Agent SDK 브릿지      │
│   (세션 유지, 스트리밍 응답)      │
└──────────────────────────────────┘
```

## 주요 기능

- **멀티 플랫폼**: 텔레그램 + 디스코드 통합 파이프라인
- **세션 연속성**: 봇 재시작 후에도 AI 대화 이어감
- **동시 실행 방지**: 파일 기반 잠금 + 스탈 감지 (30분 타임아웃)
- **작업 메모리**: 키워드 검색 가능한 과거 작업 인덱스
- **파일 지원**: 사진, 문서, 오디오, 비디오, 위치 공유
- **크로스 플랫폼**: macOS (launchd) / Linux (cron) / Windows (Task Scheduler)

## 빠른 시작

### 1. 설치

**macOS / Linux:**
```bash
git clone https://github.com/your-username/super_homunculus_bot.git
cd super_homunculus_bot
pip install -e ".[dev]"
```

**Windows:**
```
scripts\setup.bat
```
더블클릭하면 Python 확인 → 패키지 설치 → .env 생성까지 자동 처리.

### 2. 봇 토큰 설정

`.env.example`을 `.env`로 복사하고 토큰을 입력합니다.

```bash
cp .env.example .env
```

**텔레그램 봇 토큰 발급:**
1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색
2. `/newbot` 명령으로 봇 생성
3. 발급된 토큰을 `.env`에 입력

**디스코드 봇 토큰 발급:**
1. [Discord Developer Portal](https://discord.com/developers/applications) 접속
2. New Application → Bot → Token 복사
3. Bot 설정에서 **Message Content Intent** 활성화

**사용자 ID 확인:**
```bash
python scripts/get_my_id.py
```

### 3. 리스너 실행

```bash
# 텔레그램 (터미널 1)
python -m homunculus.platforms.telegram.listener

# 디스코드 (터미널 2)
python -m homunculus.platforms.discord.listener
```

### 4. 메시지 처리

**수동 실행:**
```bash
python scripts/run_telegram.py
python scripts/run_discord.py
```

**자동 스케줄링:**

| OS | 방법 |
|----|------|
| macOS | `bash scripts/setup_scheduler.sh both` |
| Linux | `bash scripts/setup_scheduler.sh both` |
| Windows | `scripts\register_scheduler.bat` (관리자 실행) |

## 프로젝트 구조

```
super_homunculus_bot/
├── homunculus/                  # 메인 패키지
│   ├── core/                    # 작업 엔진, 잠금, 메모리, SQLite 저장소
│   │   ├── engine.py            # 작업 오케스트레이션 파이프라인
│   │   ├── lock.py              # 파일 기반 동시성 제어
│   │   ├── memory.py            # 작업 인덱스 + 워크스페이스 관리
│   │   └── store.py             # SQLite 메시지 큐
│   ├── platforms/
│   │   ├── base.py              # PlatformAdapter 추상 클래스
│   │   ├── telegram/            # 텔레그램 어댑터
│   │   └── discord/             # 디스코드 어댑터
│   ├── ai/
│   │   └── bridge.py            # Claude Agent SDK 연동
│   └── session/
│       └── manager.py           # 세션 라이프사이클
├── scripts/                     # 실행 스크립트
│   ├── run_telegram.py          # 텔레그램 처리
│   ├── run_discord.py           # 디스코드 처리
│   ├── setup.bat                # Windows 자동 설정
│   ├── register_scheduler.bat   # Windows 스케줄러 등록
│   ├── setup_scheduler.sh       # macOS/Linux 스케줄러
│   └── get_my_id.py             # 사용자 ID 확인
├── tests/                       # 테스트
└── docs/                        # 문서
```

## 디자인 패턴

| 패턴 | 적용 위치 | 이유 |
|------|-----------|------|
| **Strategy** | `PlatformAdapter` | 텔레그램/디스코드를 엔진 수정 없이 교체 |
| **Template Method** | `TaskEngine` | 공통 파이프라인, 플랫폼별 커스텀 스텝 |
| **Repository** | `MessageStore` | SQLite 상세 구현을 어댑터로부터 추상화 |
| **Singleton** | 컨텍스트별 `LockManager` | 채널당 하나의 잠금 |

## 새 플랫폼 추가하기

1. `homunculus/platforms/myplatform/` 디렉토리 생성
2. `MyPlatformAdapter(PlatformAdapter)` 구현
3. listener, sender 모듈 작성
4. `scripts/run_myplatform.py` 추가

엔진과 AI 브릿지는 수정할 필요 없습니다.

## 요구사항

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- 텔레그램 / 디스코드 봇 토큰

## 라이선스

MIT

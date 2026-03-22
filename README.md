# Super Homunculus Bot

An AI-powered multi-platform chat assistant that bridges **Telegram** and **Discord** with **Claude AI** for autonomous task execution.

Send a message to your bot — it understands natural language, executes code, creates files, browses the web, and reports back with results.

## Architecture

```mermaid
graph TD
    TL[Telegram Listener] --> PA[Platform Adapters]
    DL[Discord Listener] --> PA
    PA -->|Strategy Pattern| TE[Task Engine]
    TE -->|merge → lock → workspace| CB[Claude Agent SDK Bridge]
    CB -->|session resume & streaming| AI((Claude AI))
    AI -->|result| TE
    TE -->|deliver| PA
    PA --> TL
    PA --> DL

    style AI fill:#7c3aed,color:#fff
    style TE fill:#2563eb,color:#fff
    style PA fill:#059669,color:#fff
```

## Message Processing Flow

```mermaid
sequenceDiagram
    participant U as User
    participant L as Listener
    participant S as Store
    participant E as AutoExecutor
    participant C as Claude AI

    U->>L: Send message
    L->>S: Save to JSON/SQLite
    Note over E: Runs every 30s
    E->>S: Any pending messages?
    S-->>E: Yes (N messages)
    E->>E: Merge & acquire lock
    E->>C: Execute task
    C->>C: Process (code, files, browse...)
    C->>U: Deliver result
    C->>E: Done
    E->>S: Mark completed
    E->>E: Release lock
```

## Features

- **Multi-platform**: Telegram + Discord with unified task pipeline
- **Session continuity**: AI conversations persist across bot restarts
- **Concurrent safety**: File-based locks with staleness detection
- **Task memory**: Searchable index of all past work with keyword retrieval
- **File support**: Photos, documents, audio, video, location sharing
- **Cross-platform**: macOS (launchd), Linux (cron), Windows (Task Scheduler)

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/your-username/super_homunculus_bot.git
cd super_homunculus_bot
pip install -e ".[dev]"
```

**Windows:** Double-click `scripts\setup.bat` instead.

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your bot tokens
```

**Get your Telegram bot token:**
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the token to `.env`

**Get your Discord bot token:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application → Bot → Copy token
3. Enable **Message Content Intent** under Bot settings

**Find your user ID:**
```bash
python scripts/get_my_id.py
```

### 3. Start Listeners

```bash
# Telegram (in one terminal)
python -m homunculus.platforms.telegram.listener

# Discord (in another terminal)
python -m homunculus.platforms.discord.listener
```

### 4. Process Messages

```bash
# One-shot processing
python scripts/run_telegram.py
python scripts/run_discord.py

# Or set up scheduled execution (auto-check every 30s)
bash scripts/setup_scheduler.sh          # macOS / Linux
scripts\register_scheduler.bat           # Windows (run as admin)
```

## Project Structure

```mermaid
graph LR
    subgraph homunculus
        subgraph core
            E[engine.py] --- LK[lock.py]
            E --- M[memory.py]
            E --- ST[store.py]
        end
        subgraph platforms
            B[base.py] --> TG[telegram/]
            B --> DC[discord/]
        end
        subgraph ai
            BR[bridge.py]
        end
        subgraph session
            SM[manager.py]
        end
    end
    core --> platforms
    core --> ai
    core --> session
```

## Design Patterns

```mermaid
classDiagram
    class PlatformAdapter {
        <<abstract>>
        +fetch_pending() list
        +send_text(chat_id, text) bool
        +send_files(chat_id, text, paths) bool
        +deliver_result(...)
        +mark_completed(msg_ids)
    }
    class TelegramAdapter {
        -_msg_path: str
        +fetch_pending() list
        +deliver_result(...)
    }
    class DiscordAdapter {
        -_store: MessageStore
        +fetch_pending(ctx) list
        +get_pending_contexts() list
    }

    PlatformAdapter <|-- TelegramAdapter
    PlatformAdapter <|-- DiscordAdapter
    TaskEngine --> PlatformAdapter : uses
    TaskEngine --> LockManager
    TaskEngine --> MemoryManager
```

## Adding a New Platform

1. Create `homunculus/platforms/myplatform/`
2. Implement `MyPlatformAdapter(PlatformAdapter)`
3. Add listener and sender modules
4. Create `scripts/run_myplatform.py`

That's it — the engine and AI bridge work unchanged.

## Requirements

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (for AI execution)
- Telegram/Discord bot tokens

## License

MIT

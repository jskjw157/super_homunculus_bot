# Architecture

## Layer Diagram

```
Layer 5: Session Management
  └── SessionLifecycle — persist/restore AI sessions across restarts

Layer 4: AI Integration
  └── ClaudeBridge — Agent SDK wrapper with streaming + retry

Layer 3: Platform Adapters (Strategy Pattern)
  ├── TelegramAdapter — Bot API + JSON message store
  └── DiscordAdapter — Gateway + SQLite message store

Layer 2: Core Engine
  ├── TaskEngine — orchestration pipeline
  ├── LockManager — file-based mutual exclusion
  ├── MemoryManager — workspace + manifest + search index
  └── MessageStore — SQLite message queue

Layer 1: Infrastructure
  └── .env, pyproject.toml, scripts/
```

## Data Flow

```
User sends message
  → Listener captures to store (JSON or SQLite)
  → Scheduler triggers processing script
  → Adapter.fetch_pending() returns unprocessed messages
  → Engine.merge_pending() combines into work unit
  → Adapter.send_text() acknowledges receipt
  → Engine.begin_work() acquires lock + reserves workspace
  → ClaudeBridge.run() executes via Agent SDK
  → Adapter.deliver_result() sends output to user
  → Engine.finish_work() updates index + releases lock
  → Adapter.mark_completed() marks messages as done
```

## Concurrency Model

- **Process-level**: Only one processor runs at a time per context (lock file)
- **Thread-level**: SQLite store uses `threading.Lock` for thread safety
- **Staleness**: Locks auto-expire after 30 min without heartbeat
- **Recovery**: Stale processing messages reset to pending on next cycle

# Agent Integration

This directory contains integration examples showing how agents should interact with the Content Quality system.

## Agent Pipeline

The ClaimCoach content pipeline consists of 7 agents:

```
Scout → Quill → Sage → Ezra → Herald
                                   ↓
                         Lurker ← Morgan
```

### Agent Descriptions

| Agent | Role | Integrates With |
|-------|------|----------------|
| **Scout** | Keyword research, topic discovery | Database (writes to `articles` table) |
| **Quill** | Article writer | Reference docs (loads at runtime) + Database |
| **Sage** | Quality reviewer | Validation API (`POST /validate/all`) + Database |
| **Ezra** | Publisher | Ghost API + Google Search Console + Database |
| **Herald** | Social promoter | Reddit/Twitter APIs + Database |
| **Lurker** | Opportunity scanner | Reddit API + Database |
| **Morgan** | PM/orchestrator | Database (reads all tables, spawns agents) |

## Integration Points

This quality system provides **two main integration points** for agents:

### 1. Reference Documents (Quill)

**Location:** `reference/PRODUCT_CONTEXT.md`, `reference/STATE_RULES.md`

**Usage:** Quill loads these at runtime and includes in Claude API system prompt

**See:** `integration_examples/quill_integration.py`

### 2. Validation API (Sage)

**Endpoint:** `POST http://content-quality-api:8000/validate/all`

**Usage:** Sage calls this before approving articles for publishing

**See:** `integration_examples/sage_integration.py`

### 3. Database (All Agents)

**Location:** `data/claimcoach_content.db` (SQLite)

**Usage:** All agents read/write to shared database for coordination

**Helpers:** `content_quality/db.py` provides connection helpers and atomic claim locking

## Integration Examples

- **`quill_integration.py`** - How Quill loads reference docs and writes articles
- **`sage_integration.py`** - How Sage calls validation API and processes results
- **`morgan_integration.py`** - How Morgan reads monitor results and spawns actions

## Quick Start

See `INTEGRATION.md` in project root for complete integration guide.

## Testing

Test your agent integration:

```python
# Test Quill reference loading
python integration_examples/quill_integration.py --test

# Test Sage validation call
python integration_examples/sage_integration.py --test

# Test Morgan monitoring
python integration_examples/morgan_integration.py --test
```

## Deployment

Agents are typically deployed separately from the quality system:

- **Quality System**: Railway services (API server + weekly monitors)
- **Agents**: Railway cron jobs or separate deployment

Both access the same SQLite database (shared volume).

## Support

For integration questions, see:
- `../INTEGRATION.md` - Complete integration guide
- `../ARCHITECTURE.md` - System overview
- `../DEPLOYMENT.md` - Deployment instructions

# SamvadSQL

SamvadSQL is a multi-agent SQL testing system that:

- analyzes a SQL query,
- generates critical test scenarios,
- optionally generates missing test data,
- executes each scenario in an isolated PostgreSQL sandbox,
- reports pass/fail with reasons.

## Stack

- FastAPI (backend)
- Streamlit (frontend)
- LangGraph (agent orchestration)
- PostgreSQL (execution target)
- Pandas/Plotly (UI reporting)
- OpenAI or Gemini (optional LLM enrichment)

## Setup

```bash
pip install -r requirements.txt
```

Create `.env`:

```env
# API
API_HOST=0.0.0.0
API_PORT=8000

# Database
DATABASE_URL=postgresql+psycopg2://postgres:password@localhost:5432/your_db
# OR configure POSTGRES_* fields instead of DATABASE_URL
# POSTGRES_HOST=localhost
# POSTGRES_PORT=5432
# POSTGRES_USER=postgres
# POSTGRES_PASSWORD=password
# POSTGRES_DB=your_db

# SQL test isolation mode
# transaction = run each scenario inside BEGIN/ROLLBACK (default, fast)
# database = create/drop a temporary database for each scenario
TEST_ISOLATION_MODE=transaction
# Auto fallback to database mode for non-transaction-safe SQL
TEST_ISOLATION_AUTO_FALLBACK=true

# If database isolation is used (explicitly or fallback), template DB is required
TEST_DB_TEMPLATE=test_template

# LLM (optional)
LLM_PROVIDER=none
# LLM_PROVIDER=openai
# OPENAI_API_KEY=...
# OPENAI_MODEL=gpt-4o-mini
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=...
```

## Run

Backend:

```bash
uvicorn src.api.main:app --reload
```

Frontend:

```bash
streamlit run src/ui/streamlit_app.py
```

## Safety Approval

Destructive or important SQL (e.g. `DELETE`, `DROP`, `UPDATE`) requires approval.

Provide `approval_token` in the API request (default allowed tokens: `yes`, `y`) or in the Streamlit UI.

## API

`POST /api/test-query`

Request body:

```json
{
  "sql_query": "SELECT * FROM orders",
  "generate_missing_data": true,
  "include_performance_scenario": false,
  "max_scenarios": 8,
  "approval_token": "yes"
}
```

Possible responses:

- `status=success`: report included
- `status=awaiting_approval`: approval required before execution

## Isolation Strategy

- Default: `transaction` mode (`BEGIN` -> execute scenario -> `ROLLBACK`)
- Automatic fallback to `database` mode for non-transaction-safe SQL
- You can force per-scenario database cloning with `TEST_ISOLATION_MODE=database`

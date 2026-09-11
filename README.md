# Computer-Use Automation System

A system for automating legacy bank applications through LLM-driven discovery and deterministic replay.

## Overview

This system enables AI agents to operate legacy banking applications that lack APIs. It works in two phases:

1. **Discovery**: An LLM-driven agent explores the application to accomplish a goal, recording every action
2. **Replay**: The recorded flow is saved as a typed, versioned artifact and replayed deterministically without the LLM

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   AI Agent      │────▶│  Discovery       │────▶│  Artifact       │
│   (decides      │     │  (LLM-driven     │     │  (typed,        │
│   what to do)   │     │   observe-decide-│     │   versioned)    │
└─────────────────┘     │   act loop)      │     └────────┬────────┘
                        └──────────────────┘              │
                                                         ▼
                        ┌──────────────────┐     ┌─────────────────┐
                        │  Human Operator  │◀───▶│  Replay Engine  │
                        │  (escalation)    │     │  (deterministic │
                        └──────────────────┘     │   execution)    │
                                                 └─────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- Playwright browsers: `playwright install chromium`

### Installation

```bash
# Clone and install
cd interface.ai
pip install -e ".[dev]"

# Install Playwright browsers
playwright install chromium
```

### Run the Demo App

```bash
# Terminal 1: Start the demo banking application
python -m src.cli start-demo-app
```

The demo app will be available at http://localhost:5000

### Run Discovery

```bash
# Terminal 2: Run LLM-driven discovery
python -m src.cli discover "look up member 12345 and read their current savings balance" \
  --inputs '{"member_id": "12345"}' \
  --llm-provider mock \
  --headless false
```

This will:
1. Launch a browser
2. Navigate to the demo app
3. Use the LLM to figure out how to accomplish the goal
4. Save the discovered flow as an artifact in `artifacts/`

### Run Replay

```bash
# Replay the artifact deterministically
python -m src.cli replay artifacts/discovered_artifact.json \
  --inputs '{"member_id": "12345"}' \
  --headless true
```

### Run Full Demo

```bash
# Runs complete end-to-end: demo app + discovery + replay
python -m src.cli run-full-demo --headless false --llm-provider mock
```

## Commands

| Command | Description |
|---------|-------------|
| `start-demo-app` | Start the demo banking application |
| `discover` | Run LLM-driven discovery for a goal |
| `replay` | Replay an artifact deterministically |
| `list-artifacts` | List available artifacts |
| `validate-artifact` | Validate an artifact schema |
| `run-full-demo` | Run complete end-to-end demo |

## Project Structure

```
interface.ai/
├── src/
│   ├── agent/           # LLM-driven discovery agent
│   │   ├── discovery.py     # Main discovery loop
│   │   └── llm_client.py    # LLM provider abstraction
│   ├── artifacts/       # Artifact schema and serialization
│   │   └── schema.py        # Core artifact schema (Pydantic models)
│   ├── replay/          # Deterministic replay engine
│   │   └── engine.py        # Replay execution with error handling
│   ├── safety/          # Safety guardrails
│   │   └── guardrails.py    # Allowlist, redaction, risk classification
│   ├── escalation/      # Human-in-the-loop escalation
│   │   └── manager.py       # Escalation detection and handoff
│   ├── evidence/        # Observability and evidence collection
│   │   └── collector.py     # Screenshots, DOM snapshots, logs
│   ├── surface/         # Surface abstraction layer
│   │   ├── base.py          # Abstract base class
│   │   └── web.py           # Playwright implementation
│   ├── demo_app/        # Demo banking application
│   │   └── app.py           # Flask app with legacy-style UI
│   ├── config.py        # Configuration management
│   └── cli.py           # CLI entry point
├── evidence/            # Generated evidence (gitignored)
├── artifacts/           # Saved artifacts
├── tests/               # Tests
├── pyproject.toml       # Project configuration
└── README.md            # This file
```

## Artifact Schema

The artifact is the core contract - a typed, versioned capability that AI agents can invoke.

Key components:
- **Metadata**: ID, version, description, target application
- **Inputs**: Typed parameters (e.g., `member_id: string`)
- **Outputs**: Typed return values (e.g., `savings_balance: number`)
- **Steps**: Ordered actions with robust element locators
- **Success Condition**: How to verify completion
- **Known Outcomes**: Expected business results (e.g., "member not found")

Example artifact:
```json
{
  "metadata": {
    "artifact_id": "uuid",
    "name": "lookup_member_savings_balance",
    "version": "1.0.0",
    "target_application": "demo_banking"
  },
  "inputs": [{"name": "member_id", "type": "string", "required": true}],
  "outputs": [{"name": "savings_balance", "type": "number", "source_step": "step_3"}],
  "steps": [
    {"action": "navigate", "url": "http://localhost:5000/member-lookup"},
    {"action": "type", "target": {"primary": "css_selector", "primary_value": "#member_id"}, "value": "{{member_id}}"},
    {"action": "click", "target": {"primary": "text_content", "primary_value": "Search"}},
    {"action": "extract", "target": {"primary": "text_content", "primary_value": "Savings"}, "extract_as": "savings_balance"}
  ],
  "success_condition": {"type": "checkpoint", "value": "balance_extracted"}
}
```

## Safety Features

- **Domain Allowlist**: Only permitted domains can be accessed
- **Action Allowlist**: Only permitted action types can be executed
- **Risk Classification**: Actions classified as safe/caution/risky/critical
- **Confirmation Required**: Risky actions require explicit confirmation
- **Data Redaction**: Sensitive data (SSN, account numbers, etc.) redacted from logs/artifacts

## Escalation

When the system gets stuck, it can escalate to a human operator:

1. **Detection**: Identifies stuck states (element not found, unexpected UI, errors)
2. **Context Capture**: Screenshots, DOM, accessibility tree, session state
3. **Handoff**: Pauses automation, transfers live session to operator
4. **Resume**: Operator performs manual steps, hands back control
5. **Recording**: All human actions recorded for audit

## Evidence & Observability

Every run produces:
- Structured JSON logs
- Screenshots on failure (and optionally every step)
- DOM snapshots
- Accessibility tree dumps
- Playwright traces
- Run summaries

## Extending for Production

### Surface Abstraction

The `BaseSurface` interface allows supporting different surfaces:
- `WebSurface` - Playwright (implemented)
- `DesktopSurface` - Windows UI Automation / macOS Accessibility
- `MobileSurface` - Appium

### Multi-Tenant Reuse

Artifacts support parameterization for cross-tenant reuse:
- Parameterized URLs: `/member/{{member_id}}` instead of `/member/12345`
- Per-tenant selector overrides
- Version detection for drift management

### Agent-Facing Interface

Artifacts can be exposed as callable capabilities:
```python
# Agent invokes capability
result = await capabilities.invoke("lookup_member_savings_balance", {"member_id": "12345"})
# Returns: {"savings_balance": 15420.50}
```

## Configuration

Environment variables (or `.env` file):

```bash
# LLM
LLM_PROVIDER=openai          # or anthropic, mock
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Safety
ALLOWED_DOMAINS=localhost,127.0.0.1,mybank.com
RISKY_ACTIONS=delete,transfer,approve

# Timeouts
MAX_STEPS=20
STEP_TIMEOUT_SECONDS=30
```

## Development

```bash
# Run tests
pytest tests/

# Lint
ruff check src/

# Type check
mypy src/

# Format
ruff format src/
```

## License

MIT License - See LICENSE file for details.
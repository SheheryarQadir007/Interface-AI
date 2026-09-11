# Design Report: Computer-Use Automation System

## 1. Architecture

### Overview

The system follows a clean three-layer architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Agent (Consumer)                       │
│  Decides WHAT to do → Invokes capability by name + inputs   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Capability Layer (This System)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Discovery   │  │   Artifact   │  │    Replay        │  │
│  │  (LLM loop)  │──▶│  (Contract)  │──▶│  (Deterministic) │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│         │                                    │              │
│         ▼                                    ▼              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           Cross-Cutting Concerns                      │  │
│  │  Safety │ Escalation │ Evidence │ Surface Abstraction│  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Target Surface                            │
│  Web (Playwright) │ Desktop (UIA/AX) │ Mobile (Appium)      │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Separate Discovery vs Replay** | Discovery is expensive (LLM calls) and non-deterministic; replay must be fast, cheap, and deterministic. Separating them allows artifacts to be reviewed, approved, and reused thousands of times. |
| **Artifact as Typed Contract** | Not just a step list - includes typed inputs/outputs, success conditions, known outcomes, and versioning. Enables agent-facing function calling and automated testing. |
| **Surface Abstraction** | `BaseSurface` interface decouples artifact schema from execution surface. Same artifact can replay on web, desktop, or mobile with different `LocatorResolver` implementations. |
| **Locator Fallback Chain** | Primary + fallback strategies (test_id → aria_label → role+name → text → css → xpath → coordinates). Critical for legacy apps without stable selectors. |
| **Explicit Business Outcomes** | "Member not found" is a valid result, not a failure. Artifact declares known outcomes with detection logic, separating them from technical failures. |
| **Human-in-the-Loop as First-Class** | Escalation captures full session context (cookies, storage, URL), pauses automation, lets human operate same live session, then resumes. Not a TODO - implemented with `pause_automation`/`resume_automation`. |

### Trade-offs

1. **Simplicity vs. Completeness**: Chose to implement a thin-but-real version of every core requirement rather than polishing a subset. The operator console is mocked but the handoff mechanism is real.
2. **Mock LLM for Demo**: Uses a mock LLM client by default so the system runs without API keys. Production would swap in OpenAI/Anthropic.
3. **Single-Process Architecture**: No queues, workers, or distributed systems. The core abstractions are designed to scale but the implementation is a single process for clarity.
4. **Playwright Only**: Web surface is fully implemented; desktop/mobile are designed but not built. The surface abstraction makes adding them straightforward.

## 2. Artifact Schema

### Design Principles

The artifact schema (`src/artifacts/schema.py`) is the system's central contract. It was designed for:

- **Reviewability**: Human can read and understand what a capability does
- **Invocability**: AI agent can call it with typed inputs, get typed outputs
- **Replayability**: Deterministic execution without LLM
- **Versioning**: Semantic versioning with change tracking
- **Safety**: Explicit risk classification and domain allowlists

### Schema Structure

```
AutomationArtifact
├── metadata: ArtifactMetadata (id, name, version, description, tags, target_app)
├── inputs: List[InputParameter] (name, type, required, sensitive, validation)
├── outputs: List[OutputParameter] (name, type, source_step, extract_path)
├── steps: List[ActionStep] (ordered, self-contained)
├── success_condition: SuccessCondition (checkpoint/url/element/custom)
├── known_outcomes: List[ErrorOutcome] (business results, not failures)
├── preconditions/postconditions: List[str] (contract guarantees)
└── safety: allowed_domains, risky_steps
```

### Element Locator Design

```python
ElementLocator:
  primary: LocatorStrategy (TEST_ID > ARIA_LABEL > ROLE_AND_NAME > TEXT > CSS > XPATH > COORDINATES)
  primary_value: str
  fallbacks: List[{strategy, value}]
  frame_context: Optional[str]
  description: str  # Human-readable
```

**Why this strategy order?**
- `data-testid`: Developer-added, stable, semantic - gold standard
- `aria-label`/`role`: Accessibility tree, stable across redesigns, works on desktop too
- `text_content`: Fragile but sometimes only option in legacy apps
- `css`/`xpath`: Brittle, structure-dependent
- `coordinates`: Last resort for canvas/desktop apps

### Parameterization

Inputs use `{{variable}}` substitution at replay time:
```json
{"action": "type", "value": "{{member_id}}"}
```
This enables one artifact to work across thousands of invocations with different data.

## 3. Determinism & Error Handling

### Replay Execution Model

The replay engine (`src/replay/engine.py`) executes steps sequentially with:

1. **Variable Substitution**: `{{member_id}}` → actual value from inputs
2. **Locator Resolution**: Tries primary, then fallbacks in order
3. **Action Execution**: Calls surface-specific implementation
4. **Checkpoint Verification**: Asserts expected state after action
5. **Output Capture**: Stores declared outputs for later steps/return

### Error Taxonomy

| Category | Examples | Handling |
|----------|----------|----------|
| **Business Outcomes** | "Member not found", "Insufficient funds", "Account closed" | Declared in artifact, returned as structured result, not failure |
| **Recoverable** | Transient network error, slow load, known interstitial dialog | Retry with backoff, run recovery steps, dismiss known dialogs |
| **Hard Failures** | Element truly gone, permission denied, session expired, app error | Stop, capture evidence, escalate to human |

### Key Mechanisms

**Retries with Backoff**: Per-step configurable retry count and delay
```python
step.retry_count = 3
step.retry_delay_ms = 1000
```

**Expected Errors**: Steps can declare known error patterns that are acceptable
```python
step.expected_errors = ["validation error", "please wait"]
```

**Recovery Steps**: Explicit recovery actions for known issues
```python
step.recovery_steps = [ActionStep(action=CLICK, target=dismiss_button)]
```

**Checkpoint Verification**: Every step can declare a post-condition
```python
step.checkpoint = "member_details_visible"
step.checkpoint_locator = ElementLocator(...)
```

**Success Condition**: Artifact-level verification
```python
success_condition = SuccessCondition(type="element_present", value="css:.balance")
```

## 4. Heterogeneity & Multi-Tenant

### Surface Abstraction

The `BaseSurface` interface (`src/surface/base.py`) defines the contract:

```python
class BaseSurface(ABC):
    async def click(self, locator: Dict) -> SurfaceActionResult
    async def type_text(self, locator: Dict, text: str) -> SurfaceActionResult
    async def extract(self, locator: Dict, attribute: str) -> SurfaceActionResult
    async def get_accessibility_tree(self) -> Dict
    async def pause_automation(self) -> Dict  # For handoff
    async def resume_automation(self, context: Dict)
```

**WebSurface** (`src/surface/web.py`) implements this with Playwright.
**DesktopSurface** would use Windows UI Automation / macOS Accessibility API.
**MobileSurface** would use Appium.

The `LocatorResolver` pattern allows the same `ElementLocator` to resolve differently per surface:
- Web: Playwright locators with CSS/XPath
- Desktop: UIA element properties (AutomationId, Name, ClassName)
- Mobile: Accessibility IDs, XPath

### Multi-Tenant Reuse Strategy

For hundreds of tenants running the same vendor product:

1. **Parameterized Artifacts**: URLs and values use `{{variables}}` not hardcoded values
2. **Selector Overrides**: Per-tenant selector maps in artifact metadata
   ```json
   "tenant_overrides": {
     "tenant_abc": {"member_lookup_button": "css:.btn-primary"},
     "tenant_xyz": {"member_lookup_button": "css:#searchBtn"}
   }
   ```
3. **Version Detection**: Artifact includes `target_url_pattern` and can detect app version via feature flags or DOM signatures
4. **Canonicalization**: Stretch goal - normalize `/member/12345` → `/member/:id` for cross-tenant pattern matching

### Drift Management

- **Detection**: Replay captures evidence; diff against baseline screenshots/DOM
- **Classification**: UI drift (selector broken) vs. behavioral drift (new validation)
- **Remediation**: Auto-update locators from fallback chain; flag for re-recording if primary strategy fails

## 5. Escalation & Handoff

### Detection

`EscalationDetector` (`src/escalation/manager.py`) monitors:
- Consecutive failures (configurable threshold)
- Stuck timeout (no progress for N seconds)
- Element not found after retries
- Unexpected UI state (checkpoint mismatch)
- Session expiry (login redirect detected)

### Handoff Mechanism

```
Automation                    Human Operator
     │                            │
     ├─ pause_automation() ──────▶│  (receives session context)
     │  Returns:                  │
     │  - cookies                 │
     │  - localStorage            │
     │  - current URL             │
     │  - screenshot              │
     │  - accessibility tree      │
     │                            │
     │                            ├─ Manual steps in live session
     │                            │
     │◀─ resume_automation() ─────┤  (returns new context)
     │  Restores:                 │
     │  - cookies                 │
     │  - storage                 │
     │  - navigates to URL        │
     │                            │
     ├─ Continues from step N ───▶│
```

### Implementation

- `WebSurface.pause_automation()`: Captures cookies, localStorage, sessionStorage, trace
- `WebSurface.resume_automation()`: Restores state, navigates back, restarts tracing
- `EscalationManager`: Coordinates detection, routing, and handoff
- `MockOperatorConsole`: Simulates operator UI for demo; real implementation would integrate with PagerDuty/Slack/custom console

### Context Preservation

All evidence (screenshots, logs, step history) preserved across handoff. Human actions recorded as additional steps in run evidence.

## 6. Safety

### Guardrail Layers

1. **Domain Allowlist**: `AllowlistManager` checks every navigation
   ```python
   ALLOWED_DOMAINS = ["localhost", "127.0.0.1", "*.mybank.com"]
   ```

2. **Action Allowlist**: Only permitted action types
   ```python
   ALLOWED_ACTIONS = ["navigate", "click", "type", "select", "scroll", "wait", "extract"]
   ```

3. **Risk Classification**: Per-action risk levels
   ```python
   RISKY_ACTIONS = ["delete", "transfer", "approve", "confirm"]
   ```

4. **Confirmation Gate**: Risky steps require `requires_confirmation=true` in artifact
   - Discovery: LLM can request confirmation
   - Replay: Blocks or escalates for human approval

5. **Data Redaction**: `SensitiveDataRedactor` scrubs:
   - SSN, credit cards, routing/account numbers
   - Emails, phones, DOB
   - Passwords, tokens, API keys
   - Any field marked `sensitive=true` in artifact inputs

### Implementation

- `SafetyEnforcer` checks every action before execution
- Violations logged with full context for audit
- Artifacts sanitized before storage/logging
- Evidence screenshots/DOM also redacted

### Limits

- Cannot prevent all social engineering (LLM could be tricked)
- Relies on allowlist completeness
- Desktop/app automation harder to sandbox than browser
- Human operator could bypass guardrails (audit trail mitigates)

## 7. Cuts & Next Steps

### Deliberately Cut (Scope Management)

| Feature | Reason | Future Work |
|---------|--------|-------------|
| Real operator console | Mocked; handoff mechanism is real | Build React/Vue operator UI with co-browsing |
| Desktop surface | Designed not built | Implement `DesktopSurface` with UIA/Accessibility |
| Multi-tenant infrastructure | Design only | Add tenant registry, selector override service |
| Artifact approval workflow | Design only | Add draft→approved states, confidence scoring |
| Code generation from artifact | Stretch goal | Generate Page Objects, Playwright tests |
| Assisted LLM fallback on replay failure | Stretch goal | Bounded single-step recovery with policy check |
| Cross-tenant canonicalization | Stretch goal | Normalize URLs/selectors, detect drift automatically |
| Multi-run stability testing | Stretch goal | Replay N times, report flakiness score |
| Distributed execution (queues/workers) | Premature scaling | Add when throughput demands it |
| Real LLM integration tests | Requires API keys | Add CI with test API keys |

### What I'd Build Next

1. **Operator Console**: Real-time session view with screenshot streaming, action logging, one-click resume
2. **DesktopSurface**: Windows UI Automation implementation for thick-client banking apps
3. **Artifact Catalog API**: REST/gRPC endpoint for agents to discover and invoke capabilities
4. **Confidence Scoring**: Track replay success rate per artifact, auto-promote stable ones
5. **Drift Detection Pipeline**: Scheduled replays against tenant environments, diff evidence
6. **Policy-Aware LLM Recovery**: On replay failure, allow one LLM step to recover (with strict policy)
7. **Secret Management**: Integrate with Vault/AWS Secrets Manager for credentials
8. **Audit Trail**: Immutable log of all automation runs for compliance

---

*This system demonstrates a complete vertical slice: goal → LLM discovery → typed artifact → deterministic replay with error handling → human escalation → evidence. The core abstractions (artifact schema, surface interface, locator strategy, error taxonomy) are designed to scale to the real banking environment described in the brief.*
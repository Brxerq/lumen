# Session tracking audit and fix plan

Date: 2026-09-08. Scope: discovery, running status, task identity, dashboard/notch consistency, and device assignment. This is an audit and implementation plan; application code and live settings have not been changed.

## 1. Main finding

The reported mismatch is real. Lumen conflates recent rollout-file activity with whether a Codex task is running. It also counts internal helper agents as independent user tabs.

`src/lumen/integrations/codex.py:64` selects recently updated database rows, then lines 76–80 reject them if their rollout file has not changed in ten minutes. A task can remain active while waiting on a tool or delegated work without writing to that file. Increasing the timeout merely postpones the failure.

Read-only evidence from this machine:

| Evidence | Observation |
| --- | --- |
| Codex task inventory | “Audit and fix Airdo outbound calls” and “Plan multi-user dialer numbers” both reported active. |
| Database versus rollout | At one sample, those records were updated approximately 5 and 17 seconds ago, while their rollout files were approximately 3,745 and 2,366 seconds old. |
| Existing parser | Reading those files directly returned an open turn for both. |
| Running Lumen API | Both task IDs were missing from `/api/state.sessions`. |
| Helper agents | The two audit helpers appeared in Lumen as independent, generic “Codex” sessions. Their database source metadata explicitly identifies them as children of this task. Other displayed rows included internal guardian agents. |
| Fallback metadata | Observed rows had empty project paths, null timestamps/context, and generic labels. |
| Slot allocation | One sample had nine rows at slots 0, 1, 2, 3, 5, 6, 7, 14, and 23. Slots become sparse as sessions come and go. |

These were successive live samples, not a reconstruction of the screenshot's exact instant. They confirm the same failure mechanism and related identity problems. The screenshot alone cannot tell which anonymous row corresponds to each real task.

The shared light-bar color and hardware overflow grouping in the screenshots are supported layout modes. Hardware capacity must never reduce task discovery or the running count.

## 2. Findings and proposed fixes

Priorities: P1 means incorrect or lost status; P2 means a related reliability or usability defect. “Reproduced” refers to isolated checks or live observation. “Source-confirmed” means the code path is established but the interaction was not exercised in a browser or on physical hardware.

| ID | Priority / evidence | Problem and source | Planned fix |
| --- | --- | --- | --- |
| F01 | P1 / live + reproduced | Quiet active tasks disappear through the two freshness gates in `integrations/codex.py:54–80`. | Use freshness for discovery and observation health, not proof of completion. Continue checking known active tasks even when outside the discovery window. Remove the redundant rollout-mtime veto for fresh database candidates. |
| F02 | P1 / live | Child workers and guardian threads become top-level tabs because the same query ignores source/parent metadata. | Classify threads before allocating slots. Count user tasks at the top level; associate workers with their parent and exclude internal guardian work from the default list. |
| F03 | P1 / reproduced | A SQLite failure returns `{}`; a single rollout-read exception can discard the entire fallback result. `current_sessions()` converts exceptions to `{}`, `_update_sessions()` removes absent rows, and empty aggregation becomes done (`codex.py:67–80`; `agent_sessions.py:423–428, 483–484, 615–622`). | Distinguish healthy empty results, partial results, and unavailable tracking. Isolate per-file errors; retain last observations during short outages, then show stale/unknown. Never emit completion because a read failed. |
| F04 | P1 / reproduced | An old closed rollout overrides a newer prompt hook unconditionally (`agent_sessions.py:431–441`). A stale input hook can likewise outlive the question that caused it. | Reconcile by matching turn identity and event ordering. Preserve newer hooks until corresponding rollout evidence arrives, without losing missed-Stop correction. |
| F05 | P1 / source + conditional reproduction | The parser ignores session metadata and turn IDs (`codex.py:29–51`). Start A, start B, complete A produces done. Live rollouts also contain inherited/nonmatching session metadata. | Establish which events belong to the current thread/turn before changing state. Validate fork/resume semantics against sanitized local fixtures. Do not count every unmatched historical start as active. |
| F06 | P2 / reproduced | Valid JSON with payload before top-level type is ignored by the regex, returning false (`codex.py:30–32`). | Structurally parse complete JSONL records with the existing incremental cursor. Treat unrecognized/no-boundary data as unknown, not confirmed completion. |
| F07 | P2 / live + source | Fallback returns booleans only; names, cwd, timestamps, source, and provenance are dropped (`codex.py:64, 97–98`; `agent_sessions.py:486–514`). Missing usage is represented as zero cost. | Populate available metadata in the shared record; keep unavailable usage/cost null. Prefer explicit label, safe display title, project basename, then short ID. Never use a raw first-prompt field as an unrestricted display title. |
| F08 | P1 / source-confirmed | Dashboard `refresh()` commits `S.sig` before `render()` returns early for a focused input (`server/ui/app.js:98–102, 132`). Blur does not apply the skipped snapshot. | Mark a snapshot rendered only after success; apply pending state after blur/menu close. Prevent overlapping fetches from applying an older response over a newer revision. |
| F09 | P2 / source-confirmed | The dismiss API only deletes hook files (`server/api.py:342–344`; `agent_sessions.py:414–419`). Fallback-only rows have no such file, despite displaying a dismiss button. | Define dismissal as hiding a tracked row, not closing a Codex task. Remember dismissal until a newer turn starts; a new prompt automatically restores it. Return an honest result and repaint immediately. |
| F10 | P2 / source + live sparse slots | Removed rows leave holes; every new unpinned row shifts all existing slots, including manually placed rows (`agent_sessions.py:481–503`). Overflow text can claim full capacity when gaps or offsets caused the omission (`core/effects.py:148–158`; `app.js:401–403`). | Preserve explicit pins; assign/reconcile unpinned rows into eligible free positions. Derive the explanation from actual placement: capacity, filtering, offset, or shared mode. |
| F11 | P2 / source-confirmed | Dashboard and hardware interpret targets differently: wildcard aliases, ambient eligibility, and first-versus-last matching action differ (`app.js:246, 284–285`; `core/effects.py:222–230`). | Use the backend's effective placement as the source of truth, or share equivalent rules with parity tests. Preserve action precedence and device capability constraints. |
| F12 | P1 / source-confirmed | Persistent actions are cached only by rule ID; later actions overwrite earlier device actions (`core/engine.py:274–275, 295–296`). Reconnect replay restores only the last action (`:209–214`). | Retain all effective persistent actions in order and invalidate removed/edited actions. Reconnect must restore all configured devices without another session transition. |
| F13 | P2 / source-confirmed | Notch drawing filters sessions, but clicking indexes the unfiltered list (`devices/notch.py:723, 759–767`). Codex focus itself only looks up Claude PID files (`:557–565`). | Render and hit-test the same visible list. Only offer navigation when supported; show an honest unavailable state otherwise. Do not guess a Codex window from a Claude PID. |
| F14 | P2 / source-confirmed | Codex home and `state_5.sqlite` are fixed (`integrations/codex.py:25–26, 90`). Missing/incompatible DB schemas look like no sessions. | Honor the configured Codex home, validate the supported schema, and surface a useful compatibility/error state. Avoid indiscriminate searches across unrelated profiles. |
| F15 | P2 / source-confirmed | `agent.finished` is emitted only when the entire agent aggregate changes (`agent_sessions.py:646–670`). One task finishing while a sibling runs does not produce a task-specific completion signal. | Keep existing aggregate semantics for compatibility; add a distinct per-session transition signal with session/turn identity. Never send a completion notification for disappearance, startup, dismissal, or an observation error. |

## 3. Required behavior

1. Three active user tasks produce three working top-level rows, even when one is silent for more than ten minutes. Helper agents do not inflate that count.
2. Keep three concepts separate: task execution state, tracking health, and device placement. A missing zone says nothing about whether a task is running.
3. A positive completion/abort event settles its own turn. Lack of writes, missing files, or an unreadable database does not mean successful completion.
4. Preserve the last known execution state through a short observation failure, with visible health/freshness information. If evidence remains unavailable, show unknown/stale rather than green done or indefinitely authoritative working.
5. A completed task is not necessarily a currently open tab. Where open-tab liveness cannot be established, call the list “Tracked tasks” and distinguish recent completion from a verified live task.
6. Dashboard, notch, aggregate counts, and hardware consume the same reconciled snapshot. Device filters may select a subset but must say so.
7. A waiting parent remains active while its delegated work runs; child completion cannot finish the parent. A known input request takes precedence over ordinary running status until resolved for that turn.

## 4. Implementation phases

### Phase 1 — Reproduce and fix discovery/identity together

Owner: Codex integration. Files: `src/lumen/integrations/codex.py`, `tests/test_agent_sessions.py`.

- Add a regression with three independent root threads: fresh DB updates, one fresh rollout, two rollouts older than ten minutes, all with valid open turns. Demonstrate current omission first.
- Extend the fixture with child-worker and guardian source shapes observed locally. Verify they do not become independent user tabs.
- Remove the extra file-age veto on fresh DB candidates. Retain a bounded candidate search and explicitly include already tracked active IDs on later polls.
- Read only required metadata, distinguish unknown source values from known helper types, and preserve valid standalone CLI/extension sessions.
- Carry thread/parent identity into the existing shared session record. Do not expose raw prompt contents in logs or diagnostics.

Exit: all three roots remain discoverable across the ten-minute boundary; helpers allocate no top-level slots. This narrow correction must not globally resurrect old unfinished rollouts.

### Phase 2 — Make observations and state transitions reliable

Owner: shared tracker, with Codex and Claude adapters. Files: `integrations/agent_sessions.py`, `integrations/codex.py`, `integrations/claude_code.py`, existing integration tests.

- Replace the boolean-only observation with a small explicit record containing identity, execution state, relevant turn ID/event time, observation time, read health, and available metadata. Use the existing modules; no new service or generic plugin layer.
- Resolve the configured Codex home consistently for hooks and database access. Validate the supported database columns and report missing/incompatible schemas through observation health instead of an empty session list.
- Parse JSONL structurally and incrementally. Preserve partial trailing lines for the next read; handle truncation/replacement and evict obsolete parser cache entries.
- Verify current-thread ownership and fork/resume boundaries before implementing turn matching. A naive set of all unmatched starts is specifically prohibited because inherited history can contain them.
- Reconcile hooks and file evidence by turn and event order. Distinguish timestamp of the event from timestamp of observing it.
- Isolate read failures per task. A failed query is not a successful empty inventory. Claude's unreadable transcript currently also returns false; bring it under the same unknown-state rules.
- Write hook state atomically using the existing temporary-file/replace pattern. Retain provider turn IDs and event timestamps when supplied so an old stop cannot overwrite a newer prompt. Where payloads omit them, document conservative reconciliation with rollout evidence; hook arrival time alone cannot establish event order or guarantee this distinction.
- Derive aggregate state from the reconciled snapshot once; expose counts for working, waiting, completed/idle, and unknown independently of hardware capacity.
- Add per-session transitions while retaining existing aggregate event contracts. Avoid replaying historical completion notifications at startup or recovery.

Exit: read outages, long waits, delayed hooks, child completion, and daemon restart cannot manufacture completion or make a healthy sibling disappear.

### Phase 3 — Make the UI explain the same state

Owner: API/dashboard/notch. Files: `server/api.py`, `server/ui/app.js`, `server/ui/styles.css`, `devices/notch.py`, existing dashboard/notch tests.

- Show meaningful task names and projects. Label precedence: user override, bounded single-line display name, project plus short ID. Escape all externally sourced text.
- Show tracking source and last successful observation when needed. Say “hooks configured” separately from “hooks observed”; installed configuration is not evidence that a running session invoked it.
- Preserve unknown values for context and cost; do not show zero spend or empty context as measured facts.
- Repair deferred rendering and response ordering. Keep active edits intact and flush pending updates without requiring a second server event.
- Implement hide-until-next-turn dismissal consistently for hook and fallback rows. Persist only the minimum identity/turn marker needed; do not delete Codex records.
- Use the same filtered collection for notch rendering and clicking. Do not advertise unsupported Codex task focus.
- Include meaningful identity/health changes in snapshot change detection while preserving intentional throttling of high-frequency usage updates.

Exit: every displayed row can be identified; status changes appear after editing ends; dismissal and navigation affordances have predictable effects.

### Phase 4 — Align placement and device restoration

Owner: slots/effects/engine with dashboard consumer. Files: `integrations/agent_sessions.py`, `core/slots.py`, `core/effects.py`, `core/engine.py`, `server/ui/app.js`.

- Separate explicitly pinned placement from automatic ordering. New arrivals must not silently displace explicit pins. Reuse eligible holes for unpinned sessions.
- Calculate effective placement consistently, respecting agent filters, offsets, shared mode, target aliases, ambient eligibility, and action order.
- Return accurate placement reasons. A task with no individual zone remains visible and counted.
- Retain all persistent rule actions for reconnect/rescan replay and discard stale entries after rule changes.

Exit: available zones are used as intended, the dashboard predicts actual device output, and reconnect restores every device's session colors.

### Phase 5 — Verify and document support limits

- Run the targeted regression suite, then the full existing test suite, lint, and type checks. Do not claim coverage from a passing count; measure coverage for changed logic if an existing coverage tool is available, targeting the project's 80% requirement.
- Review Python changes with the code reviewer; review JavaScript changes with the TypeScript/JavaScript reviewer. Review input parsing and externally sourced UI metadata for validation and escaping.
- Run a browser check for live updates during edits, filters, counts, dismissal, keyboard use, and disconnected state. Use fake devices for deterministic reconnect tests, followed by the actual ASUS keyboard/light bar as a separate hardware check.
- Repeat the live comparison with three user tasks, including a long delegated wait, and record only IDs/statuses/ages needed for verification.
- Update `docs/ARCHITECTURE.md`, `docs/API.md`, and `CHANGELOG.md` with final behavior and supported observation sources. Do not promise exact live/open state where the available evidence cannot establish it.

## 5. Regression and acceptance matrix

| Scenario | Required result |
| --- | --- |
| Three roots running; two rollouts silent for 601+ seconds | Three working roots remain in the API, dashboard, and notch. |
| Roots plus several children/guardians | Roots count once; known internal agents do not consume top-level zones. |
| Parent waits for worker; worker completes | Parent remains working until its own turn ends. |
| Task is genuinely abandoned without a terminal event | Tracking eventually becomes stale/unknown based on available liveness evidence; no successful completion event is fabricated. |
| Database locked/unavailable; one rollout deleted between stat/open | Healthy tasks continue updating; affected observations report degraded health; no global reset to done. |
| Recovery after temporary read failure | Stable identities/placement return without duplicate completion notifications. |
| New prompt hook arrives before new rollout start | Previous turn's completion cannot finish the new turn. |
| Old stop arrives after the next turn starts | New turn remains working. |
| Permission request, response, then next turn | Waiting resolves correctly and does not leak into the next turn. |
| Reordered JSON keys, extra fields, malformed or partial line | Valid complete events parse; incomplete data waits; corruption never means done. |
| Forked/inherited history and resumed turns | Historical starts/completions cannot control the current thread incorrectly. |
| One task completes while another stays active | Task-specific completion occurs once; overall working remains true. |
| Focused rename field while status changes, followed by blur | Current status renders immediately without losing the edit or needing another event. |
| Out-of-order HTTP responses | Older response cannot roll the dashboard back. |
| Dismiss fallback-only row, then start a new turn | Row hides immediately and reappears for the new turn; actual Codex task remains untouched. |
| Remove early rows, retain pins, then create a task | Eligible gaps are reused; explicit placements remain stable. |
| More tasks than hardware zones | Every task remains listed/countable; overflow explanation matches effective placement. |
| Two-device persistent rule; reconnect first device | Both device actions remain restorable without another task event. |
| Agent-filtered notch with mixed sessions | Click targets the visible row; hidden rows cannot receive its action. |
| Alternate Codex home or unsupported schema | Supported profile is used; incompatible/missing data produces a useful tracking-health message. |
| Restart Lumen with running, completed, dismissed tasks | Identity and intended placement survive; no historical success flashes. |

## 6. Verification completed during this audit

- Inspected screenshots, shared tracker, both agent adapters, API, dashboard, notch, slots, effects, engine, and related tests.
- Compared the running Lumen API with read-only Codex database/rollout metadata and the Codex app's task inventory. No agent prompts or tool contents are needed in the fix fixtures.
- Isolated reproductions confirmed the mismatched freshness failure, empty-on-error disappearance, valid-JSON ordering failure, and stale closed observation overriding a new running hook.
- Existing baseline: **83 tests passed in 13.72 seconds** using:

  `.venv/Scripts/python.exe -m pytest tests/test_agent_sessions.py tests/test_dashboard_api.py tests/test_notch.py tests/test_hardening.py -q -p no:cacheprovider`

- Those tests do not cover the reported mismatched database/file freshness case. UI interactions and physical reconnect behavior were source-audited, not exercised. No implementation, deployment, hook reinstallation, or live configuration changes were performed.

## 7. Decisions to validate during implementation

- Confirm the supported local lifecycle/metadata semantics for forks, resumed turns, and provider-specific home/schema variants. The Codex app tool used for this audit is a verification aid, not an assumed API dependency available to the standalone Lumen daemon.
- Choose a short observation-outage grace period and explicit stale retention policy using measured polling behavior. A timeout may change tracking health; it must not assert successful completion.
- Preserve compatibility of aggregate automation events. Introduce per-task events distinctly so existing sound/light rules do not unexpectedly multiply notifications.
- Ship discovery and helper filtering together first, then observation reliability before expanding presentation. Avoid introducing a replacement daemon, a second tracker, cloud polling, or new dependencies unless the existing modules demonstrably cannot meet these checks.

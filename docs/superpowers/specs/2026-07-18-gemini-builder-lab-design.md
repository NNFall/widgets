# Kaigo Gemini Builder Lab Design

Date: 2026-07-18

Status: Gemini-first direction approved; written specification awaiting user review

## 1. Purpose

Build a small, isolated technical laboratory that answers one question with
real evidence:

> How well can the current Gemini stack create a visually distinctive,
> premium AI-employee widget from a natural-language brief, and how useful is
> Google's ready-made Antigravity agent compared with a narrow Kaigo-owned
> generation loop?

The laboratory is not the first production version of the Kaigo SaaS. It is a
controlled experiment that produces visible widget artifacts, records the
real generation stages, and establishes stable boundaries that can later be
promoted into the platform.

The experiment must not change the behavior or publication semantics of the
existing production widgets.

## 2. Clarifying the Google Products

The names below refer to different layers and must not be conflated.

### Google Gen AI SDK

`google-genai` for Python and `@google/genai` for JavaScript are client
libraries. The SDK itself is not an autonomous coding agent. It provides typed
access to Gemini models, the Interactions API, files, streams, tools, and
managed agents.

A request such as `models.generate_content(model="gemini-3.5-flash", ...)`
returns model output. Kaigo remains responsible for deciding what to request,
validating the response, running checks, feeding errors back, storing results,
and publishing artifacts.

### Gemini 3.5 Flash

`gemini-3.5-flash` is the initial direct generation model. It is stable, fast,
multimodal, supports structured output and function calling, and is the
Gemini-first default for this experiment.

Direct model mode is best for:

- inexpensive staged generation;
- strict JSON contracts;
- predictable Kaigo-owned stages;
- high-temperature visual exploration;
- comparing prompt and artifact designs quickly.

It does not automatically become a coding agent merely because it can produce
code. The surrounding Kaigo loop supplies the stages, validation, bounded
repair, and preview.

### Antigravity Managed Agent

`antigravity-preview-05-2026` is a ready-made managed agent invoked through the
same Google Gen AI SDK and Gemini Interactions API. Google provisions a remote
Linux environment in which the agent can reason, edit files, execute Bash,
Python and Node.js, install packages, search the web, and continue work across
interactions.

Antigravity does produce real files. After an interaction Kaigo receives an
`environment_id`. The full environment can be downloaded as a tar snapshot
through the Files API. Kaigo can therefore retrieve generated HTML/CSS/JS,
build logs, tests, and reports rather than relying only on the agent's final
text.

Antigravity already supplies the general agent harness and autonomous tool
loop. It does not supply the Kaigo product contract:

- it does not know which artifact paths are authoritative unless instructed;
- it does not decide whether generated code is safe to expose to visitors;
- it does not own Kaigo draft/publish/version semantics;
- it does not support structured output;
- it is currently Public Preview;
- its default environment has unrestricted outbound networking unless Kaigo
  applies a network policy.

The laboratory uses Antigravity as a ready agent engine, then applies an
independent Kaigo extraction and validation boundary to its output.

Official references:

- https://ai.google.dev/gemini-api/docs/agents
- https://ai.google.dev/gemini-api/docs/antigravity-agent
- https://ai.google.dev/gemini-api/docs/agent-environment
- https://github.com/googleapis/python-genai

## 3. Decisions

The agreed direction is:

> Kaigo owns a small provider-neutral builder orchestrator. Gemini is the
> primary provider. Direct Gemini 3.5 Flash is the first staged generator.
> Antigravity is the ready autonomous agent mode. Codex remains a later
> comparison adapter. Only a Kaigo-validated artifact may leave the lab.

The laboratory deliberately supports two Gemini-backed modes instead of
selecting one from vendor descriptions.

### Mode A: Direct staged generation

Kaigo invokes Gemini 3.5 Flash once per semantic stage. Every successful stage
returns a complete valid candidate artifact under a structured schema. The
browser receives the candidate only after validation.

Advantages:

- lowest expected cost and latency;
- true visible stage-by-stage progress;
- complete control over the artifact contract;
- easy adjustment of temperature and visual prompts;
- easiest mode to benchmark repeatedly.

Trade-off: Kaigo owns the loop and the bounded repair logic.

### Mode B: Antigravity agent build

Kaigo mounts a starter project, `AGENTS.md`, a widget-building skill, and the
brief into a Google-hosted environment. Antigravity edits the project and runs
the fixed build and validation commands. Kaigo downloads the environment tar,
extracts only the declared output paths, and independently validates them.

Advantages:

- ready file/tool/command agent loop;
- autonomous build-error repair;
- persistent environment for follow-up edits;
- little agent infrastructure to operate during the experiment.

Trade-offs:

- Public Preview and possible breaking changes;
- no structured output;
- coarser preview updates;
- higher and less predictable token use;
- a complex run may cost several dollars;
- output still requires a Kaigo trust boundary.

### Rejected as the first experiment

The following are intentionally not the first implementation:

- forking OpenHands, Cline, OpenCode, or Bolt;
- running Codex as the primary engine;
- allowing arbitrary model-generated JavaScript on a customer site;
- modifying existing production widget assets automatically;
- implementing the complete database, billing, crawler, publishing, and
  marketplace architecture before visual generation has been proven useful.

## 4. Scope

### Included

- A dedicated builder-lab page with prompt controls on the left and a live
  widget preview on the right.
- A fixed sample brief that asks for a premium AI consultant and can be edited
  by the user.
- Direct Gemini 3.5 Flash staged generation.
- A common engine interface that can run direct Gemini and Antigravity.
- Antigravity environment creation, background execution, cancellation,
  snapshot download, safe extraction, and declared-artifact collection.
- Real stage events and a visible event timeline.
- A sandboxed preview with a trusted Kaigo wrapper.
- Structured generation usage, latency, error, and stage summaries in the lab
  session.
- Bounded validation and repair.
- A standalone lab process bound to loopback by default, separate from the
  production aiohttp application.
- Automated unit and HTTP-level tests for the new laboratory components.

### Excluded

- Public self-service registration.
- Charging a customer balance.
- Production publication or embed code.
- Website crawling and screenshot-based brand extraction.
- Telegram/email actions.
- Arbitrary npm dependencies in direct mode.
- Persistence across application restarts.
- Production-grade distributed queues.
- Making Antigravity a mandatory dependency for existing chat widgets.
- Replacing the existing OpenAI-compatible chat runtime in this experiment.

## 5. User Experience

The laboratory page has two main columns.

### Left column

- engine selector: `Gemini staged` or `Antigravity agent`;
- editable brief;
- optional creativity setting for direct mode;
- Generate, Cancel, and Retry controls;
- an append-only timeline showing actual states;
- compact usage, elapsed time, and validation results;
- the art-direction summary generated for the current candidate.

### Right column

- desktop/mobile viewport toggle;
- persistent sandboxed preview iframe;
- current revision and stage label;
- validation badge;
- a clear warning that the result is an experimental draft and cannot be
  published.

The initial brief is intentionally demanding rather than generic. It asks for
a premium AI employee with a distinctive visual metaphor, coherent motion,
clear hierarchy, Russian copy, and useful suggested actions. The prompt also
forbids a default purple-gradient chatbot bubble, random glassmorphism, and
decorative effects without a consistent art direction.

## 6. Common Domain Contract

Both engines are translated into the same Kaigo concepts.

### Builder request

```text
BuilderRequest
  run_id
  engine
  brief
  locale
  creativity
  viewport targets
  maximum repair attempts
```

### Builder event

```text
BuilderEvent
  run_id
  sequence
  timestamp
  type
  stage
  status
  message
  revision
  usage
  validation issues
```

Required event types:

```text
run.created
stage.started
stage.completed
stage.failed
artifact.validated
artifact.committed
repair.started
repair.completed
run.completed
run.failed
run.cancelled
```

Provider-specific token deltas and tool events may be recorded as diagnostic
events, but they do not update the preview until a complete candidate artifact
has passed validation.

### Safe visual artifact

The direct-mode artifact contains:

```text
WidgetArtifact
  schema_version
  revision
  stage
  art_direction
  body_html
  css
  theme_tokens
  suggested_actions
```

Direct mode does not accept generated JavaScript. Chat behavior, sample
messages, launcher behavior, focus handling, viewport resizing, and preview
error reporting are implemented by a trusted fixed Kaigo runtime.

This separation allows broad visual expression through HTML structure, CSS,
gradients, masks, SVG decoration, pseudo-elements, keyframes, and CSS custom
properties without giving the model access to arbitrary network or browser
APIs.

Antigravity mode may create source files internally, but its declared lab
output must include an equivalent safe artifact at:

```text
out/widget-artifact.json
out/build-report.json
```

Only these declared files and approved content-addressed assets are imported.
The rest of the environment snapshot remains diagnostic material.

## 7. Direct Gemini Pipeline

Direct mode performs real sequential model calls:

1. `art_direction`: choose a coherent concept, palette, typography, spatial
   language, interaction personality, and motion principles.
2. `foundation`: create the stage, background, main shell, launcher, and
   responsive geometry.
3. `identity`: add the header, AI persona, status, visual signature, and
   supporting decoration.
4. `conversation`: add the message area, composer, suggestions, and typed
   action presentation using the trusted runtime contract.
5. `motion_polish`: add bounded entry, hover, focus, and ambient motion with a
   reduced-motion fallback.
6. `validation`: run deterministic validation and, if needed, up to two repair
   calls containing only the concrete validation issues and current artifact.

Each stage receives the brief, art direction, stage contract, and previous
artifact. It returns a complete candidate, not a fragment of unfinished CSS or
HTML. Consequently, every preview commit remains renderable.

Default direct-mode settings:

```text
model: gemini-3.5-flash
temperature: 0.9
top_p: 1
maximum repair attempts: 2
external resources: forbidden
generated JavaScript: forbidden
```

Temperature is exposed only as an experimental lab control. Antigravity does
not support temperature or other standard generation configuration fields.

## 8. Antigravity Pipeline

Antigravity mode performs these steps:

1. Create a remote environment with a strict network policy.
2. Mount inline starter files, `AGENTS.md`, and the Kaigo widget-builder skill.
3. Start a background interaction with a hard wall-clock and budget limit.
4. Stream or poll real interaction status and tool progress.
5. Require the agent to run the starter project's fixed validation command.
6. Require it to write `out/widget-artifact.json` and
   `out/build-report.json`.
7. Download `environment-<environment_id>` through the Files API.
8. Extract the tar into a disposable directory using path traversal, symlink,
   total-size, file-count, and declared-path checks.
9. Validate the artifact independently of the agent's build report.
10. Commit the candidate to the preview or reject it with exact issues.

The Antigravity report is evidence about what the agent attempted, not an
authorization to publish. The Kaigo validator is authoritative.

The environment network policy initially permits only the Google endpoints
needed by the managed environment and any explicitly approved package source.
The starter project should have its dependencies predeclared to avoid broad
package-install access.

## 9. Validation and Repair

The lab validator rejects rather than silently rewrites unsafe output.

Required checks:

- JSON schema and maximum field sizes;
- supported schema version and monotonic revision;
- balanced and parseable HTML;
- allowlisted HTML elements and attributes;
- no `script`, `iframe`, `object`, `embed`, `base`, `meta`, or external form
  targets;
- no `on*` event attributes;
- no external URLs, `javascript:` URLs, `data:text/html`, `@import`, or CSS
  `url()` values;
- scoped CSS and maximum stylesheet size;
- maximum DOM node count;
- bounded animation count, duration, and iteration count;
- mandatory `prefers-reduced-motion` fallback when motion is present;
- required semantic regions and accessible labels;
- trusted preview runtime acknowledgement after render.

If deterministic validation fails, the current committed preview remains
visible. The engine receives the exact issue list and may return a repaired
complete candidate. A repeated issue fingerprint or exhaustion of two repair
attempts fails the stage.

Subjective visual quality is judged by the user in this first laboratory. A
later lab iteration may add pinned-browser screenshots and an independent
multimodal visual judge, but subjective model scoring is not required before
the first real generation comparison.

## 10. Preview Security

The preview must not reuse the existing unsandboxed admin asset iframe.

The laboratory uses a dedicated iframe with:

```text
sandbox="allow-scripts"
```

It deliberately omits `allow-same-origin`, forms, popups, downloads, and
top-navigation. The generated artifact contains no script. The trusted Kaigo
runtime is inserted by the parent into a complete preview document and handles
render acknowledgements and demo interactions.

The preview document includes a restrictive CSP:

```text
default-src 'none';
script-src 'unsafe-inline';
style-src 'unsafe-inline';
img-src data:;
font-src 'none';
connect-src 'none';
form-action 'none';
base-uri 'none';
object-src 'none';
```

Because the sandbox lacks `allow-same-origin`, the frame has an opaque origin.
The parent accepts only a small versioned `postMessage` schema and checks
`event.source`. No secret, API key, cookie, visitor transcript, or production
action is sent to the frame.

## 11. Runtime and Persistence

The first lab stores runs, events, and artifacts in memory with a bounded TTL.
This is intentional: a restart clears experiments, and no migration is needed
before the generator has demonstrated useful results.

The lab runs as a standalone aiohttp process. It is not registered in the
existing production application and therefore cannot accidentally become a
public route under the current broad nginx proxy.

Relevant configuration:

```text
KAIGO_BUILDER_LAB_HOST=127.0.0.1
KAIGO_BUILDER_LAB_PORT=8091
KAIGO_BUILDER_DEFAULT_ENGINE=direct
GEMINI_BUILDER_MODEL=gemini-3.5-flash
GEMINI_BUILDER_TEMPERATURE=0.9
GEMINI_BUILDER_MAX_REPAIRS=3
GEMINI_ANTIGRAVITY_AGENT=antigravity-preview-05-2026
GEMINI_ANTIGRAVITY_TIMEOUT_SECONDS
GEMINI_ANTIGRAVITY_MAX_SNAPSHOT_BYTES
```

The laboratory reuses the existing Gemini-only US route. Both GenerateContent
and Interactions/Files requests target
`generativelanguage.googleapis.com`; no general-purpose proxy is introduced.
The implementation must make the SDK base URL/proxy path configurable so it
can use the existing protected tunnel on the server and the official endpoint
from a supported local region.

## 12. HTTP Boundary

The lab is served by its own loopback-only process. Local development opens it
directly. A server run remains bound to `127.0.0.1:8091` and is viewed through
an SSH local port forward. It is not exposed through nginx, the production
backend, or a public domain during this experiment.

Required operations:

```text
GET    builder-lab page
POST   create generation run
GET    run snapshot
GET    run event stream using SSE and Last-Event-ID
POST   cancel run
POST   retry failed run
```

All run identifiers are unguessable. Because the server boundary is loopback
and SSH rather than the current application session, the lab does not inherit
the existing weak client-session model. If a public lab is requested later,
that requires a separate authentication design and is outside this document.

SSE is used because generation progress is server-to-browser and the client
already sends create/cancel/retry through ordinary HTTP. Heartbeats keep proxy
connections alive, and sequence numbers make reconnection idempotent.

## 13. Error Handling

User-visible failure categories are stable across engines:

```text
missing_api_key
provider_unavailable
model_unavailable
agent_unavailable
quota_exceeded
generation_timeout
invalid_artifact
snapshot_download_failed
snapshot_rejected
run_cancelled
internal_error
```

Provider messages, protected proxy paths, API keys, and environment
credentials are not returned to the browser. Detailed diagnostic errors remain
in server logs and the loopback-only event record.

Cancellation stops pending direct calls or invokes
`client.interactions.cancel()` for Antigravity. The last committed preview
remains visible.

## 14. Testing Strategy

The repository currently has no automated test suite. The implementation adds
one before production code and follows red-green-refactor.

Required automated coverage:

- artifact schema validation;
- rejection of scripts, event handlers, external URLs, unsafe CSS, excessive
  motion, and malformed artifacts;
- safe tar extraction including traversal, absolute paths, symlinks, oversized
  archives, and undeclared files;
- stage ordering and monotonic revisions;
- bounded repair and repeated-issue stopping;
- cancellation;
- event sequence, replay, and SSE `Last-Event-ID` behavior;
- direct engine request construction and structured response parsing;
- Antigravity interaction construction, environment reuse, status polling,
  snapshot download, and artifact collection using fakes;
- loopback-only host enforcement and rejection of a non-loopback default;
- preview document CSP and sandbox contract;
- no modification of existing Widget or WidgetAsset rows.

Live checks, when credentials are available:

1. Direct Gemini generates one complete run from the fixed premium brief.
2. Every displayed preview revision corresponds to a real completed model
   stage.
3. Antigravity creates an environment, completes the starter validation, and
   produces downloadable declared artifacts.
4. Both candidates render in desktop and mobile preview modes.
5. Existing public widgets and chat health checks remain unchanged.

## 15. Success Criteria

The laboratory is successful when all of the following are demonstrated:

- A user can enter a brief and watch at least four genuine committed visual
  stages appear without page reloads.
- The final direct Gemini candidate is visibly more individual than the
  existing preset widgets and has a coherent art direction.
- Unsafe or malformed candidates never reach the preview.
- A provider failure leaves the last valid preview intact and produces an
  understandable event.
- Antigravity either returns a downloaded validated artifact or reports a
  precise availability/access failure; it is never treated as successful from
  final text alone.
- Usage and elapsed time are visible for comparing the two modes.
- The experiment cannot publish or alter a production widget.
- Automated tests pass, and a live smoke test proves the real Gemini path when
  credentials and project access permit it.

## 16. Promotion Boundary

Results from this lab may inform a later production specification, but they do
not automatically authorize promotion.

Before using the builder for customers, a separate production design must add:

- tenant-safe sessions and authorization;
- durable jobs and events;
- immutable artifact storage;
- explicit draft/approved/published pointers;
- crawler SSRF isolation;
- pinned browser functional and visual checks;
- usage and financial ledgers;
- a separate production preview/runtime origin;
- domain enforcement, publish rollback, and embed loader.

The lab proves generation quality and engine behavior first. It does not hide
production engineering risk behind an attractive demo.

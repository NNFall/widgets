# Gemini Technical Foundation Design

Date: 2026-07-18

## Goal

Turn the existing Kaigo Widgets Gemini integration into a reliable, testable
foundation for future platform features. The implementation must preserve full
Gemini conversation state, support safe multi-step function calling, expose
useful per-widget generation settings, record request usage and estimated cost,
and deploy through the existing US tunnel without changing `kaigo.online`.

## Current State

- The widgets application runs in `/root/ai_project` as `ai_project_app` and is
  exposed through `https://kaigo.space/`.
- `https://kaigo.online/` is a separate static site and is outside this change.
- Gemini requests already use Google's OpenAI-compatible and native endpoints.
- Because Google rejects direct traffic from the primary server's region, the
  application uses a local SSH forward on `172.19.0.1:8787` to the existing US
  `gemini-proxy.service`.
- The proxy is intentionally Gemini-only and forwards all HTTP methods and
  token-protected paths to `https://generativelanguage.googleapis.com`.
- Chat defaults to `gemini-3-flash-preview`; request-level
  `reasoning_effort` and `top_p` are not configurable.
- Assistant tool-call messages are stored as JSON text, but history loading
  reconstructs them as plain `content`. This discards Gemini's mandatory
  `extra_content.google.thought_signature` on the next model request.
- Tool calls are displayed to the user but are not executed by a server-side
  registry.
- Provider usage, latency, and estimated cost are not persisted.
- There is no automated test suite.

## Scope

### Included

- Keep the current US tunnel and Gemini-only proxy architecture.
- Make `gemini-3.1-flash-lite` the stable default chat and transcription model.
- Preserve existing per-widget temperature and token-limit values.
- Add configurable reasoning effort, `top_p`, tool choice, provider timeout,
  retry count, and retry backoff.
- Preserve full assistant messages and tool results across database round trips.
- Execute allowlisted tools in a bounded multi-step loop.
- Persist compact usage, latency, status, and estimated cost records.
- Add tests covering request construction, history, function calling, errors,
  retries, usage accounting, and health checks.
- Update documentation, deploy to `kaigo.space`, and run live and browser smoke
  checks.

### Excluded

- Changing the static `kaigo.online` site or its nginx configuration.
- Turning the US proxy into a general-purpose proxy for arbitrary hosts.
- Adding product-specific business tools without a separate product contract.
- Persisting API keys, full request bodies, or full response bodies in audit
  records.
- Enabling streaming in this phase. The existing proxy buffers upstream
  responses, and the selected application mode remains `stream=false`.

## Architecture

### Network Boundary

Only Gemini traffic uses the US route:

```text
ai_project_app
  -> 172.19.0.1:8787/<protected-prefix>
  -> gemini-proxy-tunnel.service
  -> US gemini-proxy.service
  -> generativelanguage.googleapis.com
```

All non-Gemini integrations continue to use the primary server's normal
network path. The proxy target remains hardcoded to Google's Gemini host. A
small application health probe will distinguish tunnel connectivity from
upstream model errors without exposing the protected prefix.

### Provider Client

`core/ai_service.py` remains the provider boundary and continues to use the
OpenAI-compatible client for chat plus native `generateContent` for audio.
Provider request construction moves into focused helpers so it can be tested
without network access.

The default request is:

```json
{
  "model": "gemini-3.1-flash-lite",
  "reasoning_effort": "low",
  "temperature": 0.35,
  "top_p": 1.0,
  "stream": false,
  "tool_choice": "auto"
}
```

`tools` and `tool_choice` are sent only when tools are configured. Token limits
remain per-widget. Health checks use a sufficient completion budget and require
a non-empty final response, so consumed thinking tokens cannot produce a false
positive.

### Configuration Precedence

Effective settings are resolved in this order:

1. Explicit request or widget setting.
2. Environment default.
3. Application default.

Existing widget temperature and maximum-token values are preserved. New
widgets inherit environment defaults. The database gains explicit nullable
fields for `reasoning_effort`, `top_p`, and `tool_choice`; null means inherit.
The existing `prompt_source` remains backward compatible and can contain an
inline system prompt or a Google Docs URL.

Tool schemas may continue to come from the legacy `---TOOLS---` prompt block.
The runtime normalizes them before sending them to Gemini. Tool execution is
independent of prompt storage: a model may request only a function that exists
in the server-side registry.

### Conversation History

The `messages` table keeps the current human-readable `content` column for UI
and backward compatibility. It gains an optional `message_json` column holding
the complete provider-neutral message object.

Examples stored in `message_json` include:

- assistant text messages;
- assistant messages containing all `tool_calls` fields, including unknown
  Gemini extensions such as `extra_content.google.thought_signature`;
- tool results with `tool_call_id`, function name, and JSON content.

History loading prefers valid `message_json` and falls back to the legacy
`role` plus `content` representation. Existing rows therefore remain readable
without a destructive migration. System prompts are resolved at request time
and are not duplicated into every conversation row.

### Tool Execution Loop

A focused tool registry maps a public function name to an async Python handler
and validation metadata. Registration is explicit; arbitrary imports, shell
commands, expressions, and network destinations are not accepted from model
output.

The loop is:

1. Send complete history and advertised tool schemas to Gemini.
2. Persist the complete assistant response.
3. If there are no tool calls, persist and return final text.
4. For each tool call, parse its JSON arguments and execute only a registered
   handler.
5. Convert success or failure into a structured `role=tool` result and persist
   it with the matching `tool_call_id`.
6. Send the updated full history, including untouched thought signatures, back
   to Gemini.
7. Stop on a final assistant response or at a configurable maximum step count.

Unknown tools, malformed arguments, handler exceptions, and timeouts become
tool results rather than server crashes. The initial production registry may
be empty. Automated and live isolated smoke tests use a deterministic test
registry that is never exposed through public widget configuration.

### Usage And Cost

A new `llm_requests` SQLite table records one compact row per provider request:

- timestamp and request ID;
- user, widget ID, and widget slug;
- provider endpoint class (`chat` or `audio`);
- model and result status;
- HTTP/provider error code when present;
- prompt, completion, total, and inferred thinking tokens;
- latency in milliseconds;
- estimated USD cost and the configured USD-to-RUB rate;
- tool-call count and loop step.

No API key or full prompt/response is stored in this table. Cost rates are
configuration, not hardcoded business truth, because Google pricing can change.
Missing usage fields produce a valid row with null token and cost values.
Recording happens after the provider response and is kept small so it does not
materially affect model latency.

### Error And Retry Policy

Errors are normalized into stable application codes. Invalid keys, unsupported
models, malformed requests, missing thought signatures, and unsupported regions
are not retried. Connection failures, timeouts, HTTP 429, and transient HTTP
5xx responses are retried with bounded exponential backoff and optional jitter.

Logs contain request ID, endpoint class, model, latency, token counts, tool
count, retry count, and normalized error code. Logs never contain API keys,
protected proxy paths, thought signatures, or full customer messages.

## Database Changes

SQLite startup migration remains backward compatible:

- Add nullable `messages.message_json TEXT`.
- Create `llm_requests` with indexes for timestamp, widget, model, and status.

PostgreSQL widget configuration gains nullable fields:

- `reasoning_effort`;
- `top_p`;
- `tool_choice`.

An Alembic migration is added, and startup behavior remains compatible with the
project's existing `metadata.create_all` fallback. Existing preview-model rows
are updated to `gemini-3.1-flash-lite`; custom non-preview model selections and
temperature values are left unchanged.

## Testing

The new pytest suite uses dependency injection and local fakes rather than live
Google calls for unit tests. It covers:

- configuration precedence and validation;
- model normalization;
- request fields and omission rules;
- serialization round trips with `extra_content.google.thought_signature`;
- legacy history fallback;
- successful, unknown, malformed, failing, and timed-out tools;
- multi-step loop limits;
- retryable versus permanent provider errors;
- non-empty health-check requirements;
- usage token extraction and configurable cost calculation;
- redaction guarantees.

Deployment verification then performs real calls through the tunnel with the
configured key: model listing, plain chat, a temporary isolated function call
and tool result, health endpoint, public widget chat, and audio where a small
fixture is available.

## Git And Deployment

The server and GitHub histories diverged after the same changes were applied by
different workflows, but their current trees are identical. The histories are
joined with a normal merge commit; force-push and history rewriting are not
used.

Implementation occurs on `codex/gemini-technical-foundation`. Before deployment:

1. Preserve the current server commit and database files.
2. Merge the histories and push the tested feature branch.
3. Update GitHub `main` without force.
4. Pull the verified tree to the server.
5. Update `.env` without printing secrets.
6. Build and recreate only `ai_project_app`; keep PostgreSQL data volumes.
7. Run database migrations and health checks.
8. Roll back to the previous image and commit if live verification fails.

Browser verification targets `https://kaigo.space/` and its widget routes.
`kaigo.online` remains untouched.

## Success Criteria

- `gemini-3.1-flash-lite` answers through the existing US tunnel.
- Every configured request parameter reaches the provider as intended.
- A complete assistant tool call survives database persistence unchanged.
- A registered function runs, its result is returned as `role=tool`, and Gemini
  produces a final answer without a thought-signature error.
- Conversation history remains isolated by user and widget.
- Usage, latency, status, and estimated cost are queryable without storing
  secrets or duplicate full transcripts.
- Unit tests pass and live health, chat, tool, and browser checks succeed.
- GitHub and the deployed server contain the same verified code.
- The static `kaigo.online` site is unchanged.

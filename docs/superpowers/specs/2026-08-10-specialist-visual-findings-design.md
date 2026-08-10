# Specialist visual findings and independent confirmation

## Status

The user approved this direction in the 10 August 2026 working session. This
document records the delta to the existing visual-committee design before code
and frozen-evidence experiments are changed.

## Problem

The three visual critics intentionally have different responsibilities, but the
current judge accepts a subjective finding only when two critic roles report the
same `issue_type`. A valid specialist observation can therefore disappear merely
because the other two critics were not asked to inspect that area. The current
role prompts also share almost the same ten machine-checked criteria, so the role
names promise more specialization than the contract actually enforces.

## Chosen design

1. Keep three independent roles, but give every role a detailed specialist
   scorecard in addition to a small shared baseline. UX checks conversation and
   composer behavior; brand checks art direction and visual craft; the buyer
   checks trust, clarity and willingness to use or pay for the result.
2. Preserve every major or blocker finding with confidence at least `0.65` and a
   concrete supplied screenshot. A finding is no longer discarded only because
   a second specialist did not repeat it.
3. The fourth judge receives the original widget frames again. It may accept a
   finding through one of three explicit bases:
   - two critics independently agree;
   - one specialist reports it and the judge independently confirms the visible
     evidence in the supplied frame;
   - a server-owned browser fact deterministically proves it.
4. Every accepted finding retains its source critic, screenshot, stable
   `issue_type`, confirmation basis and repair instruction. Unsupported claims
   about Studio, references, animation or future states remain rejected.
5. The repair stage receives only confirmed findings. It must inspect the prior
   artifact and change only fields authorized by those findings. Existing browser
   gates and the post-repair verifier remain mandatory, so a repair cannot silently
   introduce overflow, clipping or unrelated redesign.
6. Codex bridge requests carry a bounded per-turn reasoning effort. This lets the
   three critics run at Luna High (and lets an offline benchmark try Max) without
   slowing every other generation stage.

## Research protocol

Use frozen evidence rather than paid full generations. The comparison set is:

- Tutu: empty first-open state, generic identity, visible transcript/header
  clipping and composer-detail concerns;
- Mindbox: empty first-open state, heavy composer focus treatment, dense or hidden
  conversation state and generic launcher/composition;
- T-Bank: generic support-chat composition, weak brand specificity, excessive
  empty space and transcript/header clipping after a real turn.

For each set, run the same prompt at Medium, High and Max at least twice when time
allows. Record elapsed time, valid contract rate, all findings by role, accepted
findings, false positives, missed known defects and judge confirmation basis. A
reasoning level is promoted to production only when it improves repeatable defect
recall without a disproportionate latency increase.

## Acceptance criteria

- A single strong UX finding can reach repair even when brand and buyer critics do
  not mention it, provided the judge independently confirms its screenshot evidence.
- A fabricated single-critic finding still fails closed.
- The judge report explains why each repair was accepted instead of showing only a
  role count.
- The three roles have distinct, test-enforced scorecards.
- Medium, High and Max can be selected per Codex turn without changing the bridge's
  global default.
- Frozen Tutu, Mindbox and T-Bank reports are saved with timings and simple Russian
  conclusions before production settings are changed.

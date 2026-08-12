# Seven-frame single-pass reference implementation plan

1. Extend the reference evidence contract and analyzer attestation from six to
   seven states, beginning with failing tests.
2. Replace the warm/reset/evidence lifecycle with one evidence-producing
   traversal and add the desktop `after_top` anchor, beginning with failing
   crawler tests.
3. Add distinct technical-capture and AI-analysis progress events, including
   Russian Studio labels and event-registry tests.
4. Run focused backend/frontend tests, static checks, and build checks.
5. Deploy the worker and frontend, then run a crawler-only production check on
   another public site. Preserve the seven screenshots and timing evidence.

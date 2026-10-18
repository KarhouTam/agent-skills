# Scenarios

The smoke suite covers `run_role.py` and the skill's file layout; it does not cover the loop in
`SKILL.md`. Run the relevant scenario after changing the round mechanics, the brief, or the role
prompts, and append the result to `README.md`.

## 1. Happy path with a negotiated brief

Shipped config (`implementer: native`, `challenger: opencode`). Cost: one small implementer turn
plus two challenger rounds — about three minutes.

1. `mkdir -p /tmp/ar-e2e/run`
2. Write `/tmp/ar-e2e/run/BRIEF.md`: goal "FizzBuzz classifier"; `A1: fizzbuzz(n) returns
   "Fizz"/"Buzz"/"FizzBuzz" for multiples of 3/5/15 and str(n) otherwise`; `A2: __main__ prints
   1..15 one per line`; artifact `/tmp/ar-e2e/fizzbuzz.py`.
3. Compose the implementer prompt from `prompts/implementer.md`: substitute `{{ROUND}}` = 1,
   `{{BRIEF}}` = the brief, `{{PAYLOAD}}` = "Produce the artifact described in the brief"; dispatch
   it through your native agent tool.
4. Compose the challenger prompt from `prompts/challenger.md` and run it:

   ```bash
   python3 scripts/run_role.py run --role challenger --round 1 \
       --prompt /tmp/ar-e2e/run/round-1-challenger.prompt.md \
       --out /tmp/ar-e2e/run/round-1-challenger.out.md \
       --run-dir /tmp/ar-e2e/run --cwd /tmp/ar-e2e --timeout 600
   ```

   Run it in the background; a real review takes minutes.
5. Parse `VERDICT`, `CRITERIA`, `FEEDBACK`, `DISPUTE RESPONSES`. If PASS on round 1, run the
   fatigue-guard round 2 (delta prompt, no full history).
6. Record `run.jsonl`, `sessions.json`, `LEDGER.md`, and both verdicts in `README.md`.

Expected: round 1 shows `"resume": false`, round 2 shows `"resume": true` with the same
`session_id`; the out file starts with `VERDICT:`; `CRITERIA` lists every `A<n>` from the brief.

## 2. Dispute and escalation

Same setup, but give the brief a criterion the challenger may over-interpret, e.g. only
`A1` (happy-path correctness) and no input-validation requirement.

1. Make the artifact wrong (drop the multiples-of-15 branch).
2. Round 1 → challenger FAIL with `F1`, `F2`.
3. The implementer disputes one item (e.g. `F2: reject non-integers` is not in the brief) with
   the `DISPUTE F2:` block, evidence = the brief has no such criterion.
4. The challenger must answer `DISPUTE RESPONSES: F2: CONCEDE|MAINTAIN — <evidence>` and keep
   `F1` open.
5. Continue disputes on `F1` until the third one is `MAINTAIN`ed.

Expected: `LEDGER.md` shows the state transitions and the dispute count reaching 3; the loop stops
and the orchestrator produces the arbitration report and asks the user. After the ruling, the
item is recorded under `## Rulings` and is never re-disputed.

## 3. Implementer-initiated escalation

Same as scenario 2, but the implementer answers with `ESCALATE F2: <the single point>` instead of
a third dispute.

Expected: the orchestrator stops before spending another round, reports that item with both
positions, and asks the user.

## 4. PASS guard

Artificially make the challenger return `VERDICT: PASS` while one `CRITERIA` line says `NOT MET`.

Expected: the orchestrator rejects the PASS, sends one inconsistency message back, and does not
return the artifact until the verdict and the criteria agree.

## 5. Brief negotiation

Give the orchestrator a vague task ("make the parser better") and check that it asks the user for
the goal, acceptance criteria, artifact, and constraints — with concrete proposals — before any
round starts, and that it refuses to invent a criterion the user did not agree to.

Expected: one question batch before round 1; `BRIEF.md` written with the agreed criteria;
`## Open questions` records anything the user deferred.

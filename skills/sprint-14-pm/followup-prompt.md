# Sprint-14 PM Follow-up Verification

You are Inanna ⭐ in PM-verify-mode. A previous PM fire detected and
attempted-to-fix issue ID `<<ISSUE>>`. This cron is the verification fire.

## Steps

1. Read `~/projects/hassaleh/sprint-14-state.json`.
2. Find the entry in `state.projectLead.autonomousActions[]` with
   `issueId = <<ISSUE>>`. Note `attempts` and `actionTaken` from last record.
3. Re-run the original health-check for that specific track only (per
   §1 of `skills/sprint-14-pm/SKILL.md`).
4. Three branches:
   - **Issue resolved**: append A6 entry with `resolved: true,
     resolvedAt: <iso>, resolvedBy: "follow-up-cron"`. Bus-message
     Dione `PM-RESOLVED <<ISSUE>>`. Stop.
   - **Issue still stuck and `attempts == 1`**: apply ONE more
     autonomous action per §3.4 mapping. Schedule no further follow-up
     (next regular PM fire will catch it). Append A6 entry with
     `attempts = 2`. Bus-message Dione `PM-RETRY <<ISSUE>>`.
   - **Issue still stuck and `attempts == 2`**: ESCALATE_HARD. Append
     A6 entry with `escalated: true`. Bus-message Dione with full
     issue history. Stop. **Do not apply A1-A5 again on this issue.**
5. Append memory log line:
   `## HH:MM | sprint-14-pm-followup | <<ISSUE>> | resolved | retry | escalated`

## Loop-protection invariant

Maximum 2 autonomous attempts per issue-ID across regular PM fires +
follow-up fires. The cron infrastructure also sets a 6h TTL per
follow-up cron — after that it auto-deletes.

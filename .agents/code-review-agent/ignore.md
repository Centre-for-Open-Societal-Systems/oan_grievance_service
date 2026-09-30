# ignore.md — Review Exclusions

This file lists what the code-review-agent must **exclude** from every review.
Read it during Context Intake (before the Structure Audit) and treat each entry
as out of scope: do not read, flag, score, or mention these in the report.

If an excluded item is the _only_ thing relevant to a request, say so explicitly
rather than reviewing it anyway.

---

## Paths — do not review

- `src/app/(dashboard)/loans/update-loan-application-status/page.tsx` — the
  update-loan-application-status page. Excluded in full; no findings from this
  file should appear in any report.

## Methods — do not use

- **Do not refine or derive findings by analysing old commits / git history.**
  Review the current working tree only. Do not run `git log`, `git blame`,
  `git diff <old-commit>`, or otherwise reconstruct intent from prior commits.

## Notes

- Exclusions apply to all 25 check domains.
- Add new exclusions as bullet points under the appropriate heading above.

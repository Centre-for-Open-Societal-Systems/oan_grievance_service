# Code Review Output Structure

> **Instructions for AI:** Use this exact format to report findings. No suggested fixes, no code snippets, no remediation guidance — diagnosis only. Every finding needs a file:line, the issue, and the consequence if left unresolved.

---

```markdown
# Code Review – <branch/PR/commit id> (<date>)

## Executive Summary
| Metric | Result |
|--------|--------|
| Overall Assessment | Excellent / Good / Needs Work / Major Issues |
| Issues Found | 🔴 X Critical — 🟡 Y Major — 🟢 Z Minor |

## 🔴 Critical Issues
| File:Line | Issue | Consequence if Unresolved |
|-----------|-------|---------------------------|

## 🟡 Major Issues
| File:Line | Issue | Risk Exposure |
|-----------|-------|----------------|

## 🟢 Minor Issues
| File:Line | Issue |
|-----------|-------|

## Issue Pattern Summary
Recurring root causes found across multiple locations (aggregated — not repeated per file):
- **Pattern:** [description of the anti-pattern] — **Locations:** [file:line, file:line, …]

## Action Checklist
- [ ] 🔴 [file:line] — [one-line issue description]
- [ ] 🟡 [file:line] — [one-line issue description]
- [ ] 🟢 [file:line] — [one-line issue description]
```

---

## Severity Definitions

| Severity       | Meaning                                                                             |
| -------------- | ----------------------------------------------------------------------------------- |
| 🔴**Critical** | Will cause a production incident, security breach, or runtime defect                |
| 🟡**Major**    | Will cause maintainability failure, performance regression, or hidden risk at scale |
| 🟢**Minor**    | Reduces clarity or consistency only                                                 |

## Rules

1. Every finding = one row with file:line, the issue, and its consequence.
2. Same anti-pattern across multiple files = **one** Pattern Summary entry with all locations listed — not one row per file.
3. No fixes, no code snippets, no "instead do X" — findings state what's wrong and what it costs, nothing else.
4. Enumerate completely — don't stop at the first issue per file.

# Merge hygiene

Every merge conflict this repo has produced so far has had the same shape: two branches
appended something to the end of the same file, or one branch reformatted a file the other
was editing. None of them were disagreements about behaviour. This document records the
three files that keep doing it, what was changed so they stop, and the conventions that keep
the fix working.

## What was changed

| Change                                                            | Stops                                                                                       |
| :---------------------------------------------------------------- | :------------------------------------------------------------------------------------------ |
| `.gitattributes`: `oan_grievance_service/patches.txt merge=union` | Two branches each appending a patch entry. Git now keeps both lines instead of conflicting. |
| `.gitattributes`: `* text=auto eol=lf`                            | A CRLF checkout being committed back as "every line changed".                               |
| `.pre-commit-config.yaml`: `end-of-file-fixer`                    | A file with no final newline, which makes every append rewrite its last line.               |
| `.pre-commit-config.yaml`: prettier on `*.md`                     | Formatting drift in `docs/` — padded vs. compact tables, blank lines added and stripped.    |

CI runs `pre-commit` on every pull request, so the formatting rules hold whether or not you
installed the hooks locally. Install them anyway — it is the difference between fixing this
before you push and after a red build:

```bash
cd apps/oan_grievance_service && pre-commit install
```

## `oan_grievance_service/patches.txt` is append-only

Add your patch on a new line at the end. Do not reorder existing lines, do not tidy the list,
and keep the trailing newline.

`merge=union` makes Git keep both sides' lines when two branches append. That is the correct
answer for a list of patches — order between independent patches does not matter and
`bench migrate` skips any patch already recorded in the Patch Log. It is only correct while
the file is append-only. If you ever need to rename or drop a patch entry, expect union to
keep the stale line as well, and read the merged file before committing.

## Docs: never reformat what you are not editing

1. **Let prettier do the formatting.** Do not hand-pad tables, and do not add or strip blank
   lines around headings to match your own taste. A branch that reformats a file turns every
   nearby edit by another branch into a conflict.
2. **Append endpoint sections at the end of their chapter** and take the next number. When two
   branches both claim the same number, the resolution is to keep both sections and renumber
   the second one. Never resolve by deleting a section.

## Two Git settings worth having

Neither is repo state; set them once on your machine.

```bash
# Show the common ancestor in a conflict, not just the two sides. Makes it obvious
# which side actually changed something and which is just the base text.
git config --global merge.conflictStyle zdiff3

# Remember how you resolved a conflict and replay it automatically the next time the
# same one shows up — which it will, on every re-merge of a long-lived branch.
git config --global rerere.enabled true
```

Merge `develop` into your feature branch early and often. All of the conflicts above were
cheap to resolve individually and only became a pile because the branch sat unmerged while
`develop` moved.

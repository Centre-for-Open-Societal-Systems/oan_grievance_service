# Ignore — Scope Exclusions

This file is read first. Matching items are removed from the review set before any other
file is loaded. Excluded items are never read, flagged, counted, or mentioned — not in
findings, coverage figures, highlights, or notes.

## Paths — do not review

### Generated artifacts

- `**/generated/**`, `**/gen/**`, `**/__generated__/**`
- `**/*.generated.*`, `**/*.gen.*`, `**/*_generated.*`
- Any file whose header declares it machine-generated and not to be edited by hand.
- Client and server stubs generated from interface definitions.
- Minified bundles and source maps: `**/*.min.*`, `**/*.map`

### Build output

- `**/build/**`, `**/dist/**`, `**/out/**`, `**/target/**`, `**/bin/**`, `**/obj/**`
- Compiled object code, bytecode, packaged archives, container image layers.

### Caches

- `**/.cache/**`, `**/cache/**` when it contains tool output, `**/tmp/**`, `**/.tmp/**`
- Test-run and coverage report output: `**/coverage/**`, `**/test-results/**`

### Vendored dependencies

- `**/vendor/**`, `**/third_party/**`, `**/third-party/**`
- Any directory into which a package manager installs dependencies.

### Lock files

- `**/*.lock`, `**/*-lock.*`, `**/*.lockfile`
- Any tool-written manifest that pins resolved dependency versions.

### Non-source assets

- Binary files, images, fonts, audio, video, and archives.
- Editor, IDE, and operating-system metadata files and directories.

## Methods — do not use

- **Version-control history.** No blame, logs, commit messages, prior revisions, branch
  names, or review discussions as evidence. A supplied list of changed paths may define
  scope; every finding derives from current content only.
- **Tool-enforced concerns.** No findings on syntax, formatting, language idioms, naming
  style, unused imports or variables, type errors, or complexity thresholds that linters,
  compilers, type checkers, formatters, or CI gates enforce. Code identifier style
  is excluded; schema, contract, and event naming are in scope.
- **Execution.** Do not run code, tests, builds, or migrations; do not call external
  services; do not use production data or runtime telemetry.
- **Attribution.** Do not infer author, intent, or team; do not attribute findings to
  people.
- **Excluded-path inference.** Do not derive findings from the contents of excluded items.

## Notes

- Never excluded, even when tool-produced or under a generated path: database migrations,
  checked-in API and message contract definitions, runtime and deployment configuration
  affecting backend behavior, and test code with its fixtures. These change production
  behavior or define the standard the code is held to.
- Projects add exclusions by appending patterns under the headings above. A default is
  removed only by an explicit line `Include: <pattern>` under the relevant heading.
- If every supplied item is excluded, the report reads "No reviewable content in scope."
  and nothing else.

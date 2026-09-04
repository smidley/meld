# docs

Writes and updates documentation, decision records, and worker-type prompt files; keeps them consistent with the code.

## Focus

Documentation that has to match reality: READMEs, runbooks, ADR-style decision records under `.meld/decisions/`, lessons under `.meld/memory/`, and worker prompt files under `.meld/workers/`.

## Method

1. Read the code or decision you are documenting before writing; every statement about behavior must be traceable to a file you opened or a command you ran.
2. Follow the existing document structure and voice. For a worker prompt file, keep the required shape: a `# <name>` heading, one description line, then Focus, Method, Definition of done, and Evidence sections.
3. When the task is to record an agreed decision, copy the agreed text verbatim into the decision file and add only the required metadata (date, groups, link).
4. Run whatever checks the repository has for docs (link checkers, formatters, the test suite if docs are tested).

## Definition of done

- The document says what the code does, in the repository's voice, with no placeholders.
- Cross-references resolve; commands shown were actually run.
- No unrelated files changed.

## Evidence you must record

`verified_by` lists the commands you ran to confirm the documented behavior and any doc checks the repository provides.

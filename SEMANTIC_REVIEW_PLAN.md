# Semantic and Context-Aware Code Review Plan

## Status

Planning document for evolving Gito from a diff-oriented reviewer into a context-aware,
cross-file code reviewer.

Repository remotes:

- `origin`: `git@github.com:n4m-ward/ward_review.git`
- `upstream`: `git@github.com:Nayjest/Gito.git`

Phase 1 is partially implemented: automatic project-instruction discovery is now available
in the review prompt. Related-file discovery and semantic indexing remain future phases.

## Background

Gito currently reviews code changes one file at a time. For a regular review, each LLM
request receives:

- The file diff.
- The full content of the changed file after the change.
- Configured prompt variables.

The relevant flow is in `gito/core.py`, where `review()` builds one prompt for each
changed file and sends the requests through `mc.llm_parallel()`.

This allows Gito to compare a diff with the rest of the same file, but it does not
systematically discover or inspect:

- Callers of a changed function.
- Implementations or consumers of a changed interface.
- Imported modules and modules importing the changed file.
- Related tests, fixtures, schemas, routes, or configuration.
- Project architecture and coding guideline documents.
- Cross-file contract violations.

The existing `aux_files` configuration is used by `gito ask`, but is not currently added
to each `gito review` prompt. The current pipeline also runs after per-file LLM review, so
its output cannot yet enrich those initial review prompts.

## Objective

Build a selective and explainable review context for every changed file:

```text
diff
+ full changed file
+ applicable project instructions
+ semantically related files
+ reason each related file was selected
+ optional deterministic tool results
-> LLM review
```

The result should detect issues such as:

- Changed signatures with outdated callers.
- Broken interfaces or implementation contracts.
- Changes that violate documented architecture.
- Missing updates to tests or schemas.
- Inconsistencies between routes, services, models, and consumers.
- Violations of coding standards applicable to the changed path.

The feature must preserve Gito's vendor-agnostic and multi-language design.

## Product Principles

### Review changes, not the entire backlog

Related files are context, not independent audit targets. A reported issue must satisfy at
least one of these conditions:

1. It is introduced directly by the reviewed diff.
2. It is an existing issue made reachable or observable by the reviewed diff.
3. It is a broken contract between the reviewed diff and a related file.
4. It is a guideline violation in the reviewed changes.

Unrelated pre-existing problems found in context files must not be reported.

### Select context instead of sending the repository

Sending the complete repository for every changed file would be slow, expensive, and likely
to reduce review quality. Context must be ranked, deduplicated, scoped, and limited by a
token budget.

### Explain every relationship

Every related file sent to the model should include a reason, for example:

```text
src/api/checkout.py
Reason: Calls the changed function calculate_total.
Symbols: calculate_total
```

This evidence helps the model distinguish a real relationship from a heuristic match.

### Keep language intelligence pluggable

The Gito core should orchestrate context discovery without becoming a compiler. Language-
specific AST, Tree-sitter, or Language Server support should be provided through adapters.

### Prefer deterministic evidence

Compiler, type checker, linter, test, and Language Server results are more reliable than LLM
inference. When available, those results should be included as evidence for the review rather
than replaced by an LLM-only implementation.

## Proposed Architecture

Introduce a context-building stage before `mc.llm_parallel()`:

```text
get_target_diff()
-> get_target_lines()
-> discover_project_instructions()
-> build_or_load_semantic_index()
-> resolve_related_files() per changed file
-> enforce_token_budgets()
-> build_review_prompt() per changed file
-> mc.llm_parallel()
-> post_process()
-> report
```

Potential central models:

```python
@dataclass
class ProjectInstruction:
    path: str
    content: str
    applies_to: list[str]
    priority: int


@dataclass
class RelatedFile:
    path: str
    reason: str
    score: float
    symbols: list[str] = field(default_factory=list)
    content: str = ""


@dataclass
class ReviewContext:
    instructions: list[ProjectInstruction]
    related_files: list[RelatedFile]
    tool_results: dict = field(default_factory=dict)
```

Potential extension interfaces:

```python
class RelatedFileResolver:
    def resolve(self, repo, changed_file, changed_symbols) -> list[RelatedFile]:
        ...


class SemanticAnalyzer:
    def supports(self, file_path: str) -> bool:
        ...

    def analyze(self, file_path: str, content: str):
        ...
```

Resolvers can be composed and their results merged:

- `ImportResolver`
- `ImporterResolver`
- `SymbolReferenceResolver`
- `TestResolver`
- `ConventionResolver`
- `ConfiguredRelatedFilesResolver`
- `LanguageServerResolver`

## Automatic Project Instructions

### Default discovery candidates

Gito should detect common project guidance files when they exist:

```text
AGENTS.md
ARCHITECTURE.md
CODE_GUIDELINES.md
CODE_STYLE.md
CONTRIBUTING.md
DEVELOPMENT.md
SECURITY.md
README.md
.github/copilot-instructions.md
.github/instructions/**/*.md
.github/instructions/**/*.instructions.md
docs/architecture/**
docs/development/**
docs/guidelines/**
```

`README.md` should have a lower default priority because it is often large and contains
product or installation information rather than review rules.

### Hierarchical scope

Instructions should be resolved for each changed file. Directory-local files override or
supplement broader files.

For `src/frontend/components/Button.tsx`, a possible precedence order is:

1. Explicit CLI or `.gito/config.toml` configuration.
2. `src/frontend/AGENTS.md`.
3. `src/AGENTS.md`.
4. Root `AGENTS.md`.
5. Matching `.github/instructions` files.
6. `CODE_GUIDELINES.md` and `CODE_STYLE.md`.
7. `ARCHITECTURE.md`.
8. `CONTRIBUTING.md`.
9. `README.md`.

The implementation must define whether closer instructions override conflicting parent
instructions or whether both are included with explicit precedence metadata. The recommended
behavior is to include both and tell the model that the closest scoped instruction wins.

### Path filters

Instruction files under `.github/instructions` may contain front matter such as:

```yaml
---
applyTo: "**/*.py"
---
```

The loader should parse supported front matter and only include an instruction when the
changed file matches its path filter.

### Safety and token control

The instruction loader must:

- Skip missing optional files without warnings.
- Ignore binary files.
- Deduplicate files and repeated content.
- Apply path scope before reading full content where possible.
- Enforce a dedicated instruction token budget.
- Include source paths in prompts.
- Preserve exact instructions instead of silently rewriting them.

## Related File Discovery

### Layer 1: universal heuristics

The initial implementation should remain dependency-light and work across languages:

- Parse obvious relative imports.
- Find files importing the changed module.
- Search for changed exported symbol names.
- Detect conventional test file names.
- Detect files sharing a base name.
- Allow explicit related-file patterns in project configuration.

These relationships must be scored. Suggested examples:

- Direct resolved import: `1.0`
- Direct importer: `0.95`
- Reference to a changed symbol: `0.9`
- Test directly importing the module: `0.9`
- Conventional matching test filename: `0.7`
- Same base filename or directory convention: `0.4`

Only the highest-ranked files fitting the token budget should be included.

### Layer 2: AST and Tree-sitter analyzers

Add language adapters that extract:

- Definitions.
- Imports and exports.
- Function and method calls.
- Class inheritance.
- Interface implementation.
- Public signatures.
- Decorators and route declarations.

Python can begin with the standard library `ast` module. Multi-language support can later use
Tree-sitter behind the same `SemanticAnalyzer` interface.

### Layer 3: Language Server integration

Optional adapters can provide precise operations:

- Go to definition.
- Find references.
- Resolve types.
- Find implementations.
- Detect type and interface errors.

Potential integrations include Pyright, TypeScript Language Server, `gopls`,
`rust-analyzer`, JDT Language Server, and Roslyn. These must remain optional because their
installation and project setup requirements vary.

### Framework conventions

Optional convention resolvers may model common relationships:

- Controller -> service -> repository.
- Route -> handler -> request/response schema.
- React component -> hook -> test.
- Django view -> serializer -> model.
- FastAPI route -> dependency -> Pydantic model.
- SQL migration -> ORM model.
- GraphQL resolver -> schema.
- CLI command -> configuration -> tests.

Framework detection and resolution should be implemented as plugins, not hardcoded into the
core review flow.

## Prompt Changes

The per-file prompt should clearly separate the supplied evidence:

```text
TASK
CHANGES TO REVIEW
FULL CHANGED FILE
APPLICABLE PROJECT INSTRUCTIONS
RELATED FILES
RELATIONSHIP EVIDENCE
DETERMINISTIC TOOL RESULTS
OUTPUT SCHEMA
```

The prompt must explicitly state:

- Context files are not independent review targets.
- Issues must be linked to the current diff.
- Project instructions are authoritative within their declared scope.
- Relationship evidence may be heuristic and should be verified against supplied code.
- Missing context should not be filled with assumptions.
- Existing confidence and severity filtering still applies.

Prompt and template text should remain in `gito/config.toml` or template files rather than
being hardcoded in Python.

## Cross-File Report Model

The current report groups issues by the file being reviewed. Cross-file findings need to
support evidence in multiple files while preserving a primary changed-file anchor.

Target representation:

```json
{
  "title": "Changed signature breaks an existing caller",
  "file": "src/payment.py",
  "affected_lines": [
    {
      "file": "src/payment.py",
      "start_line": 20,
      "end_line": 20
    },
    {
      "file": "src/checkout.py",
      "start_line": 81,
      "end_line": 81
    }
  ],
  "relationship": "checkout.py calls the modified calculate_total function"
}
```

Requirements:

- Keep one primary changed file for issue grouping and inline comment placement.
- Support links and code snippets for every affected file.
- Do not apply automatic fixes across multiple files until conflict and ordering behavior is
  explicitly designed and tested.
- Preserve loading of older report JSON only if there is a concrete compatibility requirement
  for released reports.

## Proposed Configuration

An initial configuration shape could be:

```toml
[review_context]
discover_instructions = true
discover_related_files = true
max_instruction_tokens = 12000
max_related_files = 8
max_related_file_tokens = 24000
max_relationship_depth = 1

instruction_files = [
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CODE_GUIDELINES.md",
    "CODE_STYLE.md",
    "CONTRIBUTING.md",
    ".github/copilot-instructions.md",
    ".github/instructions/**/*.md",
    ".github/instructions/**/*.instructions.md",
]

[semantic_analysis]
enabled = true
strategy = "auto"
include_imports = true
include_importers = true
include_references = true
include_tests = true
use_language_servers = false
```

Configuration names and defaults must be validated against real usage before becoming part of
the public interface.

## Delivery Plan

### Phase 1: automatic guideline context

Goal: make normal code reviews follow project-specific written rules.

Tasks:

- Add configuration fields for instruction discovery and token limits.
- Discover root, hierarchical, and `.github/instructions` files.
- Parse supported `applyTo` front matter.
- Resolve applicable instructions per changed file.
- Add instructions to the review prompt before `mc.llm_parallel()`.
- Keep instruction paths and precedence visible to the model.
- Add unit tests for discovery, precedence, glob scope, missing files, deduplication, and token
  limits.
- Add documentation and configuration examples.

Acceptance criteria:

- A changed Python file receives matching Python instructions and applicable parent
  `AGENTS.md` files.
- Unrelated scoped instructions are excluded.
- Reviews still work when no instruction files exist.
- Instruction content respects its configured token budget.
- Existing default review behavior remains unchanged when discovery is disabled.

### Phase 2: related-file context with universal heuristics

Goal: detect straightforward cross-file contract problems without requiring external language
tools.

Tasks:

- Add `RelatedFile` and `ReviewContext` models.
- Implement import, importer, symbol search, and test resolvers.
- Extract changed symbol candidates from diffs using conservative heuristics.
- Rank, merge, and deduplicate resolver results.
- Add per-file and total related-context token budgets.
- Include relationship reasons and selected content in the review prompt.
- Add observability showing which context files were selected and why.
- Add tests for ranking, deduplication, token limits, deleted files, renamed files, and false
  relationship filtering.

Acceptance criteria:

- A changed function signature can be reviewed against at least one direct caller.
- A changed module can be reviewed against a directly related test.
- Unrelated files are not included when the budget is exhausted.
- Every selected related file has a machine-readable reason and score.
- The prompt prevents unrelated pre-existing findings.

### Phase 3: semantic analyzers

Goal: replace weak text matching with symbol-aware relationships.

Tasks:

- Define a stable `SemanticAnalyzer` interface.
- Implement Python analysis with `ast` as the first adapter.
- Build a repository symbol index with cache invalidation by file hash.
- Resolve definitions, references, imports, and public signature changes.
- Add a Tree-sitter-based path for additional languages if dependency and packaging costs are
  acceptable.
- Fall back cleanly to universal heuristics for unsupported languages.

Acceptance criteria:

- Python aliases and module-qualified references are resolved more accurately than plain text
  search.
- The semantic index is reused across files in the same review.
- Unsupported or syntactically invalid files do not abort the review.
- Resolver failures become processing warnings rather than total failures.

### Phase 4: optional Language Servers and deterministic validators

Goal: provide high-confidence semantic evidence from project-native tools.

Tasks:

- Add optional Language Server adapters.
- Define a safe configuration for project-provided test, lint, type-check, and build commands.
- Capture structured diagnostics and associate them with changed files.
- Include diagnostics in prompts and reports.
- Add timeouts, output limits, environment controls, and explicit opt-in for command execution.

Acceptance criteria:

- Missing external tools degrade gracefully.
- Commands are never executed unless explicitly enabled.
- Diagnostics include tool identity, file, line, severity, and message.
- Deterministic failures are distinguishable from LLM findings in the report.

### Phase 5: cross-file reporting and safe fixes

Goal: fully represent and eventually repair multi-file contract findings.

Tasks:

- Extend issue serialization and rendering for multiple affected files.
- Add Markdown and CLI links for all evidence locations.
- Define inline-comment placement rules for GitHub and GitLab.
- Design conflict detection and ordering for multi-file fix proposals.
- Add comprehensive tests before enabling cross-file automatic fixes.

Acceptance criteria:

- A cross-file issue displays all affected locations.
- The primary comment remains anchored to a changed line when possible.
- Existing single-file issues render unchanged.
- Multi-file fixes are disabled unless all reviewed content still matches the working tree.

## Testing Strategy

Tests should cover behavior at several levels:

- Unit tests for instruction discovery and scope.
- Unit tests for each related-file resolver.
- Unit tests for context ranking and token budgeting.
- Prompt snapshot or structured-content tests.
- Report serialization and rendering tests.
- Integration tests using temporary Git repositories with known file relationships.
- Regression tests ensuring unrelated existing defects are not reported as change findings.
- Failure tests for invalid syntax, missing tools, large files, binary files, and resolver errors.

Existing project commands remain applicable:

```bash
make test
make black
make cs
```

## Performance and Cost Controls

Semantic context can increase latency and LLM usage. The implementation should include:

- One repository index per review, not one index per changed file.
- Content and semantic cache keyed by file hash.
- Maximum related files per changed file.
- Maximum relationship depth, initially `1`.
- Separate token budgets for instructions, related files, and deterministic diagnostics.
- Parallel file analysis only after shared context discovery is complete.
- Logging of selected and excluded context with token counts.
- Graceful fallback to the current diff-plus-file behavior.

## Security Considerations

- Repository instruction files are untrusted project content and may attempt prompt injection.
  Prompts should identify them as project review policy while preserving system-level boundaries.
- Automatic execution of repository commands must be disabled by default and require explicit
  configuration.
- External Language Servers and tools may execute plugins or project configuration; their use
  must be documented as trusted-code execution.
- Secrets and environment files must not be automatically selected as context.
- Default discovery must exclude common sensitive paths such as `.env`, credentials, private
  keys, generated secrets, and Git internals.
- Logs and reports must not dump complete context files unless explicitly requested.

## Risks and Mitigations

### Excessive false positives

Mitigation: require every finding to be causally connected to the diff and retain the existing
highest-confidence post-processing policy.

### Token and cost growth

Mitigation: strict budgets, ranking, caching, shallow relationship depth, and configurable
feature flags.

### Incorrect textual relationships

Mitigation: attach reasons and scores, use conservative thresholds, and progressively replace
heuristics with AST or Language Server evidence.

### Multi-language complexity

Mitigation: keep the core language-neutral and add analyzers through a small adapter interface.

### Slower CI reviews

Mitigation: build one shared index, cache by file hash, allow disabling semantic analysis, and
record timing per context stage.

### Prompt instruction conflicts

Mitigation: define explicit precedence, include instruction source paths, and make scoped local
instructions override broader project guidance.

## Open Decisions

The following decisions should be resolved before or during implementation:

1. Should guideline discovery be enabled by default or introduced as opt-in?
2. Which exact `.github/instructions` front matter formats will be supported?
3. Should instruction conflicts be resolved in code or explained to the model with precedence?
4. What are safe default token budgets for typical models?
5. Should Phase 2 begin with Python-specific import parsing or universal regex heuristics?
6. Is Tree-sitter an acceptable required dependency, an optional extra, or out of scope?
7. How should renamed and deleted files contribute semantic relationships?
8. Should related files be included in the summary prompt, only per-file prompts, or both?
9. Should pipeline steps be split into pre-review and post-review stages?
10. What compatibility guarantees are required for existing JSON reports and project configs?
11. How should GitHub and GitLab inline comments represent evidence outside changed lines?
12. Which commands, if any, may be executed in CI for deterministic validation?

## Recommended First Implementation

Start with Phase 1 only. It has the highest value-to-risk ratio and creates the context-building
seam needed by later semantic work.

The first implementation should:

1. Add a `review_context` configuration section.
2. Discover `AGENTS.md`, `ARCHITECTURE.md`, `CODE_GUIDELINES.md`, `CONTRIBUTING.md`, and
   `.github/instructions` files.
3. Resolve path scope and hierarchy per changed file.
4. Enforce an instruction token budget.
5. Pass the resulting content into the existing review prompt.
6. Add focused tests and documentation.

After that seam is stable, Phase 2 can add related-file resolvers without significantly
changing `review()` again.

## Implementation Progress

### Phase 1 slice completed

- Added `gito/review_context.py` with project-instruction discovery.
- Added hierarchical `AGENTS.md` discovery.
- Added configurable instruction patterns.
- Added `applyTo` front-matter path filtering.
- Added a whitespace-based instruction token budget.
- Added `format_project_instructions()` for prompt context.
- Added `review_context` configuration to `ProjectConfig`.
- Added the discovered instructions to the default review prompt.
- Added focused tests in `tests/test_review_context.py`.

The remaining Phase 1 work is to expand test coverage through the project's installed test
environment and document configuration examples in the user-facing documentation.

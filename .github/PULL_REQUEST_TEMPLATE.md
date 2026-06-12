<!--
Thanks for opening a PR! A few things that make review faster:

1. Use Conventional Commits in the PR title -- this drives release-please.
   See CONTRIBUTING.md for the prefix-to-version-bump mapping. Common ones:
     feat: <subject>      -> MINOR bump (or PATCH while in 0.x)
     fix: <subject>       -> PATCH bump
     feat!: <subject>     -> MAJOR bump (breaking change)
     docs:/chore:/refactor:/test:/ci: -> no version bump

2. Keep PRs focused. One feature or one bug fix per PR is much easier to
   review than a bundle. Split if needed.

3. Tests: new features should include at least one unit test; behavioural
   changes should include a regression test where practical.
-->

## What and why

<!-- One or two sentences on what this PR changes and why. -->

## How

<!--
A brief outline of the approach. Mention any non-obvious trade-offs.
For a one-line fix, this section can be a single sentence.
-->

## Tests

<!--
What did you run, what passed, what's missing? Pointing to specific test
files / cases helps the reviewer trust the diff faster.
- [ ] `pytest -m 'not bq'` passes (unit tests, fast)
- [ ] `pytest -m bq` passes against `TABLE_CHECK_TEST_PROJECT` / `TABLE_CHECK_TEST_DATASET` (BQ-integration, slow)
- [ ] `ruff check src tests` passes
-->

## Related issues

<!-- "Closes #N" or "Refs #N" -- helps with auto-close on merge. -->

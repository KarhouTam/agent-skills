# agent-skills

Personal skill collection, also some useful skills from public repos.
## Layout

- **Self-developed skills** are version-controlled: 
  - `adversarial-review`
  - `context7-mcp`
  - `deepwiki`
  - `gh-github-cli`
  - `mermaid-diagrams`
  - `pytorch-test-refactoring`
  - `skill-usage-insights`
  - `tavily-web-access`
  - `teaching-technical-topics`
  - `triaging-pytorch-issues`
  - `triaging-vllm-issues`.
- **Public skills** are fetched from upstream repos at pinned commits and are
  deliberately untracked. The manifest is [skills.json](skills.json).
- **Overlays** ([overlays/](overlays)) are tracked local adaptations of public
  skills, re-applied by the sync script after each fetch.

## Syncing

A fresh clone contains only the self-developed skills. Repopulate the rest with:

```bash
python3 sync_skills.py --list           # show the manifest and what's missing
python3 sync_skills.py                  # install anything missing
python3 sync_skills.py --force          # refresh to the pinned revisions
python3 sync_skills.py --only handoff   # one skill (repeatable)
```

`--force` overwrites the target directories, discarding any local edits made in
place — tracked adaptations in `overlays/` are re-applied afterwards. Where
upstream ships no `agents/openai.yaml`, the script generates one from the
manifest.

## Adding a self-developed skill

`.gitignore` whitelists tracked paths, so a new self-developed skill stays
untracked until its directory is added there.

## Monitoring skill usage

`skill-usage-insights` records which skills each prompt triggers, the tokens each
skill run consumes, and whether declared sub-skills fire, then turns that record
into a dated insight and a keep / update / retire decision per skill. It covers
both harnesses in use here:

- **dsh** needs no setup: the collector decodes the session logs dsh already writes
  under `$DSH_HOME/sessions`, and `analyze.py` picks up new ones by itself.
- **Claude Code** needs its hooks merged into `~/.claude/settings.json` once.

```bash
python3 skill-usage-insights/scripts/install_hooks.py   # Claude Code only; ask before running
# ...use either harness as usual, then in a session:
/skill-usage-insights
```

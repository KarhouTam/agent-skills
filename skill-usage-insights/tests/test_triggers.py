"""Trigger tests for the superpowers skills.

Three kinds of check, all zero-cost:

1. *Trigger surface* — every skill is advertised correctly: frontmatter with a
   matching name, a non-empty description, and no `disable-model-invocation`.
2. *Edges* — every `**REQUIRED SUB-SKILL:** <name>` line resolves to a real skill.
3. *Recorded outcomes* — `reference/trigger-fixtures.json` pins, against the
   collected data, what real prompts actually fired and did not. The `recorded`
   cases keep honest behavior honest; the `missed` cases document known gaps and
   flip to recorded once an Update lands, so a miss coming back is caught.
"""

import json
import re
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(SKILL_DIR / "hooks"))

import usage_lib  # noqa: E402

ROOT = SKILL_DIR.parent
REPO = ROOT / "skill-usage-insights"
FIXTURES = REPO / "reference" / "trigger-fixtures.json"
FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
SUB_SKILL = re.compile(r"\*{0,2}REQUIRED SUB-SKILL:\*{0,2}\s*`?(?:Use\s+|load\s+)?(?:superpowers:)?([A-Za-z0-9_-]+)")


def superpowers_skills():
    """The obra/superpowers set, straight from the manifest that syncs them."""
    manifest = json.loads((ROOT / "skills.json").read_text())
    for source in manifest["sources"]:
        if source["repo"] == "obra/superpowers":
            return sorted(source["skills"])
    return []


SKILLS = superpowers_skills()


def frontmatter(skill):
    return FRONTMATTER.match((ROOT / skill / "SKILL.md").read_text())


def required_sub_skills():
    """(skill, target) for every REQUIRED SUB-SKILL line in the family's bodies."""
    for skill in SKILLS:
        body = (ROOT / skill / "SKILL.md").read_text()
        for target in SUB_SKILL.findall(body):
            yield skill, target


def all_skill_dirs():
    return sorted(path.parent for path in ROOT.glob("*/SKILL.md"))


def load_fixtures():
    return json.loads(FIXTURES.read_text())


@pytest.fixture(scope="module")
def records():
    data = SKILL_DIR / "data"
    if not (data / "sessions").is_dir():
        pytest.skip("no collected data on this machine")
    rows = list(usage_lib.read_records(data))
    if not rows:
        pytest.skip("no collected records on this machine")
    return rows


@pytest.fixture(scope="module")
def activations_by_prompt(records):
    """(session_id, prompt_id) -> {skill: count}, from activation records."""
    out = {}
    for row in records:
        if row.get("kind") == "activation":
            key = (row.get("session_id"), row.get("prompt_id"))
            out.setdefault(key, {})[row.get("skill")] = out.setdefault(key, {}).get(row.get("skill"), 0) + 1
    return out


def norm(text):
    return " ".join(str(text).split())


def matching_prompts(records, case):
    prompts = [row for row in records if row.get("kind") == "prompt"
               and row.get("ts", "")[:10] == case["date"]]
    wanted, mode = norm(case["prompt"]), case.get("match", "contains")
    if mode == "exact":
        return [row for row in prompts if norm(row.get("preview")) == wanted]
    return [row for row in prompts if wanted in norm(row.get("preview"))]


# -- 1. trigger surface -------------------------------------------------------


@pytest.mark.parametrize("skill", SKILLS)
def test_trigger_surface_advertises_the_skill(skill):
    match = frontmatter(skill)
    assert match, f"{skill}/SKILL.md has no frontmatter"
    body = match.group(1)
    name = re.search(r"^name: (.+)$", body, re.MULTILINE)
    assert name and name.group(1).strip() == skill, f"{skill}: frontmatter name mismatch"
    description = re.search(r"^description: (.+)$", body, re.MULTILINE)
    assert description and len(description.group(1).strip()) > 20, f"{skill}: empty description"
    assert not re.search(r"^disable-model-invocation: true$", body, re.MULTILINE), \
        f"{skill} is not advertised to the model, so it can never be triggered"


def test_trigger_surface_is_size_bounded():
    """A description isn't a trigger if it drowns in the catalog emission."""
    for skill in SKILLS:
        match = frontmatter(skill)
        description = re.search(r"^description: (.+)$", match.group(1), re.MULTILINE)
        assert len(description.group(1)) <= 800, f"{skill}: description over 800 chars"


# -- 2. required sub-skills ---------------------------------------------------


@pytest.mark.parametrize("case", list(required_sub_skills()), ids=lambda c: f"{c[0]}->{c[1]}")
def test_required_sub_skill_resolves(case):
    skill, target = case
    base = target.split(":")[-1]
    assert (ROOT / base / "SKILL.md").is_file(), f"{skill} requires {target}, which is not installed"


# -- 3. recorded outcomes -----------------------------------------------------


def test_every_fixture_matches_the_data(records, activations_by_prompt):
    fixtures = load_fixtures()
    unbacked = []
    for section in ("recorded", "conditional_skips"):
        for case in fixtures[section]:
            hits = matching_prompts(records, case)
            if not hits:
                unbacked.append(f"{case['date']} {case['prompt'][:40]!r}")
                continue
            for prompt in hits:
                key = (prompt.get("session_id"), prompt.get("prompt_id"))
                actual = activations_by_prompt.get(key, {})
                for skill in case.get("fires", []):
                    assert skill in actual, f"{case['date']} {case['prompt'][:40]!r}: {skill} did not fire"
                for skill in case.get("skips", []):
                    assert skill not in actual, f"{case['date']} {case['prompt'][:40]!r}: {skill} fired unexpectedly"
    assert not unbacked, f"fixtures without a matching prompt record: {unbacked}"


def test_known_misses_are_still_misses(records, activations_by_prompt):
    """A documented miss stays a miss until the skill is fixed; if it starts
    firing, flip the case into `recorded` so both sides stay honest."""
    for case in load_fixtures()["missed"]:
        for prompt in matching_prompts(records, case):
            key = (prompt.get("session_id"), prompt.get("prompt_id"))
            actual = activations_by_prompt.get(key, {})
            for skill in case["expected_fire"]:
                assert skill not in actual, (
                    f"{case['date']} {case['prompt'][:40]!r}: {skill} now fires — "
                    f"move this case from `missed` into `recorded` with its real fires")


def test_every_family_skill_was_advertised_at_least_once(records):
    """The harness catalog must list the skill for the model to ever pick it."""
    emissions = [row for row in records if row.get("kind") == "catalog"]
    if not emissions:
        pytest.skip("no catalog emissions recorded yet")
    listed = {skill for row in emissions for skill in row.get("skills", [])}
    missing = [skill for skill in SKILLS if skill not in listed]
    assert not missing, f"never advertised in any recorded catalog emission: {missing}"

"""Pydantic state models for the test refactoring workflow."""

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class FlowSignal(Enum):
    """Signals the Flow sends to Claude to indicate what agent action is needed."""

    SPAWN_SINGLE = "spawn_single"
    SPAWN_PARALLEL = "spawn_parallel"
    SEND_MESSAGE = "send_message"  # send follow-up to an already-running agent
    RELAY_FINDINGS = "relay_findings"
    WAITING = "waiting"
    DONE = "done"


class BoundedRange(BaseModel):
    """A contiguous line range assigned to one coder."""

    start: int
    end: int


class ClassInfo(BaseModel):
    """Info about a test class found in the file."""

    name: str
    line_number: int
    end_line: int = 0
    base_class: str = "TestCase"
    test_count: int = 0


class AssessmentResult(BaseModel):
    """Output of scripts/assess.py — raw file analysis."""

    file_path: str
    file_name: str
    file_size: int
    coder_count: int
    line_ranges: list[BoundedRange]
    class_layout: list[ClassInfo]
    total_test_count: int
    git_dirty: bool


class AnalystFinding(BaseModel):
    """A single finding from the analyst."""

    line_number: int
    category: str
    severity: str
    description: str
    recommendation: str
    original_class: str = ""
    target_class: str = ""


class NewClassSpec(BaseModel):
    """Specification for a new class to create during refactoring.

    When an existing class contains tests of mixed strategies, the analyst
    recommends extracting some tests into a new class.  This model captures
    the new class's name, strategy, instantiation mechanism, and which
    tests to move into it.
    """

    name: str
    strategy: str  # "Strategy1" | "Strategy2" | "Strategy3"
    hw_classification: str = (
        ""  # "GENERIC" | "ACCELERATOR" | "CPU" | "CUDA" | "MPS" | "XPU"
    )
    base_class: str = "TestCase"
    instantiation: str = ""  # e.g. "@instantiate_parametrized_tests" or ""
    tests: list[str] = []  # test method names to move
    rationale: str = ""


class AnalystReport(BaseModel):
    """Parsed output of the analyst agent's report."""

    file_path: str
    original_test_count: int
    findings: list[AnalystFinding]
    class_mapping: dict[str, str] = {}
    strategy_assignments: dict[str, str] = {}
    hw_classifications: dict[
        str, str
    ] = {}  # class_name → "GENERIC"|"ACCELERATOR"|"CPU"|"CUDA"|"MPS"|"XPU"
    new_classes: list[NewClassSpec] = []
    onlycpu_evaluations: list[dict] = []
    summary: str = ""


class CoderTask(BaseModel):
    """A task assigned to a coder, scoped by a single refactoring rule.

    Each coder applies ONE rule across the entire file. After each rule,
    a checker verifies the output before the next rule proceeds.
    """

    coder_id: str
    rule: str = ""
    rule_description: str = ""
    action_items: list[AnalystFinding] = []
    instructions: str = ""


class CoderResult(BaseModel):
    """Result reported by a coder after completing their work."""

    coder_id: str
    success: bool
    tests_moved: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []


class VerificationCheck(BaseModel):
    """Result of a single verification check."""

    name: str
    passed: bool
    details: str = ""
    command: str = ""


class VerificationResult(BaseModel):
    """Output of scripts/verify.py."""

    all_passed: bool
    checks: list[VerificationCheck]
    original_test_count: int
    current_test_count: int
    test_count_match: bool


class ReviewFinding(BaseModel):
    """A single finding from the checker agent."""

    severity: str
    category: str
    description: str
    line_number: int = 0
    coder_responsible: str = ""


class ReviewFindings(BaseModel):
    """Output of the checker agent's review."""

    all_clear: bool
    findings: list[ReviewFinding]
    summary: str = ""


# ── CI Automation models (Phase 8) ──────────────────────────────────


class CIBotHints(BaseModel):
    """Best-effort parse of pytorch-bot PR comment."""

    flaky_checks: list[str] = []  # checks the bot labeled flaky
    unrelated_failures: list[str] = []  # checks labeled unrelated/not-caused-by-PR
    trunk_broken: list[str] = []  # checks broken on trunk
    raw_comment: str = ""  # the full comment text


class CICheckRun(BaseModel):
    """Single check run from GitHub Checks API."""

    name: str
    status: str  # queued | in_progress | completed
    conclusion: str  # success | failure | neutral | cancelled | timed_out | skipped
    html_url: str = ""
    log_snippet: str = ""  # truncated log for failed checks


class CIFailure(BaseModel):
    """A classified CI failure."""

    check_name: str
    log_excerpt: str
    bot_label: str = ""  # best-effort hint from bot
    debugger_verdict: str = ""  # "caused_by_us" | "unrelated" | ""
    debugger_rationale: str = ""
    fix_applied: str = ""  # description of the fix, if any


class CIDebuggerResult(BaseModel):
    """Parsed output from the debugger agent."""

    agent_id: str = ""
    agent_name: str = "debugger"
    fixes_applied: list[dict] = []
    unrelated: list[dict] = []
    summary: str = ""


class CIState(BaseModel):
    """State for the CI automation phase (Phase 8)."""

    file_path: str = ""
    workspace: Optional[Path] = None
    pr_number: Optional[int] = None
    pr_url: str = ""
    pr_branch: str = ""
    head_sha: str = ""
    check_runs: list[CICheckRun] = []
    bot_hints: Optional[CIBotHints] = None
    ci_phase: str = "monitor"  # "monitor" | "debug" | "done"
    failures: list[CIFailure] = []
    fix_history: list[str] = []  # SHA history of fix commits
    cron_job_id: Optional[str] = None
    max_fix_rounds: int = 5
    signal: FlowSignal = FlowSignal.DONE


class RefactorState(BaseModel):
    """The complete state of a refactoring workflow."""

    file_path: str = ""
    file_name: str = ""
    file_size: int = 0
    coder_count: int = 0
    total_test_count: int = 0
    line_ranges: list[BoundedRange] = []
    class_layout: list[ClassInfo] = []
    workspace: Optional[Path] = None

    analyst_report: Optional[AnalystReport] = None
    coder_tasks: Optional[list[CoderTask]] = None
    coder_results: Optional[dict[str, CoderResult]] = None
    verification: Optional[VerificationResult] = None
    review_findings: Optional[ReviewFindings] = None
    final_summary: Optional[str] = None
    ci_state: Optional[CIState] = None

    current_phase: str = "assess"
    retry_count: int = 0
    lint_gate_pending: bool = False  # True while the lint-gate fix loop is active
    lint_retry_count: int = (
        0  # lint-gate fix attempts (independent of review-fix retries)
    )
    rule_index: int = 0  # which rule we're on in the code-check loop
    rule_sub_phase: str = "code"  # "code" | "check" | "fix"
    rule_retry: int = 0  # fix attempts for current rule
    signal: FlowSignal = FlowSignal.DONE
    agent_ids: dict[str, str] = {}  # agent_name → agent_id for SendMessage resume


# ── PR feedback ingest models (sidecar module) ──────────────────────


class FeedbackComment(BaseModel):
    """A single harvested reviewer comment (human inline or claude summary)."""

    comment_id: int
    pr_number: int
    pr_title: str = ""
    author: str  # GitHub login, e.g. "can-gaa-hou" or "claude[bot]"
    body: str
    html_url: str
    created_at: str  # ISO-8601 UTC
    source: str = "inline_review"  # "inline_review" | "claude_summary"
    is_reply: bool = False  # True if the comment is part of a replied thread
    in_reply_to_id: int = 0  # 0 for top-level comments


class FeedbackFinding(BaseModel):
    """A draft ruleset change proposed by the analyst, pending human approval."""

    id: str  # f"{pr_number}-{comment_id}"
    comment_id: int
    pr_number: int
    author: str
    html_url: str
    tier: str  # "Blocker" | "Major" | "Minor"
    summary: str
    target_layers: list[str] = []  # e.g. ["coder.md", "verify.py"]
    proposed_edits: list[dict] = []  # per-layer intent specs (see Task 4 prompt)
    status: str = (
        "pending"  # "pending" | "approved" | "rejected" | "modified" | "already_fixed"
    )


class IngestState(BaseModel):
    """Persistent state for the feedback ingest sidecar module."""

    pr_timestamps: dict[str, str] = {}  # str(pr_number) -> last_checked_at ISO-8601 UTC
    processed_comment_ids: set[int] = (
        set()
    )  # comment IDs already written to a findings file
    findings: list[FeedbackFinding] = []
    last_run_at: str = ""

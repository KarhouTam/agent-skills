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


class AnalystReport(BaseModel):
    """Parsed output of the analyst agent's report."""

    file_path: str
    original_test_count: int
    findings: list[AnalystFinding]
    class_mapping: dict[str, str] = {}
    strategy_assignments: dict[str, str] = {}
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

    current_phase: str = "assess"
    retry_count: int = 0
    rule_index: int = 0  # which rule we're on in the code-check loop
    rule_sub_phase: str = "code"  # "code" | "check" | "fix"
    rule_retry: int = 0  # fix attempts for current rule
    signal: FlowSignal = FlowSignal.DONE

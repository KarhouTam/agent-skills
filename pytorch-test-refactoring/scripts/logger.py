"""Structured JSONL audit logger with status snapshot for team coordination.

Two outputs in the workspace:
- audit.jsonl: append-only event stream (full audit trail)
- status.json: current state snapshot (agents read this to know progress)
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PHASE_NUMBERS = {
    "assess": 1,
    "analyze": 2,
    "distribute": 3,
    "code": 4,
    "verify": 5,
    "review": 6,
    "finalize": 7,
}


class RefactorLogger:
    """Writes audit.jsonl (append-only) and status.json (snapshot)."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.audit_path = workspace / "audit.jsonl"
        self.status_path = workspace / "status.json"
        self._run_id = None
        self._start_time = None
        self._phase_start_time = None
        self._status = {}

    # --- Public API ---

    def run_start(self, file_path: str, file_name: str):
        self._run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        )
        self._start_time = time.time()
        self._write_audit(
            {"event": "run_start", "file_path": file_path, "file_name": file_name}
        )
        self._update_status(
            {
                "run_id": self._run_id,
                "file_name": file_name,
                "file_path": file_path,
                "current_phase": "assess",
                "phase_number": 0,
                "signal": None,
                "phases_completed": [],
                "agents_active": [],
                "agents_completed": [],
                "review_findings_count": 0,
                "retry_count": 0,
                "errors": [],
                "summary": "Starting...",
            }
        )

    def phase_start(self, phase: str):
        self._phase_start_time = time.time()
        self._write_audit(
            {
                "event": "phase_start",
                "phase": phase,
                "phase_number": PHASE_NUMBERS.get(phase, 0),
            }
        )
        self._update_status(
            {
                "current_phase": phase,
                "phase_number": PHASE_NUMBERS.get(phase, 0),
                "signal": None,
                "agents_active": [],
                "summary": f"Phase {PHASE_NUMBERS.get(phase, '?')}/7: {phase}",
            }
        )

    def phase_end(self, phase: str, status: str = "ok", **details):
        duration_ms = int(
            (time.time() - (self._phase_start_time or time.time())) * 1000
        )
        entry = {
            "event": "phase_end",
            "phase": phase,
            "status": status,
            "duration_ms": duration_ms,
        }
        entry.update(details)
        self._write_audit(entry)

        phases = list(self._status.get("phases_completed", []))
        if phase not in phases:
            phases.append(phase)

        self._update_status(
            {
                "phases_completed": phases,
                "summary": f"Phase {PHASE_NUMBERS.get(phase, '?')}/7: {phase} — {status} ({duration_ms}ms)",
            }
        )

    def signal(self, signal: str, phase: str, pending_tasks: list):
        agent_names = [t.agent_name for t in pending_tasks]
        self._write_audit(
            {
                "event": "signal",
                "phase": phase,
                "signal": signal,
                "pending_task_count": len(pending_tasks),
                "agents": agent_names,
            }
        )
        self._update_status(
            {
                "signal": signal,
                "agents_active": agent_names,
                "summary": f"Phase {PHASE_NUMBERS.get(phase, '?')}/7: {phase} — {signal} ({', '.join(agent_names)})",
            }
        )

    def agent_spawned(self, phase: str, agent_name: str, agent_type: str):
        self._write_audit(
            {
                "event": "agent_spawned",
                "phase": phase,
                "agent_name": agent_name,
                "agent_type": agent_type,
            }
        )

    def agent_completed(
        self, phase: str, agent_name: str, success: bool, summary: str = ""
    ):
        self._write_audit(
            {
                "event": "agent_completed",
                "phase": phase,
                "agent_name": agent_name,
                "success": success,
                "summary": summary,
            }
        )
        completed = list(self._status.get("agents_completed", []))
        if agent_name not in completed:
            completed.append(agent_name)
        active = [a for a in self._status.get("agents_active", []) if a != agent_name]
        self._update_status({"agents_completed": completed, "agents_active": active})

    def review_finding(
        self, severity: str, category: str, description: str, coder: str
    ):
        count = self._status.get("review_findings_count", 0) + 1
        self._write_audit(
            {
                "event": "review_finding",
                "severity": severity,
                "category": category,
                "description": description,
                "coder_responsible": coder,
            }
        )
        self._update_status({"review_findings_count": count})

    def fix_round(self, retry_count: int, verification_passed: bool):
        self._write_audit(
            {
                "event": "fix_round",
                "retry_count": retry_count,
                "verification_passed": verification_passed,
            }
        )

    def error(self, phase: str, error_type: str, error_message: str):
        self._write_audit(
            {
                "event": "error",
                "phase": phase,
                "error_type": error_type,
                "error_message": error_message,
            }
        )
        errors = list(self._status.get("errors", []))
        errors.append(
            {"phase": phase, "type": error_type, "message": error_message[:200]}
        )
        self._update_status({"errors": errors})

    def run_end(self, final_phase: str):
        total_ms = int((time.time() - (self._start_time or time.time())) * 1000)
        self._write_audit(
            {
                "event": "run_end",
                "final_phase": final_phase,
                "total_duration_ms": total_ms,
            }
        )
        self._update_status(
            {
                "current_phase": final_phase,
                "signal": "done",
                "summary": f"Complete: {final_phase} (total {total_ms}ms)",
            }
        )

    # --- Internal ---

    def _write_audit(self, entry: dict):
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        entry["run_id"] = self._run_id
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _update_status(self, updates: dict):
        self._status.update(updates)
        self._status["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.status_path.write_text(
            json.dumps(self._status, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

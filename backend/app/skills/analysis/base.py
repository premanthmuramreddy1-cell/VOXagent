"""Shared issue shape for every analyzer. Analyzers are pure functions: files in, issue dicts out."""

from typing import Any

WEIGHT = {"critical": 25, "high": 12, "medium": 5, "low": 1}
CATEGORIES = ["security", "bug", "performance", "error-handling", "naming", "tests", "style"]
CONFIDENCE = ("high", "medium", "low")
WHY = {
    "security": "An attacker could use this to read data or run code.",
    "bug": "The code can crash or give wrong results here.",
    "performance": "This wastes time or memory as input grows.",
    "error-handling": "Failures here are hidden or will surprise the caller.",
    "naming": "Unclear names make the code harder to read and change.",
    "tests": "Without tests, regressions here go unnoticed.",
    "style": "It adds noise that makes real problems harder to spot.",
}


def make_issue(severity: str, category: str, file: str, line: int, explanation: str, fix: str, source: str,
               why: str = "", cwe: str = "", confidence: str = "high") -> dict[str, Any]:
    """`code` (the offending source line) is filled in later by the review skill, not by analyzers."""
    return {
        "severity": severity, "category": category, "file": file, "line": line,
        "explanation": explanation, "why": why or WHY[category], "fix": fix, "source": source,
        "cwe": cwe, "confidence": confidence, "code": "",
    }

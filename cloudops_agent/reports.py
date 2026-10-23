"""Portable report rendering and explicit, non-overwriting local exports."""

import json
import os
import re


def _escape(value):
    return re.sub(r"([\\`*_{}\[\]<>()#!|])", r"\\\1", str(value).replace("\n", " ").replace("\r", " "))


def render_report(report, output_format="json"):
    if output_format == "json":
        return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    status = report.get("status", report.get("slo", {}).get("status", report.get("data_status", "collected")))
    lines = ["# CloudOps report", "", f"Status: **{_escape(status)}**", ""]
    for field in ("service_name", "job_name", "project", "region", "observed_at", "captured_at"):
        if report.get(field):
            lines.append(f"- {_escape(field.replace('_', ' '))}: {_escape(report[field])}")
    if report.get("findings"):
        lines.extend(["", "## Findings", ""])
        for finding in report["findings"]:
            lines.append(
                f"- **{_escape(finding.get('code', 'finding'))}**: {_escape(finding.get('observation', ''))}"
            )
            if finding.get("suggested_check"):
                lines.append(f"  - Next check: {_escape(finding['suggested_check'])}")
    if report.get("gaps"):
        lines.extend(["", "## Evidence gaps", ""])
        for gap in report["gaps"]:
            lines.append(
                f"- {_escape(gap.get('source', 'unknown'))}: {_escape(gap.get('reason') or gap.get('error', {}).get('message') or gap.get('error'))}"
            )
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    fence = "`" * max(3, max((len(match) + 1 for match in re.findall(r"`+", payload)), default=3))
    lines.extend(["", "## Full evidence", "", fence + "json", payload, fence, ""])
    return "\n".join(lines)


def write_report(path, text):
    # Exclusive creation prevents accidental replacement of saved evidence.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)

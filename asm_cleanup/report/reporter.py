"""Human and machine-readable walk result reporters."""

from __future__ import annotations

from asm_cleanup.pipeline.walk_results import WalkResult


def format_human_report(path_results: list[WalkResult]) -> str:
    """Build a human-readable multi-path discovery report.

    Args:
        path_results (list[WalkResult]): Results from processed ASM paths.

    Returns:
        str: Report text.
    """
    total_paths = len(path_results)
    paths_with_alias = sum(1 for result in path_results if result.unique_aliases > 0)
    total_aliases = sum(result.unique_aliases for result in path_results)
    lines: list[str] = [
        "=" * 60,
        " ASM Alias Discovery Report",
        "=" * 60,
        "",
    ]
    for i, result in enumerate(path_results, start=1):
        lines.extend(
            [
                f"[PATH {i}/{total_paths}] {result.display_path}",
                "-" * 60,
                "  Artifacts:",
                f"    Walk transcript : {result.outfile}",
                f"    Result summary  : {result.result_json}",
            ]
        )
        if result.fix_written:
            lines.append(f"    OMF MOVE SQL    : {result.fixfile}")
        elif result.emit_blocked:
            lines.append("    OMF MOVE SQL    : not written (emit blocked)")
        elif result.unique_aliases == 0:
            lines.append("    OMF MOVE SQL    : not written (no aliases)")
        else:
            lines.append("    OMF MOVE SQL    : not written")
        if result.emit_blocked:
            lines.append(f"    Emit status     : BLOCKED — {result.emit_blocked}")
        lines.extend(
            [
                "",
                "  Results:",
                f"    Files examined : {result.files_examined}",
                f"    Alias rows     : {result.alias_rows}",
                f"    Unique aliases : {result.unique_aliases}",
                "",
                "  Status:",
            ]
        )
        if result.emit_blocked:
            lines.append("    ✖ Inventory OK; SQL emit blocked")
        elif result.fix_written:
            lines.append("    ✔ Aliases found; SQL written")
        elif result.unique_aliases > 0:
            lines.append("    ✔ Aliases found")
        else:
            lines.append("    ✖ No aliases found")
        lines.extend(["", "-" * 60, ""])

    lines.extend(
        [
            "=" * 60,
            " Summary",
            "=" * 60,
            f"  Paths scanned   : {total_paths}",
            f"  Paths w/alias   : {paths_with_alias}",
            f"  Total aliases   : {total_aliases}",
        ]
    )
    return "\n".join(lines)

"""Tests for walk pipeline orchestration with a fake AsmCmdPort."""

from pathlib import Path

from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.pipeline.pipeline_orchestrator import PipelineOrchestrator
from asm_cleanup.pipeline.walk_results import WalkResult
from asm_cleanup.pipeline.walk_scope_resolver import WalkScopeResolver
from asm_cleanup.report.reporter import format_human_report
from asm_cleanup.transport.command_result import CommandResult
from asm_cleanup.transport.fake_asm_cmd_port import FakeAsmCmdPort
from asm_cleanup.walk.asm_walker import AsmWalker


def _ok(argv: list[str], stdout: str = "") -> CommandResult:
    """Build a successful CommandResult.

    Args:
        argv (list[str]): Command argv.
        stdout (str): Stdout text.

    Returns:
        CommandResult: Successful result.
    """
    return CommandResult(argv=argv, stdout=stdout, stderr="", exit_code=0)


def test_walk_directory_recurses_into_subdirs() -> None:
    """Walk lists a directory then recurses into trailing-slash children."""
    port = FakeAsmCmdPort(
        {
            ("asmcmd", "ls", "-l", "+DATA/mydb"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/mydb"],
                "DATAFILE a.dbf\n                                           Y    DATAFILE/\n",
            ),
            ("asmcmd", "ls", "-l", "+DATA/mydb/DATAFILE"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/mydb/DATAFILE"],
                "DATAFILE users.dbf => +DATA/OMF\n",
            ),
        }
    )
    ticks: list[tuple[int, str]] = []
    inventory = AsmWalker(port).walk(
        WalkScopeResolver.normalize_asm_path("+data/mydb"),
        on_scan=lambda n, p: ticks.append((n, p)),
    )
    paths = [d.path for d in inventory.directories]
    assert "+DATA/mydb" in paths
    assert "+DATA/mydb/DATAFILE" in paths
    assert ticks == [(1, "+DATA/mydb"), (2, "+DATA/mydb/DATAFILE")]


def test_walk_max_depth_stops_recursion() -> None:
    """max_depth limits how deep the walker recurses from the root."""
    port = FakeAsmCmdPort(
        {
            ("asmcmd", "ls", "-l", "+DATA/MYDB"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/MYDB"],
                "                                           Y    DATAFILE/\n",
            ),
            ("asmcmd", "ls", "-l", "+DATA/MYDB/DATAFILE"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/MYDB/DATAFILE"],
                "DATAFILE users.dbf => +DATA/OMF\n",
            ),
        }
    )
    inventory = AsmWalker(port, max_depth=0).walk("+DATA/MYDB")
    paths = [d.path for d in inventory.directories]
    assert paths == ["+DATA/MYDB"]


def test_orchestrator_uses_profile_max_depth() -> None:
    """PipelineOrchestrator applies scope.max_depth from the target profile."""
    port = FakeAsmCmdPort(
        {
            ("asmcmd", "ls", "-l", "+DATA/MYDB"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/MYDB"],
                "                                           Y    DATAFILE/\n",
            ),
        }
    )
    scope = ScopeConfig.model_validate({"max_depth": 0})
    move_policy = MovePolicy(destination_disk_group="+DATA")
    orch = PipelineOrchestrator(port=port, scope=scope, move_policy=move_policy)
    inventory = orch.walk_path("+DATA/MYDB", show_progress=False)
    assert [d.path for d in inventory.directories] == ["+DATA/MYDB"]


def test_command_result_fail_loud() -> None:
    """raise_for_status raises AsmCmdError on non-zero exit."""
    from asm_cleanup.transport.asm_cmd_error import AsmCmdError

    result = CommandResult(
        argv=["asmcmd", "ls", "+"],
        stdout="",
        stderr="ASMCMD-8102: no connection",
        exit_code=1,
    )
    try:
        result.raise_for_status()
        raise AssertionError("expected AsmCmdError")
    except AsmCmdError as exc:
        assert "ASMCMD-8102" in str(exc)
        assert exc.exit_code == 1


def test_run_walk_pipeline_analyze_and_fix(tmp_path: Path) -> None:
    """Analyze walked inventory and write fix SQL via the orchestrator."""
    port = FakeAsmCmdPort(
        {
            ("asmcmd", "ls", "-l", "+DATA/MYDB"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/MYDB"],
                "DATAFILE users.dbf => +DATA/MYDB/DATAFILE/USERS.256.1\n",
            ),
            ("asmcmd", "ls", "+DATA/MYDB"): _ok(["asmcmd", "ls", "+DATA/MYDB"], ""),
        }
    )
    move_policy = MovePolicy(destination_disk_group="+DATA")
    orch = PipelineOrchestrator(port=port, move_policy=move_policy)
    outfile = tmp_path / "walk.txt"
    fixfile = tmp_path / "fix.sql"
    result_json = tmp_path / "result.json"
    result = orch.process_path(
        "+DATA/MYDB",
        do_walk=True,
        do_analyze=True,
        do_fix=True,
        outfile=outfile,
        fixfile=fixfile,
        result_json=result_json,
    )
    assert result.unique_aliases == 1
    assert result.fix_written is True
    assert "ALTER DATABASE MOVE DATAFILE" in fixfile.read_text(encoding="utf-8")
    assert result_json.is_file()
    assert "# asm-cleanup-transcript:1" in outfile.read_text(encoding="utf-8")


def test_from_transcript_offline(tmp_path: Path) -> None:
    """Load a transcript offline and analyze without a live port."""
    transcript = tmp_path / "in.txt"
    transcript.write_text(
        """# asm-cleanup-transcript:1
DIR: +DATA/MYDB/DATAFILE
------------------------------------------------------------
DATAFILE users.dbf => +DATA/MYDB/DATAFILE/USERS.256.1
""",
        encoding="utf-8",
    )
    move_policy = MovePolicy(destination_disk_group="+DATA")
    orch = PipelineOrchestrator(port=None, move_policy=move_policy)
    result = orch.process_path(
        "+DATA/MYDB",
        do_walk=False,
        do_analyze=True,
        do_fix=True,
        from_transcript=transcript,
        outfile=tmp_path / "out.txt",
        fixfile=tmp_path / "out.sql",
        result_json=tmp_path / "out.json",
    )
    assert result.unique_aliases == 1
    assert result.fix_written is True


def test_format_human_report_handles_empty_aliases() -> None:
    """Report text includes a no-aliases status for empty results."""
    text = format_human_report(
        [
            WalkResult(
                asm_path="+DATA/MYDB",
                display_path="+DATA/MYDB",
                outfile=Path("logs/a.txt"),
                fixfile=Path("logs/a.sql"),
                result_json=Path("logs/a.json"),
                files_examined=0,
                alias_rows=0,
                unique_aliases=0,
                fix_written=False,
            )
        ]
    )
    assert "No aliases found" in text
    assert "Walk transcript :" in text
    assert "OMF MOVE SQL    : not written (no aliases)" in text
    assert "Paths scanned   : 1" in text


def test_format_human_report_labels_emit_blocked() -> None:
    """Report labels blocked SQL emit and names each artifact role."""
    text = format_human_report(
        [
            WalkResult(
                asm_path="+DATA/MYDB",
                display_path="+DATA/MYDB",
                outfile=Path("logs/walk.txt"),
                fixfile=Path("logs/fix.sql"),
                result_json=Path("logs/result.json"),
                files_examined=10,
                alias_rows=2,
                unique_aliases=2,
                fix_written=False,
                emit_blocked="1 unmapped PDB GUID(s): ABC",
            )
        ]
    )
    assert "Walk transcript : logs/walk.txt" in text
    assert "Result summary  : logs/result.json" in text
    assert "OMF MOVE SQL    : not written (emit blocked)" in text
    assert "Emit status     : BLOCKED — 1 unmapped PDB GUID(s): ABC" in text
    assert "Inventory OK; SQL emit blocked" in text


def test_walk_progress_reporter_tty_updates_same_line() -> None:
    """TTY reporter rewrites one line then finishes with a newline."""
    from io import StringIO

    from asm_cleanup.report.walk_progress_reporter import WalkProgressReporter

    stream = StringIO()
    stream.isatty = lambda: True  # type: ignore[method-assign]
    reporter = WalkProgressReporter("+DATA/MYDB", stream=stream)
    reporter(1, "+DATA/MYDB")
    reporter(2, "+DATA/MYDB/DATAFILE")
    reporter.finish()
    text = stream.getvalue()
    assert text.count("\r") >= 2
    assert text.endswith("\n")
    assert "2 dirs done" in text


def test_walk_progress_reporter_nontty_skips_stream() -> None:
    """Non-TTY reporter leaves the stream empty (progress goes to loguru)."""
    from io import StringIO

    from asm_cleanup.report.walk_progress_reporter import WalkProgressReporter

    stream = StringIO()
    stream.isatty = lambda: False  # type: ignore[method-assign]
    reporter = WalkProgressReporter("+DATA/MYDB", stream=stream, log_every=2)
    reporter(1, "+DATA/MYDB")
    reporter(2, "+DATA/MYDB/A")
    reporter.finish()
    assert reporter.directories_visited == 2
    assert stream.getvalue() == ""


def test_walk_scope_exclusions() -> None:
    """Respect exclude_databases and exclude_paths in WalkScopeResolver."""
    from asm_cleanup.pipeline.walk_scope_resolver import WalkScopeResolver

    scope = ScopeConfig.model_validate(
        {
            "disk_groups": ["+DATA", "+FRA"],
            "databases": ["mydb", "otherdb", "excludeddb"],
            "exclude_databases": ["excludeddb"],
            "exclude_paths": ["+FRA/*", "+DATA/otherdb"],
        }
    )
    resolver = WalkScopeResolver(scope)
    assert resolver.databases() == ["mydb", "otherdb"]
    # Expanded paths before exclusions: +DATA/mydb, +DATA/otherdb, +FRA/mydb, +FRA/otherdb
    # +FRA/* is excluded (removes +FRA/mydb, +FRA/otherdb)
    # +DATA/otherdb is excluded (removes +DATA/otherdb)
    # Only +DATA/mydb should remain
    assert resolver.resolve_paths() == ["+DATA/mydb"]


def test_pipeline_parallel_walks() -> None:
    """Run parallel walks for multiple scope paths concurrently."""
    port = FakeAsmCmdPort(
        {
            ("asmcmd", "ls", "-l", "+DATA/MYDB1"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/MYDB1"],
                "DATAFILE users.dbf => +DATA/MYDB1/DATAFILE/USERS.256.1\n",
            ),
            ("asmcmd", "ls", "-l", "+DATA/MYDB2"): _ok(
                ["asmcmd", "ls", "-l", "+DATA/MYDB2"],
                "DATAFILE users.dbf => +DATA/MYDB2/DATAFILE/USERS.256.1\n",
            ),
        }
    )
    scope = ScopeConfig.model_validate(
        {
            "disk_groups": ["+DATA"],
            "databases": ["MYDB1", "MYDB2"],
        }
    )
    move_policy = MovePolicy(destination_disk_group="+DATA")
    orch = PipelineOrchestrator(port=port, scope=scope, move_policy=move_policy)
    results = orch.run(
        do_walk=True,
        do_analyze=True,
        do_fix=True,
    )
    assert len(results) == 2
    assert results[0].unique_aliases == 1
    assert results[1].unique_aliases == 1

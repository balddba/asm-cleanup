"""Unit tests for ScanService walk helpers and failure persistence."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.services.scan_service import ScanService, WalkAliasOutcome
from asm_cleanup.walk.asm_inventory import AsmInventory


def test_walk_asm_aliases_dedupes_and_skips_failures() -> None:
    """Deduplicate aliases and continue when a path walk raises."""
    service = ScanService(MagicMock(), MagicMock(), MagicMock())
    scope = ScopeConfig(disk_groups=["+DATA"], databases=["homelab"])
    port = MagicMock()

    record = AliasRecord(
        file_type="DATAFILE",
        source_path="+DATA/homelab/DATAFILE/a.dbf",
        target_path="+DATA/HOMELAB/DATAFILE/a.dbf",
        disk_group="+DATA",
    )
    inventory = MagicMock(spec=AsmInventory)
    inventory.extract_aliases.return_value = [record, record]

    orch = MagicMock()
    orch.scope_resolver.return_value.resolve_paths.return_value = [
        "+DATA/homelab",
        "+DATA/missing",
    ]

    def walk_side_effect(path: str, show_progress: bool = False) -> AsmInventory:
        """Return inventory for the first path and fail on the second.

        Args:
            path (str): ASM path being walked.
            show_progress (bool): Unused progress flag.

        Returns:
            AsmInventory: Mock inventory for the first path.

        Raises:
            RuntimeError: For the missing path.
        """
        if path == "+DATA/missing":
            raise RuntimeError("path missing")
        return inventory

    orch.walk_path.side_effect = walk_side_effect

    with patch(
        "asm_cleanup.services.scan_service.PipelineOrchestrator", return_value=orch
    ):
        outcome = service._walk_asm_aliases(port, scope=scope)

    assert outcome.records == [record]
    assert outcome.paths_attempted == 2
    assert outcome.failed_paths == ["+DATA/missing"]


def test_walk_asm_aliases_empty_when_no_paths() -> None:
    """Return an empty list when scope resolution raises ValueError."""
    service = ScanService(MagicMock(), MagicMock(), MagicMock())
    scope = ScopeConfig(disk_groups=["+DATA"])
    orch = MagicMock()
    orch.scope_resolver.return_value.resolve_paths.side_effect = ValueError("empty")

    with patch(
        "asm_cleanup.services.scan_service.PipelineOrchestrator", return_value=orch
    ):
        assert service._walk_asm_aliases(MagicMock(), scope=scope) == WalkAliasOutcome(
            records=[], paths_attempted=0, failed_paths=[]
        )


def test_scan_service_run_marks_failed_on_exception() -> None:
    """Persist failed status and error_message when discovery raises."""
    session = MagicMock()
    target = MagicMock()
    target.host = "h"
    target.user = "u"
    target.ssh_key_path = None
    target.ssh_key_content = None
    target.destination_disk_group = "+DATA"
    target.move_online = False
    scan = MagicMock()
    scan.status = "pending"

    service = ScanService(session, target, scan)

    @contextmanager
    def boom() -> Generator[None]:
        """Raise immediately when entering the SSH context.

        Yields:
            None: Never reached.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("ssh down")
        yield  # pragma: no cover

    service._ssh_connection = boom  # type: ignore[method-assign]
    service.run()

    assert scan.status == "failed"
    assert scan.error_message == "ssh down"
    assert session.commit.call_count >= 2


def test_scan_service_run_empty_aliases_sets_placeholder_sql() -> None:
    """Write placeholder SQL when the walk finds no aliases to move."""
    session = MagicMock()
    target = MagicMock()
    target.host = "h"
    target.user = "u"
    target.ssh_key_path = None
    target.ssh_key_content = None
    target.destination_disk_group = "+DATA"
    target.move_online = False
    scan = MagicMock()
    scan.status = "pending"

    service = ScanService(session, target, scan)

    mock_conn = MagicMock()

    @contextmanager
    def fake_ssh() -> Generator[MagicMock]:
        """Yield a mock Fabric connection.

        Yields:
            MagicMock: Fake SSH connection.
        """
        yield mock_conn

    service._ssh_connection = fake_ssh  # type: ignore[method-assign]
    service._host.discover_grid_home_and_sid = MagicMock(
        return_value=("/u01/grid", "+ASM")
    )
    service._host.discover_disk_groups = MagicMock(return_value=["+DATA"])
    service._host.discover_databases = MagicMock(return_value={})
    service._walk_asm_aliases = MagicMock(
        return_value=WalkAliasOutcome(records=[], paths_attempted=1, failed_paths=[])
    )

    with (
        patch("asm_cleanup.services.scan_service.TargetMapper") as mock_mapper,
        patch("asm_cleanup.services.scan_service.SshGridAdapter"),
        patch("asm_cleanup.services.scan_service.AliasEnricher") as mock_enricher_cls,
    ):
        mock_mapper.to_connection_config.return_value = MagicMock()
        enricher = MagicMock()
        enricher.enrich_and_persist.return_value = []
        mock_enricher_cls.return_value = enricher
        service.run()

    assert scan.status == "completed"
    assert scan.progress_message is None
    assert scan.generated_sql == "-- No alias files found that require moving."


def test_scan_service_run_updates_progress_phases() -> None:
    """Commit phase progress messages while a successful scan runs."""
    session = MagicMock()
    target = MagicMock()
    target.host = "h"
    target.user = "u"
    target.ssh_key_path = None
    target.ssh_key_content = None
    target.destination_disk_group = "+DATA"
    target.move_online = False
    scan = MagicMock()
    scan.id = 42
    scan.status = "pending"
    scan.progress_message = None

    service = ScanService(session, target, scan)
    progress_messages: list[str] = []

    original_set_progress = service._set_progress

    def capture_progress(message: str) -> None:
        """Record progress text then delegate to the real helper.

        Args:
            message (str): Progress phase text.
        """
        progress_messages.append(message)
        original_set_progress(message)

    service._set_progress = capture_progress  # type: ignore[method-assign]

    mock_conn = MagicMock()

    @contextmanager
    def fake_ssh() -> Generator[MagicMock]:
        """Yield a mock Fabric connection.

        Yields:
            MagicMock: Fake SSH connection.
        """
        yield mock_conn

    service._ssh_connection = fake_ssh  # type: ignore[method-assign]
    service._host.discover_grid_home_and_sid = MagicMock(
        return_value=("/u01/grid", "+ASM")
    )
    service._host.discover_disk_groups = MagicMock(return_value=["+DATA"])
    service._host.discover_databases = MagicMock(return_value={})
    service._walk_asm_aliases = MagicMock(
        return_value=WalkAliasOutcome(records=[], paths_attempted=1, failed_paths=[])
    )

    with (
        patch("asm_cleanup.services.scan_service.TargetMapper") as mock_mapper,
        patch("asm_cleanup.services.scan_service.SshGridAdapter"),
        patch("asm_cleanup.services.scan_service.AliasEnricher") as mock_enricher_cls,
    ):
        mock_mapper.to_connection_config.return_value = MagicMock()
        enricher = MagicMock()
        enricher.enrich_and_persist.return_value = []
        mock_enricher_cls.return_value = enricher
        service.run()

    assert scan.status == "completed"
    assert scan.progress_message is None
    assert "Connecting to target host..." in progress_messages
    assert "Discovering Grid Home and ASM SID..." in progress_messages
    assert "Generating OMF move SQL..." in progress_messages
    assert session.commit.call_count >= 3


def test_scan_service_run_fails_when_every_walk_path_fails() -> None:
    """Mark the scan failed when every ASM walk path raises."""
    session = MagicMock()
    target = MagicMock()
    target.host = "h"
    target.user = "u"
    target.ssh_key_path = None
    target.ssh_key_content = None
    target.destination_disk_group = "+DATA"
    target.move_online = False
    scan = MagicMock()
    scan.status = "pending"

    service = ScanService(session, target, scan)

    mock_conn = MagicMock()

    @contextmanager
    def fake_ssh() -> Generator[MagicMock]:
        """Yield a mock Fabric connection.

        Yields:
            MagicMock: Fake SSH connection.
        """
        yield mock_conn

    service._ssh_connection = fake_ssh  # type: ignore[method-assign]
    service._host.discover_grid_home_and_sid = MagicMock(
        return_value=("/u01/grid", "+ASM")
    )
    service._host.discover_disk_groups = MagicMock(return_value=["+DATA"])
    service._host.discover_databases = MagicMock(return_value={})
    service._walk_asm_aliases = MagicMock(
        return_value=WalkAliasOutcome(
            records=[],
            paths_attempted=2,
            failed_paths=["+DATA/homelab", "+DATA/missing"],
        )
    )

    with (
        patch("asm_cleanup.services.scan_service.TargetMapper") as mock_mapper,
        patch("asm_cleanup.services.scan_service.SshGridAdapter"),
    ):
        mock_mapper.to_connection_config.return_value = MagicMock()
        service.run()

    assert scan.status == "failed"
    assert "ASM walk failed for every path" in scan.error_message

"""Thin coordinator that delegates discovery scans to ScanService."""

from __future__ import annotations

from sqlalchemy.orm import Session

from asm_cleanup.db.scan import Scan
from asm_cleanup.db.target import Target
from asm_cleanup.services.scan_service import ScanService


class TargetDiscoveryRunner:
    """Orchestrates host discovery, library walk/SQL, and SQLite persistence.

    Thin facade over ScanService for CLI/web callers.

    Attributes:
        session (Session): SQLAlchemy database session.
        target (Target): Target connection profile model.
        scan (Scan): Associated Scan model tracking progress and output.
    """

    def __init__(self, session: Session, target: Target, scan: Scan) -> None:
        """Initialize the runner.

        Args:
            session (Session): Database session.
            target (Target): Connection target.
            scan (Scan): Scan to update.
        """
        self.session = session
        self.target = target
        self.scan = scan
        self._service = ScanService(session, target, scan)

    def run(self) -> None:
        """Execute host discovery, library walk/SQL, and persist scan results."""
        self._service.run()

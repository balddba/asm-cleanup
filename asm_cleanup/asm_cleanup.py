"""ASM session: YAML host profiles, Fabric SSH, asmcmd walk / analyze / fix pipeline."""

from __future__ import annotations

import datetime
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fabric import Connection

from .asm_config import AsmConfigFile
from .host_config import HostConfig
from .walk_result import WalkResult

ASMLine = str
ASMPath = str
AliasEntry = tuple[str, str, str, str | None]
# (file_type, source_path, target_path, pdb_guid_or_none)
# pdb_guid_or_none: 32-char ASM PDB directory GUID (uppercase) when path is under a PDB subtree;
# None for CDB$ROOT-style ``.../DBNAME/DATAFILE/...`` (no GUID directory).

# Default directory for walk transcripts and generated SQL (created on write).
DEFAULT_LOG_DIR = Path("logs")
_DISKGROUP_TOKEN = re.compile(r"^\+?[A-Za-z0-9_$#-]+/?$")
_DATA_TEMPFILE_ROW = re.compile(r"^(DATAFILE|TEMPFILE)\b.*$")


class AsmCleanup:
    """ASM tools: optional host profile, optional Fabric session, and walk/analyze/fix."""

    def __init__(
        self,
        host_config: HostConfig | None = None,
        *,
        connection: Connection | None = None,
        host_id: str | None = None,
        database_filter: list[str] | tuple[str, ...] | None = None,
        debug: bool = False,
    ) -> None:
        self.host_config: HostConfig = host_config
        self.connection = connection
        self.host_id = host_id
        self._database_filter = frozenset(database_filter) if database_filter else None
        self._discovered_disk_groups: list[str] | None = None
        self._debug = bool(debug) or os.environ.get("ASM_CLEANUP_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
        self.debug(
            "session init: "
            f"host_id={host_id!r}, "
            f"has_host_config={host_config is not None}, "
            f"has_connection={connection is not None}, "
            f"database_filter={sorted(database_filter) if database_filter else None}, "
            f"use_oraenv={getattr(host_config, 'use_oraenv', None)}, "
            f"oracle_sid={getattr(host_config, 'oracle_sid', None)!r}"
        )

    def debug(self, message: str) -> None:
        """Print debug message to stdout if debug mode is enabled.

        Args:
            message (str): Debug message to print.

        Returns:
            None
        """
        if self._debug:
            print(f"[AsmCleanup:debug] {message}", flush=True)

    @property
    def databases(self) -> list[str]:
        """Database names for this host, optionally restricted by a filter.

        Returns the list of database names configured in the YAML host profile,
        optionally filtered by the database_filter set during initialization.

        Returns:
            list[str]: List of database names after applying optional filter.

        Raises:
            ValueError: If database_filter contains names not in host configuration.

        Note:
            Filter validation ensures all requested databases exist in YAML config.
        """
        # Extract database list from host configuration
        base = list(self.host_config.databases)

        # Return full list if no filter is applied
        if not self._database_filter:
            self.debug(f"databases property: using full host list {base!r} (no filter)")
            return base

        # Identify filter entries that don't match any configured database
        unknown = self._database_filter - set(base)
        self.debug(
            "databases property: "
            f"yaml_databases={base!r}, "
            f"filter={sorted(self._database_filter)!r}, "
            f"unknown_in_filter={sorted(unknown)!r}"
        )

        # Raise error if filter contains invalid database names
        if unknown:
            raise ValueError(
                f"Databases not defined for this host: {sorted(unknown)}; allowed: {base}. "
                "Drop --database / database_filter entries that are not listed under this host in YAML, "
                "or add the missing names to asm.hosts.<host_id>.databases."
            )

        # Filter base list to only include databases in the filter set
        resolved = [d for d in base if d in self._database_filter]
        self.debug(f"databases property: resolved after filter {resolved!r}")
        return resolved

    @property
    def disk_groups(self) -> list[str]:
        """List of disk group names for this host.
                
        Returns:
            list[str]: Disk group names normalized to uppercase with leading '+' (e.g., ['+DATA', '+FRA']).
        
        Note:
            Returns YAML-configured disk groups if present, otherwise auto-discovers from ASM via asmcmd.
            Discovery results are cached after first call to avoid repeated asmcmd executions.
        """
        # Get explicitly configured disk groups from YAML
        configured = list(self.host_config.disk_groups)
        if configured:
            return configured
        # Fall back to ASM discovery when YAML list is empty
        return self.discover_disk_groups()

    def discover_disk_groups(self) -> list[str]:
        """Discover disk groups from ASM when YAML ``disk_groups`` is empty.

        Returns:
            list[str]: Discovered disk group names with leading '+' and uppercase format.
        """
        # Return cached results if already discovered
        if self._discovered_disk_groups is not None:
            return list(self._discovered_disk_groups)

        # Execute 'asmcmd ls +' to list all disk groups
        raw_entries = self.run_shell_command("asmcmd ls + 2>/dev/null")
        discovered: list[str] = []
        seen: set[str] = set()

        # Process each line from asmcmd output
        for entry in raw_entries:
            token = entry.strip()
            if not token:
                continue
            # Validate the token matches expected disk group naming pattern (asmcmd ls + prints entries like DATA/ or +DATA/)
            if not _DISKGROUP_TOKEN.fullmatch(token):
                self.debug(f"discover_disk_groups: skipping non-diskgroup line {token!r}")
                continue

            # Remove trailing slash from directory-style output
            token = token.rstrip("/")
            if not token:
                continue

            # Normalize to uppercase with leading '+'
            normalized = self._normalize_disk_group_token(token)

            # Deduplicate using case-insensitive key
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            discovered.append(normalized)

        # Cache the results for performance on subsequent calls
        self._discovered_disk_groups = discovered
        self.debug(
            "discover_disk_groups: "
            f"configured_empty=True discovered={discovered!r}"
        )
        return list(discovered)

    # def resolve_asm_walk_path(self, asm_path: str | None) -> str:
    #     """Resolve the ASM path to walk from explicit argument, YAML default, or inference.
    #
    #     Resolution priority:
    #     1. Explicit asm_path argument (normalized if starts with '+')
    #     2. YAML host_config.default_asm_path (normalized if starts with '+')
    #     3. Inferred from first disk group + sole database: {disk_group[0]}/{database}
    #
    #     The inferred form requires exactly one database name in scope (after database_filter)
    #     and at least one disk group (configured in YAML or discovered from ASM). For multiple
    #     databases or disk groups, set default_asm_path in YAML or pass asm_path explicitly.
    #
    #     Args:
    #         asm_path (str | None): Explicit ASM path to walk (e.g., '+DATA/MYDB').
    #
    #     Returns:
    #         str: Resolved ASM path ready for walking.
    #
    #     Raises:
    #         ValueError: If asm_path is None/empty and no YAML host profile is available.
    #         ValueError: If inference is attempted but no disk groups are configured or discoverable from ASM.
    #         ValueError: If inference is attempted but the number of databases in scope is not exactly one.
    #
    #     Note:
    #         For SSH sessions with YAML configuration, the resolved path can come from:
    #         - Direct argument (highest priority)
    #         - YAML default_asm_path field (medium priority)
    #         - Automatic inference from first disk group + sole database (lowest priority)
    #
    #         Local sessions (no YAML profile) always require an explicit asm_path argument.
    #
    #         Disk groups are sourced from YAML disk_groups list when present, otherwise
    #         discovered from ASM via 'asmcmd ls +' (see discover_disk_groups method).
    #     """
    #     # Priority 1: Use explicit asm_path argument if provided
    #     if asm_path is not None and asm_path.strip():
    #         # Strip whitespace from input path
    #         p = asm_path.strip()
    #         # Normalize ASM paths starting with '+' to uppercase disk group
    #         return self.normalize_asm_path(p) if p.startswith("+") else p
    #
    #     # Local mode (no YAML profile) requires explicit asm_path
    #     if self.host_config is None:
    #         raise ValueError(
    #             "asm_path is required for local sessions (no YAML host profile). "
    #             "Example: ac.run('+DATA/MYDB')."
    #         )
    #
    #     # Priority 2: Use YAML default_asm_path if configured
    #     hc = self.host_config
    #     if hc.default_asm_path and hc.default_asm_path.strip():
    #         # Strip whitespace from YAML default path
    #         p = hc.default_asm_path.strip()
    #         self.debug(f"resolve_asm_walk_path: using yaml default_asm_path={p!r}")
    #         # Normalize ASM paths starting with '+' to uppercase disk group
    #         return self.normalize_asm_path(p) if p.startswith("+") else p
    #
    #     # Priority 3: Infer path from disk groups and databases
    #     # Get disk groups (from YAML or ASM discovery)
    #     dgs = self.disk_groups
    #     # Get filtered database list
    #     dbs = list(self.databases)
    #
    #     # Validate that at least one disk group is available
    #     if not dgs:
    #         raise ValueError(
    #             "Cannot infer ASM walk path: no disk groups configured and none discovered from ASM. "
    #             "Set default_asm_path in YAML or pass asm_path= to run()."
    #         )
    #
    #     # Validate exactly one database for unambiguous inference
    #     if len(dbs) != 1:
    #         raise ValueError(
    #             "Cannot infer ASM walk path: need exactly one database in scope "
    #             f"(after filter), got {dbs!r}. Set default_asm_path in YAML, pass asm_path= to run(), "
    #             "or narrow --database / database_filter to a single DB."
    #         )
    #
    #     # Construct path from first disk group and sole database
    #     # Strip whitespace from disk group
    #     dg = dgs[0].strip()
    #     # Ensure disk group has leading '+'
    #     if not dg.startswith("+"):
    #         dg = f"+{dg}"
    #     # Build and normalize the inferred path
    #     inferred = self.normalize_asm_path(f"{dg.rstrip('/')}/{dbs[0]}")
    #     self.debug(f"resolve_asm_walk_path: inferred {inferred!r} from disk_groups[0] + sole database")
    #     return inferred

    @staticmethod
    def _normalize_disk_group_token(dg: str) -> str:
        """Normalize a disk group token to a standardized format.
        
        Converts a disk group name to uppercase with a leading '+' prefix,
        stripping any trailing slashes. This ensures consistent formatting
        for disk group references throughout the codebase.
        
        Args:
            dg (str): Raw disk group token (may have leading '+', trailing '/', any case).
        
        Returns:
            str: Normalized disk group token with leading '+' and uppercase name (e.g., '+DATA').

        Note:
            If the input contains a path separator (e.g., '+DATA/subdir'),
            only the disk group portion (first segment after '+') is extracted
            and normalized. The rest of the path is discarded.
        """
        # Strip leading/trailing whitespace from input
        dg = dg.strip()

        # Ensure the token starts with '+' prefix
        if not dg.startswith("+"):
            dg = f"+{dg}"

        # Remove any trailing slashes from directory-style notation
        dg = dg.rstrip("/")

        # Extract just the disk group name (first segment after '+')
        # Split on '/' and take index [0] to get disk group portion only
        name = dg[1:].split("/", 1)[0]

        # Return normalized format: '+' prefix + uppercase disk group name
        return f"+{name.upper()}"

    @staticmethod
    def asm_path_prefix_match(path: str, prefix: str) -> bool:
        """Check if an ASM path is under a given prefix using case-insensitive comparison.

        Performs case-insensitive ASCII comparison to determine if the specified path
        starts with the given prefix. Both strings are stripped of leading/trailing
        whitespace and converted to lowercase for comparison.

        Args:
            path (str): ASM path to check (e.g., '+DATA/MYDB/DATAFILE/file.dbf').
            prefix (str): Prefix to match against (e.g., '+DATA/MYDB').

        Returns:
            bool: True if a path starts with aprefix (case-insensitive), False otherwise.

        Note:
            This method uses case-insensitive comparison suitable for ASM paths where
            disk groups and directories may have a varying case. Use this instead of
            direct string comparison when matching ASM path hierarchies.
        """
        # Strip whitespace and convert both strings to lowercase for comparison
        return path.strip().casefold().startswith(prefix.strip().casefold())

    @staticmethod
    def asm_path_is_crs(path: str) -> bool:
        """Check if an ASM path belongs to a CRS (Cluster Ready Services) disk group.

        Determines whether the specified ASM path is under a CRS disk group by checking
        if it starts with '+CRS' (case-insensitive). CRS disk groups store Oracle Grid
        Infrastructure cluster configuration files and should typically be excluded from
        database file operations.

        Args:
            path (str): ASM path to check (e.g., '+CRS/CLUSTER/file' or '+DATA/MYDB/file').

        Returns:
            bool: True if a path is under a CRS disk group (starts with '+crs', case-insensitive), False otherwise.

        Note:
            CRS disk groups contain Grid Infrastructure cluster configuration and should
            be filtered out during file access monitoring and walk operations. The check
            is case-insensitive to handle varying naming conventions.
        """
        # Strip whitespace and check if path starts with '+crs' (case-insensitive)
        return path.strip().casefold().startswith("+crs")

    def iter_asm_walk_paths(self) -> list[str]:
        """Generate ASM walk paths for all disk_group × database combinations.

        Constructs a list of ASM root paths by combining each configured disk group with
        each database name from the host profile. The method applies the database_filter
        if set during initialization. Disk groups are sourced from the YAML disk_groups
        list when present, otherwise auto-discovered from ASM via 'asmcmd ls +'.

        Returns:
            list[str]: List of normalized ASM paths in '+DISKGROUP/DATABASE' format with duplicates removed while preserving order.

        Raises:
            ValueError: If no host profile is available (local mode without YAML config) or if host profile lacks required disk_groups and databases properties.

        Note:
            - Paths are normalized via normalize_asm_path (uppercase disk group and
              intermediate directories)
            - Duplicate paths (case-insensitive) are filtered while preserving order
            - When disk_groups is empty in YAML, this method triggers ASM discovery
            - The databases property respects database_filter if set during init
            - Requires a YAML host profile (SSH session or HostConfig instance)
        """
        # Verify that a host profile is available (required for disk groups and databases)
        if self.host_config is None:
            raise ValueError("iter_asm_walk_paths requires a host profile (SSH session or HostConfig).")

        # Initialize result list and set for deduplication (case-insensitive)
        paths: list[str] = []
        seen: set[str] = set()

        # Iterate through each disk group
        for dg in self.disk_groups:
            # Normalize disk group token to '+DISKGROUP' format
            ndg = self._normalize_disk_group_token(dg)

            # Combine disk group with each database name
            for db in self.databases:
                # Construct and normalize the full ASM path
                p = AsmCleanup.normalize_asm_path(f"{ndg}/{db}")

                # Deduplicate using case-insensitive key
                key = p.casefold()
                if key not in seen:
                    seen.add(key)
                    paths.append(p)

        return paths

    @staticmethod
    def _asm_path_slug(asm_path: str) -> str:
        """Convert ASM path to a filesystem-safe token for log file naming.
    
        Strips leading '+' and replaces special characters with underscores to create
        a stable identifier suitable for use in filenames. Multiple consecutive underscores
        are collapsed to a single underscore, and leading/trailing underscores are removed.
    
        Args:
            asm_path (str): ASM path to convert (e.g., '+DATA/MYDB/DATAFILE').
    
        Returns:
            str: Filesystem-safe slug (e.g., 'DATA_MYDB_DATAFILE'). Returns 'asm' if input reduces to empty string.
        """
        # Strip leading/trailing whitespace from input path
        p = asm_path.strip()

        # Remove leading '+' character if present
        if p.startswith("+"):
            p = p[1:]

        # Replace all non-alphanumeric characters (except dots, underscores, hyphens) with underscore
        slug = re.sub(r"[^0-9A-Za-z._-]", "_", p)

        # Collapse multiple consecutive underscores to a single underscore
        slug = re.sub(r"_+", "_", slug).strip("_") or "asm"
    
        return slug

    @staticmethod
    def format_scan_path(asm_path: str) -> str:
        """Return a truncated ASM path for progress display (disk group + first child directory).

        Args:
            asm_path (str): Full ASM path to truncate (e.g., '+DATA/MYDB/DATAFILE/file.dbf').

        Returns:
            str: Abbreviated path with at most two segments (e.g., '+DATA/MYDB').
        """
        # Strip leading/trailing whitespace from input path
        raw = asm_path.strip()

        # Return non-ASM paths unchanged (no leading '+')
        if not raw.startswith("+"):
            return raw

        # Split path on '/' and filter out empty segments
        parts = [p for p in raw.split("/") if p]

        # Return full path if it already has two or fewer segments
        if len(parts) <= 2:
            return "/".join(parts)

        # Truncate to first two segments (disk group + first subdirectory)
        return "/".join(parts[:2])

    @staticmethod
    def build_walk_and_fix_paths(
            asm_path: str,
            *,
            date: str | None = None,
            sequence: int | None = None,
    ) -> tuple[Path, Path]:
        """Generate default walk transcript and fix script paths under the log directory.
    
        Creates standardized filenames using a date stamp, optional sequence number for
        multi-path walks, and a filesystem-safe slug derived from the ASM root path.
        Output files are placed under DEFAULT_LOG_DIR (typically 'logs/' directory).
    
        Args:
            asm_path (str): ASM path to derive the filename slug from (e.g., '+DATA/MYDB').
            date (str | None): Date stamp in YYYYMMDD format, defaults to current date if None.
            sequence (int | None): Two-digit sequence number for multi-path scenarios (00-99), omitted if None.
    
        Returns:
            tuple[Path, Path]: (walk_transcript_path, fix_script_path) pair under DEFAULT_LOG_DIR.
        """
        # Use current date if not provided
        stamp = date or datetime.datetime.now().strftime("%Y%m%d")

        # Convert ASM path to filesystem-safe slug (removes '+', replaces slashes with underscores)
        slug = AsmCleanup._asm_path_slug(asm_path)

        # Base directory for all log outputs
        base = DEFAULT_LOG_DIR

        # Build filenames with optional sequence number for multi-walk scenarios
        if sequence is not None:
            # Format: asm_walk_YYYYMMDD_NN_slug.txt
            walk_stem = f"asm_walk_{stamp}_{sequence:02d}_{slug}"
            # Format: asm_omf_fix_YYYYMMDD_NN_slug.sql
            fix_stem = f"asm_omf_fix_{stamp}_{sequence:02d}_{slug}"
        else:
            # Format: asm_walk_YYYYMMDD_slug.txt (no sequence)
            walk_stem = f"asm_walk_{stamp}_{slug}"
            # Format: asm_omf_fix_YYYYMMDD_slug.sql (no sequence)
            fix_stem = f"asm_omf_fix_{stamp}_{slug}"
    
        return base / f"{walk_stem}.txt", base / f"{fix_stem}.sql"

    def asmcmd_bin(self) -> str:
        """Absolute path to asmcmd binary on the remote or local host.
    
        Constructs the full filesystem path to the asmcmd utility by combining
        the grid_home directory from the host configuration with '/bin/asmcmd'.
        The grid_home path is normalized by removing any trailing slashes before
        concatenation to ensure a consistent path format.
    
        Returns:
            str: Full absolute path to the asmcmd executable (e.g., '/u01/app/grid/bin/asmcmd').
        """
        # Remove trailing slashes from grid_home for consistent path formatting
        gh = self.host_config.grid_home.rstrip("/")
        # Construct and return the full path to the asmcmd binary
        return f"{gh}/bin/asmcmd"

    def run_local_shell_command(self, cmd: str) -> list[ASMLine]:
        """Execute a shell command locally and return stdout lines."""
        result = subprocess.run(
            cmd,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.splitlines()

    def run_remote_shell_command(self, cmd: str) -> list[ASMLine]:
        """Execute a shell command remotely via SSH and return stdout lines.
    
        Wraps the command with the host's Grid Infrastructure environment setup (oraenv)
        and executes it via Fabric's SSH connection. Automatically replaces 'asmcmd' prefix
        with the full binary path from grid_home. Returns empty list on command failure.
    
        Args:
            cmd (str): Shell command to execute remotely (e.g., 'asmcmd ls +DATA').
    
        Returns:
            list[ASMLine]: List of stdout lines from command execution, empty list on failure.
    
        Raises:
            RuntimeError: If SSH connection or host profile is not configured for this session.
        """
        # Verify SSH connection and host profile are available
        if self.connection is None or self.host_config is None:
            raise RuntimeError("Remote command execution requires SSH connection and host profile.")

        # Replace 'asmcmd' prefix with full binary path from grid_home
        stripped = cmd.lstrip()
        if stripped.startswith("asmcmd "):
            # Extract command arguments after 'asmcmd ' prefix
            rest = stripped[len("asmcmd "):]
            # Build command with absolute path to asmcmd binary
            adapted = f"{self.asmcmd_bin()} {rest}"
        else:
            # Use command as-is if not an asmcmd invocation
            adapted = cmd

        # Wrap command with Grid Infrastructure environment setup (oraenv sourcing)
        script = self.host_config.wrap_remote_grid_command(adapted)
        # Quote script for safe execution via bash -lc (login shell)
        wrapped = f"bash -lc {shlex.quote(script)}"

        # Log wrapper command metadata in debug mode
        self.debug(
            f"remote run: outer_cmd_chars={len(wrapped)}; script_lines={script.count(chr(10)) + 1}"
        )

        # Show script preview in debug mode (truncate if too long)
        if self._debug:
            preview = script if len(script) <= 1200 else script[:1200] + "\n... [truncated]"
            self.debug(f"remote run script preview:\n{preview}")

        # Execute wrapped command via Fabric SSH connection
        result = self.connection.run(wrapped, hide=True, warn=True)

        # Handle command failure (non-zero exit code)
        if result.failed:
            self.debug(f"remote run failed ok={result.ok!r} exited={getattr(result, 'exited', None)!r}")
            if result.stderr:
                self.debug(f"remote stderr:\n{result.stderr.strip()}")
            # Return empty list on failure rather than raising exception
            return []
    
        # Split stdout into lines and return
        lines = (result.stdout or "").splitlines()
        self.debug(f"remote run ok, stdout_lines={len(lines)}")
        return lines

    def run_shell_command(self, cmd: str) -> list[ASMLine]:
        """Execute command locally or remotely based on session mode.
    
        Routes the shell command to either local subprocess execution or remote SSH
        execution via Fabric, depending on whether the session has an active SSH
        connection and host configuration. The method automatically detects the
        appropriate execution mode from the session state.
    
        Args:
            cmd (str): Shell command to execute (e.g., 'asmcmd ls +DATA').
    
        Returns:
            list[ASMLine]: List of stdout lines from the command execution.
    
        Raises:
            RuntimeError: If session is in an invalid state (connection and host_config
                must be both set for SSH mode or both unset for local mode).
    
        Note:
            Valid session states:
            - SSH mode: Both _connection and host_config are set (non-None)
            - Local mode: Both _connection and host_config are unset (None)
            Invalid states raise RuntimeError to prevent configuration errors.
        """
        # SSH mode: Execute command remotely via Fabric connection
        if self.connection is not None and self.host_config is not None:
            return self.run_remote_shell_command(cmd)

        # Local mode: Execute command via subprocess on current machine
        if self.connection is None and self.host_config is None:
            return self.run_local_shell_command(cmd)

        # Invalid state: Only one of connection/host_config is set
        raise RuntimeError(
            "run_shell_command needs SSH (connection + host profile) or local mode (both unset)."
        )

    def file_access_snapshot(self, conn: Connection) -> list[tuple[str, str]]:
        """Execute asmcmd lsof via SSH and return file access information.

        Runs the 'asmcmd lsof' command on the remote Grid Infrastructure host through
        the provided Fabric SSH connection. The command is wrapped with Grid environment
        setup (oraenv sourcing) to ensure asmcmd has the correct ORACLE_HOME context.
        Parses the output to extract ASM file paths from the lsof listing.

        Args:
            conn (Connection): Active Fabric SSH connection to the Grid Infrastructure host.

        Returns:
            list[tuple[str, str]]: List of (full_lsof_line, asm_path) tuples. Each tuple contains the complete lsof output line and the extracted ASM file path (last whitespace-separated field). Returns empty list on command failure or OS errors.

        Note:
            The method skips the first line of lsof output (column headers). Each subsequent
            line is split on whitespace, and the last field is extracted as the ASM path.
            Command failures (non-zero exit, OSError) are logged via debug() and return
            an empty list rather than raising exceptions.
        """
        try:
            # Construct the asmcmd lsof command with full binary path
            lsof_line = f"{self.asmcmd_bin()} lsof"
            # Wrap command with Grid Infrastructure environment setup (oraenv sourcing)
            script = self.host_config.wrap_remote_grid_command(lsof_line)
            # Quote script for safe execution via bash login shell
            wrapped = f"bash -lc {shlex.quote(script)}"
            # Log wrapper script metadata in debug mode
            self.debug(f"file_access_snapshot: lsof script chars={len(script)}")
            # Show full script or truncated preview based on length
            if self._debug and len(script) <= 1200:
                self.debug(f"file_access_snapshot script:\n{script}")
            elif self._debug:
                self.debug(f"file_access_snapshot script (truncated):\n{script[:1200]}\n...")
            # Execute wrapped command via Fabric SSH connection
            result = conn.run(wrapped, hide=True, warn=True)
        except OSError as exc:
            # Handle OS-level errors (network issues, SSH failures, etc.)
            print(f"Error running asmcmd: {exc}")
            self.debug(f"file_access_snapshot: OSError {exc!r}")
            return []

        # Check if command execution failed (non-zero exit code)
        if result.failed:
            print("asmcmd failed:")
            print(result.stderr)
            self.debug(
                "file_access_snapshot: command failed "
                f"ok={result.ok!r} exited={getattr(result, 'exited', None)!r}"
            )
            return []

        # Split stdout into lines for parsing
        lines = result.stdout.splitlines()
        if not lines:
            return []

        # Parse lsof output: skip header line, extract path from each data row
        entries: list[tuple[str, str]] = []
        for line in lines[1:]:  # Skip first line (column headers)
            # Split line on whitespace to extract fields
            parts = line.split()
            if parts:
                # Last field is the ASM file path
                path = parts[-1]
                # Store full line and extracted path as tuple
                entries.append((line, path))
        return entries

    def monitor_file_access(self, conn: Connection) -> bool:
        """Monitor file access via lsof and detect violations outside allowed DB/disk group paths.
    
        Executes multiple lsof polling attempts at configured intervals to detect ASM file
        access outside the allowed disk_group/database prefixes. Each attempt checks all
        open ASM file handles against the configured databases and disk groups, reporting
        any violations to stdout. CRS disk group paths are automatically excluded from
        violation checks.
    
        Args:
            conn (Connection): Active Fabric SSH connection to the Grid Infrastructure host.
    
        Returns:
            bool: True if no violations are detected across all monitoring attempts, False otherwise.
    
        Raises:
            Exception: Re-raises any exception from databases/disk_groups property access.
        """
        # Get monitoring configuration from host profile
        monitor_count = self.host_config.monitor_count
        monitor_interval = self.host_config.monitor_interval
        self.debug(
            "monitor_file_access: starting "
            f"monitor_count={monitor_count} interval={monitor_interval}s"
        )

        # Retrieve databases and disk groups for this host (may raise on misconfiguration)
        try:
            dbs = self.databases
            dgs = self.disk_groups
        except Exception as exc:
            self.debug(f"monitor_file_access: could not read databases/disk_groups: {exc!r}")
            raise

        # Build allowed path prefixes from all disk_group × database combinations
        prefixes = [f"{dg}/{db}" for db in dbs for dg in dgs]
        self.debug(f"monitor_file_access: disk_groups={dgs!r}")
        self.debug(f"monitor_file_access: databases_used={dbs!r}")
        self.debug(f"monitor_file_access: allow_prefixes={prefixes!r}")

        # Execute monitoring attempts at configured interval
        for i in range(monitor_count):
            print(f"Monitoring attempt {i + 1} of {monitor_count}...")
            violations = False

            # Get current snapshot of open ASM file handles via lsof
            entries = self.file_access_snapshot(conn)
            self.debug(f"monitor_file_access: attempt {i + 1} lsof rows={len(entries)}")

            # Check each open file against allowed prefixes
            for line, path in entries:
                # Skip empty paths from malformed lsof output
                if not path:
                    continue

                # Exclude CRS disk group paths (Grid Infrastructure cluster files)
                if self.asm_path_is_crs(path):
                    continue

                allowed = False

                # Check if path matches any allowed disk_group/database prefix
                for db in dbs:
                    for dg in dgs:
                        # Normalize prefix to match ASM path comparison rules
                        prefix = AsmCleanup.normalize_asm_path(f"{self._normalize_disk_group_token(dg)}/{db}")
                        # Use case-insensitive prefix matching for ASM paths
                        if self.asm_path_prefix_match(path, prefix):
                            allowed = True
                            break
                    # Break outer loop once we find a matching prefix
                    if allowed:
                        break

                # Report violation: file open outside allowed database paths
                if not allowed:
                    print(f"VIOLATION: {line}")
                    self.debug(f"monitor_file_access: violation path={path!r}")
                    violations = True

            # Return immediately on first violation detection
            if violations:
                return False
    
            # Sleep between attempts (skip after final attempt)
            if i < monitor_count - 1:
                time.sleep(monitor_interval)
    
        # No violations detected across all monitoring attempts
        return True

    @classmethod
    @contextmanager
    def ssh(
            cls,
            config_path: str | Path,
            host_id: str,
            *,
            databases: list[str] | tuple[str, ...] | None = None,
            debug: bool = False,
    ) -> Iterator[AsmCleanup]:
        """Open SSH connection using YAML host profile and yield AsmCleanup instance.

        Creates an SSH connection to the Grid Infrastructure host specified by host_id
        in the YAML configuration file. The connection is wrapped in a context manager
        that automatically closes the SSH session on exit. The method loads the host
        profile from the YAML file, establishes the Fabric SSH connection with configured
        authentication, and yields an initialized AsmCleanup instance.

        Args:
            config_path (str | Path): Path to YAML configuration file containing host profiles.
            host_id (str): Host identifier key under asm.hosts in the YAML file (e.g., 'lab').
            databases (list[str] | tuple[str, ...] | None): Optional filter to restrict operations to specific databases.
            debug (bool): Enable debug logging output to stdout.

        Yields:
            Iterator[AsmCleanup]: Configured AsmCleanup instance with active SSH connection and host profile.

        Raises:
            FileNotFoundError: If config_path does not exist.
            KeyError: If host_id is not found in the YAML asm.hosts section.
            ValueError: If YAML structure is invalid or required fields are missing.

        Note:
            The SSH connection uses authentication parameters from the host profile's
            connect_kwargs field (typically SSH key path). The connection is automatically
            closed when exiting the context manager, ensuring proper resource cleanup.
        """

        # Log initial connection parameters in debug mode
        if debug:
            print(
                f"[AsmCleanup:debug] ssh: loading {config_path!r} host_id={host_id!r} "
                f"databases={databases!r}",
                flush=True,
            )

        # Load YAML configuration file and extract host profile
        root = AsmConfigFile.load(config_path)
        profile = root.get_host(host_id)

        # Log resolved connection details in debug mode
        if debug:
            print(
                f"[AsmCleanup:debug] ssh: resolved ssh_host={profile.host!r} user={profile.user!r} "
                f"grid_home={profile.grid_home!r} yaml_databases={profile.databases!r}",
                flush=True,
            )

        # Establish SSH connection using Fabric with host profile settings
        with Connection(
                host=profile.host,
                user=profile.user,
                connect_kwargs=profile.connect_kwargs,
        ) as conn:
            # Yield configured AsmCleanup instance with active connection
            yield cls(
                profile,
                connection=conn,
                host_id=host_id,
                database_filter=databases,
                debug=debug,
            )

    @classmethod
    @contextmanager
    def local(cls, *, debug: bool = False) -> Iterator[AsmCleanup]:
        """Create a local AsmCleanup session without SSH or YAML configuration.

        This context manager initializes an AsmCleanup instance that executes asmcmd
        commands directly on the local machine via subprocess. No YAML host profile or
        SSH connection is required. Useful for running ASM operations on the Grid
        Infrastructure host itself.

        Args:
            debug (bool): Enable debug logging output to stdout.

        Yields:
            Iterator[AsmCleanup]: Configured AsmCleanup instance for local asmcmd execution.

        Note:
            Local mode requires explicit asm_path argument when calling run() since there
            is no YAML configuration to infer disk groups or databases from.
        """
        # Log session initialization in debug mode
        if bool(debug):
            print("[AsmCleanup:debug] local: no YAML host profile", flush=True)
        # Yield AsmCleanup instance with no host_config or connection (local mode)
        yield cls(debug=debug)

    def walk_directory(
            self,
            path: str,
            out_lines: list[str],
            *,
            on_scan: Callable[[int, str], None] | None = None,
    ) -> None:
        """Recursively walk an ASM directory tree using asmcmd.

        Traverses the specified ASM directory path and all subdirectories, collecting
        file listings via 'asmcmd ls -l' commands. Output lines from each directory are
        appended to the out_lines list. Automatically routes commands through SSH or local
        subprocess based on session mode (determined by presence of connection/host_config).

        Args:
            path (str): ASM directory path to walk (e.g., '+DATA/MYDB/DATAFILE').
            out_lines (list[str]): Mutable list to append asmcmd output lines to.
            on_scan (Callable[[int, str], None] | None): Optional callback invoked after scanning each directory with (file_count, directory_path).

        Returns:
            None

        Note:
            - Paths starting with '+' are normalized via normalize_asm_path before walking
            - The method uses run_shell_command which automatically handles SSH vs local execution
            - Subdirectories (entries ending with '/') are recursively traversed
            - File counts passed to on_scan callback only include DATAFILE/TEMPFILE rows
        """
        # Strip whitespace from input path
        root = path.strip()
        # Normalize ASM paths (uppercase disk group and intermediate directories)
        if root.startswith("+"):
            root = self.normalize_asm_path(root)
        # Delegate to internal recursive walker
        self._walk_directory_with_runner(root, out_lines, on_scan=on_scan)

    def _walk_directory_with_runner(
            self,
            path: str,
            out_lines: list[str],
            *,
            on_scan: Callable[[int, str], None] | None = None,
    ) -> None:
        """Recursively walk an ASM directory tree using this session command runner.

        Args:
            path (str): ASM directory path to walk (e.g., '+DATA/MYDB/DATAFILE').
            out_lines (list[str]): Mutable list to append asmcmd output lines to.
            on_scan (Callable[[int, str], None] | None): Optional callback invoked after scanning each directory with (file_count, directory_path).

        Returns:
            None
        """
        # Append directory header to output lines
        out_lines.append(f"\nDIR: {path}")
        out_lines.append("-" * 60)

        # Execute 'asmcmd ls -l' to get detailed file listing for current directory
        lines: list[ASMLine] = self.run_shell_command(f"asmcmd ls -l {path} 2>/dev/null")
        # Append all listing lines to output
        out_lines.extend(lines)
        # Invoke callback with count of DATAFILE/TEMPFILE rows if provided
        if on_scan:
            scanned_here = AsmCleanup._count_data_tempfile_rows(lines)
            if scanned_here:
                on_scan(scanned_here, path)

        # Execute 'asmcmd ls' to get list of subdirectories
        entries: list[ASMLine] = self.run_shell_command(f"asmcmd ls {path} 2>/dev/null")

        # Recursively walk each subdirectory
        for entry in entries:
            entry = entry.strip()
            # Check if entry is a directory (ends with '/')
            if entry.endswith("/"):
                # Remove trailing slash and construct full subdirectory path
                subdir: ASMPath = f"{path}/{entry[:-1]}"
                # Recursively walk the subdirectory with same parameters
                self._walk_directory_with_runner(
                    subdir,
                    out_lines,
                    on_scan=on_scan,
                )

    @staticmethod
    def normalize_asm_path(path: str) -> str:
        """Normalize ASM path for walk roots and YAML-derived paths.

        Uppercases the disk group token and intermediate directory segments while
        preserving the final path segment (often a file name). This method is
        intended for walk roots and YAML-derived paths only. Do NOT use this for
        ``asmcmd ls`` paths passed to ``ALTER DATABASE MOVE DATAFILE`` - Oracle
        matches the source string with dictionary casing. For path comparisons,
        use :meth:`asm_path_prefix_match` which performs case-insensitive matching.

        Args:
            path (str): ASM path to normalize (e.g., '+data/mydb/datafile/file.dbf').

        Returns:
            str: Normalized path with uppercase disk group and intermediate directories (e.g., '+DATA/MYDB/datafile/file.dbf').

        Note:
            Non-ASM paths (not starting with '+') are returned unchanged.
        """
        # Return non-ASM paths unchanged (no leading '+' prefix)
        if not path.startswith("+"):
            return path

        # Split path on '/' to extract individual segments
        parts = path.split("/")

        # Initialize result list with uppercase disk group (first segment)
        normalized: list[str] = [parts[0].upper()]

        # Uppercase all intermediate directory segments (exclude first and last)
        for part in parts[1:-1]:
            normalized.append(part.upper())

        # Append final segment unchanged if path has more than one segment
        # (preserves file name casing)
        if len(parts) > 1:
            normalized.append(parts[-1])

        # Rejoin segments with '/' separator and return normalized path
        return "/".join(normalized)

    _GUID32 = re.compile(r"^[0-9A-Fa-f]{32}$")

    @classmethod
    def pdb_guid_from_asm_alias_path(cls, asm_path: str) -> str | None:
        """Extract 32-character PDB directory GUID from ASM path if present.

        Oracle stores pluggable database files under the hierarchy
        ``+DISKGROUP/DB_UNIQUE_NAME/<GUID>/DATAFILE|TEMPFILE/...``. This method
        searches for the 32-hex GUID directory in the path. Paths without a GUID
        directory (direct structure like ``.../DB_UNIQUE_NAME/DATAFILE``) indicate
        CDB$ROOT files and return None.

        Args:
            cls: Class reference for accessing _GUID32 pattern.
            asm_path (str): ASM path to parse (e.g., '+DATA/MYDB/49C96937E332EB45E0631A04010ABA14/DATAFILE/file.dbf').

        Returns:
            str | None: Uppercase 32-hex GUID if path contains PDB directory, None for CDB$ROOT paths.

        Note:
            The method splits the path on '/' and searches for DATAFILE or TEMPFILE segments.
            When found, it checks if the preceding segment matches the 32-hex pattern (case-insensitive).
            The GUID is normalized to uppercase before returning.
        """
        # Split path on '/' delimiter to extract individual segments
        parts = asm_path.strip().split("/")

        # Iterate through path segments with index to access adjacent segments
        for i, seg in enumerate(parts):
            # Check if current segment is DATAFILE or TEMPFILE (case-insensitive)
            if seg.upper() in ("DATAFILE", "TEMPFILE") and i > 0:
                # Get the segment immediately before DATAFILE/TEMPFILE
                prev = parts[i - 1]

                # Check if previous segment matches 32-hex GUID pattern
                if cls._GUID32.match(prev):
                    # Return uppercase normalized GUID for PDB path
                    return prev.upper()

                # DATAFILE/TEMPFILE found but no GUID before it (CDB$ROOT structure)
                return None

        # No DATAFILE/TEMPFILE segment found in path
        return None

    @staticmethod
    def _sql_alter_session_set_container(pdb_name: str) -> str:
        """Generate ALTER SESSION SET CONTAINER SQL statement with identifier quoting.

        Creates an ALTER SESSION statement to switch the current session's container
        to the specified pluggable database. Automatically quotes identifiers that
        contain special characters or don't follow Oracle's simple naming rules.

        Args:
            pdb_name (str): Target PDB name to switch container to (e.g., 'TOOLKITPDB' or 'CDB$ROOT').

        Returns:
            str: Complete ALTER SESSION SET CONTAINER SQL statement with proper identifier quoting.

        Note:
            Simple identifiers (alphanumeric, $, #, _ starting with letter) are used unquoted.
            Complex identifiers are double-quoted with internal quotes escaped per Oracle rules.
        """
        # Check if PDB name follows Oracle's simple identifier rules (no quoting needed)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", pdb_name):
            # Use unquoted identifier for simple names
            clause = pdb_name
        else:
            # Quote complex identifiers and escape internal double quotes
            clause = '"' + pdb_name.replace('"', '""') + '"'
        # Return complete ALTER SESSION statement with normalized container clause
        return f"ALTER SESSION SET CONTAINER = {clause};"

    @staticmethod
    def extract_aliases(lines: list[str]) -> list[AliasEntry]:
        """Extract DATAFILE and TEMPFILE alias mappings from walk transcript lines.

        Parses asmcmd walk output to identify files with alias mappings (lines containing '=>').
        Tracks the current directory context from 'DIR:' lines and constructs full source paths
        by combining directory prefix with filenames. Extracts PDB GUID from ASM path structure
        when present (32-hex directory segment before DATAFILE/TEMPFILE).

        Args:
            lines (list[str]): Walk transcript lines from asmcmd ls -l output with 'DIR:' headers.

        Returns:
            list[AliasEntry]: List of (file_type, source_path, target_path, pdb_guid) tuples with duplicates removed (case-insensitive deduplication on file_type|source|target).
        """
        # Store extracted alias entries
        results: list[AliasEntry] = []
        # Track processed entries for deduplication (case-insensitive keys)
        seen: set[str] = set()

        # Track the current directory context from 'DIR:' lines
        current_dir: str | None = None

        # Regex to match alias lines: 'DATAFILE filename => +TARGET'
        pattern = re.compile(r"(DATAFILE|TEMPFILE).*?\s(\S+)\s*=>\s*(\+\S+)")

        # Process each line from the walk transcript
        for line in lines:
            line = line.strip()

            # Update directory context when encountering 'DIR:' header lines
            if line.startswith("DIR:"):
                current_dir = line.replace("DIR:", "").strip()
                continue

            # Search for alias mapping pattern in current line
            match = pattern.search(line)
            if match and current_dir:
                # Extract file type (DATAFILE or TEMPFILE)
                file_type: str = match.group(1)
                # Extract filename (source alias name)
                filename: str = match.group(2)
                # Extract target path (OMF ASM path with leading '+')
                target: str = match.group(3).strip()

                # Construct full source path from current directory + filename
                base = current_dir.rstrip("/")
                full_source = f"{base}/{filename}".replace("//", "/")

                # Extract PDB GUID from source or target path (32-hex directory segment)
                pdb_guid = AsmCleanup.pdb_guid_from_asm_alias_path(
                    full_source
                ) or AsmCleanup.pdb_guid_from_asm_alias_path(target)

                # Create deduplication key (case-insensitive comparison of all components)
                dedupe_key = f"{file_type}|{full_source.casefold()}|{target.casefold()}"
                if dedupe_key not in seen:
                    # Mark as processed and add to results
                    seen.add(dedupe_key)
                    results.append((file_type, full_source, target, pdb_guid))

        return results

    @staticmethod
    def summarize_walk_stats(lines: list[str]) -> tuple[int, int]:
        """Return files examined and alias row counts from walk transcript lines.
    
        Counts DATAFILE/TEMPFILE rows from ``asmcmd ls -l`` output as "files examined".
        Rows containing ``=>`` are counted as alias rows.
    
        Args:
            lines (list[str]): Walk transcript lines from asmcmd ls -l output.
    
        Returns:
            tuple[int, int]: (files_examined, alias_rows) counts from transcript.
        """
        # Initialize counters for files and alias mappings
        files_examined = 0
        alias_rows = 0

        # Process each line from the walk transcript
        for line in lines:
            text = line.strip()
            # Skip lines that don't match DATAFILE/TEMPFILE pattern
            if not _DATA_TEMPFILE_ROW.match(text):
                continue
            # Count all DATAFILE/TEMPFILE rows as examined files
            files_examined += 1
            # Count rows with '=>' as alias mappings
            if "=>" in text:
                alias_rows += 1
    
        return files_examined, alias_rows

    @staticmethod
    def _count_data_tempfile_rows(lines: list[str]) -> int:
        """Count DATAFILE/TEMPFILE rows in an asmcmd ls -l listing.

        Args:
            lines (list[str]): Lines from asmcmd ls -l output.

        Returns:
            int: Number of lines matching DATAFILE or TEMPFILE pattern.
        """
        # Count lines that match the DATAFILE/TEMPFILE regex pattern
        return sum(1 for line in lines if _DATA_TEMPFILE_ROW.match(line.strip()))

    @staticmethod
    def generate_fix_script(
            entries: list[AliasEntry],
            *,
            pdb_guid_map: dict[str, str] | None = None,
    ) -> str:
        """Generate SQL for OMF-based file migration with PDB container switching.

        Creates ALTER DATABASE MOVE DATAFILE/TEMPFILE statements to migrate ASM alias
        files to Oracle-Managed Files (OMF) format in the +DATA disk group. When a
        pdb_guid_map is provided from YAML configuration, automatically inserts ALTER
        SESSION SET CONTAINER statements when the resolved PDB changes between
        consecutive file operations.

        Args:
            entries (list[AliasEntry]): List of (file_type, source_path, target_path, pdb_guid) tuples.
            pdb_guid_map (dict[str, str] | None): Maps ASM PDB directory GUIDs to PDB names.

        Returns:
            str: Complete SQL script with container switching and file migration statements.

        Note:
            - CDB$ROOT files (no GUID in ASM path) use ALTER SESSION SET CONTAINER = CDB$ROOT
              only when switching from another container
            - Unmapped GUIDs generate SQL comments instead of ALTER SESSION statements
            - All GUID keys are normalized to uppercase for case-insensitive matching
            - Initial container context is '__INIT__' to handle first statement correctly
        """
        # Normalize GUID keys to uppercase for case-insensitive lookups
        guid_to_name = {k.upper(): v for k, v in (pdb_guid_map or {}).items()}

        def resolved_container(guid: str | None) -> str:
            """Resolve PDB GUID to container name or special marker.

            Args:
                guid (str | None): 32-hex PDB GUID or None for CDB$ROOT.

            Returns:
                str: PDB name, 'CDB$ROOT', or 'UNMAPPED:<guid>' marker.
            """
            # No GUID indicates CDB$ROOT file structure
            if not guid:
                return "CDB$ROOT"
            # Look up GUID in the provided mapping
            if guid in guid_to_name:
                return guid_to_name[guid]
            # Mark unmapped GUIDs for comment generation
            return f"UNMAPPED:{guid}"

        # Initialize SQL statement list
        sql: list[str] = []
        # Track last container to detect transitions (special init marker)
        last_container: str | None = "__INIT__"

        # Process each alias entry to generate migration SQL
        for file_type, source, target, pdb_guid in entries:
            # Resolve PDB container for this file
            label = resolved_container(pdb_guid)

            # Check if container transition is needed
            if label != last_container:
                if label.startswith("UNMAPPED:"):
                    # Extract GUID from UNMAPPED marker
                    g = label.split(":", 1)[1]
                    # Add comment with GUID lookup hint instead of ALTER SESSION
                    sql.append(
                        f"-- PDB ASM GUID {g} not in pdb_guid_map; add asm.hosts.<id>.pdb_guid_map then regenerate.\n"
                        f"-- Hint: SELECT name, guid FROM v$pdbs;"
                    )
                else:
                    # Skip ALTER SESSION for initial CDB$ROOT context
                    if last_container == "__INIT__" and label == "CDB$ROOT":
                        pass
                    else:
                        # Insert container switch statement
                        sql.append(AsmCleanup._sql_alter_session_set_container(label))
                # Update container tracking state
                last_container = label

            # Generate DATAFILE migration SQL block
            if file_type == "DATAFILE":
                sql.append(
                    f"""-- =========================================================
-- FIX DATAFILE
-- Source: {source}
-- Target: {target}
-- =========================================================
ALTER DATABASE MOVE DATAFILE '{source}' TO '+DATA';
""".strip()
                )

            # Generate TEMPFILE migration SQL block
            elif file_type == "TEMPFILE":
                sql.append(
                    f"""-- =========================================================
-- FIX TEMPFILE
-- Source: {source}
-- Target: {target}
-- =========================================================
ALTER DATABASE MOVE TEMPFILE '{source}' TO '+DATA';
""".strip()
                )

        # Join all SQL statements with double newline separator
        return "\n\n".join(sql)
    
    @staticmethod
    def default_walk_output_paths(asm_path: str) -> tuple[Path, Path]:
        """Generate default walk transcript and fix script paths under the log directory.

        Creates standardized filenames using current date stamp and a filesystem-safe slug
        derived from the ASM path. Output files are placed under DEFAULT_LOG_DIR.

        Args:
            asm_path (str): ASM path to derive the filename slug from (e.g., '+DATA/MYDB').

        Returns:
            tuple[Path, Path]: Pair of (walk_transcript_path, fix_script_path) under DEFAULT_LOG_DIR.
        """
        # Generate date stamp in YYYYMMDD format for current date
        date = datetime.datetime.now().strftime("%Y%m%d")
        # Convert ASM path to filesystem-safe slug (removes '+', replaces slashes)
        slug = AsmCleanup._asm_path_slug(asm_path)
        # Base directory for all log outputs
        base = DEFAULT_LOG_DIR
        # Return tuple of walk transcript and fix script paths with date and slug
        return (
            base / f"asm_walk_{date}_{slug}.txt",
            base / f"asm_omf_fix_{date}_{slug}.sql",
        )

    def _run_walk_pipeline(
            self,
            asm_path: str,
            *,
            no_walk: bool,
            no_analyze: bool,
            no_fix: bool,
            outfile: Path,
            fixfile: Path,
    ) -> WalkResult:
        """Execute the walk/analyze/fix pipeline for a single ASM path.

        Orchestrates the three-phase pipeline: walk the ASM directory tree to collect
        file listings, analyze the output to extract alias mappings, and generate SQL
        fix scripts for migrating alias files to OMF format. Each phase can be skipped
        via boolean flags.

        Args:
            asm_path (str): ASM path to walk and analyze.
            no_walk (bool): Skip the walk phase if True.
            no_analyze (bool): Skip the analyze phase if True.
            no_fix (bool): Skip the fix SQL generation phase if True.
            outfile (Path): Target path for the walk transcript output.
            fixfile (Path): Target path for the generated SQL fix script.

        Returns:
            WalkResult: Outcome summary with paths, counts, and processing status.

        Note:
            Creates parent directories for outfile and fixfile if they don't exist.
            The fix phase only generates SQL when aliases are found in the analyze phase.
            PDB GUID mappings are loaded from host_config when available for container switching.
        """
        # Initialize storage for walk output lines and extracted alias entries
        out_lines: list[ASMLine] = []
        aliases: list[AliasEntry] = []
        fix_written = False

        # Ensure output directories exist before writing files
        outfile.parent.mkdir(parents=True, exist_ok=True)
        fixfile.parent.mkdir(parents=True, exist_ok=True)

        # Format abbreviated path for progress display (disk group + first subdirectory)
        display_path = self.format_scan_path(asm_path)

        # Phase 1: Walk the ASM directory tree and collect file listings
        if not no_walk:
            print("  Walking ASM...")
            print(f"    Scanning: {display_path}")
            self.debug(f"_run_walk_pipeline: walk asm_path={asm_path!r} outfile={outfile}")
            # Recursively traverse ASM path and collect asmcmd ls -l output
            self.walk_directory(asm_path, out_lines)
            # Write walk transcript to disk for later review
            outfile.write_text("\n".join(out_lines))

        # Phase 2: Analyze walk output to extract alias mappings
        if not no_analyze:
            if out_lines:
                # Parse DATAFILE/TEMPFILE rows with '=>' alias mappings
                aliases = self.extract_aliases(out_lines)

        # Phase 3: Generate SQL fix script from extracted aliases
        if not no_fix:
            if aliases:
                # Load PDB GUID mappings from YAML host config if available
                pdb_map: dict[str, str] = {}
                if self.host_config is not None:
                    # Normalize GUID keys to uppercase for case-insensitive lookups
                    pdb_map = {k.upper(): v for k, v in self.host_config.pdb_guid_map.items()}
                # Generate ALTER DATABASE MOVE statements with container switching
                sql = self.generate_fix_script(aliases, pdb_guid_map=pdb_map)
                # Write SQL script to disk
                fixfile.write_text(sql)
                fix_written = True

        # Calculate summary statistics from walk transcript
        files_examined, alias_rows = self.summarize_walk_stats(out_lines)

        # Return structured outcome with all paths and counts
        return WalkResult(
            asm_path=asm_path,
            display_path=display_path,
            outfile=outfile,
            fixfile=fixfile,
            files_examined=files_examined,
            alias_rows=alias_rows,
            unique_aliases=len(aliases),
            fix_written=fix_written,
        )

    def run(
        self,
        asm_path: str | None = None,
        *,
        no_walk: bool = False,
        no_analyze: bool = False,
        no_fix: bool = False,
        outfile: Path | None = None,
        fixfile: Path | None = None,
    ) -> None:
        """Execute walk/analyze/fix pipeline for ASM paths with optional phase skipping.

        With a YAML host profile and no asm_path: walks every disk_group × database
        path (see run_all_configured_paths). Disk groups come from YAML disk_groups or,
        when omitted, are auto-discovered from ASM. If default_asm_path is set in YAML, only
        that single path is walked instead.

        With an explicit asm_path: one walk. outfile/fixfile are optional for that
        case; when walking all configured paths they must be omitted (each path gets its own
        files under DEFAULT_LOG_DIR, typically logs/). Local mode always requires asm_path.

        Args:
            asm_path (str | None): Explicit ASM path to walk (e.g., '+DATA/MYDB').
            no_walk (bool): Skip the ASM directory tree walk phase if True.
            no_analyze (bool): Skip the alias extraction analysis phase if True.
            no_fix (bool): Skip the SQL fix script generation phase if True.
            outfile (Path | None): Target path for walk transcript output.
            fixfile (Path | None): Target path for generated SQL fix script.

        Returns:
            None

        Raises:
            ValueError: If outfile/fixfile are set without explicit asm_path.
            ValueError: If local mode is used without explicit asm_path.
        """
        # Check if an explicit asm_path was provided (non-None and non-empty)
        explicit = asm_path is not None and bool(asm_path.strip())
        if explicit:
            # Single-path workflow: walk the explicitly provided ASM path
            raw = asm_path.strip()
            # Normalize ASM paths starting with '+' to uppercase disk group
            resolved = self.normalize_asm_path(raw) if raw.startswith("+") else raw
            # Generate default output paths if not explicitly provided
            if outfile is None or fixfile is None:
                default_out, default_fix = self.default_walk_output_paths(resolved)
                outfile = outfile or default_out
                fixfile = fixfile or default_fix
            # Execute the walk/analyze/fix pipeline for the single path
            result = self._run_walk_pipeline(
                resolved,
                no_walk=no_walk,
                no_analyze=no_analyze,
                no_fix=no_fix,
                outfile=outfile,
                fixfile=fixfile,
            )
            # Print report for the single path
            self.print_report(path_results=[result])
            return

        # Validate that outfile/fixfile are only used with explicit asm_path
        if outfile is not None or fixfile is not None:
            raise ValueError(
                "outfile and fixfile may only be set together with an explicit asm_path=. "
                "When asm_path is omitted, every configured path is walked and output files are "
                "chosen per path (or use default_asm_path in YAML for a single path)."
            )
        # Validate that local mode (no YAML profile) requires explicit asm_path
        if self.host_config is None:
            raise ValueError(
                "local mode requires asm_path= (there is no YAML host profile to expand)."
            )
        # Check if YAML default_asm_path is configured (single-path alternative)
        default_only = bool(self.host_config.default_asm_path and self.host_config.default_asm_path.strip())
        if default_only:
            # Single-path workflow using YAML default_asm_path setting
            raw = self.host_config.default_asm_path.strip()
            # Normalize ASM paths starting with '+' to uppercase disk group
            resolved = self.normalize_asm_path(raw) if raw.startswith("+") else raw
            self.debug(f"run: single walk from yaml default_asm_path={resolved!r}")
            # Generate default output paths based on resolved path
            default_out, default_fix = self.default_walk_output_paths(resolved)
            # Execute pipeline for the YAML-configured default path
            self._run_walk_pipeline(
                resolved,
                no_walk=no_walk,
                no_analyze=no_analyze,
                no_fix=no_fix,
                outfile=default_out,
                fixfile=default_fix,
            )
            return

        # Multi-path workflow: walk all disk_group × database combinations
        self.run_all_configured_paths(
            no_walk=no_walk,
            no_analyze=no_analyze,
            no_fix=no_fix,
        )

    def run_all_configured_paths(
        self,
        *,
        no_walk: bool = False,
        no_analyze: bool = False,
        no_fix: bool = False,
    ) -> None:
        """Execute walk/analyze/fix pipeline for all disk_group × database combinations.

        Iterates through all ASM paths generated from disk_group × database combinations
        (via iter_asm_walk_paths) and executes the three-phase pipeline for each path.
        Generates sequenced output files under DEFAULT_LOG_DIR with a shared date stamp.
        Prints per-path results and a final summary of all paths processed.

        Args:
            no_walk (bool): Skip the ASM directory tree walk phase if True.
            no_analyze (bool): Skip the alias extraction analysis phase if True.
            no_fix (bool): Skip the SQL fix script generation phase if True.

        Returns:
            None

        Raises:
            ValueError: If no ASM paths can be generated (empty databases list after
                filtering or no disk groups configured/discoverable from ASM).

        Note:
            Requires a YAML host profile with non-empty databases list (after
            database_filter) and either configured disk_groups or ASM-discoverable
            disk groups. Each path gets sequenced output files (00, 01, etc.) under
            DEFAULT_LOG_DIR with the current date stamp.
        """
        # Generate list of all disk_group × database ASM paths to process
        paths = self.iter_asm_walk_paths()
        # Validate that at least one path is available to walk
        if not paths:
            raise ValueError(
                "No ASM paths to walk: host needs non-empty databases in YAML (after any database_filter) "
                "and either configured disk_groups or discoverable disk groups from ASM."
            )
        # Log the number of paths to be processed
        self.debug(f"run_all_configured_paths: walking {len(paths)} path(s): {paths!r}")
        # Generate date stamp for all output files (shared across all paths)
        date = datetime.datetime.now().strftime("%Y%m%d")
        # Initialize list to collect results from all paths
        results: list[WalkResult] = []
        # Process each ASM path with a sequence number for output file naming
        for i, path in enumerate(paths):
            # Generate sequenced output file paths with date stamp
            out, fix = self.build_walk_and_fix_paths(path, date=date, sequence=i)
            # Execute the walk/analyze/fix pipeline for this path
            result = self._run_walk_pipeline(
                path,
                no_walk=no_walk,
                no_analyze=no_analyze,
                no_fix=no_fix,
                outfile=out,
                fixfile=fix,
            )
            # Store result for summary statistics
            results.append(result)
        # Print consolidated report for all processed paths
        self.print_report(path_results=results)

    @staticmethod
    def print_report(*, path_results: list[WalkResult]) -> None:
        """Print formatted report with per-path details and summary statistics.

        Outputs a multi-section report to stdout: header banner, detailed results for each
        ASM path processed (output files, statistics, status), and an overall summary with
        aggregate counts. Each path section shows walk transcript location, optional SQL
        fix file, file/alias counts, and a status indicator.

        Args:
            path_results (list[WalkResult]): Results from processing one or more ASM paths.

        Returns:
            None
        """
        # Calculate summary statistics across all processed paths
        total_paths = len(path_results)
        # Count paths that contain at least one alias mapping
        paths_with_alias = sum(1 for result in path_results if result.unique_aliases > 0)
        # Sum unique aliases found across all paths
        total_aliases = sum(result.unique_aliases for result in path_results)

        # Print report header with top banner
        print("=" * 60)
        print(" ASM Alias Discovery Report")
        print("=" * 60)
        print()

        # Print detailed section for each processed path
        for i, result in enumerate(path_results, start=1):
            # Path header with sequence number and abbreviated display path
            print(f"[PATH {i}/{total_paths}] {result.display_path}")
            print("-" * 60)

            # Output files section: walk transcript and optional SQL fix script
            print("  Output:")
            print(f"    Walk log : {result.outfile}")
            # SQL file only shown when fix script was actually generated
            if result.fix_written:
                print(f"    SQL file : {result.fixfile}")
            print()

            # Statistics section: counts from walk and analysis phases
            print("  Results:")
            print(f"    Files examined : {result.files_examined}")
            print(f"    Alias rows     : {result.alias_rows}")
            print(f"    Unique aliases : {result.unique_aliases}")
            print()

            # Status section: visual indicator of alias detection outcome
            print("  Status:")
            if result.unique_aliases > 0:
                # Green checkmark: aliases found and processed
                print("    ✔ Aliases found")
            else:
                # Red X: no aliases detected in this path
                print("    ✖ No aliases found")
            print()
            print("-" * 60)
            print()

        # Print summary section with aggregate statistics
        print("=" * 60)
        print(" Summary")
        print("=" * 60)
        print(f"  Paths scanned   : {total_paths}")
        print(f"  Paths w/alias   : {paths_with_alias}")
        print(f"  Total aliases   : {total_aliases}")

    @classmethod
    def run_asm_walk(
        cls,
        asm_path: str | None = None,
        *,
        ssh: bool,
        config: str,
        host_id: str | None = None,
        databases: list[str] | None = None,
        no_walk: bool,
        no_analyze: bool,
        no_fix: bool,
        outfile: Path | None = None,
        fixfile: Path | None = None,
        debug: bool = False,
    ) -> None:
        """Walk the ASM directory tree, analyze aliases, and generate OMF migration SQL.

        Convenience classmethod that initializes an AsmCleanup session (SSH or local) and
        executes the walk/analyze/fix pipeline. With SSH and no asm_path, walks every
        disk_group × database path (from YAML disk_groups or ASM auto-discovery) unless
        YAML sets default_asm_path (single path only). Default output paths live under
        DEFAULT_LOG_DIR.

        Args:
            asm_path (str | None): Explicit ASM path to walk (e.g., '+DATA/MYDB').
            ssh (bool): Use SSH mode with YAML host profile when True, local mode otherwise.
            config (str): Path to YAML configuration file (required for SSH mode).
            host_id (str | None): Host identifier key under asm.hosts in YAML (required for SSH).
            databases (list[str] | None): Optional filter to restrict operations to specific databases.
            no_walk (bool): Skip ASM directory tree walk phase when True.
            no_analyze (bool): Skip alias extraction analysis phase when True.
            no_fix (bool): Skip SQL fix script generation phase when True.
            outfile (Path | None): Target path for walk transcript (single-path only).
            fixfile (Path | None): Target path for SQL fix script (single-path only).
            debug (bool): Enable debug logging output to stdout.

        Returns:
            None

        Raises:
            ValueError: If outfile/fixfile are set without explicit asm_path (multi-path mode).
            ValueError: If SSH mode is used without host_id parameter.
            ValueError: If local mode is used with host_id or databases parameters.
        """
        # Determine if this is a single-path walk (explicit asm_path provided)
        single = asm_path is not None and bool(asm_path.strip())
        if single:
            # Single-path workflow: normalize the path and generate default output files
            raw_single = asm_path.strip()
            # Normalize ASM paths starting with '+' to uppercase disk group
            resolved_single = (
                cls.normalize_asm_path(raw_single) if raw_single.startswith("+") else raw_single
            )
            # Generate default output paths if not explicitly provided
            if outfile is None or fixfile is None:
                default_out, default_fix = cls.default_walk_output_paths(resolved_single)
                outfile = outfile or default_out
                fixfile = fixfile or default_fix
        else:
            # Multi-path workflow: validate that output files are not specified
            if outfile is not None or fixfile is not None:
                raise ValueError(
                    "outfile/fixfile may only be used with an explicit asm_path (single walk)."
                )

        # SSH mode: establish connection using YAML host profile
        if ssh:
            # Validate that host_id is provided for SSH mode
            if not host_id:
                raise ValueError("host_id is required when ssh=True (use --host on the CLI).")
            # Create SSH session context and execute pipeline
            with cls.ssh(config, host_id, databases=databases, debug=debug) as ac:
                ac.run(
                    asm_path,
                    no_walk=no_walk,
                    no_analyze=no_analyze,
                    no_fix=no_fix,
                    outfile=outfile,
                    fixfile=fixfile,
                )
        else:
            # Local mode: validate that SSH-specific parameters are not used
            if host_id or databases:
                raise ValueError("host_id and databases are only used with ssh=True.")
            # Create local session context and execute pipeline
            with cls.local(debug=debug) as ac:
                ac.run(
                    asm_path,
                    no_walk=no_walk,
                    no_analyze=no_analyze,
                    no_fix=no_fix,
                    outfile=outfile,
                    fixfile=fixfile,
                )

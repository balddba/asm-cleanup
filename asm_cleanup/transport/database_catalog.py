"""Shared parsers for srvctl config and sqlplus PDB/catalog output."""

from __future__ import annotations

import re

from loguru import logger

_ORACLE_HOME_RE = re.compile(r"(?im)^\s*Oracle home:\s*(.+?)\s*$")
_DB_INSTANCE_RE = re.compile(r"(?im)^\s*Database instance:\s*(\S+)\s*$")
_DB_INSTANCES_RE = re.compile(r"(?im)^\s*Database instances:\s*(.+?)\s*$")
_GUID_ROW_RE = re.compile(r"^\s*([^\s|]+)\|([0-9A-Fa-f]{32})\s*$")
_GUID32_RE = re.compile(r"^[0-9A-Fa-f]{32}$")


class DatabaseCatalogCollector:
    """Static parsers for Oracle database catalog discovery output.

    Consolidates srvctl config and v$pdbs / prefixed sqlplus row parsing used by
    host discovery and PDB GUID map collection.
    """

    @staticmethod
    def extract_srvctl_home_and_sid(
        stdout: str,
        *,
        database: str,
    ) -> tuple[str | None, str]:
        """Extract Oracle home and SID from `srvctl config database` output.

        Home may be None when the Oracle home line is missing or empty. SID falls
        back to the database unique name when no instance line is present.

        Args:
            stdout (str): Raw srvctl stdout.
            database (str): Database unique name (SID fallback when instance missing).

        Returns:
            tuple[str | None, str]: `(oracle_home_or_none, oracle_sid)`.
        """
        home_match = _ORACLE_HOME_RE.search(stdout)
        oracle_home: str | None = None
        if home_match:
            candidate = home_match.group(1).strip()
            if candidate:
                oracle_home = candidate

        sid: str | None = None
        inst = _DB_INSTANCE_RE.search(stdout)
        if inst:
            sid = inst.group(1).strip()
        else:
            multi = _DB_INSTANCES_RE.search(stdout)
            if multi:
                first = multi.group(1).split(",")[0].strip()
                if first:
                    sid = first
                    logger.debug(
                        "using first RAC instance {!r} from srvctl for database {!r}",
                        sid,
                        database,
                    )
        if not sid:
            sid = database
            logger.debug(
                "no Database instance line for {!r}; using database name as ORACLE_SID",
                database,
            )
        return oracle_home, sid

    @staticmethod
    def parse_srvctl_database_config(
        stdout: str,
        *,
        database: str,
    ) -> tuple[str, str]:
        """Parse Oracle home and SID/instance from `srvctl config database` output.

        Args:
            stdout (str): Raw srvctl stdout.
            database (str): Database unique name (SID fallback when instance missing).

        Returns:
            tuple[str, str]: `(oracle_home, oracle_sid)`.

        Raises:
            ValueError: If Oracle home cannot be parsed.
        """
        oracle_home, sid = DatabaseCatalogCollector.extract_srvctl_home_and_sid(
            stdout, database=database
        )
        if oracle_home is None:
            if not _ORACLE_HOME_RE.search(stdout):
                raise ValueError(
                    f"srvctl config database -d {database}: could not parse 'Oracle home:'"
                )
            raise ValueError(f"srvctl config database -d {database}: empty Oracle home")
        return oracle_home, sid

    @staticmethod
    def parse_vpdbs_pipe_output(stdout: str) -> dict[str, str]:
        """Parse `name|guid` rows from a quiet sqlplus v$pdbs query.

        Args:
            stdout (str): sqlplus stdout.

        Returns:
            dict[str, str]: Uppercase GUID → PDB name (excludes empty/invalid rows).

        Raises:
            ValueError: If no valid GUID rows were found.
        """
        mapping: dict[str, str] = {}
        for line in stdout.splitlines():
            parsed = DatabaseCatalogCollector.parse_name_guid_row(line)
            if parsed is None:
                continue
            name, guid = parsed
            mapping[guid] = name
        if not mapping:
            raise ValueError(
                "sqlplus v$pdbs returned no name|guid rows "
                "(is the database open? check ORACLE_SID)"
            )
        return mapping

    @staticmethod
    def parse_name_guid_row(line: str) -> tuple[str, str] | None:
        """Parse one `name|32-hex-guid` row.

        Args:
            line (str): Single sqlplus output line.

        Returns:
            tuple[str, str] | None: `(name, uppercase_guid)`, or None if not a GUID row.
        """
        text = line.strip()
        if not text or text.startswith("-"):
            return None
        match = _GUID_ROW_RE.match(text)
        if match:
            name = match.group(1).strip()
            guid = match.group(2).upper()
            if name:
                return name, guid
            return None
        if "|" not in text:
            return None
        name, _, guid = text.partition("|")
        name = name.strip()
        guid = guid.strip().upper()
        if not name or not _GUID32_RE.match(guid):
            return None
        return name, guid

    @staticmethod
    def parse_prefixed_guid_row(line: str) -> tuple[str, str] | None:
        """Parse one `GUID|name|hexguid` discovery sqlplus row.

        Args:
            line (str): Single prefixed sqlplus output line.

        Returns:
            tuple[str, str] | None: `(name, guid)`, or None if the line is not a GUID row.
        """
        text = line.strip()
        if not text.startswith("GUID|"):
            return None
        parts = text.split("|")
        if len(parts) < 3:
            return None
        name = parts[1].strip()
        guid = parts[2].strip()
        if not name or not guid:
            return None
        return name, guid

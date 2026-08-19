"""Collect PDB GUID maps using srvctl + sqlplus on the target host."""

from __future__ import annotations

import shlex

from loguru import logger

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.transport.database_catalog import DatabaseCatalogCollector
from asm_cleanup.transport.pdb_guid_map_error import PdbGuidMapError
from asm_cleanup.transport.shell_runner import ShellRunner


class PdbGuidMapCollector:
    """Collect PDB GUID maps using srvctl + sqlplus on the target host.

    Attributes:
        runner (ShellRunner): Local or SSH shell runner.
        connection (ConnectionConfig | None): Optional connection for srvctl path.
    """

    def __init__(
        self,
        runner: ShellRunner,
        connection: ConnectionConfig | None = None,
    ) -> None:
        """Initialize the collector.

        Args:
            runner (ShellRunner): Shell execution adapter.
            connection (ConnectionConfig | None): Connection with optional grid_home.
        """
        self.runner = runner
        self.connection = connection
        self._cache: dict[str, dict[str, str]] = {}

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
            PdbGuidMapError: If Oracle home cannot be parsed.
        """
        try:
            return DatabaseCatalogCollector.parse_srvctl_database_config(
                stdout, database=database
            )
        except ValueError as exc:
            raise PdbGuidMapError(str(exc)) from exc

    @staticmethod
    def parse_vpdbs_pipe_output(stdout: str) -> dict[str, str]:
        """Parse `name|guid` rows from a quiet sqlplus v$pdbs query.

        Args:
            stdout (str): sqlplus stdout.

        Returns:
            dict[str, str]: Uppercase GUID → PDB name (excludes empty/invalid rows).

        Raises:
            PdbGuidMapError: If no valid GUID rows were found.
        """
        try:
            return DatabaseCatalogCollector.parse_vpdbs_pipe_output(stdout)
        except ValueError as exc:
            raise PdbGuidMapError(str(exc)) from exc

    @staticmethod
    def build_sqlplus_vpdbs_script(oracle_home: str, oracle_sid: str) -> str:
        """Build a shell script that queries v$pdbs via OS-authenticated sqlplus.

        Args:
            oracle_home (str): Database ORACLE_HOME from srvctl.
            oracle_sid (str): Instance SID for ORACLE_SID.

        Returns:
            str: Shell script body.
        """
        oh = shlex.quote(oracle_home.rstrip("/"))
        sid = shlex.quote(oracle_sid)
        return f"""
export ORACLE_HOME={oh}
export ORACLE_SID={sid}
export PATH=$ORACLE_HOME/bin:$PATH
export LD_LIBRARY_PATH=$ORACLE_HOME/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}
sqlplus -s / as sysdba <<'SQL'
WHENEVER SQLERROR EXIT FAILURE
SET PAGESIZE 0 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON
SELECT name||'|'||RAWTOHEX(guid) FROM v$pdbs ORDER BY 1;
EXIT
SQL
""".strip()

    @staticmethod
    def merge_pdb_guid_maps(
        auto_map: dict[str, str],
        yaml_map: dict[str, str],
    ) -> dict[str, str]:
        """Merge auto-fetched and YAML PDB maps (YAML keys win on conflict).

        Args:
            auto_map (dict[str, str]): GUID → name from v$pdbs.
            yaml_map (dict[str, str]): GUID → name from move_policy.pdb_guid_map.

        Returns:
            dict[str, str]: Merged map with uppercase GUID keys.
        """
        merged = {k.upper(): v for k, v in auto_map.items()}
        for key, value in yaml_map.items():
            merged[key.upper()] = value
        return merged

    def _srvctl_bin(self) -> str:
        """Return absolute srvctl under grid_home when known.

        Returns:
            str: Path or bare `srvctl` for PATH lookup.
        """
        if self.connection and self.connection.grid_home:
            return f"{self.connection.grid_home.rstrip('/')}/bin/srvctl"
        return "srvctl"

    def collect(self, database: str) -> dict[str, str]:
        """Return GUID → PDB name for one database unique name.

        Uses srvctl to resolve ORACLE_HOME (and instance SID), then sqlplus
        `/ as sysdba` against v$pdbs. Results are cached per database.

        Args:
            database (str): Database unique name (as registered with srvctl).

        Returns:
            dict[str, str]: Uppercase GUID → PDB name.

        Raises:
            PdbGuidMapError: If srvctl or sqlplus discovery fails.
        """
        key = database.strip()
        if not key:
            raise PdbGuidMapError("database name must be non-empty")
        cache_key = key.casefold()
        if cache_key in self._cache:
            return dict(self._cache[cache_key])

        try:
            mapping = self._collect_once(key)
        except PdbGuidMapError:
            if key != key.upper():
                logger.debug(
                    "PDB GUID collect failed for {!r}; retrying uppercase",
                    key,
                )
                mapping = self._collect_once(key.upper())
            else:
                raise

        self._cache[cache_key] = mapping
        logger.info(
            "fetched {} PDB GUID(s) from v$pdbs for database {!r}",
            len(mapping),
            key,
        )
        return dict(mapping)

    def collect_many(self, databases: list[str]) -> dict[str, str]:
        """Collect and merge GUID maps for multiple databases.

        Args:
            databases (list[str]): Database unique names.

        Returns:
            dict[str, str]: Merged uppercase GUID → PDB name.

        Raises:
            PdbGuidMapError: If any database collection fails.
        """
        merged: dict[str, str] = {}
        for name in databases:
            text = name.strip()
            if not text:
                continue
            merged.update(self.collect(text))
        return merged

    def _collect_once(self, database: str) -> dict[str, str]:
        """Run srvctl config + sqlplus for one database name spelling.

        Args:
            database (str): Database unique name passed to srvctl -d.

        Returns:
            dict[str, str]: Uppercase GUID → PDB name.

        Raises:
            PdbGuidMapError: On command failure or parse errors.
        """
        srvctl = shlex.quote(self._srvctl_bin())
        db = shlex.quote(database)
        config_script = f"{srvctl} config database -d {db}"
        logger.debug("running {}", config_script)
        try:
            config_result = self.runner.run_shell(
                config_script,
                use_grid_env=True,
                argv=["srvctl", "config", "database", "-d", database],
            )
        except Exception as exc:
            raise PdbGuidMapError(
                f"srvctl config database -d {database} failed: {exc}"
            ) from exc
        if not config_result.ok:
            stderr = (config_result.stderr or "").strip()
            raise PdbGuidMapError(
                f"srvctl config database -d {database} failed "
                f"(exit {config_result.exit_code})" + (f": {stderr}" if stderr else "")
            )

        oracle_home, oracle_sid = self.parse_srvctl_database_config(
            config_result.stdout,
            database=database,
        )
        logger.debug(
            "database {!r}: ORACLE_HOME={!r} ORACLE_SID={!r}",
            database,
            oracle_home,
            oracle_sid,
        )

        sql_script = self.build_sqlplus_vpdbs_script(oracle_home, oracle_sid)
        try:
            sql_result = self.runner.run_shell(
                sql_script,
                use_grid_env=False,
                argv=["sqlplus", "-s", "/", "as", "sysdba"],
            )
        except Exception as exc:
            raise PdbGuidMapError(
                f"sqlplus v$pdbs for database {database} "
                f"(ORACLE_HOME={oracle_home}, ORACLE_SID={oracle_sid}) failed: {exc}"
            ) from exc
        if not sql_result.ok:
            stderr = (sql_result.stderr or sql_result.stdout or "").strip()
            raise PdbGuidMapError(
                f"sqlplus v$pdbs for database {database} failed "
                f"(exit {sql_result.exit_code}, ORACLE_HOME={oracle_home}, "
                f"ORACLE_SID={oracle_sid})" + (f": {stderr}" if stderr else "")
            )
        return self.parse_vpdbs_pipe_output(sql_result.stdout)

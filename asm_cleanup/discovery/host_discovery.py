"""Remote host discovery for Grid home, disk groups, and databases."""

from __future__ import annotations

import re
import shlex

from fabric import Connection
from loguru import logger

from asm_cleanup.db.target import Target
from asm_cleanup.discovery.discovery_error import DiscoveryError
from asm_cleanup.transport.database_catalog import DatabaseCatalogCollector


class HostDiscovery:
    """Discover Grid home, ASM disk groups, and database metadata on a host.

    Attributes:
        target (Target): Target connection profile with optional overrides.
    """

    def __init__(self, target: Target) -> None:
        """Initialize discovery against a Target profile.

        Args:
            target (Target): Connection target (grid_home/oracle_sid overrides).
        """
        self.target = target

    def discover_grid_home_and_sid(self, conn: Connection) -> tuple[str, str]:
        """Discover Grid Infrastructure Home and the +ASM SID on the target.

        Args:
            conn (Connection): Remote Fabric connection.

        Returns:
            tuple[str, str]: (grid_home, asm_sid).

        Raises:
            DiscoveryError: If discovery script fails or yields empty values.
        """
        if self.target.grid_home and self.target.oracle_sid:
            return self.target.grid_home, self.target.oracle_sid

        script = """
GRID_HOME=""
ASM_SID=""

# 1. Try oratab
if [ -f /etc/oratab ]; then
  line=$(grep -E '^\\+?[a-zA-Z0-9_-]*\\+ASM[0-9]*:' /etc/oratab | head -n 1)
  if [ -n "$line" ]; then
    ASM_SID=$(echo "$line" | cut -d: -f1)
    GRID_HOME=$(echo "$line" | cut -d: -f2)
  fi
fi

# 2. Try running processes if not found
if [ -z "$GRID_HOME" ] || [ -z "$ASM_SID" ]; then
  pmon_line=$(ps -ef | grep -E 'pmon_(\\+asm|\\+ASM)[0-9]*' | grep -v grep | head -n 1)
  if [ -n "$pmon_line" ]; then
    pmon_proc=$(echo "$pmon_line" | awk '{print $NF}')
    ASM_SID=${pmon_proc##*pmon_}
    pid=$(echo "$pmon_line" | awk '{print $2}')
    if [ -n "$pid" ] && [ -l /proc/$pid/exe ]; then
      exe_path=$(readlink /proc/$pid/exe)
      GRID_HOME="${exe_path%/bin/oracle}"
    fi
  fi
fi

# 3. Fallback check for standard directories if GRID_HOME still empty
if [ -z "$GRID_HOME" ]; then
  for path in /u01/app/grid /u01/app/19c/grid /u01/app/21c/grid /u01/app/23c/grid /u01/app/12.2.0/grid; do
    if [ -d "$path/bin" ]; then
      GRID_HOME="$path"
      break
    fi
  done
fi

if [ -z "$ASM_SID" ]; then
  ASM_SID="+ASM"
fi

echo "GRID_HOME=$GRID_HOME"
echo "ASM_SID=$ASM_SID"
"""
        res = conn.run(f"bash -c {shlex.quote(script.strip())}", hide=True, warn=True)
        if not res.ok:
            raise DiscoveryError(
                f"Grid home discovery script failed (exit {res.exited}): {res.stderr}"
            )

        lines = res.stdout.splitlines()
        gh, sid = "", ""
        for line in lines:
            if line.startswith("GRID_HOME="):
                gh = line.partition("=")[2].strip()
            elif line.startswith("ASM_SID="):
                sid = line.partition("=")[2].strip()

        gh = self.target.grid_home or gh
        sid = self.target.oracle_sid or sid

        if not gh:
            raise DiscoveryError(
                "Could not automatically discover Oracle Grid Home. "
                "Please specify it manually in Target settings."
            )

        return gh, sid

    def discover_disk_groups(
        self, conn: Connection, grid_home: str, asm_sid: str
    ) -> list[str]:
        """Query ASM disk groups using asmcmd.

        Args:
            conn (Connection): Remote Fabric connection.
            grid_home (str): Path to Grid Home.
            asm_sid (str): Oracle SID for ASM.

        Returns:
            list[str]: Discovered disk group names (e.g. ['+DATA', '+FRA']).
        """
        script = f"""
export ORACLE_HOME={shlex.quote(grid_home)}
export ORACLE_SID={shlex.quote(asm_sid)}
export PATH=$ORACLE_HOME/bin:$PATH
asmcmd ls +
"""
        res = conn.run(f"bash -c {shlex.quote(script.strip())}", hide=True, warn=True)
        if not res.ok:
            logger.warning("asmcmd ls + failed: {}", res.stderr or res.stdout)
            return ["+DATA"]

        disk_groups = []
        for line in res.stdout.splitlines():
            token = line.strip().rstrip("/")
            if token and re.match(r"^\+?[A-Za-z0-9_$#-]+$", token):
                if not token.startswith("+"):
                    token = f"+{token}"
                disk_groups.append(token.upper())
        return disk_groups if disk_groups else ["+DATA"]

    def discover_databases(
        self, conn: Connection, grid_home: str
    ) -> dict[str, dict[str, str]]:
        """List databases configured or running on the target.

        Args:
            conn (Connection): Remote Fabric connection.
            grid_home (str): Grid Home.

        Returns:
            dict[str, dict[str, str]]: Map of database unique name -> metadata.
        """
        quoted_home = shlex.quote(grid_home)
        quoted_srvctl = shlex.quote(f"{grid_home.rstrip('/')}/bin/srvctl")
        script = f"""
# Try srvctl first if available
if [ -n {quoted_home} ] && [ -x {quoted_srvctl} ]; then
  export ORACLE_HOME={quoted_home}
  export PATH=$ORACLE_HOME/bin:$PATH
  if OUT=$({quoted_srvctl} list databases 2>/dev/null) && [ -n "$OUT" ]; then
    echo "$OUT"
    exit 0
  fi
fi

# Fallback to oratab
if [ -f /etc/oratab ]; then
  if OUT=$(grep -v -E '^(#|\\+ASM|\\+asm|\\*|\\s*$)' /etc/oratab | cut -d: -f1) && [ -n "$OUT" ]; then
    echo "$OUT"
    exit 0
  fi
fi

# Fallback to ps -ef
ps -ef | grep -E 'pmon_[a-zA-Z0-9_-]+' | grep -v -E '(grep|\\+ASM|\\+asm)' | awk '{{print $NF}}' | sed 's/.*pmon_//'
"""
        res = conn.run(f"bash -c {shlex.quote(script.strip())}", hide=True, warn=True)
        databases: dict[str, dict[str, str]] = {}
        if res.ok:
            for line in res.stdout.splitlines():
                name = line.strip()
                if name:
                    databases[name] = {}
        else:
            logger.warning(
                "Database discovery command failed (exit code {}): {}",
                res.exited,
                (res.stderr or res.stdout).strip(),
            )
        return databases

    def get_database_home_and_sid(
        self, conn: Connection, db_name: str, grid_home: str
    ) -> tuple[str, str]:
        """Resolve ORACLE_HOME and ORACLE_SID for a database.

        Args:
            conn (Connection): Remote Fabric connection.
            db_name (str): Database unique name.
            grid_home (str): Grid Home.

        Returns:
            tuple[str, str]: (oracle_home, oracle_sid).
        """
        quoted_home = shlex.quote(grid_home)
        quoted_srvctl = shlex.quote(f"{grid_home.rstrip('/')}/bin/srvctl")
        script = f"""
if [ -n {quoted_home} ] && [ -x {quoted_srvctl} ]; then
  {quoted_srvctl} config database -d {shlex.quote(db_name)}
fi
"""
        res = conn.run(f"bash -c {shlex.quote(script.strip())}", hide=True, warn=True)
        oracle_home = ""
        oracle_sid = db_name

        if res.ok and res.stdout:
            home, sid = DatabaseCatalogCollector.extract_srvctl_home_and_sid(
                res.stdout, database=db_name
            )
            if home:
                oracle_home = home
            oracle_sid = sid

        if not oracle_home:
            quoted_name = shlex.quote(db_name)
            script_oratab = f"""
if [ -f /etc/oratab ]; then
  awk -F: -v n={quoted_name} '$1 == n {{print; exit}}' /etc/oratab
fi
"""
            res_or = conn.run(
                f"bash -c {shlex.quote(script_oratab.strip())}", hide=True, warn=True
            )
            if res_or.ok and res_or.stdout:
                parts = res_or.stdout.strip().split(":")
                if len(parts) >= 2:
                    oracle_home = parts[1]

        return oracle_home, oracle_sid

    def collect_database_details(
        self, conn: Connection, db_name: str, oracle_home: str, oracle_sid: str
    ) -> tuple[dict[str, str], list[tuple[str, str]], list[tuple[str, str, str, str]]]:
        """Collect storage params, PDBs, and active data/tempfiles from a database.

        Args:
            conn (Connection): Remote Fabric connection.
            db_name (str): Database name.
            oracle_home (str): Database ORACLE_HOME.
            oracle_sid (str): Database instance SID.

        Returns:
            tuple[dict[str, str], list[tuple[str, str]], list[tuple[str, str, str, str]]]:
                Parameters dict, PDB (name, guid) list, and file
                (path, con_id, con_name, type) list.
        """
        sql_query = """
SET PAGESIZE 0 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON
WHENEVER SQLERROR EXIT FAILURE

-- Parameter settings
SELECT 'PARAM|' || name || '=' || value 
FROM v$parameter 
WHERE name IN ('db_create_file_dest', 'db_recovery_file_dest');

-- PDB list
SELECT 'GUID|' || name || '|' || RAWTOHEX(guid) 
FROM v$pdbs;

-- File list
SELECT 'FILE|' || f.name || '|' || f.con_id || '|' || c.name || '|DATAFILE'
FROM v$datafile f
JOIN v$containers c ON f.con_id = c.con_id
UNION ALL
SELECT 'FILE|' || t.name || '|' || t.con_id || '|' || c.name || '|TEMPFILE'
FROM v$tempfile t
JOIN v$containers c ON t.con_id = c.con_id;

EXIT
"""
        oh = shlex.quote(oracle_home)
        sid = shlex.quote(oracle_sid)

        script = f"""
export ORACLE_HOME={oh}
export ORACLE_SID={sid}
export PATH=$ORACLE_HOME/bin:$PATH
export LD_LIBRARY_PATH=$ORACLE_HOME/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}

sqlplus -s / as sysdba <<'SQL'
{sql_query.strip()}
SQL
"""
        res = conn.run(f"bash -c {shlex.quote(script.strip())}", hide=True, warn=True)

        db_params: dict[str, str] = {}
        db_pdbs: list[tuple[str, str]] = []
        db_files: list[tuple[str, str, str, str]] = []

        if not res.ok:
            logger.warning(
                "sqlplus check failed for database {} (SID={}): {}",
                db_name,
                oracle_sid,
                res.stderr or res.stdout,
            )
            return db_params, db_pdbs, db_files

        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith(("PARAM|", "GUID|", "FILE|")):
                continue

            guid_row = DatabaseCatalogCollector.parse_prefixed_guid_row(line)
            if guid_row is not None:
                db_pdbs.append(guid_row)
                continue

            parts = line.split("|")
            prefix = parts[0]

            if prefix == "PARAM" and len(parts) >= 2:
                param_key, _, param_val = parts[1].partition("=")
                db_params[param_key] = param_val
            elif prefix == "FILE" and len(parts) >= 5:
                db_files.append((parts[1], parts[2], parts[3], parts[4]))

        return db_params, db_pdbs, db_files

# asm-cleanup

Python helpers for Oracle ASM: run **`asmcmd`** locally or over **SSH** (Fabric), walk an ASM directory tree, parse **`asmcmd ls -l`** lines for DATAFILE/TEMPFILE aliases, emit draft **OMF move** SQL, and optionally check **`asmcmd lsof`** paths against allowed **disk group + database** prefixes from YAML.

## Requirements

- **Python 3.13+**
- **`asmcmd`** available on the target (local shell or remote Grid home)
- For SSH: key or password in Fabric **`connect_kwargs`**, and a YAML **`asm.hosts`** entry

## Install

**Shell** (clone or cd into the repo, then sync):

```bash
cd /path/to/asm-cleanup
uv sync
```

**Shell** (editable install so `asm-cleanup` is on your PATH):

```bash
pip install -e .
```

This exposes the **`asm-cleanup`** console script.

### Debug output

- **CLI:** pass **`--debug`** to print `[AsmCleanup:debug]` lines (SSH host resolution, `database_filter` vs YAML `databases`, remote shell script previews, `asmcmd` stderr, monitoring prefixes).
- **Library:** `AsmCleanup(..., debug=True)`, or `AsmCleanup.ssh(..., debug=True)`, or `AsmCleanup.run_asm_walk(..., debug=True)`.
- **Environment:** set **`ASM_CLEANUP_DEBUG=1`** (or `true` / `yes` / `on`) to enable the same traces without code changes.

---

## Configuration (`config.yaml`)

Use a top-level **`asm:`** key and a **`hosts`** map. Each **key** (`lab`, `prod`, …) is a **host id** you pass to the CLI or API. Values are SSH + Grid settings plus **`databases`** and **`disk_groups`** used by **`monitor_file_access`**.

**YAML** (example `asm.hosts` fragment; save as e.g. `config.yaml`):

```yaml
asm:
  hosts:
    lab:
      host: "grid-lab.example.com"
      user: "oracle"
      grid_home: "/u01/app/19c/grid"
      # Typical fix for ASMCMD-8102 over SSH — see README section below.
      oracle_sid: "+ASM"
      use_oraenv: true
      oraenv_path: "/usr/local/bin/oraenv"
      monitor_interval: 5
      monitor_count: 5
      connect_kwargs:
        key_filename: "/home/you/.ssh/id_ed25519"
      disk_groups:
        - "+DATA"
        - "+FRA"
      databases:
        - MYDB
        - OTHERDB
      # Optional: if set, ``ac.run()`` / CLI with no asm_path walks ONLY this path instead of
      # every disk_groups × databases combination.
      # default_asm_path: "+DATA/MYDB"
      # Optional: map each PDB’s 32-hex ASM directory GUID to its PDB name so generated move SQL
      # can emit ``ALTER SESSION SET CONTAINER`` when switching between PDBs (see below).
      # pdb_guid_map:
      #   "49C96937E332EB45E0631A04010ABA14": "TOOLKITPDB"
    prod:
      host: "grid-prod.example.com"
      user: "grid"
      grid_home: "/u01/app/grid"
      connect_kwargs:
        key_filename: "/home/you/.ssh/id_ed25519"
      disk_groups:
        - "+DATA"
      databases:
        - PRODDB
```

**Python script** (load host profile from disk):

```python
from asm_cleanup import AsmConfigFile

cfg = AsmConfigFile.load("config.yaml")
lab = cfg.get_host("lab")
print(lab.host, lab.grid_home, lab.databases)
```

### Default: all configured ASM paths (`disk_groups` × `databases`)

With **SSH** and **no** `asm_path`, **`ac.run()`** / **`asm-cleanup`** walks **every** combination (e.g. `+DATA/homelab` and `+FRA/homelab`), one walk per path, with per-path output files. **`--database`** / **`database_filter`** limits which database names are included in that product.

If YAML sets **`default_asm_path`**, omitting `asm_path` walks **only that one** directory instead (override when you do not want the full grid).

You can also call **`ac.run_all_configured_paths()`** directly if you prefer not to use **`ac.run()`**.

### ASM walk path (explicit `asm_path`)

Pass **`asm_path`** (CLI positional or **`ac.run("+DATA/…")`**) when you want **one** subtree. **`resolve_asm_walk_path()`** is still available for programmatic use (e.g. infer one path when you have a single DB and no `default_asm_path`).

**Local** mode always requires an explicit **`asm_path`** (no YAML host profile).

### PDB paths and `ALTER SESSION SET CONTAINER`

On multitenant databases, ASM often stores PDB files under **`+DISKGROUP/DB_UNIQUE_NAME/<32-hex-GUID>/DATAFILE|TEMPFILE/`**. The walk transcript is used to detect that GUID (from the alias **source** path, or from the **target** OMF path when the source is a short alias). Files under **`.../DB_UNIQUE_NAME/DATAFILE/`** with **no** GUID directory are treated as **CDB$ROOT**.

`asmcmd` does **not** print the human-readable PDB name, so the tool cannot emit a correct **`ALTER SESSION SET CONTAINER`** until you add optional **`pdb_guid_map`** on the host in YAML: keys are the 32-character GUID (any case), values are the PDB name as Oracle expects it in **`SET CONTAINER`**. When the map is present, generated move SQL inserts **`ALTER SESSION SET CONTAINER = …`** whenever the resolved PDB changes between consecutive statements (including back to **`CDB$ROOT`**). Unmapped GUIDs produce SQL comments instead of a session switch.

### ASMCMD-8102 (“no connection to Oracle ASM”) over SSH

Non-interactive SSH usually does **not** match an interactive login: **`ORACLE_HOME`** may point at the wrong tree, **`ORACLE_BASE`** and **`LD_LIBRARY_PATH`** are unset, and **`PATH`** may omit **`$ORACLE_HOME/bin`**. `asmcmd` can then print **“Connected to an idle instance”** and **ASMCMD-8102** even though ASM is healthy.

**Recommended (matches `ORAENV_ASK=NO` + `. oraenv` on the server):**

**YAML** (merge under a host entry in `config.yaml`):

```yaml
oracle_sid: "+ASM"       # same value you would type for oraenv (often +ASM or +ASM1)
use_oraenv: true
oraenv_path: "/usr/local/bin/oraenv"   # run `which oraenv` on the host if unsure
```

That runs, before each remote `asmcmd` line:

**Shell (what runs on the remote host; illustration only):**

```bash
export ORACLE_SID=…
export ORAENV_ASK=NO
. /usr/local/bin/oraenv
```

…so **`ORACLE_HOME`**, **`ORACLE_BASE`**, and library paths come from **`/etc/oratab`**, like your manual session.

**Simpler fallback** (no `oraenv` on the host): set only **`oracle_sid`**. Then the tool exports **`ORACLE_HOME`** from **`oracle_home`** if set, otherwise from **`grid_home`**, plus optional **`oracle_base`**, **`PATH`**, and **`LD_LIBRARY_PATH=$ORACLE_HOME/lib:…`**. Use this only when **`grid_home`** really is the GI **`ORACLE_HOME`** for that SID.

**Full control:** set **`asm_env_init`** only; then **`use_oraenv`** / **`oracle_sid`** shortcuts are **not** applied. Example:

**YAML** (host entry fragment):

```yaml
asm_env_init: |
  export ORACLE_SID=+ASM
  export ORAENV_ASK=NO
  . /usr/local/bin/oraenv
```

---

## Examples

Operational examples all use the same layout: a **`###` subheading** under this section as the example title (large type and outline entry on GitHub), then three labeled slots in order:

1. **Python script** — run with `python your_script.py` (or paste into a `*.py` file).
2. **CLI (no config file)** — local `asmcmd`; no `--ssh` and no `asm.hosts` YAML.
3. **CLI (with config file)** — `--ssh` plus a well-formed `config.yaml` and **`--host`**.

If a slot is not supported for that title, the label is still shown and the body is a short **note** instead of a command.

Use `python` instead of `uv run python` if you are not using `uv`.

### Walking one ASM subtree

**Python script:**

```python
#!/usr/bin/env python3
from asm_cleanup import AsmCleanup

with AsmCleanup.local() as ac:
    ac.run("+DATA/MYDB")
```

**CLI (no config file):**

```bash
asm-cleanup +DATA/MYDB
```

**CLI (with config file):**

```bash
asm-cleanup +DATA/MYDB --ssh --config config.yaml --host lab
```

### Walking every disk group × database path

**Python script:**

```python
#!/usr/bin/env python3
from asm_cleanup import AsmCleanup

with AsmCleanup.ssh("config.yaml", "lab") as ac:
    ac.run()
```

**CLI (no config file):**

```text
Not supported: expanding every disk_groups × databases pair requires asm.hosts in YAML. Use local single-path mode in the previous example, or the SSH command below.
```

**CLI (with config file):**

```bash
asm-cleanup --ssh --config config.yaml --host lab
```

### Walking with a database filter

Names in **`--database`** / `database_filter` must exist in that host’s **`databases`** list in YAML.

**Python script:**

```python
#!/usr/bin/env python3
from asm_cleanup import AsmCleanup

with AsmCleanup.ssh("config.yaml", "lab", databases=["MYDB"]) as ac:
    ac.run()
```

**CLI (no config file):**

```text
Not supported: --database is only valid with --ssh. Local CLI has no YAML databases list to filter.
```

**CLI (with config file):**

```bash
asm-cleanup --ssh --config config.yaml --host lab --database MYDB
```

### Walking without emitting fix SQL

**Python script:**

```python
#!/usr/bin/env python3
from asm_cleanup import AsmCleanup

with AsmCleanup.local() as ac:
    ac.run("+DATA/MYDB", no_fix=True)
```

**CLI (no config file):**

```bash
asm-cleanup +DATA/MYDB --no-fix
```

**CLI (with config file):**

```bash
asm-cleanup +DATA/MYDB --ssh --config config.yaml --host lab --no-fix
```

### Walking with debug logging

**Python script:**

```python
#!/usr/bin/env python3
from asm_cleanup import AsmCleanup

with AsmCleanup.local(debug=True) as ac:
    ac.run("+DATA/MYDB")
```

**CLI (no config file):**

```bash
asm-cleanup +DATA/MYDB --debug
```

**CLI (with config file):**

```bash
asm-cleanup +DATA/MYDB --ssh --config config.yaml --host lab --debug
```

### Writing walk and fix output to custom paths

**Python script:**

```python
#!/usr/bin/env python3
from pathlib import Path

from asm_cleanup import AsmCleanup

with AsmCleanup.ssh("config.yaml", "lab") as ac:
    ac.run(
        "+DATA/MYDB",
        outfile=Path("artifacts/walk.txt"),
        fixfile=Path("artifacts/fix.sql"),
    )
```

**CLI (no config file):**

```text
Not supported: the CLI does not accept custom outfile/fixfile paths. Omitting them writes under logs/ with the default naming (see “Default output files” below).
```

**CLI (with config file):**

```bash
# SSH still uses default logs/ names; custom paths require the Python block above.
asm-cleanup +DATA/MYDB --ssh --config config.yaml --host lab
```

### Checking open files against allowed ASM prefixes

Uses **`monitor_file_access`** (library API; no CLI wrapper).

**Python script:**

```python
#!/usr/bin/env python3
from fabric import Connection
from asm_cleanup import AsmCleanup, AsmConfigFile

root = AsmConfigFile.load("config.yaml")
profile = root.get_host("lab")
ac = AsmCleanup(profile, database_filter=["MYDB"])

with Connection(
    host=profile.host,
    user=profile.user,
    connect_kwargs=profile.connect_kwargs,
) as conn:
    ac.monitor_file_access(conn)
```

**CLI (no config file):**

```text
Not supported: there is no asm-cleanup subcommand for monitor_file_access; use the Python block above.
```

**CLI (with config file):**

```text
Not supported on the CLI — monitoring needs a Fabric Connection plus YAML (Python block above).
```

### Parsing a saved walk transcript

No **`asmcmd`** — works from an existing **`logs/asm_walk_*.txt`** file.

**Python script:**

```python
#!/usr/bin/env python3
from pathlib import Path

from asm_cleanup import AsmCleanup

path = AsmCleanup.normalize_asm_path("+data/mydb/system01.dbf")
print(path)
assert AsmCleanup.asm_path_prefix_match("+DATA/MYDB/x", "+data/mydb")

lines = Path("logs/asm_walk_20260101_DATA_mydb.txt").read_text().splitlines()
aliases = AsmCleanup.extract_aliases(lines)
sql = AsmCleanup.generate_fix_script(aliases)
```

**CLI (no config file):**

```text
Not supported: parsing is library-only. Run a walk first (see “walking one asm subtree”), then use the Python block to read logs/asm_walk_*.txt.
```

**CLI (with config file):**

```text
Same as local — use Python to parse an existing transcript file.
```

**CLI reference (built-in help):**

```bash
asm-cleanup --help
```

---

## Default output files

Walk transcripts and generated SQL use **`logs/`** in the process working directory. That folder is created automatically when a walk runs. File names use a date stamp, a short slug from the ASM root (for example `+DATA/MYDB` → `DATA_MYDB`), and in multi-walk mode a two-digit sequence (`00`, `01`, …).

| File | Meaning |
|------|--------|
| `logs/asm_walk_YYYYMMDD_DATA_MYDB.txt` | Single walk (explicit `asm_path` or YAML `default_asm_path`) |
| `logs/asm_omf_fix_YYYYMMDD_DATA_MYDB.sql` | OMF SQL for that walk |
| `logs/asm_walk_YYYYMMDD_NN_DATA_MYDB.txt` | One file per path when walking every configured `disk_groups` × `databases` pair (see **`AsmCleanup.output_paths_for_asm_path`**) |
| `logs/asm_omf_fix_YYYYMMDD_NN_DATA_MYDB.sql` | SQL for that path |

---

## Package layout

| Module | Contents |
|------|----------|
| `asm_cleanup.asm_cleanup` | `AsmCleanup`, `DEFAULT_LOG_DIR` — walk / analyze / fix, command runners, monitoring |
| `asm_cleanup.asm_config` | `AsmConfigFile`, `HostConfig`, `AsmConfigFile.load()` |
| `asm_cleanup.cli` | `asm-cleanup` entrypoint |

---

## Disclaimer

Generated SQL is a **starting point** only. Review and test on non-production systems. This project does not open an Oracle SQL*Net session; it shells out to **`asmcmd`** and parses text.

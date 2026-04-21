"""Command-line entry for the ASM walk / analyze / fix pipeline."""

from __future__ import annotations

import argparse

from asm_cleanup.asm_cleanup import AsmCleanup


def main() -> None:
    """Parse argv and run :meth:`AsmCleanup.run_asm_walk`."""
    parser = argparse.ArgumentParser(
        description="ASM Walker + Analyzer + OMF Fix Generator (local or SSH via Fabric)"
    )

    parser.add_argument(
        "asm_path",
        nargs="?",
        default=None,
        metavar="ASM_PATH",
        help=(
            "Starting ASM directory (e.g. +DATA/DBNAME). With --ssh, omit to walk every "
            "disk_groups × databases path (or only default_asm_path if set in YAML). "
            "If host disk_groups is empty, all ASM disk groups are auto-discovered."
        ),
    )

    parser.add_argument(
        "--ssh",
        action="store_true",
        help="Run asmcmd on the remote host using Fabric (requires --host and asm.hosts in YAML).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="YAML path containing an asm: block (used with --ssh).",
    )
    parser.add_argument(
        "--host",
        "-H",
        metavar="HOST_ID",
        dest="host_id",
        help="Host id from asm.hosts in config (required with --ssh).",
    )
    parser.add_argument(
        "--database",
        "-D",
        action="append",
        dest="databases",
        metavar="NAME",
        help=(
            "Database name under the chosen host (repeat for several). "
            "Restricts monitoring allow-lists; optional. Must exist in that host's databases list."
        ),
    )

    parser.add_argument("--no-walk", action="store_true")
    parser.add_argument("--no-analyze", action="store_true")
    parser.add_argument("--no-fix", action="store_true")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print [AsmCleanup:debug] diagnostics (SSH env, database filter, remote scripts).",
    )

    args = parser.parse_args()

    if args.ssh and not args.host_id:
        parser.error("--host is required when using --ssh")

    if not args.ssh and (args.host_id or args.databases):
        parser.error("--host and --database are only valid with --ssh")

    if not args.ssh and args.asm_path is None:
        parser.error("asm_path is required without --ssh")

    AsmCleanup.run_asm_walk(
        args.asm_path,
        ssh=args.ssh,
        config=args.config,
        host_id=args.host_id,
        databases=args.databases,
        no_walk=args.no_walk,
        no_analyze=args.no_analyze,
        no_fix=args.no_fix,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()

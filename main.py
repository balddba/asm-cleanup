"""Example script: SSH walk using ``config.yaml`` in the current directory."""

from asm_cleanup import AsmCleanup


if __name__ == "__main__":
    with AsmCleanup.ssh(config_path="config.yaml", host_id="lab") as ac:
        # No asm_path: walks every disk_group × database path (unless default_asm_path in YAML).
        ac.run()

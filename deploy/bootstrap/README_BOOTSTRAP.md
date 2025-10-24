# Bootstrap Scripts

These scripts help install missing dependencies on Windows and macOS hosts. They are idempotent and safe to run multiple times. Each script exits with:

* `0` – success.
* `1` – warning (manual action required or optional tool skipped).
* `2` – failure (unexpected error).

Use `scripts/auto_fix_env.py` to orchestrate these scripts automatically.

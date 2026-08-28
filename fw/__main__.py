"""`python -m fw` — the same entry point as the `fw` script.

The launchers (run.bat, run.sh) use this form because it works before, and without,
console-script shims being on PATH.
"""

from fw.cli.main import main

main()

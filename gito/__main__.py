"""Allow running the package with `python -m gito`."""
import sys

# Fast-path for `python -m gito ... version` to avoid importing heavy
# dependencies at module import time in environments without optional deps.
if any(arg == "version" for arg in sys.argv[1:]):
    from gito.commands.version import version

    # Call `version()` which prints the version and returns it.
    version()
    sys.exit(0)

# Otherwise, run the full entrypoint (lazy-imports inside entrypoint.main).
from gito.entrypoint import main

if __name__ == "__main__":
    main()

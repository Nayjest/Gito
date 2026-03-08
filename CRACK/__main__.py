"""Allow running the package with `python -m CRACK`."""

# Use an absolute import (package-qualified) here; otherwise, the Windows build
# produced by PyInstaller fails.
from CRACK.entrypoint import main

if __name__ == "__main__":
    main()

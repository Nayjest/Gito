"""Allow running the package with `python -m gito`."""
# package name is required here,
# otherside windows build made by pyinstaller fails.
from gito.entrypoint import main

if __name__ == "__main__":
    main()

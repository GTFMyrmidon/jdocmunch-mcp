"""Enable `python -m jdocmunch_mcp ...` invocation.

The watch login service (service_installer._exec_cmd) launches the daemon via
`sys.executable -m jdocmunch_mcp watch`, so this entry point must exist.
"""
from .server import main

if __name__ == "__main__":
    main()

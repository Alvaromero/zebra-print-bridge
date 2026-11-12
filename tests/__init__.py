import os

# The tests must not depend on an organization config installed on the machine running them.
os.environ.setdefault("ZEBRA_PRINT_BRIDGE_SYSTEM_CONFIG", "/nonexistent/zebra-print-bridge/config.json")

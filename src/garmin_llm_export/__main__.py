"""Allow ``python -m src``."""

import sys

from .cli import main

sys.exit(main())

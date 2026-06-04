"""Allow ``python -m garmin_llm_export``."""

import sys

from .cli import main

sys.exit(main())

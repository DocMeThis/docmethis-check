# Copyright (c) 2026 DocMeThis SAS. All rights reserved.

"""Entry point for ``python -m docmethis_check``."""

import sys

from docmethis_check.cli import main

if __name__ == "__main__":
    sys.exit(main())

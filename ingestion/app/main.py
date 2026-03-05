#!/usr/bin/env python3
"""Pricing Data Ingestion – Main Entry Point.

One-shot CLI job that collects Azure retail pricing data
and stores it in PostgreSQL.
"""

import os
import sys

# Ensure the app directory is on the Python path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import main  # noqa: E402

if __name__ == "__main__":
    main()

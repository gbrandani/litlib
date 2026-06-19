#!/usr/bin/env python3
"""Compatibility wrapper for bib-only Semantic Scholar fetch."""

from __future__ import annotations

import library_fetch


def main() -> int:
    return library_fetch.main(mode="bib_only")


if __name__ == "__main__":
    raise SystemExit(main())

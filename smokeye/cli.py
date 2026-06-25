"""Unified command-line entry point for SmokEye pollutant downscaling."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from smokeye import ai_downscaler, downscaler


def main(argv: Optional[List[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--method",
        choices=["deterministic", "ai"],
        default="deterministic",
        help="Downscaling weight strategy to use. Defaults to deterministic.",
    )
    args, remaining = parser.parse_known_args(argv)
    if "-h" in argv or "--help" in argv:
        downscaler.main(method_name=args.method, argv=remaining, include_method_help=True)
        return

    if args.method == "ai":
        downscaler.main(
            weight_builder=ai_downscaler.build_ai_weights,
            raster_tag_builder=ai_downscaler.ai_raster_tags,
            method_name="ai",
            argv=remaining,
        )
    else:
        downscaler.main(method_name="deterministic", argv=remaining)

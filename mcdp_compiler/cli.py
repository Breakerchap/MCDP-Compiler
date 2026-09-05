from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compiler import Compiler
from .errors import MCDPError


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(prog="mcdp", description="Compile an .mcdp source file into a Minecraft Java datapack.")
  parser.add_argument("source", type=Path, help="input .mcdp file")
  parser.add_argument("-o", "--output", type=Path, help="output datapack directory (default: ./<source-name>)")
  parser.add_argument("--no-clean", action="store_true", help="do not delete the existing output directory before compiling")
  return parser


def main(argv: list[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  output = args.output or Path(args.source.stem)
  try:
    source = args.source.read_text(encoding="utf-8")
    compiler = Compiler.from_source(source)
    compiler.write(output, clean=not args.no_clean)
  except (OSError, MCDPError) as exc:
    print(f"mcdp: error: {exc}", file=sys.stderr)
    return 1
  print(f"Compiled {args.source} -> {output}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

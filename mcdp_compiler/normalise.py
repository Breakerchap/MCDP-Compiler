from __future__ import annotations

from dataclasses import dataclass

_BLOCK_PREFIXES = ("func ", "execute ", "if ", "else if ", "else", "unless ", "while ", "for ", "schedule ", "every ", "raw")


@dataclass
class SourceLine:
  number: int
  text: str

def _strip_doc_comment(line: str) -> str:
  in_string = False
  escaped = False
  for i, ch in enumerate(line):
    if escaped:
      escaped = False
      continue
    if ch == "\\" and in_string:
      escaped = True
      continue
    if ch == '"':
      in_string = not in_string
      continue
    if not in_string and ch == "#" and i + 1 < len(line) and line[i + 1] in "?*":
      return line[:i].rstrip()
  return line.rstrip()


def _split_semicolons(text: str) -> list[str]:
  out: list[str] = []
  start = 0
  in_string = False
  escaped = False
  depth = 0
  for i, ch in enumerate(text):
    if escaped:
      escaped = False
      continue
    if ch == "\\" and in_string:
      escaped = True
      continue
    if ch == '"':
      in_string = not in_string
    elif not in_string:
      if ch in "([":
        depth += 1
      elif ch in ")]":
        depth = max(0, depth - 1)
      elif ch == ";" and depth == 0:
        part = text[start:i].strip()
        if part:
          out.append(part)
        start = i + 1
  part = text[start:].strip()
  if part:
    out.append(part)
  return out


def _normalise_lines(source: str) -> list[SourceLine]:
  lines: list[SourceLine] = []
  for number, original in enumerate(source.splitlines(), 1):
    stripped = original.strip()
    if not stripped:
      continue
    if stripped.startswith("#!"):
      lines.append(SourceLine(number, stripped))
      continue
    if stripped.startswith("#"):
      continue
    text = _strip_doc_comment(original).strip()
    if not text:
      continue
    if text.startswith("}") and len(text) > 1:
      lines.append(SourceLine(number, "}"))
      rest = text[1:].strip()
      if rest:
        lines.append(SourceLine(number, rest))
      continue
    if text.endswith("}") and "{" in text:
      before, inner = text.rsplit("{", 1)
      if inner[:-1].strip() == "" and (before.strip() + " {").startswith(_BLOCK_PREFIXES):
        lines.append(SourceLine(number, before.rstrip() + " {"))
        lines.append(SourceLine(number, "}"))
        continue
    is_block_header = text.endswith("{") and text.startswith(_BLOCK_PREFIXES)
    if is_block_header:
      lines.append(SourceLine(number, text))
    else:
      for part in _split_semicolons(text):
        lines.append(SourceLine(number, part))
  return lines

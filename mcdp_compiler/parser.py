from __future__ import annotations

import re

from .errors import MCDPError
from .expr_parser import parse_condition, parse_expr
from .model import (
  Assign, BreakStmt, ContinueStmt, EveryBlock, ExecuteBlock, ForStmt, FunctionCall,
  FunctionDef, GlobalConst, GlobalVar, IfBranch, IfStmt, Param, Program, RawBlock,
  RawCommand, ReturnStmt, ScheduleBlock, ScheduleCall, UnaryExpr, VarDecl, WhileStmt,
)
from .normalise import SourceLine, _normalise_lines, _split_semicolons

class Parser:
  def __init__(self, source: str):
    self.lines = _normalise_lines(source)
    self.i = 0

  def parse(self) -> Program:
    program = Program()
    while self.i < len(self.lines):
      line = self.lines[self.i]
      text = line.text
      if text.startswith("#!"):
        body = text[2:].strip()
        if " " in body:
          key, value = body.split(None, 1)
        else:
          key, value = body, ""
        if len(value) >= 2 and value[0] == value[-1] == '"':
          value = value[1:-1]
        program.directives[key] = value
        self.i += 1
        continue
      if text.startswith("global "):
        program.globals.append(self._parse_global(line))
        self.i += 1
        continue
      if text.startswith("const "):
        program.constants.append(self._parse_global_const(line))
        self.i += 1
        continue
      if text.startswith("func "):
        program.functions.append(self._parse_function())
        continue
      raise MCDPError("top-level code must be a directive, global/const declaration, or func", line.number)
    return program

  def _parse_global(self, line: SourceLine) -> GlobalVar:
    m = re.fullmatch(r"global\s+(int|bool)\s+([A-Za-z_][\w]*)\s*=\s*(.+)", line.text)
    if not m:
      raise MCDPError("invalid global declaration", line.number)
    return GlobalVar(line=line.number, var_type=m.group(1), name=m.group(2), value=parse_expr(m.group(3), line.number))

  def _parse_global_const(self, line: SourceLine) -> GlobalConst:
    m = re.fullmatch(r"const\s+(int|bool|str)\s+([A-Za-z_][\w]*)\s*=\s*(.+)", line.text)
    if not m:
      raise MCDPError("invalid const declaration", line.number)
    return GlobalConst(line=line.number, const_type=m.group(1), name=m.group(2), value=parse_expr(m.group(3), line.number))

  def _parse_function(self) -> FunctionDef:
    line = self.lines[self.i]
    text = line.text[:-1].rstrip()
    m = re.fullmatch(r"func\s+([A-Za-z_][A-Za-z0-9_./-]*)(?:\((.*)\))?(?:\s*->\s*(int|bool))?", text)
    if not m:
      raise MCDPError("invalid func declaration", line.number)
    name = m.group(1)
    params = self._parse_params(m.group(2) or "", line.number)
    return_type = m.group(3)
    self.i += 1
    body = self._parse_block()
    return FunctionDef(line=line.number, name=name, params=params, return_type=return_type, body=body)

  def _parse_params(self, text: str, line: int) -> list[Param]:
    if not text.strip():
      return []
    parts = self._split_args(text)
    out: list[Param] = []
    seen_default = False
    for part in parts:
      m = re.fullmatch(r"(int|bool|str)\s+([A-Za-z_][\w]*)(?:\s*=\s*(.+))?", part.strip())
      if not m:
        raise MCDPError(f"invalid parameter {part!r}", line)
      default = parse_expr(m.group(3), line) if m.group(3) is not None else None
      if default is not None:
        seen_default = True
      elif seen_default:
        raise MCDPError("required parameters cannot follow default parameters", line)
      out.append(Param(var_type=m.group(1), name=m.group(2), default=default))
    return out

  def _split_args(self, text: str) -> list[str]:
    out: list[str] = []
    start = 0
    depth = 0
    in_string = False
    escaped = False
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
        if ch == "(":
          depth += 1
        elif ch == ")":
          depth -= 1
        elif ch == "," and depth == 0:
          out.append(text[start:i].strip())
          start = i + 1
    out.append(text[start:].strip())
    return [p for p in out if p]

  def _parse_block(self) -> list:
    body = []
    while self.i < len(self.lines):
      line = self.lines[self.i]
      text = line.text
      if text == "}":
        self.i += 1
        return body
      if text.startswith("execute ") and text.endswith("{"):
        prefix = text[len("execute "):-1].strip()
        self.i += 1
        body.append(ExecuteBlock(line=line.number, prefix=prefix, body=self._parse_block()))
        continue
      if text.startswith("if ") and text.endswith("{"):
        body.append(self._parse_if())
        continue
      if text.startswith("unless ") and text.endswith("{"):
        cond = UnaryExpr(line=line.number, op="!", value=parse_condition(text[len("unless "):-1], line.number))
        self.i += 1
        branch_body = self._parse_block()
        body.append(IfStmt(line=line.number, branches=[IfBranch(condition=cond, body=branch_body, line=line.number)]))
        continue
      if text.startswith("while ") and text.endswith("{"):
        cond = parse_condition(text[len("while "):-1], line.number)
        self.i += 1
        body.append(WhileStmt(line=line.number, condition=cond, body=self._parse_block()))
        continue
      if text.startswith("for ") and text.endswith("{"):
        body.append(self._parse_for())
        continue
      if text.startswith("schedule ") and text.endswith("{"):
        delay = text[len("schedule "):-1].strip()
        self._validate_delay(delay, line.number)
        self.i += 1
        body.append(ScheduleBlock(line=line.number, delay=delay, body=self._parse_block()))
        continue
      if text.startswith("every ") and text.endswith("{"):
        body.append(self._parse_every())
        continue
      if text == "raw {":
        self.i += 1
        raw_lines: list[str] = []
        while self.i < len(self.lines) and self.lines[self.i].text != "}":
          raw_lines.append(self.lines[self.i].text)
          self.i += 1
        if self.i >= len(self.lines):
          raise MCDPError("unterminated raw block", line.number)
        self.i += 1
        body.append(RawBlock(line=line.number, lines=raw_lines))
        continue
      body.append(self._parse_statement(line))
      self.i += 1
    raise MCDPError("unterminated block", self.lines[-1].number if self.lines else 1)

  def _parse_if(self) -> IfStmt:
    first = self.lines[self.i]
    branches: list[IfBranch] = []
    cond = parse_condition(first.text[len("if "):-1], first.number)
    self.i += 1
    branches.append(IfBranch(condition=cond, body=self._parse_block(), line=first.number))
    else_body = None
    while self.i < len(self.lines):
      line = self.lines[self.i]
      if line.text.startswith("else if ") and line.text.endswith("{"):
        cond = parse_condition(line.text[len("else if "):-1], line.number)
        self.i += 1
        branches.append(IfBranch(condition=cond, body=self._parse_block(), line=line.number))
      elif line.text == "else {":
        self.i += 1
        else_body = self._parse_block()
        break
      else:
        break
    return IfStmt(line=first.number, branches=branches, else_body=else_body)

  def _parse_for(self) -> ForStmt:
    line = self.lines[self.i]
    header = line.text[len("for "):-1].strip()
    parts = _split_semicolons(header)
    if len(parts) != 3:
      raise MCDPError("for header must be 'init; condition; step'", line.number)
    init_text = parts[0].strip()
    if re.match(r"^(int|bool|str)\s+", init_text):
      init_text = "set " + init_text
    init = self._parse_statement(SourceLine(line.number, init_text))
    condition = parse_condition(parts[1], line.number)
    step = self._parse_statement(SourceLine(line.number, parts[2]))
    self.i += 1
    return ForStmt(line=line.number, init=init, condition=condition, step=step, body=self._parse_block())

  def _parse_every(self) -> EveryBlock:
    line = self.lines[self.i]
    header = line.text[len("every "):-1].strip()
    m = re.fullmatch(r"(\d+(?:t|s|d))(?:\s+while\s+(.+))?", header)
    if not m:
      raise MCDPError("every syntax is 'every <time> {' or 'every <time> while <condition> {'", line.number)
    interval = m.group(1)
    condition = parse_condition(m.group(2), line.number) if m.group(2) else None
    self.i += 1
    return EveryBlock(line=line.number, interval=interval, condition=condition, body=self._parse_block())

  def _validate_delay(self, delay: str, line: int) -> None:
    if not re.fullmatch(r"\d+(?:t|s|d)", delay):
      raise MCDPError("time must look like 20t, 5s, or 1d", line)

  def _parse_statement(self, line: SourceLine):
    text = line.text.strip()
    if text == "return":
      return ReturnStmt(line=line.number, value=None)
    if text.startswith("return "):
      return ReturnStmt(line=line.number, value=parse_expr(text[len("return "):], line.number))
    if text == "break":
      return BreakStmt(line=line.number)
    if text == "continue":
      return ContinueStmt(line=line.number)

    m = re.fullmatch(r"set\s+(int|bool|str)\s+([A-Za-z_][\w]*)(?:\(([^)]+)\))?\s*=\s*(.+)", text)
    if m:
      return VarDecl(line=line.number, var_type=m.group(1), name=m.group(2), holder=m.group(3), value=parse_expr(m.group(4), line.number))
    m = re.fullmatch(r"const\s+(int|bool|str)\s+([A-Za-z_][\w]*)\s*=\s*(.+)", text)
    if m:
      return VarDecl(line=line.number, var_type=m.group(1), name=m.group(2), holder=None, value=parse_expr(m.group(3), line.number), is_const=True)

    if text.startswith("schedule function "):
      m = re.fullmatch(r"schedule\s+function\s+([A-Za-z_][\w./-]*)\((.*)\)\s+(\d+(?:t|s|d))", text)
      if not m:
        raise MCDPError("invalid scheduled function call", line.number)
      args = [parse_expr(x, line.number) for x in self._split_args(m.group(2))] if m.group(2).strip() else []
      return ScheduleCall(line=line.number, call=FunctionCall(line=line.number, name=m.group(1), args=args), delay=m.group(3))

    if text.startswith("function "):
      m = re.fullmatch(r"function\s+([A-Za-z_][\w./-]*)\((.*)\)", text)
      if m:
        args = [parse_expr(x, line.number) for x in self._split_args(m.group(2))] if m.group(2).strip() else []
        return FunctionCall(line=line.number, name=m.group(1), args=args)
      return RawCommand(line=line.number, text=text)

    m = re.fullmatch(r"(.+?)(\+\+|--)", text)
    if m and self._looks_like_target(m.group(1).strip()):
      return Assign(line=line.number, target=parse_expr(m.group(1).strip(), line.number), op=m.group(2))

    m = re.fullmatch(r"(.+?)\s*(\+=|-=|\*=|/=|%=|=)\s*(.+)", text)
    if m and self._looks_like_target(m.group(1).strip()):
      return Assign(line=line.number, target=parse_expr(m.group(1).strip(), line.number), op=m.group(2), value=parse_expr(m.group(3), line.number))

    return RawCommand(line=line.number, text=text)

  @staticmethod
  def _looks_like_target(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][\w.]*?(?:\([^)]*\))?", text))


def parse(source: str) -> Program:
  return Parser(source).parse()

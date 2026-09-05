from __future__ import annotations

import re

from .errors import MCDPError
from .model import BinaryExpr, CallExpr, Expr, Literal, Name, NativeCondition, UnaryExpr

_NATIVE_CONDITION_PREFIXES = ("entity ", "block ", "biome ", "predicate ", "loaded ", "dimension ", "items ", "data ")

class ExprLexer:
  _token_re = re.compile(
    r'''\s*(?:(?P<NUMBER>\d+)|(?P<STRING>"(?:\\.|[^"\\])*")|(?P<SELECTOR>@[pares](?:\[[^\]]*\])?)|(?P<IDENT>[A-Za-z_][A-Za-z0-9_./-]*)|(?P<OP>==|!=|>=|<=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|%=|->|[()+\-*/%!,<>]))'''
  )

  def __init__(self, text: str, line: int):
    self.tokens: list[tuple[str, str]] = []
    self.line = line
    pos = 0
    while pos < len(text):
      m = self._token_re.match(text, pos)
      if not m:
        raise MCDPError(f"cannot parse expression near {text[pos:]!r}", line)
      kind = m.lastgroup
      value = m.group(kind)
      self.tokens.append((kind, value))
      pos = m.end()
    self.tokens.append(("EOF", ""))
    self.i = 0

  def peek(self, value: str | None = None) -> bool:
    token = self.tokens[self.i]
    return token[1] == value if value is not None else token[0] != "EOF"

  def pop(self, value: str | None = None) -> tuple[str, str]:
    token = self.tokens[self.i]
    if value is not None and token[1] != value:
      raise MCDPError(f"expected {value!r}, got {token[1]!r}", self.line)
    self.i += 1
    return token


class ExprParser:
  PRECEDENCE = {
    "||": 1, "&&": 2, "==": 3, "!=": 3, "<": 3, "<=": 3, ">": 3, ">=": 3,
    "+": 4, "-": 4, "*": 5, "/": 5, "%": 5,
  }

  def __init__(self, text: str, line: int):
    self.lex = ExprLexer(text, line)
    self.line = line

  def parse(self) -> Expr:
    expr = self.parse_binary(0)
    if self.lex.tokens[self.lex.i][0] != "EOF":
      raise MCDPError(f"unexpected token {self.lex.tokens[self.lex.i][1]!r}", self.line)
    return expr

  def parse_binary(self, min_prec: int) -> Expr:
    left = self.parse_unary()
    while True:
      kind, op = self.lex.tokens[self.lex.i]
      if kind != "OP" or op not in self.PRECEDENCE or self.PRECEDENCE[op] < min_prec:
        break
      self.lex.pop()
      right = self.parse_binary(self.PRECEDENCE[op] + 1)
      left = BinaryExpr(line=self.line, left=left, op=op, right=right)
    return left

  def parse_unary(self) -> Expr:
    if self.lex.peek("!") or self.lex.peek("-") or self.lex.peek("+"):
      op = self.lex.pop()[1]
      return UnaryExpr(line=self.line, op=op, value=self.parse_unary())
    return self.parse_primary()

  def parse_primary(self) -> Expr:
    kind, value = self.lex.pop()
    if kind == "NUMBER":
      return Literal(line=self.line, value=int(value))
    if kind == "STRING":
      return Literal(line=self.line, value=bytes(value[1:-1], "utf-8").decode("unicode_escape"))
    if kind == "SELECTOR":
      return Name(line=self.line, value=value)
    if kind == "IDENT":
      if value == "true":
        return Literal(line=self.line, value=True)
      if value == "false":
        return Literal(line=self.line, value=False)
      if self.lex.peek("("):
        self.lex.pop("(")
        args: list[Expr | str] = []
        if not self.lex.peek(")"):
          while True:
            if self.lex.tokens[self.lex.i][0] == "SELECTOR":
              args.append(self.lex.pop()[1])
            else:
              args.append(self.parse_binary(0))
            if self.lex.peek(","):
              self.lex.pop(",")
              continue
            break
        self.lex.pop(")")
        return CallExpr(line=self.line, name=value, args=args)
      return Name(line=self.line, value=value)
    if value == "(":
      inner = self.parse_binary(0)
      self.lex.pop(")")
      return inner
    raise MCDPError(f"unexpected token {value!r}", self.line)


def parse_expr(text: str, line: int) -> Expr:
  return ExprParser(text.strip(), line).parse()


def parse_condition(text: str, line: int) -> Expr:
  text = text.strip()
  negated = False
  if text.startswith("!"):
    negated = True
    text = text[1:].lstrip()
  if text.startswith(_NATIVE_CONDITION_PREFIXES):
    node: Expr = NativeCondition(line=line, text=text)
    return UnaryExpr(line=line, op="!", value=node) if negated else node
  if negated:
    return UnaryExpr(line=line, op="!", value=parse_expr(text, line))
  return parse_expr(text, line)

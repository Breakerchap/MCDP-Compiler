from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
  line: int


@dataclass
class Expr(Node):
  pass


@dataclass
class Literal(Expr):
  value: int | bool | str


@dataclass
class Name(Expr):
  value: str


@dataclass
class CallExpr(Expr):
  name: str
  args: list[Expr | str]


@dataclass
class UnaryExpr(Expr):
  op: str
  value: Expr


@dataclass
class BinaryExpr(Expr):
  left: Expr
  op: str
  right: Expr


@dataclass
class NativeCondition(Expr):
  text: str


@dataclass
class Statement(Node):
  pass


@dataclass
class RawCommand(Statement):
  text: str


@dataclass
class RawBlock(Statement):
  lines: list[str]


@dataclass
class VarDecl(Statement):
  var_type: str
  name: str
  holder: str | None
  value: Expr
  is_const: bool = False


@dataclass
class Assign(Statement):
  target: Expr
  op: str
  value: Expr | None = None


@dataclass
class FunctionCall(Statement):
  name: str
  args: list[Expr]


@dataclass
class ExecuteBlock(Statement):
  prefix: str
  body: list[Statement]


@dataclass
class IfBranch:
  condition: Expr
  body: list[Statement]
  line: int


@dataclass
class IfStmt(Statement):
  branches: list[IfBranch]
  else_body: list[Statement] | None = None


@dataclass
class WhileStmt(Statement):
  condition: Expr
  body: list[Statement]


@dataclass
class ForStmt(Statement):
  init: Statement
  condition: Expr
  step: Statement
  body: list[Statement]


@dataclass
class ReturnStmt(Statement):
  value: Expr | None


@dataclass
class BreakStmt(Statement):
  pass


@dataclass
class ContinueStmt(Statement):
  pass


@dataclass
class ScheduleBlock(Statement):
  delay: str
  body: list[Statement]


@dataclass
class ScheduleCall(Statement):
  call: FunctionCall
  delay: str


@dataclass
class EveryBlock(Statement):
  interval: str
  condition: Expr | None
  body: list[Statement]


@dataclass
class Param:
  var_type: str
  name: str
  default: Expr | None = None


@dataclass
class FunctionDef(Node):
  name: str
  params: list[Param]
  return_type: str | None
  body: list[Statement]


@dataclass
class GlobalVar(Node):
  var_type: str
  name: str
  value: Expr


@dataclass
class GlobalConst(Node):
  const_type: str
  name: str
  value: Expr


@dataclass
class Program:
  directives: dict[str, str] = field(default_factory=dict)
  globals: list[GlobalVar] = field(default_factory=list)
  constants: list[GlobalConst] = field(default_factory=list)
  functions: list[FunctionDef] = field(default_factory=list)

from __future__ import annotations

from .errors import MCDPError
from .model import Assign, BinaryExpr, CallExpr, Expr, FunctionCall, Name, ReturnStmt, UnaryExpr
from .state import CTL_OBJECTIVE, RET_OBJECTIVE, CompileContext, ScoreRef


class ExpressionCodegenMixin:
  def _compile_assignment(self, stmt: Assign, ctx: CompileContext, out: list[str]) -> None:
    if isinstance(stmt.target, Name) and stmt.target.value in ctx.consts:
      raise MCDPError(f"compile-time value {stmt.target.value!r} cannot be reassigned", stmt.line)
    target = self._resolve_target(stmt.target, ctx)
    if stmt.op == "=":
      assert stmt.value is not None
      self._compile_expr_to(stmt.value, target, ctx, out)
      return
    if stmt.op in ("++", "--"):
      delta = 1 if stmt.op == "++" else -1
      verb = "add" if delta > 0 else "remove"
      self._emit(f"scoreboard players {verb} {target.holder} {target.objective} 1", ctx, out)
      return
    assert stmt.value is not None
    if stmt.op in ("+=", "-="):
      const = self._try_eval_int(stmt.value, ctx.consts)
      if const is not None:
        amount = const if stmt.op == "+=" else -const
        verb = "add" if amount >= 0 else "remove"
        self._emit(f"scoreboard players {verb} {target.holder} {target.objective} {abs(amount)}", ctx, out)
        return
    temp = self._new_temp()
    self._compile_expr_to(stmt.value, temp, ctx, out)
    op_map = {"+=": "+=", "-=": "-=", "*=": "*=", "/=": "/=", "%=": "%="}
    self._emit(f"scoreboard players operation {target.holder} {target.objective} {op_map[stmt.op]} {temp.holder} {temp.objective}", ctx, out)

  def _compile_expr_to(self, expr: Expr, target: ScoreRef, ctx: CompileContext, out: list[str]) -> None:
    const = self._try_eval_int(expr, ctx.consts)
    if const is not None:
      self._emit(f"scoreboard players set {target.holder} {target.objective} {const}", ctx, out)
      return
    if isinstance(expr, Name):
      src = self._resolve_name(expr, ctx)
      self._emit(f"scoreboard players operation {target.holder} {target.objective} = {src.holder} {src.objective}", ctx, out)
      return
    if isinstance(expr, CallExpr):
      if expr.name in self.functions:
        if not self.functions[expr.name].definition.return_type:
          raise MCDPError(f"void function {expr.name!r} cannot be used as an expression", expr.line)
        call = FunctionCall(line=expr.line, name=expr.name, args=[a for a in expr.args if isinstance(a, Expr)])
        self._compile_call(call, ctx, out, store_to=target)
        return
      src = self._resolve_call_ref(expr, ctx)
      self._emit(f"scoreboard players operation {target.holder} {target.objective} = {src.holder} {src.objective}", ctx, out)
      return
    if isinstance(expr, UnaryExpr):
      if expr.op == "+":
        self._compile_expr_to(expr.value, target, ctx, out)
        return
      if expr.op == "-":
        temp = self._new_temp()
        self._compile_expr_to(expr.value, temp, ctx, out)
        self._emit(f"scoreboard players set {target.holder} {target.objective} 0", ctx, out)
        self._emit(f"scoreboard players operation {target.holder} {target.objective} -= {temp.holder} {temp.objective}", ctx, out)
        return
      if expr.op == "!":
        temp = self._new_temp()
        self._compile_expr_to(expr.value, temp, ctx, out)
        self._emit(f"scoreboard players set {target.holder} {target.objective} 0", ctx, out)
        self._emit(f"execute if score {temp.holder} {temp.objective} matches 0 run scoreboard players set {target.holder} {target.objective} 1", ctx, out)
        return
    if isinstance(expr, BinaryExpr) and expr.op in ("+", "-", "*", "/", "%"):
      self._compile_expr_to(expr.left, target, ctx, out)
      right = self._new_temp()
      self._compile_expr_to(expr.right, right, ctx, out)
      op_map = {"+": "+=", "-": "-=", "*": "*=", "/": "/=", "%": "%="}
      self._emit(f"scoreboard players operation {target.holder} {target.objective} {op_map[expr.op]} {right.holder} {right.objective}", ctx, out)
      return
    raise MCDPError("expression cannot be compiled to an integer score", expr.line)

  def _compile_call(self, call: FunctionCall, ctx: CompileContext, out: list[str], store_to: ScoreRef | None) -> None:
    if call.name not in self.functions:
      raise MCDPError(f"unknown MCDP function {call.name!r}; native function calls must use a resource location without parentheses", call.line)
    info = self.functions[call.name]
    fn = info.definition
    args = list(call.args)
    required = sum(1 for p in fn.params if p.default is None)
    if not (required <= len(args) <= len(fn.params)):
      raise MCDPError(f"{call.name} expects {required}..{len(fn.params)} arguments, got {len(args)}", call.line)
    full_args: list[Expr] = []
    for i, p in enumerate(fn.params):
      if i < len(args):
        full_args.append(args[i])
      elif p.default is not None:
        full_args.append(p.default)
      else:
        raise AssertionError
    str_args: dict[str, str] = {}
    for p, arg in zip(fn.params, full_args):
      if p.var_type == "str":
        value = self._eval_const(arg, ctx.consts)
        if not isinstance(value, str):
          raise MCDPError(f"string parameter {p.name} requires a compile-time string", call.line)
        str_args[p.name] = value
      else:
        target = info.runtime_vars[p.name].ref
        self._compile_expr_to(arg, target, ctx, out)
    if str_args:
      resource = self._specialise(call.name, str_args)
    else:
      resource = self._resource_path(call.name)
    command = f"function {self.namespace}:{resource}"
    if store_to is not None:
      command = f"execute store result score {store_to.holder} {store_to.objective} run {command}"
    self._emit(command, ctx, out)

  def _compile_return(self, stmt: ReturnStmt, ctx: CompileContext, out: list[str]) -> None:
    fn = ctx.function
    if stmt.value is not None:
      if not fn.definition.return_type:
        raise MCDPError("cannot return a value from a void function", stmt.line)
      target = ScoreRef(self._return_value_holder(fn), RET_OBJECTIVE, fn.definition.return_type)
      self._compile_expr_to(stmt.value, target, ctx, out)
    elif fn.definition.return_type:
      raise MCDPError("non-void function must return a value", stmt.line)
    self._emit(f"scoreboard players set {self._return_flag_holder(fn)} {CTL_OBJECTIVE} 1", ctx, out, include_return_guard=False)

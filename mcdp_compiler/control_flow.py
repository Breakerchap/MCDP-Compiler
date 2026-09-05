from __future__ import annotations

from .errors import MCDPError
from .model import BinaryExpr, CallExpr, EveryBlock, Expr, ForStmt, IfStmt, Name, NativeCondition, Statement, UnaryExpr, WhileStmt
from .state import CTL_OBJECTIVE, CompileContext, ScoreRef


class ControlFlowMixin:
  def _compile_if(self, stmt: IfStmt, ctx: CompileContext, out: list[str]) -> None:
    remaining_paths: list[list[str]] = [[]]
    for branch in stmt.branches:
      true_paths = self._condition_paths(branch.condition, True, ctx)
      branch_paths = self._combine_paths(remaining_paths, true_paths)
      helper = self._compile_helper("if", branch.body, ctx)
      for path in branch_paths:
        self._emit_function_with_clauses(helper, path, ctx, out)
      false_paths = self._condition_paths(branch.condition, False, ctx)
      remaining_paths = self._combine_paths(remaining_paths, false_paths)
    if stmt.else_body is not None:
      helper = self._compile_helper("else", stmt.else_body, ctx)
      for path in remaining_paths:
        self._emit_function_with_clauses(helper, path, ctx, out)

  def _compile_while(self, stmt: WhileStmt, ctx: CompileContext, out: list[str]) -> None:
    helper = self._new_generated("while")
    break_ref = self._new_control("break")
    continue_ref = self._new_control("continue")
    self._emit(f"scoreboard players set {break_ref.holder} {break_ref.objective} 0", ctx, out)
    self._emit(f"scoreboard players set {continue_ref.holder} {continue_ref.objective} 0", ctx, out)
    self._emit(f"function {self.namespace}:{helper}", ctx, out)

    helper_out: list[str] = []
    self.files[f"data/{self.namespace}/function/{helper}.mcfunction"] = helper_out
    helper_ctx = ctx.child(execute_parts=[], loop_break=break_ref, loop_continue=continue_ref)
    helper_out.append(f"scoreboard players set {continue_ref.holder} {continue_ref.objective} 0")
    body_helper = self._compile_helper("while_body", stmt.body, helper_ctx)
    for path in self._condition_paths(stmt.condition, True, helper_ctx):
      clauses = path + [f"unless score {break_ref.holder} {break_ref.objective} matches 1"]
      self._emit_function_with_clauses(body_helper, clauses, helper_ctx, helper_out, include_context=False)
    for path in self._condition_paths(stmt.condition, True, helper_ctx):
      clauses = path + [f"unless score {break_ref.holder} {break_ref.objective} matches 1"]
      if ctx.function.has_return:
        clauses.append(f"unless score {self._return_flag_holder(ctx.function)} {CTL_OBJECTIVE} matches 1")
      helper_out.append(f"execute {' '.join(clauses)} run function {self.namespace}:{helper}")

  def _compile_for(self, stmt: ForStmt, ctx: CompileContext, out: list[str]) -> None:
    self._compile_statement(stmt.init, ctx, out)
    helper = self._new_generated("for")
    break_ref = self._new_control("break")
    continue_ref = self._new_control("continue")
    self._emit(f"scoreboard players set {break_ref.holder} {break_ref.objective} 0", ctx, out)
    self._emit(f"scoreboard players set {continue_ref.holder} {continue_ref.objective} 0", ctx, out)
    self._emit(f"function {self.namespace}:{helper}", ctx, out)

    helper_out: list[str] = []
    self.files[f"data/{self.namespace}/function/{helper}.mcfunction"] = helper_out
    loop_ctx = ctx.child(execute_parts=[], loop_break=break_ref, loop_continue=continue_ref)
    helper_out.append(f"scoreboard players set {continue_ref.holder} {continue_ref.objective} 0")
    body_helper = self._compile_helper("for_body", stmt.body, loop_ctx)
    true_paths = self._condition_paths(stmt.condition, True, loop_ctx)
    for path in true_paths:
      clauses = path + [f"unless score {break_ref.holder} {break_ref.objective} matches 1"]
      self._emit_function_with_clauses(body_helper, clauses, loop_ctx, helper_out, include_context=False)
    step_ctx = loop_ctx.child(
      extra_guards=loop_ctx.extra_guards + [f"unless score {break_ref.holder} {break_ref.objective} matches 1"],
      loop_continue=None,
    )
    self._compile_statement(stmt.step, step_ctx, helper_out)
    for path in self._condition_paths(stmt.condition, True, loop_ctx):
      clauses = path + [f"unless score {break_ref.holder} {break_ref.objective} matches 1"]
      if ctx.function.has_return:
        clauses.append(f"unless score {self._return_flag_holder(ctx.function)} {CTL_OBJECTIVE} matches 1")
      helper_out.append(f"execute {' '.join(clauses)} run function {self.namespace}:{helper}")

  def _compile_every(self, stmt: EveryBlock, ctx: CompileContext, out: list[str]) -> None:
    helper = self._new_generated("every")
    helper_out: list[str] = []
    self.files[f"data/{self.namespace}/function/{helper}.mcfunction"] = helper_out
    helper_ctx = ctx.child(execute_parts=[], extra_guards=[], return_guard=False)
    if stmt.condition is None:
      self._compile_statements(stmt.body, helper_ctx, helper_out)
      helper_out.append(f"schedule function {self.namespace}:{helper} {stmt.interval} replace")
    else:
      body_helper = self._compile_helper("every_body", stmt.body, helper_ctx)
      for path in self._condition_paths(stmt.condition, True, helper_ctx):
        self._emit_function_with_clauses(body_helper, path, helper_ctx, helper_out, include_context=False)
      for path in self._condition_paths(stmt.condition, True, helper_ctx):
        helper_out.append(f"execute {' '.join(path)} run schedule function {self.namespace}:{helper} {stmt.interval} replace")
    self._emit(f"schedule function {self.namespace}:{helper} {stmt.interval} replace", ctx, out)

  def _compile_helper(self, kind: str, body: list[Statement], ctx: CompileContext) -> str:
    helper = self._new_generated(kind)
    helper_out: list[str] = []
    self.files[f"data/{self.namespace}/function/{helper}.mcfunction"] = helper_out
    helper_ctx = ctx.child(execute_parts=[])
    self._compile_statements(body, helper_ctx, helper_out)
    if not helper_out:
      helper_out.append("# empty block")
    return helper

  def _condition_paths(self, expr: Expr, want_true: bool, ctx: CompileContext) -> list[list[str]]:
    const = self._try_eval_bool(expr, ctx.consts)
    if const is not None:
      return [[]] if const == want_true else []
    if isinstance(expr, UnaryExpr) and expr.op == "!":
      return self._condition_paths(expr.value, not want_true, ctx)
    if isinstance(expr, BinaryExpr) and expr.op == "&&":
      if want_true:
        return self._combine_paths(self._condition_paths(expr.left, True, ctx), self._condition_paths(expr.right, True, ctx))
      return self._condition_paths(expr.left, False, ctx) + self._combine_paths(self._condition_paths(expr.left, True, ctx), self._condition_paths(expr.right, False, ctx))
    if isinstance(expr, BinaryExpr) and expr.op == "||":
      if want_true:
        return self._condition_paths(expr.left, True, ctx) + self._combine_paths(self._condition_paths(expr.left, False, ctx), self._condition_paths(expr.right, True, ctx))
      return self._combine_paths(self._condition_paths(expr.left, False, ctx), self._condition_paths(expr.right, False, ctx))
    clause = self._atomic_condition_clause(expr, want_true, ctx)
    if clause is None:
      return [[]] if want_true else []
    return [[clause]]

  def _atomic_condition_clause(self, expr: Expr, want_true: bool, ctx: CompileContext) -> str | None:
    if isinstance(expr, NativeCondition):
      return f"{'if' if want_true else 'unless'} {expr.text}"
    if isinstance(expr, Name):
      ref = self._resolve_name(expr, ctx)
      return f"{'if' if want_true else 'unless'} score {ref.holder} {ref.objective} matches 1"
    if isinstance(expr, CallExpr) and expr.name not in self.functions:
      ref = self._resolve_call_ref(expr, ctx)
      return f"{'if' if want_true else 'unless'} score {ref.holder} {ref.objective} matches 1"
    if isinstance(expr, BinaryExpr) and expr.op in ("==", "!=", "<", "<=", ">", ">="):
      return self._comparison_clause(expr, want_true, ctx)
    raise MCDPError("conditions must compare score values/constants, use bools, or use a native execute-if condition", expr.line)

  def _comparison_clause(self, expr: BinaryExpr, want_true: bool, ctx: CompileContext) -> str:
    left_const = self._try_eval_int(expr.left, ctx.consts)
    right_const = self._try_eval_int(expr.right, ctx.consts)
    if left_const is not None and right_const is not None:
      result = self._compare(left_const, expr.op, right_const)
      return "unless score #never _mcdp_tmp matches 1" if result == want_true else "if score #never _mcdp_tmp matches 1"

    if left_const is None:
      left_ref = self._resolve_score_expr(expr.left, ctx)
    else:
      left_ref = self._resolve_score_expr(expr.right, ctx)
      right_const = left_const
      reverse = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}
      op = reverse[expr.op]
      return self._score_vs_int_clause(left_ref, op, right_const, want_true)

    if right_const is not None:
      return self._score_vs_int_clause(left_ref, expr.op, right_const, want_true)
    right_ref = self._resolve_score_expr(expr.right, ctx)
    op = expr.op
    if op == "==":
      base = f"score {left_ref.holder} {left_ref.objective} = {right_ref.holder} {right_ref.objective}"
      positive = True
    elif op == "!=":
      base = f"score {left_ref.holder} {left_ref.objective} = {right_ref.holder} {right_ref.objective}"
      positive = False
    else:
      base = f"score {left_ref.holder} {left_ref.objective} {op} {right_ref.holder} {right_ref.objective}"
      positive = True
    use_if = want_true == positive
    return f"{'if' if use_if else 'unless'} {base}"

  def _score_vs_int_clause(self, ref: ScoreRef, op: str, value: int, want_true: bool) -> str:
    if op == "==":
      rng, positive = str(value), True
    elif op == "!=":
      rng, positive = str(value), False
    elif op == ">":
      rng, positive = f"{value + 1}..", True
    elif op == ">=":
      rng, positive = f"{value}..", True
    elif op == "<":
      rng, positive = f"..{value - 1}", True
    elif op == "<=":
      rng, positive = f"..{value}", True
    else:
      raise AssertionError(op)
    use_if = want_true == positive
    return f"{'if' if use_if else 'unless'} score {ref.holder} {ref.objective} matches {rng}"

  def _emit_function_with_clauses(self, helper: str, clauses: list[str], ctx: CompileContext, out: list[str], include_context: bool = True) -> None:
    parts = list(ctx.execute_parts) if include_context else []
    parts.extend(self._guard_clauses(ctx, include_return=True))
    parts.extend(clauses)
    cmd = f"function {self.namespace}:{helper}"
    if parts:
      out.append(f"execute {' '.join(parts)} run {cmd}")
    else:
      out.append(cmd)

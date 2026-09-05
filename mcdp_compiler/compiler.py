from __future__ import annotations

import shutil
from pathlib import Path

from .control_flow import ControlFlowMixin
from .errors import MCDPError
from .expression_codegen import ExpressionCodegenMixin
from .model import (
  Assign,
  BreakStmt,
  ContinueStmt,
  EveryBlock,
  ExecuteBlock,
  ForStmt,
  FunctionCall,
  FunctionDef,
  IfStmt,
  Name,
  Program,
  RawBlock,
  RawCommand,
  ReturnStmt,
  ScheduleBlock,
  ScheduleCall,
  Statement,
  VarDecl,
  WhileStmt,
)
from .parser import parse
from .runtime import RuntimeMixin
from .state import CTL_OBJECTIVE, RET_OBJECTIVE, TMP_OBJECTIVE, CompileContext, FunctionInfo, ScoreRef, VarSymbol


class Compiler(ExpressionCodegenMixin, ControlFlowMixin, RuntimeMixin):
  def __init__(self, program: Program):
    self.program = program
    self.namespace = self._validate_namespace(program.directives.get("namespace", "mcdp"))
    self.description = program.directives.get("description", program.directives.get("name", "Compiled MCDP datapack"))
    self.pack_format = program.directives.get("pack_format", "auto")
    self.functions: dict[str, FunctionInfo] = {}
    self.global_vars: dict[str, VarSymbol] = {}
    self.custom_objectives: dict[str, str] = {}
    self.global_consts: dict[str, int | bool | str] = {}
    self.objectives: set[str] = {TMP_OBJECTIVE, CTL_OBJECTIVE, RET_OBJECTIVE}
    self.files: dict[str, list[str]] = {}
    self.generated_counter = 0
    self.temp_counter = 0
    self.control_counter = 0
    self.specialisations: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
    self.pending_specialisations: list[tuple[str, dict[str, str], str]] = []
    self._prepare_symbols()

  @classmethod
  def from_source(cls, source: str) -> "Compiler":
    return cls(parse(source))

  def compile(self) -> dict[str, str]:
    for name, info in self.functions.items():
      if any(p.var_type == "str" for p in info.definition.params):
        continue
      self._compile_function(info, self._resource_path(name), {})
    while self.pending_specialisations:
      name, str_args, resource_path = self.pending_specialisations.pop(0)
      self._compile_function(self.functions[name], resource_path, str_args)
    self._inject_load_setup()
    self._add_pack_metadata()
    self._add_tags()
    return {path: "\n".join(lines).rstrip() + "\n" for path, lines in self.files.items()}

  def write(self, output: str | Path, clean: bool = True) -> Path:
    out = Path(output)
    resolved = out.resolve()
    cwd = Path.cwd().resolve()
    root = Path(resolved.anchor).resolve()
    if clean and out.exists():
      if resolved in (cwd, root):
        raise MCDPError(f"refusing to delete unsafe output directory {out}")
      shutil.rmtree(out)
    for rel, content in self.compile().items():
      path = out / rel
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(content, encoding="utf-8")
    return out

  def _prepare_symbols(self) -> None:
    for const in self.program.constants:
      if const.name in self.global_consts:
        raise MCDPError(f"duplicate constant {const.name!r}", const.line)
      self.global_consts[const.name] = self._eval_const(const.value, self.global_consts)
      self._check_const_type(const.const_type, self.global_consts[const.name], const.line)
    for glob in self.program.globals:
      full = f"global.{glob.name}"
      if full in self.global_vars:
        raise MCDPError(f"duplicate global {glob.name!r}", glob.line)
      ref = ScoreRef("global", self._objective_for(full), glob.var_type)
      self.objectives.add(ref.objective)
      self.global_vars[full] = VarSymbol(glob.name, "global", glob.var_type, ref)
    for fn in self.program.functions:
      if fn.name in self.functions:
        raise MCDPError(f"duplicate function {fn.name!r}", fn.line)
      self.functions[fn.name] = FunctionInfo(definition=fn, has_return=self._contains_return(fn.body))
    for name, info in self.functions.items():
      fn = info.definition
      for param in fn.params:
        if param.var_type == "str":
          continue
        self._add_runtime_symbol(info, param.name, param.var_type, None, fn.line)
      self._collect_decls(info, fn.body)

  def _collect_decls(self, info: FunctionInfo, body: list[Statement]) -> None:
    for stmt in body:
      if isinstance(stmt, VarDecl):
        if stmt.var_type == "str" or stmt.is_const:
          if stmt.name in info.local_const_decls:
            raise MCDPError(f"duplicate compile-time local {stmt.name!r}", stmt.line)
          info.local_const_decls[stmt.name] = stmt
        else:
          self._add_runtime_symbol(info, stmt.name, stmt.var_type, stmt.holder, stmt.line)
      elif isinstance(stmt, ForStmt):
        if isinstance(stmt.init, VarDecl):
          init = stmt.init
          if init.var_type == "str" or init.is_const:
            info.local_const_decls[init.name] = init
          else:
            self._add_runtime_symbol(info, init.name, init.var_type, init.holder, init.line)
        self._collect_decls(info, stmt.body)
      elif isinstance(stmt, ExecuteBlock):
        self._collect_decls(info, stmt.body)
      elif isinstance(stmt, IfStmt):
        for b in stmt.branches:
          self._collect_decls(info, b.body)
        if stmt.else_body:
          self._collect_decls(info, stmt.else_body)
      elif isinstance(stmt, WhileStmt):
        self._collect_decls(info, stmt.body)
      elif isinstance(stmt, ScheduleBlock):
        self._collect_decls(info, stmt.body)
      elif isinstance(stmt, EveryBlock):
        self._collect_decls(info, stmt.body)

  def _add_runtime_symbol(self, info: FunctionInfo, name: str, var_type: str, holder: str | None, line: int) -> None:
    if name in info.runtime_vars:
      existing = info.runtime_vars[name]
      if holder is not None and (existing.ref.holder != holder or existing.ref.objective != name):
        raise MCDPError(f"runtime variable {name!r} declared twice with different storage", line)
      return
    if holder is not None:
      objective = self._validate_objective(name, line)
      ref = ScoreRef(holder, objective, var_type)
      self.custom_objectives[name] = var_type
    else:
      full = f"{info.definition.name}.{name}"
      ref = ScoreRef(info.definition.name, self._objective_for(full), var_type)
    self.objectives.add(ref.objective)
    info.runtime_vars[name] = VarSymbol(name, info.definition.name, var_type, ref, custom_holder=holder is not None)

  def _compile_function(self, info: FunctionInfo, resource_path: str, str_args: dict[str, str]) -> None:
    file_path = f"data/{self.namespace}/function/{resource_path}.mcfunction"
    if file_path in self.files:
      return
    lines: list[str] = []
    self.files[file_path] = lines
    consts = dict(self.global_consts)
    consts.update(str_args)
    ctx = CompileContext(function=info, consts=consts)
    if info.has_return:
      lines.append(f"scoreboard players set {self._return_flag_holder(info)} {CTL_OBJECTIVE} 0")
      if info.definition.return_type:
        lines.append(f"scoreboard players set {self._return_value_holder(info)} {RET_OBJECTIVE} 0")
    self._compile_statements(info.definition.body, ctx, lines)
    if info.definition.return_type:
      lines.append(f"return run scoreboard players get {self._return_value_holder(info)} {RET_OBJECTIVE}")

  def _compile_statements(self, statements: list[Statement], ctx: CompileContext, out: list[str]) -> None:
    for stmt in statements:
      self._compile_statement(stmt, ctx, out)

  def _compile_statement(self, stmt: Statement, ctx: CompileContext, out: list[str]) -> None:
    if isinstance(stmt, RawCommand):
      self._emit_interpolated(stmt.text, ctx, out)
      return
    if isinstance(stmt, RawBlock):
      for line in stmt.lines:
        self._emit(line, ctx, out, interpolate=False)
      return
    if isinstance(stmt, VarDecl):
      if stmt.var_type == "str" or stmt.is_const:
        value = self._eval_const(stmt.value, ctx.consts)
        self._check_const_type(stmt.var_type, value, stmt.line)
        ctx.consts[stmt.name] = value
        return
      target = self._resolve_name(Name(line=stmt.line, value=stmt.name), ctx)
      self._compile_expr_to(stmt.value, target, ctx, out)
      return
    if isinstance(stmt, Assign):
      self._compile_assignment(stmt, ctx, out)
      return
    if isinstance(stmt, FunctionCall):
      self._compile_call(stmt, ctx, out, store_to=None)
      return
    if isinstance(stmt, ExecuteBlock):
      child = ctx.child(execute_parts=ctx.execute_parts + [stmt.prefix])
      self._compile_statements(stmt.body, child, out)
      return
    if isinstance(stmt, IfStmt):
      self._compile_if(stmt, ctx, out)
      return
    if isinstance(stmt, WhileStmt):
      self._compile_while(stmt, ctx, out)
      return
    if isinstance(stmt, ForStmt):
      self._compile_for(stmt, ctx, out)
      return
    if isinstance(stmt, ReturnStmt):
      self._compile_return(stmt, ctx, out)
      return
    if isinstance(stmt, BreakStmt):
      if ctx.loop_break is None:
        raise MCDPError("break used outside a loop", stmt.line)
      self._emit(f"scoreboard players set {ctx.loop_break.holder} {ctx.loop_break.objective} 1", ctx, out)
      return
    if isinstance(stmt, ContinueStmt):
      if ctx.loop_continue is None:
        raise MCDPError("continue used outside a loop", stmt.line)
      self._emit(f"scoreboard players set {ctx.loop_continue.holder} {ctx.loop_continue.objective} 1", ctx, out)
      return
    if isinstance(stmt, ScheduleBlock):
      helper = self._new_generated("schedule")
      helper_out: list[str] = []
      self.files[f"data/{self.namespace}/function/{helper}.mcfunction"] = helper_out
      helper_ctx = ctx.child(execute_parts=[], extra_guards=[], return_guard=False)
      self._compile_statements(stmt.body, helper_ctx, helper_out)
      self._emit(f"schedule function {self.namespace}:{helper} {stmt.delay} replace", ctx, out)
      return
    if isinstance(stmt, ScheduleCall):
      helper = self._new_generated("schedule_call")
      helper_out: list[str] = []
      self.files[f"data/{self.namespace}/function/{helper}.mcfunction"] = helper_out
      helper_ctx = ctx.child(execute_parts=[], extra_guards=[], return_guard=False)
      self._compile_call(stmt.call, helper_ctx, helper_out, store_to=None)
      self._emit(f"schedule function {self.namespace}:{helper} {stmt.delay} replace", ctx, out)
      return
    if isinstance(stmt, EveryBlock):
      self._compile_every(stmt, ctx, out)
      return
    raise MCDPError(f"unsupported statement {type(stmt).__name__}", stmt.line)


def compile_file(source: str | Path, output: str | Path, clean: bool = True) -> Path:
  source_path = Path(source)
  compiler = Compiler.from_source(source_path.read_text(encoding="utf-8"))
  return compiler.write(output, clean=clean)

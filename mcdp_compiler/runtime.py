from __future__ import annotations

import hashlib
import json
import re

from .errors import MCDPError
from .model import BinaryExpr, CallExpr, ExecuteBlock, Expr, ForStmt, FunctionDef, IfStmt, Literal, Name, ReturnStmt, ScheduleBlock, Statement, UnaryExpr, WhileStmt
from .state import CURRENT_DATA_PACK_FORMAT, CTL_OBJECTIVE, RET_OBJECTIVE, TMP_OBJECTIVE, CompileContext, FunctionInfo, ScoreRef


class RuntimeMixin:
  def _emit_interpolated(self, text: str, ctx: CompileContext, out: list[str]) -> None:
    pattern = re.compile(r"\$([A-Za-z_][A-Za-z0-9_.]*(?:\([^)]*\))?)")
    runtime_refs: list[tuple[str, ScoreRef]] = []

    def repl(match: re.Match[str]) -> str:
      token = match.group(1)
      if token in ctx.consts:
        return str(ctx.consts[token]).lower() if isinstance(ctx.consts[token], bool) else str(ctx.consts[token])
      expr = self._parse_ref_token(token, ctx, match)
      if isinstance(expr, ScoreRef):
        key = f"v{len(runtime_refs)}"
        runtime_refs.append((key, expr))
        return f"$({key})"
      raise AssertionError

    replaced = pattern.sub(repl, text)
    if not runtime_refs:
      self._emit(replaced, ctx, out, interpolate=False)
      return

    macro = self._new_generated("macro")
    macro_file = f"data/{self.namespace}/function/{macro}.mcfunction"
    self.files[macro_file] = ["$" + replaced]
    storage_path = f"macro.{self.generated_counter}"
    for key, ref in runtime_refs:
      prep = f"execute store result storage {self.namespace}:mcdp {storage_path}.{key} int 1 run scoreboard players get {ref.holder} {ref.objective}"
      self._emit(prep, ctx, out, interpolate=False)
    self._emit(f"function {self.namespace}:{macro} with storage {self.namespace}:mcdp {storage_path}", ctx, out, interpolate=False)

  def _parse_ref_token(self, token: str, ctx: CompileContext, match=None) -> ScoreRef:
    if "(" in token:
      name, holder = token[:-1].split("(", 1)
      return self._custom_ref(name, holder, 0)
    return self._resolve_name(Name(line=0, value=token), ctx)

  def _emit(self, command: str, ctx: CompileContext, out: list[str], interpolate: bool = False, include_return_guard: bool = True) -> None:
    if interpolate:
      self._emit_interpolated(command, ctx, out)
      return
    parts = list(ctx.execute_parts)
    parts.extend(self._guard_clauses(ctx, include_return=include_return_guard))
    if parts:
      out.append(f"execute {' '.join(parts)} run {command}")
    else:
      out.append(command)

  def _guard_clauses(self, ctx: CompileContext, include_return: bool) -> list[str]:
    guards = list(ctx.extra_guards)
    if include_return and ctx.return_guard and ctx.function.has_return:
      guards.append(f"unless score {self._return_flag_holder(ctx.function)} {CTL_OBJECTIVE} matches 1")
    if ctx.loop_break is not None:
      guards.append(f"unless score {ctx.loop_break.holder} {ctx.loop_break.objective} matches 1")
    if ctx.loop_continue is not None:
      guards.append(f"unless score {ctx.loop_continue.holder} {ctx.loop_continue.objective} matches 1")
    return guards

  def _resolve_target(self, expr: Expr, ctx: CompileContext) -> ScoreRef:
    if isinstance(expr, Name):
      return self._resolve_name(expr, ctx)
    if isinstance(expr, CallExpr) and expr.name not in self.functions:
      return self._resolve_call_ref(expr, ctx)
    raise MCDPError("assignment target must be an int/bool variable or score-holder variable", expr.line)

  def _resolve_score_expr(self, expr: Expr, ctx: CompileContext) -> ScoreRef:
    if isinstance(expr, Name):
      return self._resolve_name(expr, ctx)
    if isinstance(expr, CallExpr) and expr.name not in self.functions:
      return self._resolve_call_ref(expr, ctx)
    raise MCDPError("comparison side must be a score variable or integer constant", expr.line)

  def _resolve_name(self, expr: Name, ctx: CompileContext) -> ScoreRef:
    name = expr.value
    if name in ctx.function.runtime_vars:
      return ctx.function.runtime_vars[name].ref
    if name.startswith("global."):
      if name not in self.global_vars:
        raise MCDPError(f"unknown global variable {name!r}", expr.line or None)
      return self.global_vars[name].ref
    if "." in name:
      scope, local = name.split(".", 1)
      if scope in self.functions and local in self.functions[scope].runtime_vars:
        return self.functions[scope].runtime_vars[local].ref
    raise MCDPError(f"unknown runtime variable {name!r}", expr.line or None)

  def _resolve_call_ref(self, expr: CallExpr, ctx: CompileContext) -> ScoreRef:
    if len(expr.args) != 1:
      raise MCDPError("score-holder variable access requires exactly one holder", expr.line)
    holder_arg = expr.args[0]
    if isinstance(holder_arg, str):
      holder = holder_arg
    elif isinstance(holder_arg, Name):
      holder = holder_arg.value
    else:
      const = self._eval_const(holder_arg, ctx.consts)
      holder = str(const)
    return self._custom_ref(expr.name, holder, expr.line)

  def _custom_ref(self, name: str, holder: str, line: int) -> ScoreRef:
    objective = self._validate_objective(name, line or None)
    if name not in self.custom_objectives:
      self.custom_objectives[name] = "int"
      self.objectives.add(objective)
    return ScoreRef(holder, objective, self.custom_objectives.get(name, "int"))

  def _eval_const(self, expr: Expr, env: dict[str, int | bool | str]) -> int | bool | str:
    if isinstance(expr, Literal):
      return expr.value
    if isinstance(expr, Name):
      if expr.value in env:
        return env[expr.value]
      raise MCDPError(f"{expr.value!r} is not a compile-time constant", expr.line)
    if isinstance(expr, UnaryExpr):
      v = self._eval_const(expr.value, env)
      if expr.op == "-" and isinstance(v, int): return -v
      if expr.op == "+" and isinstance(v, int): return +v
      if expr.op == "!" and isinstance(v, (int, bool)): return not bool(v)
      raise MCDPError("invalid compile-time unary expression", expr.line)
    if isinstance(expr, BinaryExpr):
      a = self._eval_const(expr.left, env)
      b = self._eval_const(expr.right, env)
      if expr.op == "+": return a + b
      if expr.op == "-": return a - b
      if expr.op == "*": return a * b
      if expr.op == "/": return int(a / b)
      if expr.op == "%": return a % b
      if expr.op == "==": return a == b
      if expr.op == "!=": return a != b
      if expr.op == "<": return a < b
      if expr.op == "<=": return a <= b
      if expr.op == ">": return a > b
      if expr.op == ">=": return a >= b
      if expr.op == "&&": return bool(a) and bool(b)
      if expr.op == "||": return bool(a) or bool(b)
    raise MCDPError("expression is not compile-time constant", expr.line)

  def _try_eval_int(self, expr: Expr, env: dict[str, int | bool | str]) -> int | None:
    try: value = self._eval_const(expr, env)
    except MCDPError: return None
    if isinstance(value, bool): return int(value)
    return value if isinstance(value, int) else None

  def _try_eval_bool(self, expr: Expr, env: dict[str, int | bool | str]) -> bool | None:
    try: value = self._eval_const(expr, env)
    except MCDPError: return None
    if isinstance(value, (bool, int)): return bool(value)
    return None

  @staticmethod
  def _compare(a: int, op: str, b: int) -> bool:
    return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]

  def _specialise(self, name: str, str_args: dict[str, str]) -> str:
    key = (name, tuple(sorted(str_args.items())))
    if key in self.specialisations: return self.specialisations[key]
    digest = hashlib.sha1(repr(key).encode()).hexdigest()[:10]
    resource = f"__generated/{self._resource_path(name).replace('/', '_')}_str_{digest}"
    self.specialisations[key] = resource
    self.pending_specialisations.append((name, dict(str_args), resource))
    return resource

  def _new_temp(self) -> ScoreRef:
    holder = f"#t{self.temp_counter}"
    self.temp_counter += 1
    return ScoreRef(holder, TMP_OBJECTIVE)

  def _new_control(self, kind: str) -> ScoreRef:
    holder = f"#{kind[0]}{self.control_counter}"
    self.control_counter += 1
    return ScoreRef(holder, CTL_OBJECTIVE, "bool")

  def _new_generated(self, kind: str) -> str:
    value = f"__generated/{kind}_{self.generated_counter}"
    self.generated_counter += 1
    return value

  def _return_flag_holder(self, info: FunctionInfo) -> str:
    digest = hashlib.sha1(info.definition.name.encode()).hexdigest()[:8]
    return f"#r{digest}"

  def _return_value_holder(self, info: FunctionInfo) -> str:
    digest = hashlib.sha1(info.definition.name.encode()).hexdigest()[:8]
    return f"#v{digest}"

  def _objective_for(self, full_name: str) -> str:
    if len(full_name) <= 16 and re.fullmatch(r"[A-Za-z0-9_.+-]+", full_name): return full_name
    return "m." + hashlib.sha1(full_name.encode()).hexdigest()[:14]

  def _validate_objective(self, name: str, line: int | None) -> str:
    if len(name) > 16: raise MCDPError(f"custom scoreboard objective {name!r} exceeds Minecraft's 16-character limit", line)
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", name): raise MCDPError(f"invalid scoreboard objective name {name!r}", line)
    return name

  def _validate_namespace(self, namespace: str) -> str:
    if not re.fullmatch(r"[a-z0-9_.-]+", namespace): raise MCDPError("namespace may contain only lowercase a-z, digits, _, -, and .")
    return namespace

  def _resource_path(self, name: str) -> str:
    parts = name.split("/")
    converted = []
    for part in parts:
      s = re.sub(r"(?<!^)(?=[A-Z])", "_", part).lower()
      s = re.sub(r"[^a-z0-9_.-]", "_", s)
      converted.append(s)
    return "/".join(converted)

  @staticmethod
  def _combine_paths(a: list[list[str]], b: list[list[str]]) -> list[list[str]]:
    return [x + y for x in a for y in b]

  def _contains_return(self, body: list[Statement]) -> bool:
    for stmt in body:
      if isinstance(stmt, ReturnStmt): return True
      nested: list[list[Statement]] = []
      if isinstance(stmt, ExecuteBlock): nested.append(stmt.body)
      elif isinstance(stmt, IfStmt):
        nested.extend(b.body for b in stmt.branches)
        if stmt.else_body: nested.append(stmt.else_body)
      elif isinstance(stmt, WhileStmt): nested.append(stmt.body)
      elif isinstance(stmt, ForStmt): nested.append(stmt.body)
      elif isinstance(stmt, ScheduleBlock): nested.append(stmt.body)
      if any(self._contains_return(x) for x in nested): return True
    return False

  @staticmethod
  def _check_const_type(var_type: str, value, line: int) -> None:
    ok = ((var_type == "str" and isinstance(value, str)) or (var_type == "bool" and isinstance(value, bool)) or (var_type == "int" and isinstance(value, int) and not isinstance(value, bool)))
    if not ok: raise MCDPError(f"expected compile-time {var_type}, got {type(value).__name__}", line)

  def _inject_load_setup(self) -> None:
    load_resource = self._resource_path("load")
    load_path = f"data/{self.namespace}/function/{load_resource}.mcfunction"
    if load_path not in self.files: self.files[load_path] = []
    setup = [f"scoreboard objectives add {obj} dummy" for obj in sorted(self.objectives)]
    dummy_info = self.functions.get("load")
    if dummy_info is None:
      fn = FunctionDef(line=0, name="load", params=[], return_type=None, body=[])
      dummy_info = FunctionInfo(definition=fn)
    ctx = CompileContext(function=dummy_info, consts=dict(self.global_consts))
    init_lines: list[str] = []
    for glob in self.program.globals:
      target = self.global_vars[f"global.{glob.name}"].ref
      self._compile_expr_to(glob.value, target, ctx, init_lines)
    self.files[load_path] = setup + init_lines + self.files[load_path]

  def _add_pack_metadata(self) -> None:
    if self.pack_format == "auto":
      min_format = max_format = CURRENT_DATA_PACK_FORMAT
      pack = {"pack": {"description": self.description, "min_format": min_format, "max_format": max_format}}
    else:
      m = re.fullmatch(r"(\d+)(?:\.(\d+))?", self.pack_format)
      if not m: raise MCDPError("pack_format must be 'auto' or a version like 107.1")
      version = [int(m.group(1)), int(m.group(2) or 0)]
      if version[0] >= 82: pack = {"pack": {"description": self.description, "min_format": version, "max_format": version}}
      else: pack = {"pack": {"description": self.description, "pack_format": version[0], "supported_formats": version[0]}}
    self.files["pack.mcmeta"] = json.dumps(pack, indent=2).splitlines()

  def _add_tags(self) -> None:
    for special in ("load", "tick"):
      if special in self.functions or special == "load":
        resource = self._resource_path(special)
        content = {"values": [f"{self.namespace}:{resource}"]}
        self.files[f"data/minecraft/tags/function/{special}.json"] = json.dumps(content, indent=2).splitlines()


from __future__ import annotations

from dataclasses import dataclass, field

from .model import FunctionDef, VarDecl


CURRENT_DATA_PACK_FORMAT = [107, 1]  # Minecraft Java 26.2
TMP_OBJECTIVE = "_mcdp_tmp"
CTL_OBJECTIVE = "_mcdp_ctl"
RET_OBJECTIVE = "_mcdp_ret"


@dataclass(frozen=True)
class ScoreRef:
  holder: str
  objective: str
  var_type: str = "int"


@dataclass
class VarSymbol:
  source_name: str
  scope: str
  var_type: str
  ref: ScoreRef
  custom_holder: bool = False


@dataclass
class FunctionInfo:
  definition: FunctionDef
  runtime_vars: dict[str, VarSymbol] = field(default_factory=dict)
  local_const_decls: dict[str, VarDecl] = field(default_factory=dict)
  has_return: bool = False


@dataclass
class CompileContext:
  function: FunctionInfo
  consts: dict[str, int | bool | str]
  execute_parts: list[str] = field(default_factory=list)
  extra_guards: list[str] = field(default_factory=list)
  loop_break: ScoreRef | None = None
  loop_continue: ScoreRef | None = None
  return_guard: bool = True

  def child(self, **changes) -> "CompileContext":
    data = {
      "function": self.function,
      "consts": dict(self.consts),
      "execute_parts": list(self.execute_parts),
      "extra_guards": list(self.extra_guards),
      "loop_break": self.loop_break,
      "loop_continue": self.loop_continue,
      "return_guard": self.return_guard,
    }
    data.update(changes)
    return CompileContext(**data)

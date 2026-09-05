import json
from pathlib import Path

import pytest

from mcdp_compiler.compiler import Compiler
from mcdp_compiler.errors import MCDPError


def compile_text(source: str):
  return Compiler.from_source(source).compile()


def test_basic_execute_and_variables():
  files = compile_text('''
#! namespace test
#! description "test"
#! pack_format auto

func hello(int n) {
  set int x = 2
  x += 3
  execute as @a at @s {
    if n > 2 {
      say value=$x
    }
  }
}

func load {}
func tick {
  function hello(4)
}
''')
  assert "pack.mcmeta" in files
  assert "data/test/function/hello.mcfunction" in files
  assert "data/minecraft/tags/function/tick.json" in files
  assert any("scoreboard players" in text for text in files.values())
  assert any("with storage test:mcdp" in text for text in files.values())


def test_string_specialisation():
  files = compile_text('''
#! namespace test
func announce(str message) {
  say $message
}
func load {
  function announce("hello")
}
''')
  assert any("$message" not in content and "say hello" in content for path, content in files.items() if "__generated" in path)


def test_const_reassignment_errors():
  with pytest.raises(MCDPError):
    compile_text('''
#! namespace test
func bad {
  set str x = "a"
  x = "b"
}
''')


def test_return_value_and_call_expression():
  files = compile_text('''
#! namespace test
func square(int n) -> int {
  return n * n
}
func use {
  set int x = square(5)
}
func load {}
''')
  use = files["data/test/function/use.mcfunction"]
  assert "execute store result score" in use
  square = files["data/test/function/square.mcfunction"]
  assert "return run scoreboard players get" in square


def test_current_pack_metadata():
  files = compile_text('#! namespace test\nfunc load {}\n')
  meta = json.loads(files["pack.mcmeta"])
  assert meta["pack"]["min_format"] == [107, 1]
  assert meta["pack"]["max_format"] == [107, 1]


def test_compact_empty_function_and_semiglobal_access():
  files = compile_text('''
#! namespace test
func maths {
  set int a = 1
}
func other {
  maths.a = 4
}
func load{}
''')
  assert "scoreboard players set maths maths.a 4" in files["data/test/function/other.mcfunction"]


def test_custom_score_holder_and_boolean_paths():
  files = compile_text('''
#! namespace test
func check(bool enabled) {
  set int score(@s) = 2
  if enabled || score(@s) > 3 {
    say yes
  }
}
func load {}
''')
  text = files["data/test/function/check.mcfunction"]
  assert "if score check check.enabled matches 1" in text
  assert "unless score check check.enabled matches 1" in text
  assert "if score @s score matches 4.." in text


def test_for_continue_still_increments():
  files = compile_text('''
#! namespace test
func loop {
  for int i = 0; i < 3; i++ {
    continue
  }
}
func load {}
''')
  helper_text = "\n".join(v for k, v in files.items() if "/__generated/for_" in k and "for_body" not in k)
  assert "scoreboard players add loop loop.i 1" in helper_text


def test_schedule_and_every_generate_helpers():
  files = compile_text('''
#! namespace test
func timers {
  schedule 5s {
    say later
  }
  every 1s {
    say again
  }
}
func load {}
''')
  timers = files["data/test/function/timers.mcfunction"]
  assert "schedule function test:__generated/schedule_" in timers
  assert "schedule function test:__generated/every_" in timers


def test_void_function_cannot_be_expression():
  with pytest.raises(MCDPError):
    compile_text('''
#! namespace test
func nope {}
func use {
  set int x = nope()
}
''')

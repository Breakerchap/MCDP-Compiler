# MCDP Compiler

MCDP is a small superset of Java Edition `mcfunction`. It keeps native commands valid, but adds blocks, typed scoreboard variables, compile-time strings/constants, parameters, control flow, scheduling, and automatic datapack generation.

The compiler currently targets **Minecraft Java 26.2** when `#! pack_format auto` is used (data pack format `107.1`).

## Install / run

Requires Python 3.10+.

```bash
python -m pip install -e .
mcdp example.mcdp -o build/demo
```

Or without installing:

```bash
python -m mcdp_compiler example.mcdp -o build/demo
```

The output directory is a complete datapack containing `pack.mcmeta`, generated functions, helper functions, and automatic `minecraft:load` / `minecraft:tick` tags.

## Core syntax

```mcdp
#! namespace demo
#! name "Demo"
#! description "A demo datapack"
#! pack_format auto

global int round = 0
global bool running = false
const int MAX_ROUNDS = 5
const str PREFIX = "[Demo]"

func load {
  say $PREFIX loaded
}

func tick {
  if global.running {
    function gameTick()
  }
}

func maths(int n = 0) -> int {
  set int x = n + 2
  return x * 2
}

func gameTick {
  execute as @a at @s {
    set int score(@s) = 0
    score(@s)++

    if score(@s) >= 10 {
      say score=$score(@s)
    }
  }
}
```

### Variables

- `int` and `bool` are runtime scoreboard-backed values.
- `str` is compile-time only and immutable.
- `const int`, `const bool`, and `const str` are compile-time constants.
- Runtime locals are semi-global: `x` inside `maths` can be addressed elsewhere as `maths.x`.
- `set int score(@s) = 10` exposes a real scoreboard objective named `score` using `@s` as its score holder.
- Long internal objective names are automatically shortened; source-level variable names do not change.
- CamelCase MCDP function names are emitted as lowercase snake_case resource paths, so `welcomePlayer` becomes `demo:welcome_player`.

### Control flow

```mcdp
if n > 2 {
  ...
}
else if n != 2 {
  ...
}
else {
  ...
}

if entity @s[tag=admin] {
  ...
}

unless block ~ ~-1 ~ minecraft:stone {
  ...
}

while n > 0 {
  n--
  if n == 5 {
    continue
  }
  if n == 2 {
    break
  }
}

for int i = 0; i < 10; i++ {
  say $i
}
```

`while` and `for` run in the current tick using generated recursive helper functions. Large or infinite same-tick loops can still hit Minecraft's command/function limits.

### Functions and returns

```mcdp
func square(int n) -> int {
  return n * n
}

func announce(str message) {
  say $message
}

func demo {
  set int x = square(5)
  function announce("hello")
}
```

`str` parameters are compile-time-specialised. Integer and bool parameters are scoreboard-backed and semi-global like other runtime variables.

### Runtime interpolation

Compile-time values are substituted directly:

```mcdp
const str PREFIX = "[Demo]"
say $PREFIX hello
```

Runtime score values can also be interpolated:

```mcdp
say score=$score(@s)
tp @s $x $y $z
```

The compiler generates modern Minecraft function macros plus storage plumbing for these commands.

### Execute blocks

```mcdp
execute as @a at @s {
  if entity @s[gamemode=adventure] {
    tp @s ^10 ^ ^10
    say hi
  }
}
```

Execution context is preserved through generated helper functions.

### Scheduling

```mcdp
schedule 5s {
  say later
}

schedule function maths(5) 10s

every 1s {
  say every second
}

every 1t while global.running {
  global.round++
}
```

Scheduled blocks execute later as normal scheduled functions; as with vanilla `/schedule`, executor/position context is not preserved.

### Native mcfunction

Any unrecognised statement is emitted as an ordinary Minecraft command:

```mcdp
function minecraft:some_native_function
execute as @a run effect give @s minecraft:speed 5 1 true
```

Use `raw` when you explicitly want a block of lines emitted without MCDP statement parsing:

```mcdp
raw {
  execute as @a at @s run say raw mcfunction
}
```

### Comments

- `# comment` — discarded.
- `#? explanation` — discarded; intended for language/compile notes.
- `#* generated example` — discarded; intended for showing expected output.

## Notes

MCDP deliberately stays close to Minecraft's execution model rather than pretending Minecraft is a normal VM. Runtime values are scores, delayed functions do not capture command context, and same-tick loops are still bounded by Minecraft's command execution limits.

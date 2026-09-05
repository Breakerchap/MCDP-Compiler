"""MCDP compiler package."""

from .compiler import Compiler, compile_file
from .errors import MCDPError

__all__ = ["Compiler", "MCDPError", "compile_file"]
__version__ = "0.1.0"

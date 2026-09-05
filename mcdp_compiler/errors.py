class MCDPError(Exception):
  """A user-facing MCDP parse or compilation error."""

  def __init__(self, message: str, line: int | None = None):
    self.message = message
    self.line = line
    prefix = f"line {line}: " if line is not None else ""
    super().__init__(prefix + message)

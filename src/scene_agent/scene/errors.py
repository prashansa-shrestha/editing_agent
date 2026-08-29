"""Exceptions used by the scene I/O boundary.

Keeping format, path, decoder, and resource failures distinct lets callers
give an actionable message without catching broad ``Exception`` instances.
"""


class PLYError(ValueError):
    """Base class for malformed or unsupported PLY files."""


class PLYHeaderError(PLYError):
    """The PLY header is missing, malformed, or uses an unsupported format."""


class PLYSchemaError(PLYError):
    """A PLY header does not match the requested scene schema."""


class PLYPayloadError(PLYError):
    """A PLY payload is truncated, has trailing bytes, or has bad sizing."""


class PLYValidationError(PLYError):
    """Compatibility exception for any PLY validation failure."""


class UnsafePathError(ValueError):
    """A requested output path escapes the permitted output directory."""


class OutputExistsError(FileExistsError):
    """The operation refuses to overwrite an existing output."""


class DecoderError(RuntimeError):
    """Base class for decoder orchestration failures."""


class DecoderUnavailableError(DecoderError):
    """Node.js or the frozen decoder script is unavailable."""


class DecoderInvocationError(DecoderError):
    """The frozen decoder process could not produce a valid result."""


class SourceChangedError(DecoderError):
    """The immutable source changed while a decoder operation was running."""


class MemoryBudgetExceeded(DecoderError):
    """A requested operation exceeds the hard memory budget."""


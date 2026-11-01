"""Validation errors for the preprocessing toolbox.

Raised by :func:`selene_space_factor_research.preprocessing.apply` and the
method registry when a preprocessing step cannot be resolved or its
parameters are invalid.
"""


class PreprocessError(Exception):
    """Base error for the preprocessing toolbox."""


class PreprocessMethodError(PreprocessError, KeyError):
    """Raised when a method name does not resolve to a registered method."""


class PreprocessParamError(PreprocessError, TypeError):
    """Raised when a method is called with missing or unknown parameters."""

"""What the assistant layer raises. Features show the message as it is."""

from __future__ import annotations


class LLMError(Exception):
    """A request could not be made or answered; the message is for the user."""


class NotConfigured(LLMError):
    """The assistant is off, or a setting it needs is missing."""


class BudgetExceeded(LLMError):
    """Today's request allowance from Preferences is used up."""

"""Typed invalid model output; the paid response is already journaled, never retry it."""
class ModelOutputInvalid(ValueError):
    pass

"""Core contracts and orchestration for avatar.

Everything platform-, model- and trigger-specific lives behind the interfaces
defined in this package. Concrete adapters register themselves with the
registry (see ``avatar.core.registry``) and are wired together by the engine.
"""

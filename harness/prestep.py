"""Retrieve-as-pre-step plugin seam (DeepSeek steal #4).

Thin hook bus: registered ``AgentHook``s run ``before_model(ctx) -> ctx``
in order, then the caller runs (or finishes) the model step. First built-in
is ``RetrievePreStep`` (hybrid retrieve + pack). This is a Code-Harness
seam, not a Cordis / ``@deepseek-ai/*`` vendor.

Default is **on** so ``query`` / ``chat`` / session retrieve the same way as
before. Disable with ``--no-retrieve-prestep`` / ``CODEHARNESS_RETRIEVE_PRESTEP=0``
/ ``prestep.retrieve: false``. Skip when the caller already supplied packs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


_FALSEY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass
class PreStepContext:
    """Shared bag passed through ``before_model`` hooks."""

    query: str
    top_k: int = 20
    config: Any = None
    retriever: Any = None
    context_builder: Any = None
    generate: Optional[Callable] = None
    verifier: Optional[Callable] = None
    must_cite_paths: Optional[Sequence[str]] = None
    cache: Any = None
    repo_name: str = ""
    repo_path: str = ""
    extra: Optional[Dict[str, Any]] = None
    args: Any = None
    environ: Optional[Dict[str, str]] = None
    results: Any = None
    packed: Any = None
    outcome: Any = None
    skipped: List[str] = field(default_factory=list)
    ran: List[str] = field(default_factory=list)
    hook_trace: List[str] = field(default_factory=list)


class AgentHook:
    """Minimal pre-step plugin: ``before_model(ctx) -> ctx``."""

    name = "hook"

    def before_model(self, ctx: PreStepContext) -> PreStepContext:
        return ctx


class HookRegistry:
    """Ordered hook list. First registered runs first."""

    def __init__(self, hooks: Optional[Sequence[AgentHook]] = None):
        self._hooks: List[Any] = list(hooks or [])

    def register(self, hook: Any) -> "HookRegistry":
        self._hooks.append(hook)
        return self

    @property
    def hooks(self) -> List[Any]:
        return list(self._hooks)

    @property
    def names(self) -> List[str]:
        return [getattr(hook, "name", hook.__class__.__name__) for hook in self._hooks]

    def run_before_model(self, ctx: PreStepContext) -> PreStepContext:
        for hook in self._hooks:
            ctx = hook.before_model(ctx)
        return ctx


def retrieve_prestep_enabled(
    args=None,
    environ=None,
    config=None,
) -> bool:
    """Default **on**. ``--no-retrieve-prestep`` / env ``0`` wins."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("CODEHARNESS_RETRIEVE_PRESTEP") or "").strip().lower()
    if args is not None and getattr(args, "no_retrieve_prestep", False):
        return False
    if raw in _FALSEY:
        return False
    if args is not None and getattr(args, "retrieve_prestep", False):
        return True
    if raw in _TRUTHY:
        return True
    if config is not None:
        prestep = getattr(config, "prestep", None) or {}
        if "retrieve" in prestep:
            return bool(prestep.get("retrieve"))
    return True


def apply_retrieve_prestep_config(config, args=None, environ=None):
    """Stamp ``prestep.retrieve`` from flags / env / config."""
    prestep = getattr(config, "prestep", None)
    if prestep is None:
        config.prestep = {}
        prestep = config.prestep
    prestep["retrieve"] = retrieve_prestep_enabled(
        args, environ=environ, config=config
    )
    return config


def _already_supplied(ctx: PreStepContext) -> bool:
    if getattr(ctx, "packed", None) is not None:
        return True
    if getattr(ctx, "results", None) is not None:
        return True
    if getattr(ctx, "outcome", None) is not None:
        return True
    return False


def run_retrieve_pack(ctx: PreStepContext) -> PreStepContext:
    """Always run hybrid retrieve + pack. Used by RetrievePreStep and serve.

    Query-cache reuse is honored when ``ctx.cache`` is set. Callers that
    already wrap their own cache (HTTP ``/v1/retrieve``) pass ``cache=None``.
    """
    from .config import Config
    from .loop import LoopConfig, QueryLoop
    from .query_cache import run_with_query_cache

    if ctx.retriever is None:
        raise RuntimeError("retrieve pre-step has no retriever")
    builder = ctx.context_builder
    cfg = ctx.config or Config()
    if builder is None:
        from .context_builder import ContextBuilder

        builder = ContextBuilder(cfg)
        ctx.context_builder = builder
    loop = QueryLoop(ctx.retriever, builder, LoopConfig.from_mapping(getattr(cfg, "retrieval", None)))
    top_k = int(ctx.top_k or (getattr(cfg, "retrieval", None) or {}).get("top_k") or 20)
    run_kwargs: Dict[str, Any] = {}
    if ctx.must_cite_paths:
        run_kwargs["must_cite_paths"] = list(ctx.must_cite_paths)
    if ctx.generate is not None:
        run_kwargs["generate"] = ctx.generate
    if ctx.verifier is not None:
        run_kwargs["verifier"] = ctx.verifier
    outcome = run_with_query_cache(
        loop,
        ctx.query,
        top_k=top_k,
        cache=ctx.cache,
        config=cfg,
        repo_name=ctx.repo_name,
        repo_path=ctx.repo_path or getattr(cfg, "repo_path", None) or ".",
        extra=ctx.extra,
        **run_kwargs,
    )
    ctx.outcome = outcome
    ctx.results = list(getattr(outcome, "results", None) or [])
    ctx.packed = getattr(outcome, "packed", None)
    if ctx.packed is None:
        ctx.packed = builder.build_context_report(ctx.query, ctx.results)
    if "retrieve" not in ctx.ran:
        ctx.ran.append("retrieve")
    return ctx


class RetrievePreStep(AgentHook):
    """Built-in: hybrid retrieve + pack, once, unless disabled or already packed."""

    name = "retrieve"

    def before_model(self, ctx: PreStepContext) -> PreStepContext:
        if _already_supplied(ctx):
            ctx.skipped.append(self.name)
            return ctx
        if not retrieve_prestep_enabled(ctx.args, ctx.environ, ctx.config):
            ctx.skipped.append(self.name)
            return ctx
        return run_retrieve_pack(ctx)


def default_registry() -> HookRegistry:
    """Built-in hooks in registration order.

    1. ``RetrievePreStep`` — hybrid retrieve + pack (this PR). Respects
       wiki / memory / knowledge-prefix / prefix-stable / query-cache /
       clear-tool-results (caller still runs clear *before* the seam) /
       citation ``--verify`` (unrelated completion gate stays on session).

    To register a second hook (e.g. a memory brief, or a later verify
    *prep* that only annotates packs):

        registry = default_registry()
        registry.register(MemoryBriefPreStep())  # after retrieve; sees ctx.packed

        # or run memory *before* retrieve so it can seed the prefix:
        registry = HookRegistry([MemoryBriefPreStep(), RetrievePreStep()])

    Each hook is ``before_model(ctx) -> ctx``. Do not import Cordis.
    Session-event FTS is a later PR, not a second hook here.
    """
    return HookRegistry([RetrievePreStep()])


def run_query_turn(
    query: str,
    *,
    retriever,
    context_builder,
    config,
    top_k: int = 20,
    cache=None,
    generate=None,
    verifier=None,
    must_cite_paths=None,
    extra=None,
    repo_name: str = "",
    repo_path: str = "",
    args=None,
    environ=None,
    registry: Optional[HookRegistry] = None,
) -> PreStepContext:
    """Shared query/chat/session entry: run pre-steps then return the bag."""
    ctx = PreStepContext(
        query=query,
        top_k=top_k,
        config=config,
        retriever=retriever,
        context_builder=context_builder,
        generate=generate,
        verifier=verifier,
        must_cite_paths=must_cite_paths,
        cache=cache,
        repo_name=repo_name,
        repo_path=repo_path,
        extra=extra,
        args=args,
        environ=environ,
    )
    return (registry or default_registry()).run_before_model(ctx)

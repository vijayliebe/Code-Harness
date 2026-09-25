"""Local health checks for Code-Harness (Fusion PR #5 / Agent-Reach doctor).

No network calls. Prints pass/fail/warn plus an actionable hint.
Exit 0 when no required check fails; warnings (LLM key, redact off) are OK.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .audit import default_audit_path
from .config import Config
from .knowledge_graph import KnowledgeGraph
from .redact import redaction_enabled


CORE_DEPS = (
    ("dotenv", "python-dotenv", True),
    ("numpy", "numpy", True),
    ("networkx", "networkx", True),
    ("yaml", "pyyaml", True),
    ("rank_bm25", "rank-bm25", True),
    ("chromadb", "chromadb", False),
    ("sentence_transformers", "sentence-transformers", False),
)

EMBED_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "voyage": "VOYAGE_API_KEY",
    "jina": "JINA_API_KEY",
}

LLM_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    hint: str = ""


@dataclass
class DoctorReport:
    checks: List[CheckResult] = field(default_factory=list)
    repo_path: str = "."
    repo_name: str = ""

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def format(self) -> str:
        tags = {"pass": "ok", "fail": "fail", "warn": "warn", "skip": "skip"}
        lines: List[str] = []
        n_pass = sum(1 for c in self.checks if c.status == "pass")
        n_fail = sum(1 for c in self.checks if c.status == "fail")
        n_warn = sum(1 for c in self.checks if c.status == "warn")
        for check in self.checks:
            tag = tags.get(check.status, check.status)
            lines.append(f"[{tag}] {check.name}: {check.message}")
            if check.hint and check.status != "pass":
                lines.append(f"      hint: {check.hint}")
        verdict = "PASS" if self.ok else "FAIL"
        lines.append("")
        lines.append(
            f"[pass] {n_pass}  [warn] {n_warn}  [fail] {n_fail}  → doctor: {verdict}"
        )
        return "\n".join(lines)


def run_doctor(
    config: Optional[Config] = None,
    *,
    repo_path: str = ".",
    repo_name: str = "",
    environ: Optional[Dict[str, str]] = None,
) -> DoctorReport:
    """Probe local backends. Never opens an outbound socket."""
    cfg = config or Config()
    env = environ if environ is not None else os.environ
    root = os.path.abspath(repo_path or getattr(cfg, "repo_path", None) or ".")
    name = repo_name or os.path.basename(root) or "repo"
    cfg.repo_path = root
    report = DoctorReport(repo_path=root, repo_name=name)

    report.checks.append(_check_python())
    report.checks.append(_check_deps())
    report.checks.append(_check_index(cfg, root))
    report.checks.append(_check_graph(cfg, root, name))
    report.checks.append(_check_embedding(cfg, env))
    report.checks.append(_check_redact(cfg, env))
    report.checks.append(_check_audit(cfg, root))
    report.checks.append(_check_llm(cfg, env))
    return report


def format_report(report: DoctorReport) -> str:
    return report.format()


def _check_python() -> CheckResult:
    ver = sys.version_info
    version = f"{ver.major}.{ver.minor}.{ver.micro}"
    if ver < (3, 9):
        return CheckResult(
            "python",
            "fail",
            f"{version} (need 3.9+)",
            "Install Python 3.9 or newer and recreate the virtualenv.",
        )
    return CheckResult("python", "pass", version)


def _check_deps() -> CheckResult:
    missing_core: List[str] = []
    missing_opt: List[str] = []
    present: List[str] = []
    for module, pip_name, required in CORE_DEPS:
        try:
            importlib.import_module(module)
            present.append(module)
        except Exception:
            (missing_core if required else missing_opt).append(pip_name)
    if missing_core:
        return CheckResult(
            "deps",
            "fail",
            "missing " + ", ".join(missing_core),
            "pip install -r requirements.txt",
        )
    extra = f"; optional missing: {', '.join(missing_opt)}" if missing_opt else ""
    return CheckResult("deps", "pass", f"{len(present)} importable{extra}")


def _candidate_dirs(root: str, rel: str) -> List[str]:
    if not rel:
        return []
    if os.path.isabs(rel):
        return [rel]
    out = []
    for base in (root, os.getcwd()):
        path = os.path.abspath(os.path.join(base, rel))
        if path not in out:
            out.append(path)
    return out


def _check_index(config: Config, root: str) -> CheckResult:
    rel = (config.vector_store or {}).get("persist_directory") or ".code-harness/chromadb"
    candidates = _candidate_dirs(root, rel)
    found = None
    for path in candidates:
        sqlite = os.path.join(path, "chroma.sqlite3")
        if os.path.isfile(sqlite) or (os.path.isdir(path) and any(os.scandir(path))):
            found = path
            break
    if not found:
        shown = candidates[0] if candidates else rel
        return CheckResult(
            "index",
            "fail",
            f"no chroma persist at {shown}",
            f"python main.py index {root}",
        )
    return CheckResult("index", "pass", found)


def _graph_candidates(config: Config, root: str, repo_name: str) -> List[str]:
    kg = KnowledgeGraph(config, repo_name=repo_name)
    paths = [os.path.abspath(kg.persist_path)]
    rel = (config.knowledge_graph or {}).get("persist_path") or ".code-harness/graph.json"
    directory = os.path.dirname(rel) if rel else ".code-harness"
    name = f"graph_{repo_name}.json" if repo_name else os.path.basename(rel) or "graph.json"
    for base in (root, os.getcwd()):
        paths.append(os.path.abspath(os.path.join(base, directory, name)))
        paths.append(os.path.abspath(os.path.join(base, ".code-harness", name)))
    # de-dupe
    seen = []
    for path in paths:
        if path not in seen:
            seen.append(path)
    return seen


def _check_graph(config: Config, root: str, repo_name: str) -> CheckResult:
    candidates = _graph_candidates(config, root, repo_name)
    found = next((p for p in candidates if os.path.isfile(p)), None)
    if not found:
        return CheckResult(
            "graph",
            "fail",
            f"no graph_{repo_name}.json",
            f"python main.py index {root}  # writes .code-harness/graph_{repo_name}.json",
        )
    try:
        import json

        with open(found, encoding="utf-8") as fh:
            data = json.load(fh)
        nodes = data.get("nodes") or []
        return CheckResult("graph", "pass", f"{found} ({len(nodes)} nodes)")
    except Exception as exc:
        return CheckResult(
            "graph",
            "fail",
            f"unreadable {found}: {exc}",
            f"python main.py index {root}",
        )


def _check_embedding(config: Config, env: Dict[str, str]) -> CheckResult:
    emb = getattr(config, "embedding", None) or {}
    provider = str(emb.get("provider") or "local").strip().lower()
    model = str(emb.get("model") or "").strip()
    if not model:
        return CheckResult(
            "embedding",
            "fail",
            "no embedding.model set",
            'Set embedding.model (default: "all-MiniLM-L6-v2") in code-harness.json',
        )
    if provider == "local":
        try:
            importlib.import_module("sentence_transformers")
        except Exception:
            return CheckResult(
                "embedding",
                "fail",
                f"local/{model} (sentence-transformers missing)",
                "pip install sentence-transformers",
            )
        return CheckResult("embedding", "pass", f"local/{model} (no key, no ping)")
    env_name = EMBED_KEY_ENV.get(provider)
    key = emb.get("api_key") or (env.get(env_name) if env_name else None)
    if env_name and not key:
        return CheckResult(
            "embedding",
            "fail",
            f"{provider}/{model} (no API key)",
            f"export {env_name}=... or switch embedding.provider to local",
        )
    return CheckResult("embedding", "pass", f"{provider}/{model} (key present, no ping)")


def _check_redact(config: Config, env: Dict[str, str]) -> CheckResult:
    on = redaction_enabled(config, environ=env)
    if on:
        return CheckResult("redact", "pass", "enabled (default-on)")
    return CheckResult(
        "redact",
        "warn",
        "disabled",
        "Unset CODEHARNESS_REDACT / redaction.enabled so retrieve/LLM bodies stay redacted.",
    )


def _check_audit(config: Config, root: str) -> CheckResult:
    path = default_audit_path(root, config=config)
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass
        if os.access(path, os.W_OK):
            return CheckResult("audit", "pass", f"writable {path}")
        raise PermissionError(path)
    except OSError as exc:
        return CheckResult(
            "audit",
            "fail",
            f"not writable: {path} ({exc})",
            "chmod the audit directory or set redaction.audit_path to a writable file.",
        )


def _check_llm(config: Config, env: Dict[str, str]) -> CheckResult:
    llm = getattr(config, "llm", None) or {}
    provider = str(llm.get("provider") or "openai").strip().lower()
    model = str(llm.get("model") or "")
    if provider in ("ollama", "custom"):
        return CheckResult(
            "llm",
            "pass",
            f"{provider}/{model or 'local'} (no cloud key required)",
        )
    env_name = LLM_KEY_ENV.get(provider)
    key = llm.get("api_key") or (env.get(env_name) if env_name else None)
    if env_name and not key:
        return CheckResult(
            "llm",
            "warn",
            f"{provider}/{model or '?'} (no API key; query --no-llm still works)",
            f"export {env_name}=... or use --llm-provider ollama / --no-llm",
        )
    return CheckResult("llm", "pass", f"{provider}/{model} (key present, no ping)")


def print_report(report: DoctorReport, file=None) -> None:
    print(report.format(), file=file)

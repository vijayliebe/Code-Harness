import os
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from .models import Chunk, RetrievalResult
from .config import Config


@dataclass
class ContextReport:
    context: str
    prompt_tokens: int
    packed_chunk_ids: List[str] = field(default_factory=list)
    packed_paths: List[str] = field(default_factory=list)
    mmr_latency_ms: float = 0.0

CONTEXT_FILE_NAMES = ["ARCHITECTURE.md", "AGENTS.md", "CLAUDE.md"]


def _chunk_similarity(a: Chunk, b: Chunk) -> float:
    if a.file_path == b.file_path:
        return 0.6
    ft_a = a.entity_type.value if hasattr(a.entity_type, 'value') else str(a.entity_type)
    ft_b = b.entity_type.value if hasattr(b.entity_type, 'value') else str(b.entity_type)
    type_score = 0.3 if ft_a == ft_b else 0.0
    name_a = a.entity_name.lower()[:30]
    name_b = b.entity_name.lower()[:30]
    name_a_parts = set(name_a.replace("-", "_").split("_"))
    name_b_parts = set(name_b.replace("-", "_").split("_"))
    overlap = len(name_a_parts & name_b_parts)
    name_score = min(0.4, overlap * 0.1)
    return min(0.95, type_score + name_score)


def _find_context_files(repo_root: str) -> Dict[str, str]:
    found = {}
    for name in CONTEXT_FILE_NAMES:
        path = os.path.join(repo_root, name)
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    content = f.read()
                    if content.strip():
                        found[name] = content
            except Exception:
                pass
    return found


class ContextBuilder:
    def __init__(self, config: Config):
        self.config = config
        self.max_context_tokens = config.llm.get("max_tokens", 4096) * 2

    def estimate_tokens(self, text: str) -> int:
        return self._estimate_tokens(text)

    def build_context(self, query: str, results: List[RetrievalResult]) -> str:
        return self.build_context_report(query, results).context

    def build_context_report(self, query: str, results: List[RetrievalResult]) -> ContextReport:
        if not results:
            empty = "No relevant code found."
            return ContextReport(
                context=empty,
                prompt_tokens=self.estimate_tokens(empty),
            )

        project_docs = self._load_project_context()
        deduplicated = self._deduplicate(results)
        started = time.perf_counter()
        scored = self._rerank(query, deduplicated)
        mmr_ms = (time.perf_counter() - started) * 1000.0
        packed: List[RetrievalResult] = []
        context = self._assemble_context(query, scored, project_docs, packed=packed)
        packed_ids = [r.chunk.id for r in packed]
        packed_paths: List[str] = []
        seen = set()
        for result in packed:
            path = result.chunk.file_path.replace("\\", "/")
            while path.startswith("./"):
                path = path[2:]
            if path and path not in seen:
                seen.add(path)
                packed_paths.append(path)
        return ContextReport(
            context=context,
            prompt_tokens=self.estimate_tokens(context),
            packed_chunk_ids=packed_ids,
            packed_paths=packed_paths,
            mmr_latency_ms=mmr_ms,
        )

    def _load_project_context(self) -> Dict[str, str]:
        repo_root = self.config.repo_path
        if not repo_root or not os.path.isdir(repo_root):
            return {}
        return _find_context_files(repo_root)

    def _deduplicate(self, results: List[RetrievalResult]) -> List[RetrievalResult]:
        seen_content: set = set()
        seen_range: set = set()
        unique = []
        for r in sorted(results, key=lambda x: -x.score):
            content_hash = hash(r.chunk.content[:300])
            range_key = (r.chunk.file_path, r.chunk.start_line, r.chunk.end_line)
            if range_key in seen_range:
                continue
            overlap = False
            for f, s, e in seen_range:
                if f == r.chunk.file_path and not (r.chunk.end_line < s or r.chunk.start_line > e):
                    overlap = True
                    break
            if overlap:
                continue
            seen_range.add(range_key)
            seen_content.add(content_hash)
            unique.append(r)
        return unique

    def _rerank(self, query: str, results: List[RetrievalResult]) -> List[RetrievalResult]:
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        for r in results:
            boost = 0
            content_lower = r.chunk.content.lower()

            term_matches = sum(1 for t in query_terms if t in content_lower)
            boost += term_matches * 0.05

            name_lower = r.chunk.entity_name.lower()
            if any(t in name_lower for t in query_terms):
                boost += 0.2

            et = r.chunk.entity_type.value if hasattr(r.chunk.entity_type, 'value') else r.chunk.entity_type
            if et in ("function", "method"):
                boost += 0.15
            elif et == "class":
                boost += 0.1
            elif et == "file":
                boost -= 0.1

            if r.chunk.docstring:
                boost += 0.05

            r.score = max(0, r.score + boost)

        results.sort(key=lambda r: r.score, reverse=True)
        return self._diversity_rerank(results)

    @staticmethod
    def _diversity_rerank(results: List[RetrievalResult],
                          lambda_div: float = 0.3) -> List[RetrievalResult]:
        if len(results) <= 2:
            return results

        selected = [results[0]]
        candidates = results[1:]

        while candidates and len(selected) < len(results):
            best_idx = None
            best_score = -float("inf")
            for i, cand in enumerate(candidates):
                relevance = cand.score
                max_sim = max(
                    _chunk_similarity(cand.chunk, sel.chunk)
                    for sel in selected
                )
                mmr = relevance - lambda_div * max_sim
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i
            if best_idx is not None:
                selected.append(candidates.pop(best_idx))
            else:
                break

        return selected

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def _assemble_context(self, query: str,
                          results: List[RetrievalResult],
                          project_docs: Optional[Dict[str, str]] = None,
                          packed: Optional[List[RetrievalResult]] = None) -> str:
        sections = []
        total_estimate = 0
        max_estimate = self.max_context_tokens

        sections.append(f"# Query: {query}\n")
        sections.append("## Relevant Code Context\n")
        total_estimate += 20

        if project_docs:
            doc_section = "\n### Project-level Context Files\n"
            for name, content in project_docs.items():
                header = f"\n**{name}** (project root)\n"
                doc_entry = f"{header}```\n{content}\n```\n"
                estimated = self._estimate_tokens(doc_entry)
                if total_estimate + estimated < max_estimate * 0.4:
                    sections.append(doc_entry)
                    total_estimate += estimated
                else:
                    remaining = int((max_estimate - total_estimate) * 0.8)
                    if remaining > 80:
                        doc_entry = f"{header}```\n{content[:remaining * 4]}...\n```\n"
                        sections.append(doc_entry)
                    break

        by_file: Dict[str, List[RetrievalResult]] = {}
        for r in results:
            fp = r.chunk.file_path
            if fp not in by_file:
                by_file[fp] = []
            by_file[fp].append(r)

        for file_path, file_results in sorted(by_file.items()):
            if total_estimate >= max_estimate:
                break

            file_section = f"\n### File: {file_path}\n"
            file_estimate = self._estimate_tokens(file_section)
            total_estimate += file_estimate

            if total_estimate < max_estimate:
                sections.append(file_section)

            for r in file_results:
                if total_estimate >= max_estimate:
                    break

                entity_type = r.chunk.entity_type.value.capitalize()
                entity_name = r.chunk.entity_name
                lines = f"Lines {r.chunk.start_line}-{r.chunk.end_line}"

                header = f"**{entity_type}: `{entity_name}`** [{lines}] (relevance: {r.score:.2f})\n"
                code = f"```\n{r.chunk.content}\n```\n"
                entry = header + code

                estimated_tokens = self._estimate_tokens(entry)
                if total_estimate + estimated_tokens > max_estimate:
                    remaining = max_estimate - total_estimate
                    if remaining > 50:
                        max_chars = remaining * 4
                        entry = header + f"```\n{r.chunk.content[:max_chars]}...\n```\n"
                        sections.append(entry)
                        if packed is not None:
                            packed.append(r)
                    break

                sections.append(entry)
                total_estimate += estimated_tokens
                if packed is not None:
                    packed.append(r)

        return "\n".join(sections)

    def build_system_prompt(self) -> str:
        return """You are an expert code analyst. Your task is to answer questions about a codebase using the provided context.

Guidelines:
1. Use the provided code context to give accurate, specific answers
2. Reference file paths, line numbers, and function/class names
3. If the context doesn't contain enough information, say so clearly
4. Provide code examples when relevant
5. Explain the purpose and relationships between components
6. Be concise but thorough in your analysis

The context below contains relevant code snippets from the repository, including file paths and line numbers.
Project-level documentation files (ARCHITECTURE.md, AGENTS.md, CLAUDE.md) may be included for high-level understanding."""

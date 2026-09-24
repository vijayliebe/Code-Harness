#!/usr/bin/env python3
"""Code Harness - Vectorize, index, and query any code repository with AI."""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
load_dotenv(".env.local", override=True)

from harness import (
    CodeParser, CodeChunker, Embedder, VectorStore,
    KnowledgeGraph, RepoGraph, Retriever, ContextBuilder, LLMInterface, Config
)


def _derive_repo_name(args) -> str:
    """Derive the repository name from args, using --repo-name override or basename."""
    if hasattr(args, 'repo_name') and args.repo_name:
        return args.repo_name
    repo_path = getattr(args, 'repo', '.') or '.'
    return os.path.basename(os.path.abspath(repo_path))


def cmd_index(args):
    config = _load_config(args)
    config.repo_path = args.repo
    repo_path = os.path.abspath(args.repo)
    repo_name = _derive_repo_name(args)

    print(f"[*] Indexing repository: {repo_path}")
    print(f"[*] Repo name: {repo_name}")
    print(f"[*] Config: embedding={config.embedding['model']}, "
          f"provider={config.embedding['provider']}")
    print()

    parser = CodeParser(config)
    print(f"[*] Discovering files...")
    files = parser.discover_files(repo_path)
    print(f"[*] Found {len(files)} files to index")

    print(f"[*] Parsing code...")
    entities, _ = parser.parse_repository(repo_path)
    print(f"[*] Extracted {len(entities)} code entities")

    chunker = CodeChunker(config)
    chunks = chunker.chunk_entities(entities)
    # Tag each chunk with the repo name
    for c in chunks:
        c.repo_name = repo_name
    print(f"[*] Created {len(chunks)} chunks")

    print(f"[*] Building knowledge graph...")
    kg = KnowledgeGraph(config, repo_name=repo_name)
    kg.build(entities)
    kg.save()
    print(f"[*] Knowledge graph: {kg.graph.number_of_nodes()} nodes, "
          f"{kg.graph.number_of_edges()} edges")

    print(f"[*] Generating embeddings ({config.embedding['model']})...")
    embedder = Embedder(config)
    texts = [c.content for c in chunks]
    embeddings = embedder.embed(texts)
    print(f"[*] Generated {len(embeddings)} embeddings (dim={len(embeddings[0]) if embeddings else 0})")

    print(f"[*] Storing in vector database...")
    vs = VectorStore(config)
    # Incremental: only delete chunks for this repo, not the entire collection
    vs.delete_by_repo(repo_name)
    vs.add_chunks(chunks, embeddings, repo_name=repo_name)
    print(f"[*] Vector store: {vs.count()} chunks indexed (total across all repos)")

    # Pre-warm BM25 disk cache for fast cold starts
    retriever = Retriever(config, embedder, vs, kg, repo_name=repo_name)
    retriever.index_chunks(chunks, persist=True)

    # Update inter-repo relationship graph
    print(f"[*] Updating inter-repo relationship graph...")
    rg = RepoGraph(config)
    rg.load()
    rg.update_repo(repo_name, repo_path, entities)
    rg.build_cross_repo_edges()
    rg.save()
    related = rg.get_related_repos(repo_name)
    if related:
        print(f"[*] Cross-repo relationships found: {len(related)}")
        for rel in related[:5]:
            print(f"    -> {rel['repo']} ({rel.get('relationship', 'related')})")
    else:
        print(f"[*] No cross-repo relationships detected (index more repos to see connections)")

    if args.save_config:
        config_path = args.save_config if isinstance(args.save_config, str) else "code-harness.json"
        config.save(config_path)
        print(f"[*] Config saved to: {config_path}")

    print(f"[+] Indexing complete!")


def cmd_query(args):
    config = _load_config(args)
    config.repo_path = args.repo or "."
    repo_name = _derive_repo_name(args)
    cross_repo = getattr(args, 'cross_repo', False)

    vs = VectorStore(config)
    count = vs.count()
    print(f"[*] Vector store: {count} chunks (total)")
    if count == 0:
        print("[!] No indexed data. Run 'index' first.")
        return
    embedder = Embedder(config)

    if cross_repo:
        print(f"[*] Cross-repo query across all indexed repositories")
        kg = KnowledgeGraph(config)
        kg.load()
        retriever = Retriever(config, embedder, vs, kg, repo_name="")
        if retriever.try_load_bm25():
            print("[*] BM25 index loaded from disk")
        else:
            chunks = vs.get_all()
            retriever.index_chunks(chunks, persist=False)
    else:
        print(f"[*] Loading index for: {config.repo_path} (repo: {repo_name})")
        kg = KnowledgeGraph(config, repo_name=repo_name)
        kg.load()
        print(f"[*] Knowledge graph loaded: {kg.graph.number_of_nodes()} nodes")
        retriever = Retriever(config, embedder, vs, kg, repo_name=repo_name)
        if retriever.try_load_bm25():
            print("[*] BM25 index loaded from disk")
        else:
            chunks = vs.get_all(repo_name=repo_name)
            retriever.index_chunks(chunks, persist=False)

    context_builder = ContextBuilder(config)
    llm = LLMInterface(config) if not args.no_llm else None

    query = args.query

    print(f"\n[*] Retrieving context for: {query}\n")
    if context_builder.pack_mode != "full":
        print(f"[*] Pack mode: {context_builder.pack_mode}")
    if config.retrieval.get("max_loops", 0):
        print(f"[*] Query loop: max_loops={config.retrieval.get('max_loops')}")

    from harness.loop import LoopConfig, QueryLoop, verify_answer

    loop = QueryLoop(retriever, context_builder, LoopConfig.from_mapping(config.retrieval))
    generate = None
    if llm is not None and not args.no_llm:
        if args.stream:
            generate = lambda system, context, user_q: llm.stream_query(system, context, user_q)
        else:
            generate = lambda system, context, user_q: llm.query(system, context, user_q)
    verifier = None
    if config.retrieval.get("verify") and llm is not None:
        verifier = lambda q, a, packed: verify_answer(llm, q, a, packed)

    debug = getattr(args, 'debug', False)
    outcome = loop.run(
        query,
        top_k=config.retrieval.get("top_k", 20),
        generate=generate,
        verifier=verifier,
    )
    results = outcome.results
    report = outcome.packed or context_builder.build_context_report(query, results)
    if debug:
        _print_loop_debug(outcome)

    if args.verbose:
        print(f"[*] Retrieved {len(results)} chunks (attempts={outcome.attempts})")
        for i, r in enumerate(results[:5]):
            print(f"  {i+1}. [{r.source}] {r.chunk.entity_name} "
                  f"({r.chunk.file_path}:{r.chunk.start_line}) "
                  f"score={r.score:.3f}")

    context = report.context
    expand_ids = getattr(args, "expand_chunks", None) or []
    if expand_ids:
        context = context_builder.expand_into_context(context, expand_ids)
        print(f"[*] Expanded {len(expand_ids)} chunk(s)")
    if debug:
        print(f"[*] Context pack={report.pack_mode} tokens_full={report.prompt_tokens_full} "
              f"tokens_packed={report.prompt_tokens_packed} prefix={report.prefix_hash}")

    if args.no_llm:
        print(context)
        return

    print("[*] Generating AI response...\n")
    response = outcome.answer
    if response is None:
        system_prompt = context_builder.build_system_prompt()
        if args.stream:
            response = llm.stream_query(system_prompt, context, query)
        else:
            response = llm.query(system_prompt, context, query)
    if not args.stream:
        print(response)
        print()
    if outcome.verify:
        supported = outcome.verify.get("supported")
        missing = outcome.verify.get("missing_paths") or []
        print(f"[*] Verify: supported={supported} missing={missing}")


def cmd_interactive(args):
    config = _load_config(args)
    config.repo_path = args.repo or "."
    repo_name = _derive_repo_name(args)
    cross_repo = getattr(args, 'cross_repo', False)

    vs = VectorStore(config)
    count = vs.count()
    if count == 0:
        print("[!] No indexed data. Run 'index' first.")
        return

    embedder = Embedder(config)

    if cross_repo:
        print("[*] Cross-repo mode — searching all indexed repositories")
        kg = KnowledgeGraph(config)
        kg.load()
        retriever = Retriever(config, embedder, vs, kg, repo_name="")
        if retriever.try_load_bm25():
            print("[*] BM25 index loaded from disk")
        else:
            chunks = vs.get_all()
            retriever.index_chunks(chunks, persist=False)
    else:
        kg = KnowledgeGraph(config, repo_name=repo_name)
        kg.load()
        retriever = Retriever(config, embedder, vs, kg, repo_name=repo_name)
        if retriever.try_load_bm25():
            print("[*] BM25 index loaded from disk")
        else:
            chunks = vs.get_all(repo_name=repo_name)
            retriever.index_chunks(chunks, persist=False)

    context_builder = ContextBuilder(config)

    llm_available = args.llm
    llm = LLMInterface(config) if llm_available else None

    print("=" * 60)
    print("  Code Harness - Interactive Mode")
    print("  Commands: /help, /context, /retrieve <id>, /llm on|off, /clear, /quit")
    print("=" * 60)
    print()

    context_history = []

    while True:
        try:
            query = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query:
            continue

        if query.startswith("/"):
            cmd = query.lower().split()
            if cmd[0] == "/quit" or cmd[0] == "/exit":
                break
            elif cmd[0] == "/help":
                print("Commands:")
                print("  /help             - Show this help")
                print("  /llm on|off       - Enable/disable LLM responses")
                print("  /context          - Show current context")
                print("  /retrieve <id>    - Print a cached original chunk")
                print("  /expand <id>      - Alias for /retrieve")
                print("  /clear            - Clear screen")
                print("  /quit             - Exit")
                print("  Any other text    - Query the codebase")
            elif cmd[0] == "/llm":
                if len(cmd) > 1:
                    llm_available = cmd[1] == "on"
                    llm = LLMInterface(config) if llm_available else None
                    print(f"[*] LLM {'enabled' if llm_available else 'disabled'}")
            elif cmd[0] == "/context":
                if context_history:
                    print(context_history[-1][:2000])
                else:
                    print("No context yet")
            elif cmd[0] in ("/retrieve", "/expand"):
                raw = query.split(None, 1)
                if len(raw) < 2:
                    print("[!] Usage: /retrieve <chunk_id>")
                else:
                    from harness.ccr import retrieve_chunk
                    text = context_builder.cache.get(raw[1]) or retrieve_chunk(
                        raw[1],
                        spill_dir=(config.ccr or {}).get("spill_dir"),
                    )
                    if text is None:
                        print(f"[!] chunk not in cache: {raw[1]}")
                    else:
                        print(text)
            elif cmd[0] == "/clear":
                os.system("clear" if os.name == "posix" else "cls")
            continue

        from harness.loop import LoopConfig, QueryLoop, verify_answer

        loop = QueryLoop(retriever, context_builder, LoopConfig.from_mapping(config.retrieval))
        generate = None
        if llm_available and llm:
            if args.stream:
                generate = lambda system, context, user_q: llm.stream_query(system, context, user_q)
            else:
                generate = lambda system, context, user_q: llm.query(system, context, user_q)
        verifier = None
        if config.retrieval.get("verify") and llm:
            verifier = lambda q, a, packed: verify_answer(llm, q, a, packed)
        outcome = loop.run(
            query,
            top_k=config.retrieval.get("top_k", 20),
            generate=generate,
            verifier=verifier,
        )
        debug = getattr(args, 'debug', False)
        if debug:
            _print_loop_debug(outcome)
        report = outcome.packed or context_builder.build_context_report(query, outcome.results)
        context = report.context
        context_history.append(context)

        if not llm_available or not llm:
            print(context[:3000] + ("..." if len(context) > 3000 else ""))
            continue

        response = outcome.answer
        if response is None:
            system_prompt = context_builder.build_system_prompt()
            if args.stream:
                response = llm.stream_query(system_prompt, context, query)
            else:
                response = llm.query(system_prompt, context, query)
        if not args.stream and response:
            print(response)
        if outcome.verify:
            print(f"[*] Verify: supported={outcome.verify.get('supported')}")
        print()


def cmd_info(args):
    config = _load_config(args)
    config.repo_path = args.repo or "."
    repo_name = _derive_repo_name(args)

    print(f"[*] Repository: {os.path.abspath(config.repo_path)}")
    print(f"[*] Repo name: {repo_name}")
    print()

    parser = CodeParser(config)
    files = parser.discover_files(config.repo_path)
    print(f"Files: {len(files)}")

    vs = VectorStore(config)
    try:
        count = vs.count()
        print(f"Indexed chunks: {count} (total across all repos)")
    except Exception:
        print("Indexed chunks: 0 (not indexed)")

    kg = KnowledgeGraph(config, repo_name=repo_name)
    kg.load()
    print(f"Knowledge graph ({repo_name}): {kg.graph.number_of_nodes()} nodes, "
          f"{kg.graph.number_of_edges()} edges")

    ext_counts: Dict[str, int] = {}
    for f in files:
        ext = f.suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    print()
    print("Files by type:")
    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
        print(f"  {ext}: {count}")

    # Show inter-repo relationships
    rg = RepoGraph(config)
    rg.load()
    print()
    print(rg.summary())


def cmd_clear(args):
    config = _load_config(args)
    repo_name = _derive_repo_name(args) if args.repo else ""
    base = os.path.dirname(
        config.vector_store.get("persist_directory", ".code-harness/chromadb")
    )

    vs = VectorStore(config)

    if repo_name:
        config.repo_path = os.path.abspath(args.repo)
        vs.delete_by_repo(repo_name)
        print(f"[*] Vector store: cleared chunks for repo '{repo_name}'")

        graph_file = os.path.join(
            os.path.dirname(config.knowledge_graph.get("persist_path", ".code-harness/graph.json")),
            f"graph_{repo_name}.json"
        )
        if os.path.exists(graph_file):
            os.remove(graph_file)
            print(f"[*] Knowledge graph cleared for '{repo_name}'")

        bm25_file = os.path.join(base, f"bm25_{repo_name}.pkl")
        if os.path.exists(bm25_file):
            os.remove(bm25_file)
            print(f"[*] BM25 index cleared for '{repo_name}'")

        rg = RepoGraph(config)
        rg.load()
        rg.remove_repo(repo_name)
        rg.build_cross_repo_edges()
        rg.save()
        print(f"[*] Removed '{repo_name}' from inter-repo graph")
    else:
        vs.delete_collection()
        print("[*] Vector store cleared (all repos)")

        graph_dir = os.path.dirname(
            config.knowledge_graph.get("persist_path", ".code-harness/graph.json")
        )
        if os.path.isdir(graph_dir):
            for gf in glob.glob(os.path.join(graph_dir, "graph_*.json")):
                os.remove(gf)
            legacy = os.path.join(graph_dir, "graph.json")
            if os.path.exists(legacy):
                os.remove(legacy)
        print("[*] All knowledge graphs cleared")

        for f in glob.glob(os.path.join(base, "*.pkl")):
            os.remove(f)
        print("[*] BM25 indices cleared")

        repo_graph_path = config.repo_graph.get("persist_path", ".code-harness/repo_graph.json")
        if os.path.exists(repo_graph_path):
            os.remove(repo_graph_path)
            print("[*] Inter-repo graph cleared")

    print("[+] Index cleared")


def cmd_retrieve_chunk(args):
    from harness.ccr import retrieve_chunk

    spill_dir = args.spill_dir or ".code-harness/ccr"
    text = retrieve_chunk(args.chunk_id, spill_dir=spill_dir)
    if text is None:
        print(f"[!] chunk not in cache: {args.chunk_id}")
        print(f"    looked in {os.path.abspath(spill_dir)}")
        sys.exit(1)
    print(text)


def _add_pack_flags(parser):
    parser.add_argument(
        "--pack-mode",
        choices=["full", "ccr_lite"],
        default=None,
        help="Context pack mode (default: full; override config/CODEHARNESS_PACK_MODE)",
    )
    parser.add_argument(
        "--expand-chunk",
        action="append",
        dest="expand_chunks",
        metavar="ID",
        help="Materialize a cached original chunk into the prompt (repeatable)",
    )


def _add_loop_flags(parser, include_verify: bool = False):
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Enable one extra corrective retrieve (opt-in; default is one-shot)",
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=None,
        metavar="N",
        help="Extra retrieve rounds (0-2). Default 0 preserves one-shot retrieval",
    )
    if include_verify:
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Independent LLM citation check on {query, answer, packed chunks} (off by default)",
        )


def _apply_loop_args(config, args):
    max_loops = int(config.retrieval.get("max_loops") or 0)
    if getattr(args, "max_loops", None) is not None:
        max_loops = int(args.max_loops)
    elif getattr(args, "loop", False):
        max_loops = max(max_loops, 1)
    config.retrieval["max_loops"] = max(0, min(max_loops, 2))
    if getattr(args, "verify", False):
        config.retrieval["verify"] = True
    return config


def _print_loop_debug(outcome):
    print("\n" + "=" * 70)
    print("  QUERY LOOP TRACE")
    print("=" * 70)
    print(
        f"  attempts={outcome.attempts}  grade={outcome.grade:.3f}  "
        f"coverage={outcome.coverage:.3f}  stop={outcome.stop_reason}  "
        f"mode={outcome.mode}"
    )
    for trace in outcome.traces or []:
        print(
            f"  [{trace.get('attempt')}] mode={trace.get('mode')}  "
            f"grade={trace.get('grade')}  action={trace.get('action')}  "
            f"q={trace.get('query')}"
        )
        _print_debug_trace(trace.get("query") or "", trace)
    print("=" * 70 + "\n")


def cmd_eval(args):
    from harness.eval import load_suite, print_summary, run_eval, write_report

    suite_path = args.suite
    try:
        fixtures, meta = load_suite(suite_path)
    except (OSError, ValueError) as exc:
        print(f"[!] Failed to load suite: {exc}")
        sys.exit(1)

    print(f"[*] Eval suite: {meta['suite']} ({len(fixtures)} fixtures)")
    print(f"[*] Suite path: {os.path.abspath(suite_path)}")

    if getattr(args, "dry_run", False):
        for fixture in fixtures:
            print(f"  - {fixture.id} [{fixture.difficulty}] {fixture.query}")
        print("[+] Dry-run OK (suite valid; no retrieval)")
        return

    config = _load_config(args)
    config.repo_path = args.repo or "."
    repo_name = _derive_repo_name(args)

    vs = VectorStore(config)
    count = vs.count()
    print(f"[*] Vector store: {count} chunks (total)")
    if count == 0:
        print("[!] No indexed data. Run 'python main.py index <repo>' first, then re-run eval.")
        sys.exit(1)

    embedder = Embedder(config)
    print(f"[*] Loading index for: {os.path.abspath(config.repo_path)} (repo: {repo_name})")
    kg = KnowledgeGraph(config, repo_name=repo_name)
    kg.load()
    retriever = Retriever(config, embedder, vs, kg, repo_name=repo_name)
    if retriever.try_load_bm25():
        print("[*] BM25 index loaded from disk")
    else:
        chunks = vs.get_all(repo_name=repo_name)
        retriever.index_chunks(chunks, persist=False)
        print("[*] BM25 index rebuilt from vector store")

    context_builder = ContextBuilder(config)
    k = args.k or meta.get("k") or config.retrieval.get("top_k", 10)
    k = int(k)

    snapshot = {
        "retrieval": dict(config.retrieval),
        "embedding": {
            "provider": config.embedding.get("provider"),
            "model": config.embedding.get("model"),
        },
        "llm": {
            "max_tokens": config.llm.get("max_tokens"),
        },
        "chunking": dict(config.chunking),
        "context": dict(config.context),
        "ccr": dict(config.ccr),
        "pack_mode": context_builder.pack_mode,
        "loop": {
            "max_loops": config.retrieval.get("max_loops", 0),
            "grade_threshold": config.retrieval.get("grade_threshold", 0.35),
            "citation_threshold": config.retrieval.get("citation_threshold", 0.5),
        },
    }

    from harness.loop import LoopConfig

    loop_config = LoopConfig.from_mapping(config.retrieval)
    if loop_config.max_loops:
        print(f"[*] Query loop: max_loops={loop_config.max_loops}")

    print(f"[*] Scoring {len(fixtures)} queries at k={k} (retrieval only, no LLM)\n")
    report = run_eval(
        fixtures=fixtures,
        retriever=retriever,
        context_builder=context_builder,
        k=k,
        suite_name=meta["suite"],
        suite_path=suite_path,
        repo_path=config.repo_path,
        repo_name=repo_name,
        config_snapshot=snapshot,
        loop_config=loop_config,
    )
    out_path = write_report(report, args.output)
    print_summary(report)
    print(f"[+] Wrote eval report: {out_path}")


def cmd_visualize(args):
    config = _load_config(args)
    output = args.output
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(script_dir, "visualizer", "visualize.py")]
    if output:
        cmd.extend(["--output", output])
    env = os.environ.copy()
    env["CODE_HARNESS_CONFIG"] = args.config or ""
    print("[*] Launching visualizer...")
    subprocess.run(cmd, env=env)
    print("[*] Visualizer closed.")


def cmd_watch(args):
    config = _load_config(args)
    repo_path = os.path.abspath(args.repo)
    repo_name = _derive_repo_name(args)

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("[!] watchdog not installed. Install with: pip install watchdog")
        return

    include_extensions = set(config.indexing.get("include_extensions", []))
    exclude_patterns = set(config.indexing.get("exclude_patterns", []))

    class ReindexHandler(FileSystemEventHandler):
        def __init__(self):
            self._timer = None
            self._pending = set()

        def _schedule_reindex(self):
            import threading
            if self._timer and self._timer.is_alive():
                return
            self._timer = threading.Timer(2.0, self._do_reindex)
            self._timer.daemon = True
            self._timer.start()

        def _do_reindex(self):
            changed = list(self._pending)
            self._pending.clear()
            if not changed:
                return
            print(f"\n[*] Change detected in {len(changed)} file(s), re-indexing...")
            for f in changed:
                print(f"    {f}")
            try:
                import sys as _sys
                _sys.argv = ["main.py", "index", repo_path]
                cmd_index(args)
                print(f"[*] Watch: re-index complete. Waiting for changes...")
            except Exception as e:
                print(f"[!] Re-index failed: {e}")

        def _should_watch(self, path: str) -> bool:
            ext = os.path.splitext(path)[1].lower()
            if ext not in include_extensions:
                return False
            for part in path.replace("\\", "/").split("/"):
                if part in exclude_patterns or part.startswith("."):
                    return False
            return True

        def on_modified(self, event):
            if not event.is_directory and self._should_watch(event.src_path):
                self._pending.add(event.src_path)
                self._schedule_reindex()

        def on_created(self, event):
            if not event.is_directory and self._should_watch(event.src_path):
                self._pending.add(event.src_path)
                self._schedule_reindex()

        def on_deleted(self, event):
            if not event.is_directory and self._should_watch(event.src_path):
                self._pending.add(event.src_path)
                self._schedule_reindex()

    print(f"[*] Watching: {repo_path}")
    print(f"[*] Extensions: {sorted(include_extensions)}")
    print("[*] Press Ctrl+C to stop\n")

    event_handler = ReindexHandler()
    observer = Observer()
    observer.schedule(event_handler, repo_path, recursive=True)
    observer.start()

    try:
        while True:
            import time as _time
            _time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print("[*] File watcher stopped.")


def _load_config(args) -> Config:
    if hasattr(args, 'config') and args.config:
        config = Config.from_file(args.config)
    else:
        config = Config()

    if hasattr(args, 'repo') and args.repo:
        config.repo_path = os.path.abspath(args.repo)

    if hasattr(args, 'verbose'):
        config.verbose = args.verbose

    env_mode = os.environ.get("CODEHARNESS_PACK_MODE")
    if env_mode in ("full", "ccr_lite"):
        config.context["pack_mode"] = env_mode
    if getattr(args, "pack_mode", None):
        config.context["pack_mode"] = args.pack_mode
    if getattr(args, "spill_ccr", False):
        config.ccr["spill"] = True

    _apply_loop_args(config, args)

    if not args.llm_provider:
        config.llm["provider"] = os.environ.get("LLM_PROVIDER", config.llm["provider"])
    else:
        config.llm["provider"] = args.llm_provider
    if not args.llm_model:
        config.llm["model"] = os.environ.get("LLM_MODEL", config.llm["model"])
    else:
        config.llm["model"] = args.llm_model
    if hasattr(args, 'embed_model') and args.embed_model:
        config.embedding["model"] = args.embed_model

    for key, provider in [("OPENAI_API_KEY", "openai"),
                           ("ANTHROPIC_API_KEY", "anthropic"),
                           ("GEMINI_API_KEY", "gemini")]:
        env_val = os.environ.get(key)
        if env_val and config.llm["provider"] == provider:
            config.llm["api_key"] = config.llm.get("api_key") or env_val

    return config


def _print_debug_trace(query: str, trace: dict):
    print("\n" + "=" * 70)
    print("  RETRIEVAL DEBUG TRACE")
    print("=" * 70)
    for source_name in ("dense", "sparse", "graph", "reranked"):
        results = trace.get(source_name, [])
        label_map = {
            "dense": f"DENSE (Vector DB) — {len(results)} results",
            "sparse": f"SPARSE (BM25) — {len(results)} results",
            "graph": f"GRAPH (Knowledge Graph) — {len(results)} results",
            "reranked": f"CROSS-ENCODER RERANKED — {len(results)} results",
        }
        print(f"\n  [{label_map[source_name]}]")
        if not results:
            print("    (none)")
            continue
        for i, r in enumerate(results[:8]):
            repo = f" [{r.chunk.repo_name}]" if r.chunk.repo_name else ""
            print(f"    {i+1}. {r.chunk.entity_name}{repo}")
            print(f"        {r.chunk.file_path}:{r.chunk.start_line}-{r.chunk.end_line}")
            print(f"        score={r.score:.4f}  type={r.chunk.entity_type.value}")
    latencies = trace.get("latencies_ms") or {}
    if latencies:
        parts = [f"{name}={value:.1f}ms" for name, value in latencies.items()]
        print("  [LATENCY] " + "  ".join(parts))
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Code Harness - Vectorize, index, and query code repos with AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s index ./my-project                            # Index a repo
  %(prog)s query ./my-project -q "how does auth work?"   # Query with AI
  %(prog)s query ./my-project --no-llm -q "find auth"    # Show context only
  %(prog)s interactive ./my-project                      # Interactive mode
   %(prog)s index ./my-project --embed-model all-MiniLM-L6-v2
   %(prog)s info ./my-project                             # Show repo stats
   %(prog)s watch ./my-project                            # Watch and auto re-index
  %(prog)s eval . --suite .docs/research/eval/code-harness.fixture.yaml
  %(prog)s eval . --suite .docs/research/eval/code-harness.fixture.yaml --loop
  %(prog)s query . --no-llm --pack-mode ccr_lite -q "how does the chunker work?"
  %(prog)s query . --no-llm --loop -q "Who calls Retriever index_chunks?"
  %(prog)s retrieve-chunk class:harness/chunker.py:CodeChunker:abcd1234
        """
    )
    parser.add_argument("--config", "-c", help="Config file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--llm-provider", choices=["openai", "anthropic", "gemini", "ollama", "custom"],
                        help="LLM provider")
    parser.add_argument("--llm-model", help="LLM model name")
    parser.add_argument("--embed-model", help="Embedding model name")
    parser.add_argument("--repo-name", help="Override the auto-derived repository name")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    idx = subparsers.add_parser("index", help="Index a repository")
    idx.add_argument("repo", nargs="?", default=".", help="Repository path")
    idx.add_argument("--save-config", nargs="?", const=True, help="Save config to file")
    idx.add_argument("--embed-model", help="Embedding model name (overrides config)")
    idx.set_defaults(func=cmd_index)

    q = subparsers.add_parser("query", help="Query the indexed repository")
    q.add_argument("repo", nargs="?", default=".", help="Repository path")
    q.add_argument("-q", "--query", required=True, help="Your question about the codebase")
    q.add_argument("--no-llm", action="store_true", help="Show context without LLM")
    q.add_argument("--stream", "-s", action="store_true", help="Stream LLM response")
    q.add_argument("--cross-repo", action="store_true",
                   help="Search across all indexed repositories")
    q.add_argument("--debug", action="store_true",
                   help="Show per-source retrieval breakdown (dense/sparse/graph/cross-encoder)")
    _add_pack_flags(q)
    _add_loop_flags(q, include_verify=True)
    q.set_defaults(func=cmd_query)

    int_p = subparsers.add_parser("interactive", aliases=["i"],
                                  help="Interactive query mode")
    int_p.add_argument("repo", nargs="?", default=".", help="Repository path")
    int_p.add_argument("--no-llm", action="store_false", dest="llm",
                       help="Disable LLM in interactive mode")
    int_p.add_argument("--stream", "-s", action="store_true", help="Stream LLM responses")
    int_p.add_argument("--cross-repo", action="store_true",
                      help="Search across all indexed repositories")
    int_p.add_argument("--debug", action="store_true",
                      help="Show per-source retrieval breakdown (dense/sparse/graph/cross-encoder)")
    _add_pack_flags(int_p)
    _add_loop_flags(int_p, include_verify=True)
    int_p.set_defaults(func=cmd_interactive)

    info = subparsers.add_parser("info", help="Show repository information")
    info.add_argument("repo", nargs="?", default=".", help="Repository path")
    info.set_defaults(func=cmd_info)

    clear = subparsers.add_parser("clear", help="Clear indexed data")
    clear.add_argument("repo", nargs="?", default=".", help="Repository path")
    clear.set_defaults(func=cmd_clear)

    viz = subparsers.add_parser("visualize", help="Launch vector space visualizer")
    viz.add_argument("--output", "-o", default=None,
                     help="Output path for the HTML visualization")
    viz.set_defaults(func=cmd_visualize)

    watch_p = subparsers.add_parser("watch", help="Watch repo and auto re-index on file changes")
    watch_p.add_argument("repo", nargs="?", default=".", help="Repository path")
    watch_p.set_defaults(func=cmd_watch)

    ev = subparsers.add_parser(
        "eval",
        help="Score retrieval against a golden suite (no LLM)",
    )
    ev.add_argument("repo", nargs="?", default=".", help="Repository path")
    ev.add_argument("--suite", required=True, help="Path to YAML/JSON golden suite")
    ev.add_argument("--k", type=int, default=None, help="Recall/nDCG cutoff (default: suite k or config top_k)")
    ev.add_argument("--output", "-o", help="JSON report path (default: .code-harness/eval/{suite}-{timestamp}.json)")
    ev.add_argument("--dry-run", action="store_true", help="Validate the suite without retrieving")
    ev.add_argument(
        "--pack-mode",
        choices=["full", "ccr_lite"],
        default=None,
        help="Context pack mode used for citation/token columns (default: full)",
    )
    _add_loop_flags(ev, include_verify=False)
    ev.set_defaults(func=cmd_eval)

    rc = subparsers.add_parser(
        "retrieve-chunk",
        help="Print a CCR-cached original chunk by id",
    )
    rc.add_argument("chunk_id", help="Chunk id from a packed context header")
    rc.add_argument(
        "--spill-dir",
        default=".code-harness/ccr",
        help="CCR cache directory (default: .code-harness/ccr)",
    )
    rc.set_defaults(func=cmd_retrieve_chunk)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()

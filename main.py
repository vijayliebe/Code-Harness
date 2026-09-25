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
    from harness.vector_store import describe_backend

    print(f"[*] Vector backend: {describe_backend(config)}")
    print()

    parser = CodeParser(config)
    print(f"[*] Discovering files...")
    files = parser.discover_files(repo_path)
    print(f"[*] Found {len(files)} files to index")

    print(f"[*] Parsing code...")
    entities, _ = parser.parse_repository(repo_path)
    print(f"[*] Extracted {len(entities)} code entities")

    try:
        from harness.kg_enrich import collect_enrichment_entities
        extra = collect_enrichment_entities(entities, repo_path, config)
        if extra:
            entities.extend(extra)
            print(f"[*] KG enrichment entities: {len(extra)} (endpoints/gloss)")
    except Exception as exc:
        print(f"[!] KG enrichment entities skipped: {exc}")

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
    vs.persist()
    print(f"[*] Vector store ({vs.backend_name}): {vs.count()} chunks indexed (total across all repos)")
    if vs.experimental:
        print("[!] Experimental backend: switching to/from Chroma requires a full re-index.")

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
    from harness.session import (
        Session,
        SessionTurn,
        apply_profile,
        handle_slash,
        parse_slash,
        resolve_profile_name,
        should_auto_expand,
    )

    config = _load_config(args)
    config.repo_path = args.repo or "."
    repo_name = _derive_repo_name(args)
    cross_repo = getattr(args, 'cross_repo', False)
    try:
        profile_name = resolve_profile_name(args)
    except ValueError as exc:
        print(f"[!] {exc}")
        sys.exit(2)

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

    session_cfg = getattr(config, "session", None) or {}
    session_dir = session_cfg.get("dir") or os.path.join(".code-harness", "sessions")
    max_tokens = int(config.llm.get("max_tokens") or 4096)
    multiplier = float((config.context or {}).get("max_tokens_multiplier") or 2)
    session = Session(
        repo=repo_name,
        profile=profile_name,
        session_dir=session_dir,
        input_usd_per_1m=config.llm.get("input_usd_per_1m"),
        output_usd_per_1m=config.llm.get("output_usd_per_1m"),
        budget_tokens=int(max_tokens * multiplier),
        keep_recent=int(session_cfg.get("keep_recent") or 1),
        config=config,
        redact=config.redaction.get("enabled", True) and config.redaction.get("session", True),
        audit_path=config.redaction.get("audit_path"),
    )

    print("=" * 60)
    print("  Code Harness - Interactive Session")
    print(f"  profile={session.profile}  pack={context_builder.pack_mode}")
    print("  /help /compact /cost /profile /exit")
    print("=" * 60)
    print()

    context_history = []

    while True:
        try:
            query = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            _maybe_auto_extract_session(session, config)
            break

        if not query:
            continue

        slash = parse_slash(query)
        if slash is not None:
            result = handle_slash(session, slash)
            if result.should_exit:
                _maybe_auto_extract_session(session, config)
                break
            if result.kind == "profile" and result.profile:
                try:
                    apply_profile(config, result.profile)
                    context_builder = ContextBuilder(config)
                    print(result.message)
                    print(f"[*] pack_mode={context_builder.pack_mode}")
                except ValueError as exc:
                    print(f"[!] {exc}")
                continue
            if result.llm is not None:
                llm_available = result.llm
                llm = LLMInterface(config) if llm_available else None
                print(result.message)
                continue
            if result.clear_screen:
                os.system("clear" if os.name == "posix" else "cls")
                continue
            if result.kind == "context":
                if context_history:
                    print(context_history[-1][:2000])
                else:
                    print("No context yet")
                continue
            if result.expand_ref:
                _print_expand(context_builder, config, result.expand_ref, session)
                continue
            if result.memory_brief:
                _print_memory_brief(config)
                continue
            if result.wiki_page:
                _print_wiki_page(config, result.wiki_page)
                continue
            if result.message:
                print(result.message)
            if result.kind == "compact":
                _maybe_auto_extract_session(session, config)
            continue

        from harness.loop import LoopConfig, QueryLoop, verify_answer

        loop = QueryLoop(retriever, context_builder, LoopConfig.from_mapping(config.retrieval))
        generate = None
        can_generate = llm_available and llm is not None and _llm_ready(llm, config)
        if can_generate:
            if args.stream:
                generate = lambda system, context, user_q: llm.stream_query(system, context, user_q)
            else:
                generate = lambda system, context, user_q: llm.query(system, context, user_q)
        verifier = None
        if config.retrieval.get("verify") and can_generate:
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
        expand_ids = []
        if should_auto_expand(
            query=query,
            expand_on=config.ccr.get("expand_on") or [],
            omitted_ids=report.omitted_chunk_ids or [],
            packed_ids=report.packed_chunk_ids or [],
            max_loops=int(config.retrieval.get("max_loops") or 0),
        ):
            expand_ids = list(report.omitted_chunk_ids or [])[:3]
            if expand_ids:
                context = context_builder.expand_into_context(context, expand_ids)
                print(f"[*] auto-expand {len(expand_ids)} omitted chunk(s) (explain/omit)")
        context_history.append(context)

        session.record_turn(SessionTurn(role="user", text=query))
        answer = outcome.answer
        if not can_generate:
            if llm_available and llm is not None and not _llm_ready(llm, config):
                print("[!] LLM key/provider missing; printing packed context.")
            preview = context[:3000] + ("..." if len(context) > 3000 else "")
            print(preview)
            answer = preview
        else:
            if answer is None:
                system_prompt = context_builder.build_system_prompt()
                history = session.history_for_prompt()
                if history:
                    context = context.rstrip() + "\n\n## Session history\n" + history + "\n"
                if args.stream:
                    answer = llm.stream_query(system_prompt, context, query)
                else:
                    answer = llm.query(system_prompt, context, query)
            if not args.stream and answer:
                print(answer)
            if outcome.verify:
                print(f"[*] Verify: supported={outcome.verify.get('supported')}")
        completion = context_builder.estimate_tokens(answer or "")
        session.record_turn(
            SessionTurn(
                role="assistant",
                text=answer or "",
                chunk_ids=list(report.packed_chunk_ids or []),
                packed_tokens=int(report.prompt_tokens_packed or report.prompt_tokens or 0),
                full_tokens=int(report.prompt_tokens_full or report.prompt_tokens or 0),
                completion_tokens=completion,
                loop_attempts=int(getattr(outcome, "attempts", 1) or 1),
                pack_mode=report.pack_mode,
            )
        )
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
    if getattr(args, "mermaid", False):
        focus = getattr(args, "focus", None)
        print()
        print(kg.to_mermaid(focus=focus, max_nodes=getattr(args, "max_nodes", 40) or 40))
        if not getattr(args, "stats", False):
            return

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
    from harness.redact import redact_and_audit, redaction_enabled

    explicit = False if getattr(args, "no_redact", False) else None
    if redaction_enabled(explicit=explicit):
        text = redact_and_audit(text, action="redact.retrieve").text
    print(text)


def cmd_audit(args):
    from harness.audit import default_audit_path, tail_audit

    action = getattr(args, "audit_cmd", None) or "show"
    if action not in ("show", "tail"):
        parser = getattr(args, "audit_parser", None)
        if parser is not None:
            parser.print_help()
        else:
            print("usage: main.py audit {show,tail}")
        sys.exit(2)

    n = int(getattr(args, "last", None) or 20)
    repo = getattr(args, "repo", None) or "."
    path = getattr(args, "path", None) or default_audit_path(repo)
    rows = tail_audit(n, path=path)
    if not rows:
        print(f"[!] No audit events in {path}")
        return
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


def cmd_wiki(args):
    from harness.okf import default_wiki_dir
    from harness.wiki import MissingGraphError, generate_from_repo, list_pages, show_page

    action = getattr(args, "wiki_cmd", None)
    if not action:
        parser = getattr(args, "wiki_parser", None)
        if parser is not None:
            parser.print_help()
        else:
            print("usage: main.py wiki {generate,list,show}")
        sys.exit(2)

    repo = getattr(args, "repo", None) or "."
    config = _load_config(args)
    config.repo_path = os.path.abspath(repo)
    repo_name = _derive_repo_name(args)
    out_dir = getattr(args, "out", None) or default_wiki_dir(config.repo_path)

    if action == "generate":
        try:
            result = generate_from_repo(
                config.repo_path,
                config=config,
                repo_name=repo_name,
                out_dir=out_dir,
                graph_path=getattr(args, "graph", None),
                module=getattr(args, "module", None),
                dirty=bool(getattr(args, "dirty", False)),
            )
        except MissingGraphError as exc:
            print(f"[!] {exc}")
            sys.exit(1)
        if result.skipped_reason:
            print(f"[!] Wiki dirty-generate skipped: {result.skipped_reason}")
            return
        mode = " (dirty)" if result.dirty else ""
        print(f"[*] Wiki generate{mode}: {result.page_count} pages → {result.wiki_dir}")
        for name in result.pages:
            print(f"    {name}")
        if result.dirty and result.skipped:
            print(f"[*] Skipped (clean): {', '.join(result.skipped)}")
        if result.citations:
            print(f"[*] Sample cite: `{result.citations[0]}`")
        if result.mermaid_pages:
            print(f"[*] Mermaid on: {', '.join(result.mermaid_pages)}")
        if result.dirty and result.page_count == 0:
            print("[+] Wiki already clean")
        else:
            print("[+] Wiki written")
        return

    if action == "list":
        try:
            pages = list_pages(out_dir)
        except FileNotFoundError as exc:
            print(f"[!] {exc}")
            sys.exit(1)
        if not pages:
            print(f"[!] No wiki pages in {out_dir}")
            print(f"    Run: python main.py wiki generate {repo}")
            return
        print(f"[*] Wiki pages in {out_dir}")
        for item in pages:
            kind = f" ({item['type']})" if item.get("type") else ""
            print(f"    {item['path']}: {item['title']}{kind}")
        return

    if action == "show":
        try:
            text = show_page(out_dir, args.page)
        except FileNotFoundError as exc:
            print(f"[!] {exc}")
            sys.exit(1)
        print(text)
        return


def cmd_memory(args):
    from harness.memory import MemoryError, MemoryStore
    from harness.okf import default_memory_dir

    action = getattr(args, "memory_cmd", None)
    if not action:
        parser = getattr(args, "memory_parser", None)
        if parser is not None:
            parser.print_help()
        else:
            print("usage: main.py memory {add,list,brief,export,import,extract}")
        sys.exit(2)

    repo = getattr(args, "repo", None) or "."
    config = _load_config(args)
    config.repo_path = os.path.abspath(repo)
    memory_dir = getattr(args, "dir", None) or default_memory_dir(config.repo_path)
    store = MemoryStore(config.repo_path, memory_dir=memory_dir)

    try:
        if action == "add":
            body = _memory_body(args)  # MemoryError if missing
            links = getattr(args, "link", None) or []
            entry = store.add(
                kind=args.type,
                title=args.title,
                body=body,
                links=links,
                tags=getattr(args, "tag", None) or [],
                supersedes=getattr(args, "supersedes", None),
                timestamp=getattr(args, "timestamp", None),
                entry_id=getattr(args, "id", None),
            )
            print(f"[+] Memory {entry.kind} {entry.id}")
            print(f"    {entry.rel_path}")
            if entry.links:
                print(f"    links: {', '.join(entry.links)}")
            if entry.supersedes:
                print(f"    supersedes: {entry.supersedes}")
            return

        if action == "list":
            rows = store.list(
                kind=getattr(args, "type", None),
                as_of=getattr(args, "as_of", None),
                include_inactive=bool(getattr(args, "all", False)),
            )
            if not rows:
                print(f"[!] No memory entries in {memory_dir}")
                print(f"    Run: python main.py memory add {repo} --type decision --title '...' --body '...'")
                return
            print(f"[*] Memory entries in {memory_dir}")
            for entry in rows:
                cites = f"  {', '.join(entry.links)}" if entry.links else ""
                pointer = f"  supersedes={entry.supersedes}" if entry.supersedes else ""
                print(
                    f"    [{entry.status}] {entry.kind} {entry.id}: {entry.title}{cites}{pointer}"
                )
            return

        if action == "brief":
            brief = store.brief(
                query=getattr(args, "query", None) or "",
                redact=bool(getattr(args, "redact", False)),
            )
            if not brief.text:
                print(f"[!] No active memory to brief in {memory_dir}")
                return
            print(brief.text.rstrip())
            print(f"\n[*] tokens={brief.token_count} entries={len(brief.entry_ids)}")
            return

        if action == "export":
            dest = getattr(args, "bundle", None) or getattr(args, "out", None)
            if not dest:
                print("[!] memory export requires a destination directory")
                sys.exit(2)
            count = store.export_okf(dest, redact=bool(getattr(args, "redact", False)))
            print(f"[+] Exported {count} OKF concept(s) → {dest}")
            return

        if action == "import":
            src = getattr(args, "bundle", None)
            if not src:
                print("[!] memory import requires a source bundle directory")
                sys.exit(2)
            count = store.import_okf(src)
            print(f"[+] Imported {count} OKF concept(s) → {memory_dir}")
            return

        if action == "extract":
            from harness.memory_extract import (
                MemoryExtractError,
                extract_session,
                format_extract_report,
            )

            llm = None
            use_llm = bool(getattr(args, "use_llm", False))
            if use_llm:
                try:
                    llm = LLMInterface(config)
                except Exception:
                    llm = None
            try:
                report = extract_session(
                    repo=config.repo_path,
                    session_path=getattr(args, "session", None),
                    store=store,
                    dry_run=bool(getattr(args, "dry_run", False)),
                    use_llm=use_llm,
                    config=config,
                    llm=llm,
                )
            except MemoryExtractError as exc:
                print(f"[!] {exc}")
                sys.exit(1)
            print(format_extract_report(report))
            return
    except MemoryError as exc:
        print(f"[!] {exc}")
        sys.exit(1)


def _memory_body(args) -> str:
    body_file = getattr(args, "body_file", None)
    if body_file:
        if body_file == "-":
            return sys.stdin.read()
        with open(body_file, encoding="utf-8") as fh:
            return fh.read()
    body = getattr(args, "body", None)
    if body is None:
        from harness.memory import MemoryError
        raise MemoryError("memory add requires --body or --body-file")
    return body


def _llm_ready(llm, config) -> bool:
    if llm is None:
        return False
    provider = (config.llm or {}).get("provider")
    if provider in ("ollama", "custom"):
        return True
    return bool(getattr(llm, "api_key", None))


def _print_expand(context_builder, config, ref: str, session):
    from harness.ccr import retrieve_chunk

    text = context_builder.cache.get(ref) or retrieve_chunk(
        ref,
        spill_dir=(config.ccr or {}).get("spill_dir"),
    )
    if text is None:
        needle = ref.replace("\\", "/")
        for cid in session.latest_pack_ids() + session.all_kept_chunk_ids():
            if needle in cid.replace("\\", "/"):
                text = context_builder.cache.get(cid) or retrieve_chunk(
                    cid,
                    spill_dir=(config.ccr or {}).get("spill_dir"),
                )
                if text is not None:
                    print(f"[*] resolved {ref} → {cid}")
                    break
    if text is None:
        print(f"[!] chunk not in cache: {ref}")
        return
    from harness.redact import redact_and_audit, redaction_enabled

    if redaction_enabled(config):
        text = redact_and_audit(text, action="redact.retrieve", config=config).text
    print(text)


def _print_memory_brief(config):
    from harness.memory import MemoryStore

    store = MemoryStore.for_repo(
        config.repo_path or ".",
        memory_dir=(config.context or {}).get("memory_dir") or None,
    )
    brief = store.brief()
    if not brief.text:
        print("[!] No active memory to brief")
        return
    print(brief.text.rstrip())
    print(f"\n[*] tokens={brief.token_count} entries={len(brief.entry_ids)}")


def _maybe_auto_extract_session(session, config):
    from harness.memory_extract import format_extract_report, maybe_auto_extract

    report = maybe_auto_extract(config=config, session=session)
    if report is None:
        return
    print(format_extract_report(report))


def _print_wiki_page(config, page: str):
    from harness.okf import default_wiki_dir
    from harness.wiki import show_page

    out_dir = default_wiki_dir(config.repo_path or ".")
    try:
        print(show_page(out_dir, page))
    except FileNotFoundError as exc:
        print(f"[!] {exc}")


def cmd_doctor(args):
    from harness.doctor import run_doctor

    config = _load_config(args)
    config.repo_path = os.path.abspath(getattr(args, "repo", None) or ".")
    repo_name = _derive_repo_name(args)
    report = run_doctor(config, repo_path=config.repo_path, repo_name=repo_name)
    print(report.format())
    if report.exit_code:
        sys.exit(report.exit_code)


def cmd_serve(args):
    from harness.serve import (
        DEFAULT_HOST,
        DEFAULT_PORT,
        BindError,
        QueryCache,
        build_retrieve_service,
        make_server,
        resolve_bind_host,
        serve_forever,
    )

    config = _load_config(args)
    config.repo_path = os.path.abspath(getattr(args, "repo", None) or ".")
    repo_name = _derive_repo_name(args)
    serve_cfg = getattr(config, "serve", None) or {}
    host = getattr(args, "host", None) or serve_cfg.get("host") or DEFAULT_HOST
    port = getattr(args, "port", None)
    if port is None:
        port = serve_cfg.get("port") or DEFAULT_PORT
    allow_public = bool(getattr(args, "allow_public", False))
    try:
        bind = resolve_bind_host(host, allow_public=allow_public)
    except BindError as exc:
        print(f"[!] {exc}")
        sys.exit(2)

    use_cache = serve_cfg.get("cache") is not False
    if getattr(args, "no_cache", False):
        use_cache = False
    cache = None
    if use_cache:
        cache_path = getattr(args, "cache_path", None) or serve_cfg.get("cache_path")
        if not cache_path:
            cache_path = os.path.join(config.repo_path, ".code-harness", "query_cache.sqlite")
        elif not os.path.isabs(cache_path):
            cache_path = os.path.join(config.repo_path, cache_path)
        cache = QueryCache(cache_path)
        print(f"[*] query cache: {cache_path}")

    print(f"[*] Loading retrieve service for {config.repo_path} (repo: {repo_name})")
    try:
        service = build_retrieve_service(config, repo_name, cache=cache)
    except Exception as exc:
        print(f"[!] Failed to load index: {exc}")
        print(f"    hint: python main.py index {config.repo_path}")
        sys.exit(1)

    try:
        server = make_server(host=bind, port=int(port), service=service, allow_public=allow_public)
    except BindError as exc:
        print(f"[!] {exc}")
        sys.exit(2)
    except OSError as exc:
        print(f"[!] bind failed: {exc}")
        sys.exit(1)

    actual_host, actual_port = server.server_address[:2]
    print(f"[*] health:       http://{actual_host}:{actual_port}/health")
    print(f"[*] retrieve:     POST http://{actual_host}:{actual_port}/v1/retrieve")
    print(f"[*] MCP tools:    POST http://{actual_host}:{actual_port}/mcp")
    print("[!] loopback only by default; --allow-public binds all interfaces (no auth, dangerous)")
    serve_forever(server)


def _add_serve_flags(parser, include_repo=True):
    if include_repo:
        parser.add_argument("repo", nargs="?", default=".", help="Repository path")
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default: 127.0.0.1 / localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: 7432)",
    )
    parser.add_argument(
        "--allow-public",
        action="store_true",
        dest="allow_public",
        help="DANGEROUS: allow 0.0.0.0 / all interfaces. No auth. Default is loopback only.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the optional query-hash retrieve cache",
    )
    parser.add_argument(
        "--cache-path",
        default=None,
        help="SQLite query cache path (default: <repo>/.code-harness/query_cache.sqlite)",
    )
    _add_pack_flags(parser)
    _add_loop_flags(parser)
    _add_profile_flag(parser)
    _add_redact_flag(parser)
    parser.set_defaults(func=cmd_serve)


def _add_redact_flag(parser):
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Disable secret redaction (tests / explicit opt-out only)",
    )


def _add_profile_flag(parser):
    parser.add_argument(
        "--profile",
        choices=["default", "sage"],
        default=None,
        help="Named flag pack (sage: ccr_lite, more graph expand, higher pack budget)",
    )


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
    parser.add_argument(
        "--include-memory-brief",
        action="store_true",
        help="Inject typed memory brief (≤800 tokens) after project docs (default off)",
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


def _eval_snapshot(config, context_builder, backend_name: str) -> Dict:
    return {
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
        "vector_backend": backend_name,
        "loop": {
            "max_loops": config.retrieval.get("max_loops", 0),
            "grade_threshold": config.retrieval.get("grade_threshold", 0.35),
            "citation_threshold": config.retrieval.get("citation_threshold", 0.5),
        },
    }


def _run_eval_backend(config, fixtures, meta, suite_path, repo_name, k, loop_config):
    from harness.eval import run_eval
    from harness.vector_store import BackendUnavailable, describe_backend

    print(f"[*] Vector backend: {describe_backend(config)}")
    try:
        vs = VectorStore(config)
    except BackendUnavailable as exc:
        return None, str(exc)
    count = vs.count()
    print(f"[*] Vector store ({vs.backend_name}): {count} chunks (total)")
    if count == 0:
        return None, (
            f"no index for {vs.backend_name}; "
            f"run python main.py index <repo> --vector-backend {vs.backend_name} "
            "(rebuild required when switching backends)"
        )
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
    report = run_eval(
        fixtures=fixtures,
        retriever=retriever,
        context_builder=context_builder,
        k=k,
        suite_name=meta["suite"],
        suite_path=suite_path,
        repo_path=config.repo_path,
        repo_name=repo_name,
        config_snapshot=_eval_snapshot(config, context_builder, vs.backend_name),
        loop_config=loop_config,
    )
    return report, None


def cmd_eval(args):
    from harness.eval import load_suite, print_summary, write_report
    from harness.vector_eval import (
        compare_backend_reports,
        gate_exit_code,
        parse_backend_list,
        print_backend_comparison,
        probe_backend,
        selected_backend_name,
    )
    from harness.vector_store import apply_vector_backend

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
    from harness.loop import LoopConfig

    loop_config = LoopConfig.from_mapping(config.retrieval)
    if loop_config.max_loops:
        print(f"[*] Query loop: max_loops={loop_config.max_loops}")

    k = args.k or meta.get("k") or config.retrieval.get("top_k", 10)
    k = int(k)
    selected = selected_backend_name(config)
    compare_names = parse_backend_list(getattr(args, "compare_backends", None))
    if selected == "turbovec" and "chromadb" not in compare_names:
        compare_names = ["chromadb", "turbovec"] + [n for n in compare_names if n not in ("chromadb", "turbovec")]
    if not compare_names:
        compare_names = [selected]

    print(f"[*] Scoring {len(fixtures)} queries at k={k} (retrieval only, no LLM)\n")
    reports = {}
    skips = {}
    for name in compare_names:
        ok, reason = probe_backend(name, config)
        if not ok:
            print(f"[!] Skipping {name}: {reason}")
            skips[name] = reason
            continue
        cfg_one = Config.from_dict({
            "embedding": dict(config.embedding),
            "chunking": dict(config.chunking),
            "vector_store": dict(config.vector_store),
            "knowledge_graph": dict(config.knowledge_graph),
            "repo_graph": dict(config.repo_graph),
            "retrieval": dict(config.retrieval),
            "llm": dict(config.llm),
            "indexing": dict(config.indexing),
            "context": dict(config.context),
            "ccr": dict(config.ccr),
            "session": dict(config.session),
            "redaction": dict(config.redaction),
            "serve": dict(config.serve),
            "repo_path": config.repo_path,
            "verbose": config.verbose,
        })
        apply_vector_backend(cfg_one, name)
        report, err = _run_eval_backend(
            cfg_one, fixtures, meta, suite_path, repo_name, k, loop_config
        )
        if err:
            print(f"[!] Skipping {name}: {err}")
            skips[name] = err
            continue
        reports[name] = report
        print_summary(report)

    primary = reports.get(selected)
    if primary is None and selected not in reports:
        if selected in skips:
            print(f"[!] Selected backend {selected} unavailable: {skips[selected]}")
            sys.exit(1)
        print("[!] No indexed data. Run 'python main.py index <repo>' first, then re-run eval.")
        sys.exit(1)

    if primary is not None:
        out_path = write_report(primary, args.output)
        print(f"[+] Wrote eval report: {out_path}")

    if len(compare_names) > 1 or selected == "turbovec":
        baseline = reports.get("chromadb")
        candidate = reports.get("turbovec")
        skip_reason = skips.get("turbovec", "")
        if candidate is None and "turbovec" not in skips and "turbovec" not in compare_names:
            skip_reason = skip_reason or "turbovec not in --compare-backends"
        result = compare_backend_reports(
            baseline,
            candidate,
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=k,
            relative_tolerance=getattr(args, "gate_tolerance", None) or 0.05,
            force_experimental=bool(getattr(args, "force_experimental", False)),
            skip_reason=skip_reason,
        )
        print_backend_comparison(result)
        code = gate_exit_code(result, selected)
        if code:
            print("[!] Experimental backend missed the recall gate. Stay on chromadb or pass --force-experimental.")
            sys.exit(code)


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
                try:
                    from harness.wiki import maybe_dirty_wiki_regen
                    wiki_result = maybe_dirty_wiki_regen(
                        repo_path, config=config, repo_name=repo_name,
                    )
                    if wiki_result and wiki_result.skipped_reason:
                        print(f"[*] Watch: wiki dirty-regen skipped ({wiki_result.skipped_reason.splitlines()[0]})")
                    elif wiki_result:
                        print(
                            f"[*] Watch: wiki dirty-regen "
                            f"{wiki_result.page_count} page(s)"
                        )
                except Exception as wiki_exc:
                    print(f"[*] Watch: wiki dirty-regen skipped ({wiki_exc})")
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

    from harness.session import apply_profile, resolve_profile_name

    try:
        profile_name = resolve_profile_name(args)
    except ValueError:
        profile_name = "default"
    if profile_name != "default" and not getattr(args, "skip_profile", False):
        apply_profile(config, profile_name)

    env_mode = os.environ.get("CODEHARNESS_PACK_MODE")
    if env_mode in ("full", "ccr_lite"):
        config.context["pack_mode"] = env_mode
    if getattr(args, "pack_mode", None):
        config.context["pack_mode"] = args.pack_mode
    if getattr(args, "spill_ccr", False):
        config.ccr["spill"] = True
    env_brief = os.environ.get("CODEHARNESS_MEMORY_BRIEF", "").strip().lower()
    if env_brief in ("1", "true", "yes", "on"):
        config.context["include_memory_brief"] = True
    if getattr(args, "include_memory_brief", False):
        config.context["include_memory_brief"] = True

    env_redact = os.environ.get("CODEHARNESS_REDACT", "").strip().lower()
    if env_redact in ("0", "false", "off", "no"):
        config.redaction["enabled"] = False
    if getattr(args, "no_redact", False):
        config.redaction["enabled"] = False
    env_audit = os.environ.get("CODEHARNESS_AUDIT", "").strip().lower()
    if env_audit in ("0", "false", "off", "no"):
        config.redaction["audit"] = False

    _apply_loop_args(config, args)

    env_backend = os.environ.get("CODEHARNESS_VECTOR_BACKEND", "").strip()
    backend_name = getattr(args, "vector_backend", None) or env_backend or None
    if backend_name:
        from harness.vector_store import apply_vector_backend

        apply_vector_backend(config, backend_name)

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
  %(prog)s chat ./my-project                             # Interactive session
  %(prog)s chat ./my-project --profile sage              # Sage pack + expand
  %(prog)s interactive ./my-project                      # Alias for chat
   %(prog)s index ./my-project --embed-model all-MiniLM-L6-v2
   %(prog)s info ./my-project                             # Show repo stats
   %(prog)s info ./my-project --mermaid --focus class:harness/context_builder.py:ContextBuilder
   %(prog)s wiki generate ./my-project                    # Living wiki from the KG
   %(prog)s wiki generate ./my-project --dirty             # Only pages whose sources changed
   %(prog)s wiki list ./my-project
   %(prog)s wiki show architecture
   %(prog)s memory add . --type decision --title "Keep Chroma" --body "Until TurboVec gates pass." --link harness/vector_store.py:VectorStore
   %(prog)s memory list .
   %(prog)s memory brief . -q "why chroma?"
   %(prog)s memory export . ./okf-bundle
   %(prog)s memory import . ./okf-bundle
   %(prog)s memory extract . --dry-run
   %(prog)s memory extract . --session .code-harness/sessions/20260925.jsonl
   %(prog)s audit show --last 20
   %(prog)s audit tail --path .code-harness/audit/audit.jsonl
   %(prog)s doctor ./my-project                           # Local health check
   %(prog)s serve ./my-project                            # POST /v1/retrieve on 127.0.0.1
   %(prog)s mcp serve ./my-project                        # MCP tools + retrieve (localhost)
   %(prog)s api serve ./my-project                        # Alias for serve
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
    idx.add_argument(
        "--vector-backend",
        default=None,
        help="Dense backend: chromadb (default) or turbovec (experimental). Rebuild required on switch.",
    )
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
    _add_profile_flag(q)
    _add_redact_flag(q)
    q.set_defaults(func=cmd_query)

    int_p = subparsers.add_parser(
        "interactive",
        aliases=["i", "chat", "session", "repl"],
        help="Interactive query session (REPL with /compact and /cost)",
    )
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
    _add_profile_flag(int_p)
    _add_redact_flag(int_p)
    int_p.set_defaults(func=cmd_interactive)

    info = subparsers.add_parser("info", help="Show repository information")
    info.add_argument("repo", nargs="?", default=".", help="Repository path")
    info.add_argument(
        "--mermaid",
        action="store_true",
        help="Export a Mermaid subgraph from the knowledge graph (wiki precursor)",
    )
    info.add_argument(
        "--focus",
        default=None,
        metavar="ENTITY_ID",
        help="Center the Mermaid subgraph on this entity id",
    )
    info.add_argument(
        "--max-nodes",
        type=int,
        default=40,
        help="Cap Mermaid nodes (default: 40)",
    )
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
        "--vector-backend",
        default=None,
        help="Dense backend for this run: chromadb (default) or turbovec (experimental)",
    )
    ev.add_argument(
        "--compare-backends",
        default=None,
        help="Comma list to A/B (e.g. chromadb,turbovec). Prints Recall@k / nDCG@k side-by-side.",
    )
    ev.add_argument(
        "--force-experimental",
        action="store_true",
        help="Do not fail when the experimental backend misses the recall gate",
    )
    ev.add_argument(
        "--gate-tolerance",
        type=float,
        default=None,
        help="Relative Recall/nDCG drop allowed vs chromadb (default: 0.05)",
    )
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
    _add_redact_flag(rc)
    rc.set_defaults(func=cmd_retrieve_chunk)

    wiki = subparsers.add_parser(
        "wiki",
        help="Generate and inspect a living project wiki (OKF WikiPage)",
    )
    wiki.set_defaults(func=cmd_wiki, wiki_parser=wiki)
    wiki_sub = wiki.add_subparsers(dest="wiki_cmd")

    wiki_gen = wiki_sub.add_parser(
        "generate",
        help="Write OKF WikiPage markdown from the knowledge graph",
    )
    wiki_gen.add_argument("repo", nargs="?", default=".", help="Repository path")
    wiki_gen.add_argument(
        "--out",
        default=None,
        help="Wiki directory (default: <repo>/knowledge/wiki)",
    )
    wiki_gen.add_argument(
        "--graph",
        default=None,
        help="Override path to graph_{repo}.json",
    )
    wiki_gen.add_argument(
        "--module",
        default=None,
        metavar="PKG_OR_PATH",
        help="Regenerate one package page (plus architecture index)",
    )
    wiki_gen.add_argument(
        "--dirty",
        action="store_true",
        help="Regenerate only pages whose source hashes or KG subgraph changed",
    )
    wiki_gen.set_defaults(func=cmd_wiki)

    wiki_list = wiki_sub.add_parser("list", help="List generated wiki pages")
    wiki_list.add_argument("repo", nargs="?", default=".", help="Repository path")
    wiki_list.add_argument(
        "--out",
        default=None,
        help="Wiki directory (default: <repo>/knowledge/wiki)",
    )
    wiki_list.set_defaults(func=cmd_wiki)

    wiki_show = wiki_sub.add_parser("show", help="Print one wiki page")
    wiki_show.add_argument("page", help="Page name (e.g. architecture or harness.md)")
    wiki_show.add_argument("repo", nargs="?", default=".", help="Repository path")
    wiki_show.add_argument(
        "--out",
        default=None,
        help="Wiki directory (default: <repo>/knowledge/wiki)",
    )
    wiki_show.set_defaults(func=cmd_wiki)

    mem = subparsers.add_parser(
        "memory",
        help="Typed project memory (decision/error/preference/fact) + OKF import/export",
    )
    mem.set_defaults(func=cmd_memory, memory_parser=mem)
    mem_sub = mem.add_subparsers(dest="memory_cmd")

    mem_add = mem_sub.add_parser("add", help="Write one OKF memory concept")
    mem_add.add_argument("repo", nargs="?", default=".", help="Repository path")
    mem_add.add_argument(
        "--type",
        required=True,
        help="decision | error | preference | fact",
    )
    mem_add.add_argument("--title", required=True, help="Required human title")
    mem_add.add_argument("--body", default=None, help="Memory body (prose)")
    mem_add.add_argument(
        "--body-file",
        default=None,
        help="Read body from a file (use - for stdin)",
    )
    mem_add.add_argument(
        "--link",
        action="append",
        default=[],
        metavar="PATH:SYMBOL",
        help="Optional path:symbol citation (repeatable)",
    )
    mem_add.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Optional tag (repeatable)",
    )
    mem_add.add_argument("--supersedes", default=None, help="Id of the entry this replaces")
    mem_add.add_argument("--id", default=None, help="Override generated mem/YYYYMMDD-slug id")
    mem_add.add_argument("--timestamp", default=None, help="ISO-8601 timestamp (default: now UTC)")
    mem_add.add_argument(
        "--dir",
        default=None,
        help="Memory directory (default: <repo>/knowledge/memory)",
    )
    mem_add.set_defaults(func=cmd_memory)

    mem_list = mem_sub.add_parser("list", help="List typed memories")
    mem_list.add_argument("repo", nargs="?", default=".", help="Repository path")
    mem_list.add_argument("--type", default=None, help="Filter by kind")
    mem_list.add_argument("--as-of", dest="as_of", default=None, help="ISO-8601 as-of filter")
    mem_list.add_argument(
        "--all",
        action="store_true",
        help="Include superseded / forgotten tombstones",
    )
    mem_list.add_argument(
        "--dir",
        default=None,
        help="Memory directory (default: <repo>/knowledge/memory)",
    )
    mem_list.set_defaults(func=cmd_memory)

    mem_brief = mem_sub.add_parser("brief", help="Pack active memories (≤800 tokens)")
    mem_brief.add_argument("repo", nargs="?", default=".", help="Repository path")
    mem_brief.add_argument("-q", "--query", default="", help="Optional ranking query")
    mem_brief.add_argument(
        "--dir",
        default=None,
        help="Memory directory (default: <repo>/knowledge/memory)",
    )
    mem_brief.add_argument(
        "--redact",
        action="store_true",
        help="Strip secrets from the printed brief (default off)",
    )
    mem_brief.set_defaults(func=cmd_memory)

    mem_export = mem_sub.add_parser("export", help="Write an OKF markdown bundle")
    mem_export.add_argument("repo", nargs="?", default=".", help="Repository path")
    mem_export.add_argument("bundle", help="Destination directory")
    mem_export.add_argument(
        "--dir",
        default=None,
        help="Memory directory (default: <repo>/knowledge/memory)",
    )
    mem_export.add_argument(
        "--redact",
        action="store_true",
        help="Strip secrets from exported OKF files (default off)",
    )
    mem_export.set_defaults(func=cmd_memory)

    mem_import = mem_sub.add_parser("import", help="Import an OKF markdown bundle")
    mem_import.add_argument("repo", nargs="?", default=".", help="Repository path")
    mem_import.add_argument("bundle", help="Source bundle directory")
    mem_import.add_argument(
        "--dir",
        default=None,
        help="Memory directory (default: <repo>/knowledge/memory)",
    )
    mem_import.set_defaults(func=cmd_memory)

    mem_extract = mem_sub.add_parser(
        "extract",
        help="Heuristic extract typed memories from a session JSONL",
    )
    mem_extract.add_argument("repo", nargs="?", default=".", help="Repository path")
    mem_extract.add_argument(
        "--session",
        default=None,
        help="Session JSONL path (default: last file under <repo>/.code-harness/sessions)",
    )
    mem_extract.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates without writing OKF files",
    )
    mem_extract.add_argument(
        "--llm",
        dest="use_llm",
        action="store_true",
        help="Optional LLM refine; no-op when client+key are missing",
    )
    mem_extract.add_argument(
        "--dir",
        default=None,
        help="Memory directory (default: <repo>/knowledge/memory)",
    )
    mem_extract.set_defaults(func=cmd_memory)

    audit = subparsers.add_parser(
        "audit",
        help="Show append-only redaction/LLM audit JSONL (no raw secrets)",
    )
    audit.set_defaults(func=cmd_audit, audit_parser=audit, audit_cmd="show")
    audit_sub = audit.add_subparsers(dest="audit_cmd")

    audit_show = audit_sub.add_parser("show", help="Print the last N audit events")
    audit_show.add_argument("repo", nargs="?", default=".", help="Repository path")
    audit_show.add_argument(
        "--last",
        type=int,
        default=20,
        help="How many trailing events to print (default: 20)",
    )
    audit_show.add_argument(
        "--path",
        default=None,
        help="Override audit JSONL path (default: <repo>/.code-harness/audit/audit.jsonl)",
    )
    audit_show.set_defaults(func=cmd_audit, audit_cmd="show")

    audit_tail = audit_sub.add_parser("tail", help="Alias for audit show")
    audit_tail.add_argument("repo", nargs="?", default=".", help="Repository path")
    audit_tail.add_argument(
        "--last",
        type=int,
        default=20,
        help="How many trailing events to print (default: 20)",
    )
    audit_tail.add_argument(
        "--path",
        default=None,
        help="Override audit JSONL path",
    )
    audit_tail.set_defaults(func=cmd_audit, audit_cmd="tail")

    doc = subparsers.add_parser(
        "doctor",
        help="Local health check (Python, deps, index, graph, embed, redact, audit)",
        description=(
            "Probe local health: Python/deps, index + graph on disk, embedding "
            "config, secret redaction, audit path writable, optional LLM key "
            "(no network). Prints pass/fail plus a fix hint."
        ),
    )
    doc.add_argument("repo", nargs="?", default=".", help="Repository path")
    doc.set_defaults(func=cmd_doctor)

    _SERVE_DESC = (
        "Localhost retrieve API + MCP tools. POST /v1/retrieve and GET /health "
        "bind 127.0.0.1 only. --allow-public is dangerous (no auth)."
    )
    srv = subparsers.add_parser(
        "serve",
        help="Serve POST /v1/retrieve + MCP on 127.0.0.1",
        description=_SERVE_DESC,
    )
    _add_serve_flags(srv)

    mcp = subparsers.add_parser(
        "mcp",
        help="MCP tool server (localhost retrieve on 127.0.0.1)",
        description=_SERVE_DESC,
    )
    mcp.set_defaults(func=cmd_serve, repo=".")
    _add_serve_flags(mcp, include_repo=False)
    mcp_sub = mcp.add_subparsers(dest="mcp_cmd")
    mcp_serve = mcp_sub.add_parser(
        "serve",
        help="Serve MCP + POST /v1/retrieve on 127.0.0.1",
        description=_SERVE_DESC,
    )
    _add_serve_flags(mcp_serve)

    api = subparsers.add_parser(
        "api",
        help="HTTP retrieve API (localhost POST /v1/retrieve)",
        description=_SERVE_DESC,
    )
    api.set_defaults(func=cmd_serve, repo=".")
    _add_serve_flags(api, include_repo=False)
    api_sub = api.add_subparsers(dest="api_cmd")
    api_serve = api_sub.add_parser(
        "serve",
        help="Serve POST /v1/retrieve on 127.0.0.1",
        description=_SERVE_DESC,
    )
    _add_serve_flags(api_serve)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()

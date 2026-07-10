#!/usr/bin/env python3
"""Clean all indexed data (vector store, knowledge graphs, chunks, inter-repo graph)."""

import argparse
import os
import shutil

DATA_DIRS = [
    ".code-harness/chromadb",    # ChromaDB vector store
    ".code-harness",             # graph_*.json, chunks_*.json, repo_graph.json
]

DATA_FILES = [
    ".code-harness/repo_graph.json",
]


def clean(root: str, dry_run: bool = False):
    root = os.path.abspath(root)
    removed_dirs = []
    removed_files = []

    for rel in DATA_DIRS:
        path = os.path.join(root, rel)
        if os.path.isdir(path):
            if dry_run:
                print(f"[dry-run] Would remove directory: {path}")
            else:
                shutil.rmtree(path)
                removed_dirs.append(path)

    for rel in DATA_FILES:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            if dry_run:
                print(f"[dry-run] Would remove file: {path}")
            else:
                os.remove(path)
                removed_files.append(path)

    # Also clean any loose graph_/chunks_/bm25 files in .code-harness
    code_harness_dir = os.path.join(root, ".code-harness")
    if os.path.isdir(code_harness_dir):
        for fname in os.listdir(code_harness_dir):
            if fname.startswith(("graph_", "chunks_", "bm25")) or fname in ("graph.json", "chunks.json"):
                path = os.path.join(code_harness_dir, fname)
                if dry_run:
                    print(f"[dry-run] Would remove file: {path}")
                else:
                    os.remove(path)
                    removed_files.append(path)

    if not dry_run:
        # Try to remove .code-harness itself if empty (only if chromadb was already cleaned)
        try:
            if os.path.isdir(code_harness_dir) and not os.listdir(code_harness_dir):
                os.rmdir(code_harness_dir)
                removed_dirs.append(code_harness_dir)
        except OSError:
            pass

    if dry_run:
        print(f"[dry-run] Total: {len([p for p in removed_dirs])} directories, "
              f"{len([p for p in removed_files])} files would be removed")
    else:
        print(f"[+] Cleaned {len(removed_dirs)} directories and {len(removed_files)} files")


def main():
    parser = argparse.ArgumentParser(description="Clean all indexed data")
    parser.add_argument("root", nargs="?", default=".",
                        help="Project root directory (default: current dir)")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Show what would be removed without deleting")
    args = parser.parse_args()

    clean(args.root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

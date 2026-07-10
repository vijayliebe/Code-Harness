# Knowledge Graph

## Overview

The knowledge graph models relationships between code entities. It enables context expansion during retrieval — when a function is matched, the graph finds its containing class, callers, callees, and related imports.

There are two graph layers:
- **KnowledgeGraph** (`harness/knowledge_graph.py`): intra-repo entity relationships
- **RepoGraph** (`harness/repo_graph.py`): inter-repo import/export relationships

## Intra-Repo Graph (KnowledgeGraph)

**Backend**: NetworkX `DiGraph` (directed graph)  
**Persistence**: JSON via `nx.node_link_data` / `nx.node_link_graph`  
**File**: `.code-harness/graph_{repo}.json` (per-repo), falls back to `.code-harness/graph.json`

### Schema

#### Node Properties

Every code entity becomes a graph node:

| Property | Type | Description |
|----------|------|-------------|
| `name` | string | Entity name (function name, class name, filename) |
| `type` | string | EntityType value: `file`, `class`, `function`, `method` |
| `file_path` | string | Relative path from repo root |
| `start_line` | int | Starting line number |
| `end_line` | int | Ending line number |
| `repo_name` | string | Repository name (for multi-repo scoping) |

Node ID format: `{entity_type}:{file_path}:{entity_name}`

Examples:
```
file:harness/chunker.py
class:harness/chunker.py:CodeChunker
method:harness/chunker.py:CodeChunker._chunk_file
func:main.py:cmd_index
doc:README.md:Quick Start
```

#### Relationship Types

| Relationship | Direction | Description |
|-------------|-----------|-------------|
| `contains` | FILE → entity | A file contains a class/function |
| `contains` | CLASS → METHOD | A class contains a method |
| `inherits` | CLASS → CLASS | Class inherits from another class |
| `calls` | FUNC/METHOD → entity | Function calls or imports another entity |
| `references` | FUNC/METHOD → entity | Function references a variable/function name |

#### Edge Properties

Edges carry a single property:

| Property | Type | Description |
|----------|------|-------------|
| `relationship` | string | One of: `contains`, `inherits`, `calls`, `references` |

### Construction

Built in `KnowledgeGraph.build(entities)`:

#### 1. Add all entities as nodes
Every `CodeEntity` becomes a node with its metadata including `repo_name`.

#### 2. Create `contains` edges (FILE → entities)
For each FILE entity, link to all entities sharing its `file_path`.

#### 3. Create `inherits` edges (CLASS → parent CLASS)
For each CLASS entity, extract base class names from `metadata["bases"]` and link to matching entities by name (supports partial/qualified name matching).

#### 4. Create `contains` edges (CLASS → METHOD)
For each CLASS entity, extract method names from `metadata["methods"]` and link to matching METHOD entities.

#### 5. Create `calls` edges (FUNC/METHOD → entities)
Uses `extract_imports()` from `utils.py` with regex patterns:
- `import X` / `from X import Y`
- `require('X')` / `from 'X'`
Links to entities matching the imported name.

#### 6. Create `references` edges (FUNC/METHOD → entities)
Extracts all identifier-like words from source code, filters out Python/reserved keywords, and links to matching entities by name.

### Retrieval Usage

#### Neighbor Expansion

During query, when entities are matched via dense or sparse search, the graph finds their neighbors:

```
matched entities (from dense/sparse search)
    │
    ▼
[get_related_chunks(entity_ids, max_depth=3)]
    │ Breadth-first traversal up to 3 hops
    ▼
related entity IDs (neighbors, neighbors-of-neighbors)
    │
    ▼
matched against available chunks → graph retrieval results
```

#### Scoring

Graph-retrieved chunks get score based on token overlap with query:

```
score = min(0.8, 0.3 + (overlap_tokens / query_tokens) * 0.5)
```

These scores are then weighted during RRF fusion. Default graph weight: 0.2.

### Example

For `harness/chunker.py`:

```
file:chunker.py
  │  contains
  ├── class:CodeChunker
  │   ├── contains
  │   │   ├── method:__init__
  │   │   ├── method:chunk_entity
  │   │   ├── method:chunk_entities
  │   │   ├── method:_chunk_file
  │   │   ├── method:_chunk_class
  │   │   ├── method:_chunk_function
  │   │   └── method:_chunk_documentation
  │   ├── inherits (none)
  │   └── calls
  │       ├── CodeEntity (from models.py)
  │       ├── EntityType (from models.py)
  │       ├── Chunk (from models.py)
  │       └── Config (from config.py)
  └── calls
      ├── re
      ├── uuid
      └── ...
```

A query matching `_chunk_file` expands to:
- `CodeChunker` class (contains relationship)
- `__init__`, `chunk_entity`, `_chunk_class` (siblings via class → contains)
- `Chunk`, `CodeEntity`, `EntityType` (imports via calls)

---

## Inter-Repo Graph (RepoGraph)

**Backend**: NetworkX `DiGraph` (directed graph)  
**Persistence**: `.code-harness/repo_graph.json`

### Schema

#### Node Properties

Each indexed repository becomes a node:

| Property | Type | Description |
|----------|------|-------------|
| `repo_path` | string | Absolute path to repository |
| `imports` | list | All module names imported across the repo (stdlib excluded) |
| `exports` | list | All entity names defined (classes, functions, methods) |
| `entity_count` | int | Total number of code entities |

#### Relationship Types

| Relationship | Direction | Description |
|-------------|-----------|-------------|
| `shared_import` | bidir | Both repos import the same module name |
| `shared_entity` | bidir | Both repos define an entity with the same name |
| `depends_on` | A → B | Repo A imports a module that Repo B exports |

#### Edge Properties

| Property | Type | Description |
|----------|------|-------------|
| `relationships` | list | Array of `{type, module/entity}` objects |

### Construction

Built in `RepoGraph.build_cross_repo_edges()`:

```
1. Build reverse index: module_name → set of repos that import it
2. Build reverse index: entity_name → set of repos that export it

3. For each module imported by ≥2 repos:
   → Add shared_import edge between every pair

4. For each entity exported by ≥2 repos:
   → Add shared_entity edge between every pair

5. For each repo's imports:
   → If imported module is exported by another repo
   → Add depends_on edge (repo → exporter)
```

### Skip Modules

Standard library modules are filtered out to avoid noise:

```
os, sys, re, json, math, typing, collections,
dataclasses, enum, pathlib, abc, io, functools,
itertools, datetime, copy, hashlib, uuid
```

Configurable via `config.repo_graph.skip_modules`.

### Example

```
repo-a --[depends_on: pkg_x]--> repo-b
repo-a --[shared_entity: Config]--> repo-c
repo-b --[shared_import: requests]--> repo-c
```

---

## Graph Statistics (code-harness self-index)

| Metric | KnowledgeGraph | RepoGraph |
|--------|---------------|-----------|
| Nodes | ~120 | Depends on repos indexed |
| Edges | ~511 | Depends on repos indexed |
| Edge types | contains, calls, references, inherits | shared_import, shared_entity, depends_on |
| Storage | ~40 KB (JSON) | ~10 KB (JSON) |

## Limitations

- **Imports on functions only**: Import detection runs on function/method source code, not at file level. Cross-file imports are only captured when referenced within a function body.
- **Name-based linking**: Entity matching is by name string only. If two entities share a name (e.g., `Config` in different modules), the first match wins. No namespace resolution.
- **No transitive closure**: Graph expansion uses `max_depth=3` (default). Deeply indirect relationships (5+ hops) are not captured.
- **Static analysis only**: The graph is built from source text, not execution traces. Dynamic calls, monkey-patching, and runtime dispatch are invisible.
- **Repo-level dedup**: The inter-repo graph is built from aggregate imports/exports per repo, not per file. Within-repo namespace collisions are not resolved.

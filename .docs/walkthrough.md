# End-to-End Walkthrough

This document traces a single piece of code through every stage of the pipeline, from raw file to query response, incorporating tree-sitter parsing, HyDE query expansion, cross-encoder reranking, and MMR diversity.

## Sample Code

Consider this fictional `auth.py`:

```python
import hashlib
import os

def hash_password(password: str, salt: str = None) -> str:
    """Hash a password with a salt for secure storage."""
    if salt is None:
        salt = os.urandom(16).hex()
    hashed = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${hashed}"

class User:
    """Represents a user in the system."""

    def __init__(self, username: str, password_hash: str):
        self.username = username
        self.password_hash = password_hash

    def verify_password(self, password: str) -> bool:
        salt = self.password_hash.split("$")[0]
        expected = hash_password(password, salt)
        return self.password_hash == expected


class AuthService:
    """Handles user authentication."""

    def __init__(self):
        self.users: dict[str, User] = {}

    def register(self, username: str, password: str) -> User:
        if username in self.users:
            raise ValueError("User already exists")
        pw_hash = hash_password(password)
        user = User(username, pw_hash)
        self.users[username] = user
        return user

    def login(self, username: str, password: str) -> bool:
        user = self.users.get(username)
        if not user:
            return False
        return user.verify_password(password)
```

---

## Stage 1: Parsing — Code → Entities

The parser reads `auth.py` via tree-sitter (Python) and creates one entity per meaningful unit.

### Entity Output

```
FILE:  auth.py                        (lines 1-49)
CLASS: User                           (lines 16-27)
METHOD: User.__init__                 (lines 18-20)
METHOD: User.verify_password          (lines 22-26)
CLASS: AuthService                    (lines 29-49)
METHOD: AuthService.__init__          (lines 31-32)
METHOD: AuthService.register          (lines 34-40)
METHOD: AuthService.login             (lines 42-48)
FUNCTION: hash_password               (lines 4-11)
```

Each entity stores:
- Its **source code** (the actual text)
- **File path**, **start/end line**
- **Docstring** (extracted)
- **Metadata**: params, return type, bases, methods

For example, the `hash_password` function entity looks like:

```
Entity {
  id:       "func:auth.py:hash_password"
  name:     "hash_password"
  type:     FUNCTION
  file:     "auth.py"
  lines:    4-11
  docstring:"Hash a password with a salt for secure storage."
  source:   "def hash_password(password: str, salt: str = None) -> str:\n
              \"\"\"Hash ...\"\"\"\n
              if salt is None:\n
                  salt = os.urandom(16).hex()\n
              hashed = hashlib.sha256((password + salt).encode()).hexdigest()\n
              return f\"{salt}${hashed}\""
  params:   ["password: str", "salt: str = None"]
  returns:  "str"
}
```

---

## Stage 2: Chunking — Entities → Chunks

Each entity is split into chunks suitable for embedding. The strategy depends on entity type:

### Chunking Decisions

| Entity | Size | Strategy | Result |
|--------|------|----------|--------|
| `auth.py` (file) | 1321 chars | `max_chunk_size=1500` → small enough | **1 chunk** (entire file) |
| `hash_password` | 356 chars | Function → single chunk | **1 chunk** |
| `User` (class) | 345 chars | ≤1500 → single chunk | **1 chunk** (class + methods) |
| `User.__init__` | 108 chars | Method → single chunk | **1 chunk** |
| `User.verify_password` | 135 chars | Method → single chunk | **1 chunk** |
| `AuthService` (class) | 532 chars | ≤1500 → single chunk | **1 chunk** |
| `AuthService.register` | 207 chars | Method → single chunk | **1 chunk** |
| `AuthService.login` | 163 chars | Method → single chunk | **1 chunk** |

### Result: 8 chunks

| Chunk ID | Type | Content Summary |
|----------|------|-----------------|
| `func:auth.py:hash_password:abc123` | function | `def hash_password(...)` full body |
| `class:auth.py:User:def456` | class | `class User:` + both methods |
| `method:auth.py:User.__init__:ghi789` | method | `def __init__(...)` body |
| `method:auth.py:User.verify_password:jkl012` | method | `def verify_password(...)` body |
| `class:auth.py:AuthService:mno345` | class | `class AuthService:` + all methods |
| `method:auth.py:AuthService.__init__:pqr678` | method | `def __init__(...)` body |
| `method:auth.py:AuthService.register:stu901` | method | `def register(...)` body |
| `method:auth.py:AuthService.login:vwx234` | method | `def login(...)` body |

The **content** field of each chunk is the actual source code text — this is what gets embedded.

---

## Stage 3: Embedding — Chunks → Vectors

Each chunk's source code text is fed to the embedding model.

### Input to embedder

```python
chunks = [
    Chunk(content="def hash_password(password: str, salt: str = None) -> str:\n    ..."),
    Chunk(content="class User:\n    def __init__(self, ...)\n    def verify_password(...)"),
    # ... 6 more chunks
]

embedder = Embedder(config)
embeddings = embedder.embed([c.content for c in chunks])
# Returns: List of 8 vectors, each 384-dimensional
```

### Vector Representation (simplified)

```
hash_password chunk  →  [0.23, -0.45, 0.12, ..., 0.67]  (384 floats)
User class chunk     →  [0.56, 0.33, -0.21, ..., -0.08]
AuthService class    →  [0.41, -0.12, 0.55, ..., 0.19]
...
```

The vector captures **semantic meaning**: `hash_password` is close to concepts like "password hashing", "cryptography", "security". The `User` class is close to "user model", "entity", "data class".

---

## Stage 4: Storage — Vectors → Queryable Index

### Vector Store (ChromaDB)

Each chunk is stored with its embedding vector and metadata:

```
Collection "code_chunks":

ID: "uuid-1"
├─ embedding: [0.23, -0.45, ...]  (384-dim float array, HNSW indexed)
├─ document: "def hash_password(password: str, salt: str = None) -> str:\n    ..."
└─ metadata:
    ├─ chunk_id: "func:auth.py:hash_password:abc123"
    ├─ entity_id: "func:auth.py:hash_password"
    ├─ entity_name: "hash_password"
    ├─ entity_type: "function"
    ├─ file_path: "auth.py"
    ├─ start_line: "4"
    ├─ end_line: "11"
    └─ repo_name: "my-project"
```

HNSW index (`ef_search=256`, `ef_construction=200`, `M=32`) enables fast approximate nearest-neighbor search in O(log n).

### Knowledge Graph (NetworkX)

Nodes: 8 entities with bidirectional edges:

```
file:auth.py ───contains──→ func:hash_password
file:auth.py ───contains──→ class:User
file:auth.py ───contains──→ class:AuthService
class:User ───contains──→ method:User.__init__
class:User ───contains──→ method:User.verify_password
class:AuthService ───contains──→ method:AuthService.__init__
class:AuthService ───contains──→ method:AuthService.register
class:AuthService ───contains──→ method:AuthService.login
func:hash_password ───calls──→ hashlib (std, filtered)
func:hash_password ───calls──→ os (std, filtered)
method:User.verify_password ───calls──→ hash_password
method:AuthService.register ───calls──→ hash_password
method:AuthService.login ───calls──→ User.verify_password
```

### BM25 Index (serialized to disk)

All 8 chunk texts are tokenized and indexed, then persisted to `bm25_my-project.pkl`:

```
Token          → Postings
"hash"         → [chunk_0, chunk_1, ...]
"password"     → [chunk_0, chunk_4, chunk_5, ...]
"salt"         → [chunk_0]
"user"         → [chunk_1, chunk_3, ...]
"authenticate" → [chunk_5, chunk_6, chunk_7]
...
```

On subsequent queries, BM25 loads from disk directly — no ChromaDB scan needed.

---

## Stage 5: Retrieval — User Query → Context

### User Query

```python
query = "How do I verify a user's password?"
```

### Step 5a: Embed the query (with expansion)

```python
# Without HyDE:
query_vec = embedder.embed_query("How do I verify a user's password?")
# Internally expands to: "code that How do I verify a user's password?"

# With HyDE enabled:
# Generates hypothetical document first:
# "The following code implements How do I verify a user's password?..."
# Then embeds: query + hyde_text concatenated
```

The query vector is compared against all 8 chunk vectors in ChromaDB via cosine similarity.

### Step 5b: Dense Search (semantic)

ChromaDB finds the 40 nearest neighbors:

| Chunk | Cosine Similarity |
|-------|------------------|
| `User.verify_password` | **0.89** |
| `User` (class) | 0.76 |
| `AuthService.login` | 0.71 |
| `hash_password` | 0.52 |
| `AuthService.register` | 0.45 |

**Why**: "verify a user's password" is semantically close to `verify_password` method body. The `User` class is close because it contains the method. `AuthService.login` is relevant because it calls `verify_password`.

### Step 5c: BM25 Search (keyword exact match)

Tokenizing the query: `["how", "do", "i", "verify", "a", "user", "s", "password"]`

| Chunk | Score | Why |
|-------|-------|-----|
| `User.verify_password` | **2.31** | contains "password" (3x), "verify" (1x) |
| `User` (class) | 1.12 | contains "password" (2x) |
| `AuthService.login` | 0.89 | contains "password" (2x), "user" (1x) |
| `hash_password` | 0.67 | contains "password" (3x), no "verify" or "user" |

**Why**: BM25 catches exact term presence. `verify_password` has "verify" in the name and "password" appears 3x.

### Step 5d: Graph Expansion

Entity IDs from dense + sparse results:
- `method:auth.py:User.verify_password`
- `class:auth.py:User`
- `method:auth.py:AuthService.login`

Graph traversal (max_depth=3):

| Seed Entity | Neighbors Found |
|------------|-----------------|
| `User.verify_password` | `User` (contains), `hash_password` (calls) |
| `User` | `auth.py` (contains), `User.__init__` (contains), `verify_password` (contains) |
| `AuthService.login` | `AuthService` (contains), `User.verify_password` (calls), `AuthService.__init__` (contains) |

New entities discovered: `hash_password`, `User.__init__`, `auth.py`

### Step 5e: RRF Fusion

Three ranked lists combined via Reciprocal Rank Fusion:

```
RRF score = dense_weight * Σ 1/(60 + rank_dense)
          + sparse_weight * Σ 1/(60 + rank_sparse)
          + graph_boost (if graph-matched)
```

| Chunk | Dense Rank | Sparse Rank | Graph? | RRF Score |
|-------|-----------|-------------|--------|-----------|
| `User.verify_password` | 1 | 1 | yes | **0.337** |
| `User` (class) | 2 | 2 | yes | 0.282 |
| `AuthService.login` | 3 | 3 | yes | 0.243 |
| `hash_password` | 4 | 5 | yes (via graph) | 0.230 |
| `AuthService` (class) | 6 | 7 | yes | 0.170 |

### Step 5f: Cross-Encoder Rerank

Top 15 candidates are re-scored by the cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`):

```
pairs = [(query, chunk.content[:2048]) for chunk in top_candidates]
scores = cross_encoder.predict(pairs)
```

The cross-encoder evaluates query-chunk relevance jointly (not just cosine distance), producing more accurate relevance scores. Results are re-sorted by cross-encoder score and merged back with the tail.

### Step 5g: MMR Diversity Rerank

Context builder applies Maximum Marginal Relevance to prevent the same file from dominating context:

```
MMR score = relevance - lambda * max(similarity_to_selected)
```

With `lambda=0.3`, similar chunks from `auth.py` are downweighted, allowing other files (if they existed) to surface.

### Step 5h: Context Assembly (with ARCHITECTURE.md injection)

If an `ARCHITECTURE.md` exists in the repo root, it's prepended:

```markdown
# Query: How do I verify a user's password?

## Relevant Code Context

### Project-level Context Files

**ARCHITECTURE.md** (project root)
```
# Auth System Architecture
...
```

### File: auth.py

**Method: `verify_password`** [Lines 22-26] (relevance: 0.42)
```
def verify_password(self, password: str) -> bool:
    salt = self.password_hash.split("$")[0]
    expected = hash_password(password, salt)
    return self.password_hash == expected
```

**Class: `User`** [Lines 16-27] (relevance: 0.35)
```
class User:
    """Represents a user in the system."""
    def __init__(self, username: str, password_hash: str):
        self.username = username
        self.password_hash = password_hash
    def verify_password(self, password: str) -> bool:
        ...
```

**Method: `login`** [Lines 42-48] (relevance: 0.30)
```
def login(self, username: str, password: str) -> bool:
    user = self.users.get(username)
    if not user:
        return False
    return user.verify_password(password)
```

**Function: `hash_password`** [Lines 4-11] (relevance: 0.29)
```
def hash_password(password: str, salt: str = None) -> str:
    """Hash a password with a salt for secure storage."""
    ...
```

(truncated at context budget)
```

### Step 5i: LLM Response

```python
system_prompt = "You are an expert code analyst..."
context = (the assembled context above)
user_query = "How do I verify a user's password?"

llm.query(system_prompt, context, user_query)
# All non-streaming calls retry on failure (3 retries, exponential backoff)
```

The LLM receives the question + relevant code context and produces a response like:

> To verify a user's password, call the `verify_password` method on a `User` instance:
>
> ```python
> user = User("alice", stored_hash)
> is_valid = user.verify_password("input_password")
> ```
>
> This method (line 22 in `auth.py`) splits the stored hash to extract the salt, re-hashes the input password with that salt via `hash_password()`, and compares the result to the stored hash. The `AuthService.login()` method (line 42) wraps this with user lookup.

---

## Summary: What Gets Embedded & Searched

| What | Content | Purpose |
|------|---------|---------|
| **Embedded** | Full source code of each chunk | Semantic similarity search |
| **BM25-indexed** | Same source code, tokenized (persisted to pkl) | Exact keyword matching |
| **Graph nodes** | Entity metadata (name, file, type) | Relationship traversal |
| **Cross-encoder** | Query + chunk text pairs | Joint relevance scoring |
| **Context files** | ARCHITECTURE.md / AGENTS.md / CLAUDE.md | High-level project context |
| **Returned** | Source code + metadata + context files | Context for the LLM |

The user's query is **never compared to names only** — it's compared semantically to the full implementation code. The entity name, type, and file path are metadata for organization and graph relationships, not the primary search signal.

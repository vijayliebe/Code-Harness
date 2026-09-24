from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum


class EntityType(str, Enum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    DOCUMENTATION = "documentation"
    ENDPOINT = "endpoint"


class RelationshipType(str, Enum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    REFERENCES = "references"
    EXPOSES = "exposes"
    TESTED_BY = "tested_by"
    GLOSS = "gloss"
    # Cross-repo relationships
    SHARED_IMPORT = "shared_import"
    SHARED_ENTITY = "shared_entity"
    DEPENDS_ON = "depends_on"


@dataclass
class CodeEntity:
    id: str
    name: str
    type: EntityType
    file_path: str
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    source_code: str = ""
    metadata: Dict = field(default_factory=dict)

    @property
    def signature(self) -> str:
        if self.type in (EntityType.FUNCTION, EntityType.METHOD):
            params = self.metadata.get("params", [])
            return f"{self.name}({', '.join(params)})"
        if self.type == EntityType.CLASS:
            bases = self.metadata.get("bases", [])
            if bases:
                return f"{self.name}({', '.join(bases)})"
            return self.name
        return self.name


@dataclass
class Relationship:
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    metadata: Dict = field(default_factory=dict)


@dataclass
class Chunk:
    id: str
    content: str
    entity_id: str
    entity_name: str
    entity_type: EntityType
    file_path: str
    start_line: int
    end_line: int
    embedding: Optional[List[float]] = None
    docstring: str = ""
    repo_name: str = ""
    metadata: Dict = field(default_factory=dict)


@dataclass
class RetrievalResult:
    chunk: Chunk
    score: float
    source: str


@dataclass
class PackedChunk:
    id: str
    preview: str
    omitted: bool
    omitted_line_count: int = 0
    original: str = ""

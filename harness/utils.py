import re
import time
from typing import Any, Callable, Iterable, List, Optional, Set, TypeVar

from .models import CodeEntity, EntityType

IMPORT_PATTERNS = [
    r'import\s+(\w+)',
    r'from\s+(\w+)',
    r'require\([\'"](\w+)',
    r'from\s+[\'"](\w+)',
]

T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    retryable_exceptions: tuple = (Exception,),
) -> T:
    last_exc = None
    delay = base_delay
    for attempt in range(max_retries + 1):
        try:
            return func()
        except retryable_exceptions as e:
            last_exc = e
            if attempt < max_retries:
                # Use Retry-After header if available (429 responses)
                import httpx
                if isinstance(e, (httpx.HTTPStatusError, IOError)):
                    resp = getattr(e, "response", None)
                    if resp and resp.headers.get("Retry-After"):
                        try:
                            delay = float(resp.headers["Retry-After"])
                        except ValueError:
                            pass
                time.sleep(delay)
                delay *= backoff
    raise last_exc


def extract_imports(source_code: str) -> Set[str]:
    imports = set()
    for pattern in IMPORT_PATTERNS:
        imports.update(re.findall(pattern, source_code))
    return imports


def extract_imports_from_entities(entities: Iterable[CodeEntity],
                                  skip_builtins: Optional[Set[str]] = None) -> Set[str]:
    imports = set()
    for entity in entities:
        source = entity.source_code
        if not source:
            continue
        imports |= extract_imports(source)
        if entity.type == EntityType.IMPORT:
            name = entity.name.strip()
            if name:
                imports.add(name.split(".")[0])
    if skip_builtins:
        imports -= skip_builtins
    return imports


def extract_exports(entities: Iterable[CodeEntity]) -> Set[str]:
    exports = set()
    for entity in entities:
        if entity.type in (
            EntityType.CLASS, EntityType.FUNCTION,
            EntityType.MODULE, EntityType.METHOD,
        ):
            exports.add(entity.name)
    return exports

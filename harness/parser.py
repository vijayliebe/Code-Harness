import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from fnmatch import fnmatch

from rich.progress import track

from .models import CodeEntity, EntityType
from .config import Config


LANGUAGE_PATTERNS: Dict[str, Dict] = {
    ".py": {
        "name": "python",
        "comment": "#",
        "multiline_comment": (r'"""[\s\S]*?"""', r"'''[\s\S]*?'''"),
    },
    ".js": {"name": "javascript", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".jsx": {"name": "javascript", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".ts": {"name": "typescript", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".tsx": {"name": "typescript", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".go": {"name": "go", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".rs": {"name": "rust", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".java": {"name": "java", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".c": {"name": "c", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".cpp": {"name": "cpp", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".h": {"name": "c", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".hpp": {"name": "cpp", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".rb": {"name": "ruby", "comment": "#", "multiline_comment": (r"=begin[\s\S]*?=end",)},
    ".php": {"name": "php", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".swift": {"name": "swift", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".kt": {"name": "kotlin", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".scala": {"name": "scala", "comment": "//", "multiline_comment": (r"/\*[\s\S]*?\*/",)},
    ".ex": {"name": "elixir", "comment": "#"},
    ".exs": {"name": "elixir", "comment": "#"},
    ".md": {"name": "markdown", "comment": "", "is_doc": True},
    ".rst": {"name": "restructuredtext", "comment": "", "is_doc": True},
    ".yaml": {"name": "yaml", "comment": "#"},
    ".yml": {"name": "yaml", "comment": "#"},
    ".json": {"name": "json", "comment": "", "is_data": True},
    ".toml": {"name": "toml", "comment": "#"},
}


class CodeParser:
    def __init__(self, config: Config):
        self.config = config
        self.exclude_patterns = config.indexing.get("exclude_patterns", [])
        self.include_extensions = set(config.indexing.get("include_extensions", []))
        self.max_file_size = config.indexing.get("max_file_size_kb", 512) * 1024
        self.ts_parser = None
        use_ts = config.indexing.get("use_treesitter", True)
        if use_ts:
            try:
                from .parser_treesitter import TreeSitterParser
                self.ts_parser = TreeSitterParser(config)
            except ImportError:
                pass

    def _should_include(self, file_path: str) -> bool:
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext not in self.include_extensions:
            return False

        for part in path.parts:
            if any(fnmatch(part, p) for p in self.exclude_patterns if "*" not in p):
                return False

        for pattern in self.exclude_patterns:
            if "*" in pattern and fnmatch(str(path), pattern):
                return False

        try:
            if path.stat().st_size > self.max_file_size:
                return False
        except OSError:
            return False

        return True

    def discover_files(self, repo_path: str) -> List[Path]:
        files = []
        for root, dirs, _ in os.walk(repo_path):
            dirs[:] = [d for d in dirs
                       if d not in self.exclude_patterns
                       and not d.startswith(".")]
            for d in list(dirs):
                if any(fnmatch(d, p) for p in self.exclude_patterns if "*" not in p):
                    dirs.remove(d)

            for f in _:
                full = Path(root) / f
                if self._should_include(str(full)):
                    files.append(full)

        return sorted(files)

    def parse_file(self, file_path: Path) -> List[CodeEntity]:
        ext = file_path.suffix.lower()
        lang = LANGUAGE_PATTERNS.get(ext, {})
        entities = []

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return entities

        rel_path = str(file_path.relative_to(self.config.repo_path)
                       if self.config.repo_path else file_path)

        file_entity = CodeEntity(
            id=f"file:{rel_path}",
            name=file_path.name,
            type=EntityType.FILE,
            file_path=rel_path,
            start_line=1,
            end_line=len(source.splitlines()),
            source_code=source,
            metadata={"language": lang.get("name", "unknown"), "size": len(source)},
        )
        entities.append(file_entity)

        if not lang:
            return entities

        if self.ts_parser and self.ts_parser.available:
            ts_entities = self.ts_parser.parse_file(source, rel_path, file_entity)
            if ts_entities:
                return [file_entity] + ts_entities

        if ext == ".py":
            entities.extend(self._parse_python(source, rel_path, file_entity))
        elif lang.get("name") in ("javascript", "typescript"):
            entities.extend(self._parse_js_like(source, rel_path, file_entity))
        elif lang.get("name") == "go":
            entities.extend(self._parse_go_like(source, rel_path, file_entity, "go"))
        elif lang.get("name") == "rust":
            entities.extend(self._parse_go_like(source, rel_path, file_entity, "rust"))
        elif lang.get("name") in ("java", "kotlin", "scala"):
            entities.extend(self._parse_java_like(source, rel_path, file_entity))
        elif lang.get("name") in ("c", "cpp"):
            entities.extend(self._parse_c_like(source, rel_path, file_entity))
        elif lang.get("name") == "ruby":
            entities.extend(self._parse_ruby_like(source, rel_path, file_entity))
        elif lang.get("name") == "php":
            entities.extend(self._parse_php_like(source, rel_path, file_entity))
        elif lang.get("is_doc"):
            entities.extend(self._parse_documentation(source, rel_path, file_entity))
        elif lang.get("is_data"):
            entities.extend(self._parse_data_file(source, rel_path, file_entity))

        return entities

    def _parse_python(self, source: str, rel_path: str,
                      file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return self._parse_any_language(source, rel_path, file_entity)

        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child.parent = node

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                docstring = ast.get_docstring(node) or ""
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                method_bodies = []
                for m in methods:
                    m_doc = ast.get_docstring(m) or ""
                    m_source = ast.unparse(m) if hasattr(ast, 'unparse') else ""
                    method_bodies.append(f"    {m_source}" if m_source else f"    def {m.name}(...): ...")
                    method_entity = CodeEntity(
                        id=f"method:{rel_path}:{node.name}.{m.name}",
                        name=m.name,
                        type=EntityType.METHOD,
                        file_path=rel_path,
                        start_line=getattr(m, 'lineno', 0),
                        end_line=getattr(m, 'end_lineno', 0),
                        docstring=m_doc,
                        source_code=ast.unparse(m) if hasattr(ast, 'unparse') else "",
                        metadata={"class": node.name, "params": [arg.arg for arg in m.args.args]},
                    )
                    entities.append(method_entity)

                class_source = ast.unparse(node) if hasattr(ast, 'unparse') else "\n".join(method_bodies)
                bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, 'unparse') else []

                class_entity = CodeEntity(
                    id=f"class:{rel_path}:{node.name}",
                    name=node.name,
                    type=EntityType.CLASS,
                    file_path=rel_path,
                    start_line=node.lineno,
                    end_line=getattr(node, 'end_lineno', 0),
                    docstring=docstring,
                    source_code=class_source,
                    metadata={"bases": bases, "methods": [m.name for m in methods]},
                )
                entities.append(class_entity)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not isinstance(getattr(node, 'parent', None), ast.ClassDef):
                    docstring = ast.get_docstring(node) or ""
                    func_source = ast.unparse(node) if hasattr(ast, 'unparse') else ""
                    func_entity = CodeEntity(
                        id=f"func:{rel_path}:{node.name}",
                        name=node.name,
                        type=EntityType.FUNCTION,
                        file_path=rel_path,
                        start_line=node.lineno,
                        end_line=getattr(node, 'end_lineno', 0),
                        docstring=docstring,
                        source_code=func_source,
                        metadata={
                            "params": [arg.arg for arg in node.args.args],
                            "returns": ast.unparse(node.returns) if node.returns and hasattr(ast, 'unparse') else "",
                        },
                    )
                    entities.append(func_entity)

        return entities

    def _parse_js_like(self, source: str, rel_path: str,
                       file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []

        class_pattern = re.compile(
            r'(?:export\s+)?(?:default\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?\s*\{',
            re.MULTILINE
        )

        func_pattern = re.compile(
            r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\('
            r'|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)'
            r'|(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s+)?(\w+)\s*\([^)]*\)\s*\{',
            re.MULTILINE
        )

        for match in class_pattern.finditer(source):
            name = match.group(1)
            bases = [match.group(2)] if match.group(2) else []
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"bases": bases},
            ))

        for match in func_pattern.finditer(source):
            name = match.group(1) or match.group(2) or match.group(3) or ""
            if not name:
                continue
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"func:{rel_path}:{name}",
                name=name, type=EntityType.FUNCTION, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
            ))

        return entities

    def _parse_go_like(self, source: str, rel_path: str,
                       file_entity: CodeEntity, lang: str) -> List[CodeEntity]:
        entities = []

        func_pattern = re.compile(
            r'(?:func\s+)(?:\([^)]*\)\s+)?(\w+)\s*\(([^)]*)\)(?:\s*\(?[^)]*\)?)?(?:\s*\{)',
            re.MULTILINE
        )

        struct_pattern = re.compile(
            r'(?:type\s+)(\w+)\s+struct\s*\{',
            re.MULTILINE
        )

        for match in func_pattern.finditer(source):
            name = match.group(1)
            params = match.group(2)
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"func:{rel_path}:{name}",
                name=name, type=EntityType.FUNCTION, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"params": [p.strip() for p in params.split(",") if p.strip()]},
            ))

        for match in struct_pattern.finditer(source):
            name = match.group(1)
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"struct:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
            ))

        return entities

    def _parse_java_like(self, source: str, rel_path: str,
                         file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []

        class_pattern = re.compile(
            r'(?:public|private|protected)?\s*(?:abstract|final)?\s*class\s+(\w+)'
            r'(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?\s*\{',
            re.MULTILINE
        )

        method_pattern = re.compile(
            r'(?:public|private|protected|static|final|abstract|synchronized|native|\s)*\s+'
            r'(\w+(?:\[\])*(?:<[^>]*>)?)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+\w+(?:,\s*\w+)*)?\s*\{',
            re.MULTILINE
        )

        for match in class_pattern.finditer(source):
            name = match.group(1)
            bases = [match.group(2)] if match.group(2) else []
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"bases": bases},
            ))

        for match in method_pattern.finditer(source):
            return_type = match.group(1)
            name = match.group(2)
            params = match.group(3)
            if name in ("if", "for", "while", "switch", "catch"):
                continue
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"method:{rel_path}:{name}",
                name=name, type=EntityType.METHOD, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={
                    "return_type": return_type,
                    "params": [p.strip() for p in params.split(",") if p.strip()],
                },
            ))

        return entities

    def _parse_c_like(self, source: str, rel_path: str,
                      file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []

        func_pattern = re.compile(
            r'(?:static\s+)?(?:inline\s+)?(?:\w+(?:\s*\*)?\s+)+'
            r'(\w+)\s*\(([^)]*)\)\s*\{',
            re.MULTILINE
        )

        class_pattern = re.compile(
            r'(?:class|struct)\s+(\w+)(?:\s*:\s*(?:public|private|protected)?\s*(\w+))?\s*\{',
            re.MULTILINE
        )

        for match in class_pattern.finditer(source):
            name = match.group(1)
            bases = [match.group(2)] if match.group(2) else []
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"bases": bases},
            ))

        for match in func_pattern.finditer(source):
            name = match.group(1)
            params = match.group(2)
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"func:{rel_path}:{name}",
                name=name, type=EntityType.FUNCTION, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"params": [p.strip() for p in params.split(",") if p.strip()]},
            ))

        return entities

    def _parse_ruby_like(self, source: str, rel_path: str,
                         file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []

        class_pattern = re.compile(
            r'(?:class|module)\s+(\w+(?:::\w+)*)(?:\s*<\s*(\w+(?:::\w+)*))?\s*',
            re.MULTILINE
        )

        def_pattern = re.compile(
            r'(?:def\s+)(?:self\.)?(\w+(?:[?!]))?\s*(?:\(([^)]*)\))?',
            re.MULTILINE
        )

        for match in class_pattern.finditer(source):
            name = match.group(1).split("::")[-1]
            bases = [match.group(2)] if match.group(2) else []
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"bases": bases},
            ))

        for match in def_pattern.finditer(source):
            name = match.group(1)
            params = match.group(2) or ""
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"func:{rel_path}:{name}",
                name=name, type=EntityType.FUNCTION, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"params": [p.strip() for p in params.split(",") if p.strip()]},
            ))

        return entities

    def _parse_php_like(self, source: str, rel_path: str,
                        file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []

        class_pattern = re.compile(
            r'(?:abstract\s+|final\s+)?(?:class\s+)(\w+)'
            r'(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?\s*\{',
            re.MULTILINE
        )

        func_pattern = re.compile(
            r'(?:public|private|protected|static|final|abstract|\s)*\s*function\s+(\w+)\s*\(([^)]*)\)',
            re.MULTILINE
        )

        for match in class_pattern.finditer(source):
            name = match.group(1)
            bases = [match.group(2)] if match.group(2) else []
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
                metadata={"bases": bases},
            ))

        return entities

    def _parse_documentation(self, source: str, rel_path: str,
                             file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []
        sections = re.split(r'\n(?=#{1,6}\s|\S[^\n]*\n[=-]+\n)', source)
        offset = 0

        for section in sections:
            if not section.strip():
                continue
            lines = section.split('\n')
            title_line = next((l for l in lines if l.strip()), "")
            title = re.sub(r'^#+\s*', '', title_line).strip()
            if not title:
                title = file_entity.name
            start = offset + 1
            end = offset + len(lines)
            entities.append(CodeEntity(
                id=f"doc:{rel_path}:{title}",
                name=title, type=EntityType.DOCUMENTATION, file_path=rel_path,
                start_line=start, end_line=end, source_code=section,
            ))
            offset += len(lines)

        if not entities:
            entities.append(file_entity)

        return entities

    def _parse_data_file(self, source: str, rel_path: str,
                         file_entity: CodeEntity) -> List[CodeEntity]:
        return [file_entity]

    def _parse_any_language(self, source: str, rel_path: str,
                            file_entity: CodeEntity) -> List[CodeEntity]:
        entities = []
        lines = source.split('\n')
        i = 0

        generic_func = re.compile(
            r'^\s*(?:def\s+|function\s+|func\s+|fn\s+)?(\w+)\s*\([^)]*\)\s*(?::|->|\{|=)',
            re.MULTILINE
        )
        generic_class = re.compile(
            r'^\s*(?:class|struct|trait|interface|type)\s+(\w+)',
            re.MULTILINE
        )

        for match in generic_class.finditer(source):
            name = match.group(1)
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
            ))

        for match in generic_func.finditer(source):
            name = match.group(1)
            if name in ("if", "for", "while", "switch", "catch", "else", "elif"):
                continue
            start = source[:match.start()].count('\n') + 1
            end = self._find_block_end(source, match.start())
            body = source[match.start():end]
            entities.append(CodeEntity(
                id=f"func:{rel_path}:{name}",
                name=name, type=EntityType.FUNCTION, file_path=rel_path,
                start_line=start, end_line=end, source_code=body,
            ))

        return entities

    @staticmethod
    def _find_block_end(source: str, start_pos: int) -> int:
        depth = 0
        in_string = False
        string_char = None
        escaped = False
        i = start_pos

        while i < len(source):
            ch = source[i]

            if escaped:
                escaped = False
                i += 1
                continue

            if ch == '\\' and in_string:
                escaped = True
                i += 1
                continue

            if not in_string:
                if ch in ('"', "'", '`'):
                    in_string = True
                    string_char = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth <= 0:
                        return i + 1
            else:
                if ch == string_char:
                    in_string = False
                    string_char = None

            i += 1

        return len(source)

    def parse_repository(self, repo_path: str,
                         workers: Optional[int] = None) -> Tuple[List[CodeEntity], str]:
        self.config.repo_path = repo_path
        files = self.discover_files(repo_path)

        if workers is None:
            workers = min(32, (os.cpu_count() or 1) * 2)

        all_entities: List[CodeEntity] = []

        if workers <= 1 or len(files) < 4:
            for file_path in track(files, description="  Parsing files"):
                try:
                    entities = self.parse_file(file_path)
                    all_entities.extend(entities)
                except Exception:
                    continue
            return all_entities, repo_path

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.parse_file, fp): fp for fp in files}
            for future in track(as_completed(futures), total=len(files),
                                description="  Parsing files"):
                try:
                    entities = future.result()
                    all_entities.extend(entities)
                except Exception:
                    continue

        return all_entities, repo_path

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import CodeEntity, EntityType
from .config import Config

_TREE_SITTER_AVAILABLE = False
try:
    import tree_sitter
    import tree_sitter_languages
    _TREE_SITTER_AVAILABLE = True
except ImportError:
    pass

_TS_LANGUAGE_CACHE: Dict[str, object] = {}


def _get_ts_language(lang_name: str):
    if lang_name in _TS_LANGUAGE_CACHE:
        return _TS_LANGUAGE_CACHE[lang_name]
    try:
        lang = tree_sitter_languages.get_language(lang_name)
        _TS_LANGUAGE_CACHE[lang_name] = lang
        return lang
    except Exception:
        return None


TS_EXT_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
}


class TreeSitterParser:
    def __init__(self, config: Config):
        self.config = config
        self.available = _TREE_SITTER_AVAILABLE

    def parse_file(self, source: str, rel_path: str,
                   file_entity: CodeEntity) -> List[CodeEntity]:
        if not self.available:
            return []
        ext = Path(rel_path).suffix.lower()
        ts_lang = TS_EXT_MAP.get(ext)
        if not ts_lang:
            return []
        lang_obj = _get_ts_language(ts_lang)
        if not lang_obj:
            return []

        parser = tree_sitter.Parser(lang_obj) if hasattr(tree_sitter, 'Parser') else (
            tree_sitter_languages.Parser(lang_obj)
        )
        try:
            tree = parser.parse(bytes(source, "utf-8"))
        except Exception:
            return []

        entities = []
        root_node = tree.root_node
        self._walk_node(root_node, source, rel_path, entities, depth=0)
        return entities

    def _walk_node(self, node, source: str, rel_path: str,
                   entities: List[CodeEntity], depth: int = 0):
        if depth > 10:
            return
        node_type = node.type
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        start_byte = node.start_byte
        end_byte = node.end_byte
        source_code = source[start_byte:end_byte]

        if node_type in ("class_definition", "class_declaration"):
            name = self._node_text(node.child_by_field_name("name"), source) or ""
            body = node.child_by_field_name("body")
            bases = []
            superclass = node.child_by_field_name("superclass")
            if superclass:
                bases.append(self._node_text(superclass, source) or "")
            interfaces = node.child_by_field_name("interfaces")
            if interfaces:
                for child in interfaces.children:
                    txt = self._node_text(child, source) or ""
                    if txt and txt != ",":
                        bases.append(txt)

            class_entity = CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start_line, end_line=end_line,
                source_code=source_code,
                metadata={"bases": bases},
            )
            entities.append(class_entity)

            if body:
                self._walk_children(body, source, rel_path, entities, depth + 1, class_name=name)

        elif node_type in ("method_definition", "function_definition",
                           "function_declaration", "method_declaration"):
            name = self._node_text(node.child_by_field_name("name"), source) or ""
            params_node = node.child_by_field_name("parameters")
            params = []
            if params_node:
                for child in params_node.children:
                    child_name = self._node_text(
                        child.child_by_field_name("name") or child, source
                    ) or ""
                    if child_name and child_name not in ("(", ")", ",", ")"):
                        params.append(child_name)

            parent_class = getattr(self, "_current_class", None)
            etype = EntityType.METHOD if parent_class else EntityType.FUNCTION
            eid_prefix = "method" if parent_class else "func"

            entity = CodeEntity(
                id=f"{eid_prefix}:{rel_path}:{name}",
                name=name, type=etype, file_path=rel_path,
                start_line=start_line, end_line=end_line,
                source_code=source_code,
                metadata={
                    "params": params,
                    "class": parent_class or "",
                },
            )
            entities.append(entity)

        elif node_type in ("struct_item", "struct_declaration"):
            name = self._node_text(node.child_by_field_name("name"), source) or ""
            entity = CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start_line, end_line=end_line,
                source_code=source_code,
            )
            entities.append(entity)

        elif node_type == "impl_item":
            trait = node.child_by_field_name("trait")
            type_name = node.child_by_field_name("type")
            impl_for = self._node_text(trait or type_name, source) or ""
            body = node.child_by_field_name("body")
            if body:
                self._walk_children(body, source, rel_path, entities, depth + 1,
                                    class_name=impl_for)

        elif node_type == "trait_item":
            name = self._node_text(node.child_by_field_name("name"), source) or ""
            entity = CodeEntity(
                id=f"class:{rel_path}:{name}",
                name=name, type=EntityType.CLASS, file_path=rel_path,
                start_line=start_line, end_line=end_line,
                source_code=source_code,
            )
            entities.append(entity)

        self._walk_children(node, source, rel_path, entities, depth + 1)

    def _walk_children(self, node, source, rel_path, entities, depth,
                       class_name: Optional[str] = None):
        old_class = getattr(self, "_current_class", None)
        self._current_class = class_name or old_class
        for child in node.children:
            self._walk_node(child, source, rel_path, entities, depth)
        self._current_class = old_class

    def _node_text(self, node, source: str) -> Optional[str]:
        if node is None:
            return None
        try:
            return source[node.start_byte:node.end_byte]
        except Exception:
            return None

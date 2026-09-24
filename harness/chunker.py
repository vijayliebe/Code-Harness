import uuid
from typing import List, Optional

from rich.progress import track

from .models import Chunk, CodeEntity, EntityType
from .config import Config


class CodeChunker:
    def __init__(self, config: Config):
        self.config = config
        self.max_chunk_size = config.chunking.get("max_chunk_size", 1500)
        self.min_chunk_size = config.chunking.get("min_chunk_size", 50)
        self.overlap_lines = config.chunking.get("overlap_lines", 20)

    def chunk_entity(self, entity: CodeEntity, file_source: str = "") -> List[Chunk]:
        entity_type = entity.type
        if entity_type == EntityType.FILE:
            return self._chunk_file(entity, file_source)
        elif entity_type == EntityType.CLASS:
            return self._chunk_class(entity)
        elif entity_type in (EntityType.FUNCTION, EntityType.METHOD):
            return self._chunk_function(entity)
        elif entity_type == EntityType.DOCUMENTATION:
            return self._chunk_documentation(entity)
        else:
            return [self._make_chunk(entity, entity.source_code)]

    def chunk_entities(self, entities: List[CodeEntity]) -> List[Chunk]:
        chunks: List[Chunk] = []
        file_map: dict = {}

        for entity in entities:
            if entity.type == EntityType.FILE:
                file_map[entity.file_path] = entity.source_code

        for entity in track(entities, description="  Chunking entities"):
            source = file_map.get(entity.file_path, "")
            entity_chunks = self.chunk_entity(entity, source)
            chunks.extend(entity_chunks)

        return chunks

    def _chunk_file(self, entity: CodeEntity, file_source: str) -> List[Chunk]:
        source = file_source or entity.source_code
        lines = source.split('\n')

        if len(source) <= self.max_chunk_size:
            return [self._make_chunk(entity, source)]

        chunks = []
        start_line = 1

        prev_start = 0
        while start_line <= len(lines):
            available_lines = []
            current_size = 0

            for i in range(start_line - 1, len(lines)):
                line_len = len(lines[i]) + 1
                if current_size + line_len > self.max_chunk_size and current_size >= self.min_chunk_size:
                    break
                available_lines.append(lines[i])
                current_size += line_len

            if not available_lines:
                available_lines = [lines[start_line - 1]]
                start_line += 1
                continue

            chunk_content = '\n'.join(available_lines)
            end_line = start_line + len(available_lines) - 1
            chunk_id = f"{entity.id}:chunk:{uuid.uuid4().hex[:8]}"

            chunk = Chunk(
                id=chunk_id,
                content=chunk_content,
                entity_id=entity.id,
                entity_name=entity.name,
                entity_type=EntityType.FILE,
                file_path=entity.file_path,
                start_line=start_line,
                end_line=end_line,
                metadata={"file_size": len(source), **entity.metadata},
            )
            chunks.append(chunk)

            if end_line >= len(lines):
                break

            new_start = end_line - self.overlap_lines + 1
            if new_start <= prev_start:
                new_start = prev_start + 1
            start_line = new_start
            if start_line < 1:
                start_line = 1
            prev_start = start_line

        return chunks

    def _chunk_class(self, entity: CodeEntity) -> List[Chunk]:
        source = entity.source_code
        if len(source) <= self.max_chunk_size:
            return [self._make_chunk(entity, source)]

        chunks = []
        lines = source.split('\n')

        class_def_line = lines[0] if lines else ""
        body_start = 1

        chunk_id = f"{entity.id}:header:{uuid.uuid4().hex[:8]}"
        header_chunk = Chunk(
            id=chunk_id,
            content=class_def_line + "\n    ...",
            entity_id=entity.id,
            entity_name=entity.name,
            entity_type=EntityType.CLASS,
            file_path=entity.file_path,
            start_line=entity.start_line,
            end_line=entity.start_line,
            metadata={"section": "header", **entity.metadata},
        )
        chunks.append(header_chunk)

        current_chunk_lines = [class_def_line]
        current_size = len(class_def_line) + 1
        current_start = entity.start_line + 1

        for i, line in enumerate(lines[body_start:], start=entity.start_line + 1):
            line_len = len(line) + 1
            if current_size + line_len > self.max_chunk_size and len(current_chunk_lines) >= self.min_chunk_size:
                content = '\n'.join(current_chunk_lines)
                chunk_id = f"{entity.id}:body:{uuid.uuid4().hex[:8]}"
                chunks.append(Chunk(
                    id=chunk_id,
                    content=content,
                    entity_id=entity.id,
                    entity_name=entity.name,
                    entity_type=EntityType.CLASS,
                    file_path=entity.file_path,
                    start_line=current_start,
                    end_line=i - 1,
                    metadata={"section": "body", **entity.metadata},
                ))
                overlap = current_chunk_lines[-(self.overlap_lines):]
                current_chunk_lines = [class_def_line] + overlap
                current_size = sum(len(l) + 1 for l in current_chunk_lines)
                current_start = i - self.overlap_lines

            current_chunk_lines.append(line)
            current_size += line_len

        if len(current_chunk_lines) > 1:
            content = '\n'.join(current_chunk_lines)
            chunk_id = f"{entity.id}:body:{uuid.uuid4().hex[:8]}"
            chunks.append(Chunk(
                id=chunk_id,
                content=content,
                entity_id=entity.id,
                entity_name=entity.name,
                entity_type=EntityType.CLASS,
                file_path=entity.file_path,
                start_line=current_start,
                end_line=entity.end_line,
                metadata={"section": "body", **entity.metadata},
            ))

        return chunks

    def _chunk_function(self, entity: CodeEntity) -> List[Chunk]:
        source = entity.source_code
        return [self._make_chunk(entity, source)]

    def _chunk_documentation(self, entity: CodeEntity) -> List[Chunk]:
        source = entity.source_code
        lines = source.split('\n')

        if len(source) <= self.max_chunk_size:
            return [self._make_chunk(entity, source)]

        chunks = []
        current_section = []
        current_size = 0
        section_start = entity.start_line

        for i, line in enumerate(lines):
            is_header = line.startswith('#') and (not current_section or
                                                   current_section[-1].strip() == '')

            if is_header and current_section:
                content = '\n'.join(current_section)
                chunk_id = f"{entity.id}:section:{uuid.uuid4().hex[:8]}"
                meta = dict(entity.metadata or {})
                meta["section"] = current_section[0].strip('#').strip()
                chunks.append(Chunk(
                    id=chunk_id,
                    content=content,
                    entity_id=entity.id,
                    entity_name=entity.name,
                    entity_type=EntityType.DOCUMENTATION,
                    file_path=entity.file_path,
                    start_line=section_start,
                    end_line=section_start + len(current_section) - 1,
                    metadata=meta,
                ))
                current_section = []
                section_start = entity.start_line + i

            current_section.append(line)
            current_size += len(line) + 1

            if current_size > self.max_chunk_size:
                content = '\n'.join(current_section)
                chunk_id = f"{entity.id}:section:{uuid.uuid4().hex[:8]}"
                chunks.append(Chunk(
                    id=chunk_id,
                    content=content,
                    entity_id=entity.id,
                    entity_name=entity.name,
                    entity_type=EntityType.DOCUMENTATION,
                    file_path=entity.file_path,
                    start_line=section_start,
                    end_line=entity.start_line + i,
                    metadata=dict(entity.metadata or {}),
                ))
                overlap = current_section[-(self.overlap_lines):]
                current_section = overlap
                current_size = sum(len(l) + 1 for l in overlap)
                section_start = entity.end_line - len(overlap) + 1

        if current_section:
            content = '\n'.join(current_section)
            chunk_id = f"{entity.id}:section:{uuid.uuid4().hex[:8]}"
            chunks.append(Chunk(
                id=chunk_id,
                content=content,
                entity_id=entity.id,
                entity_name=entity.name,
                entity_type=EntityType.DOCUMENTATION,
                file_path=entity.file_path,
                start_line=section_start,
                end_line=entity.end_line,
                metadata=dict(entity.metadata or {}),
            ))

        return chunks

    def _make_chunk(self, entity: CodeEntity, content: str) -> Chunk:
        uid = uuid.uuid4().hex[:8]
        return Chunk(
            id=f"{entity.id}:{uid}",
            content=content,
            entity_id=entity.id,
            entity_name=entity.name,
            entity_type=entity.type,
            file_path=entity.file_path,
            start_line=entity.start_line,
            end_line=entity.end_line,
            metadata=dict(entity.metadata),
        )

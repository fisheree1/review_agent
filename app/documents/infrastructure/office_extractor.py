from __future__ import annotations

import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from app.documents.application.upload_validation import DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_ARCHIVE_ENTRIES = 5_000
MAX_COMPRESSION_RATIO = 200
PARSER_VERSION = "1"
MAX_LOCATOR_TEXT_LENGTH = 300


def _deny_network_access(event: str, _: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise PermissionError("Office parser subprocess cannot access the network")


def _normalize_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\x00", "").splitlines()).strip()


def _locator_text(value: str) -> str:
    return value[:MAX_LOCATOR_TEXT_LENGTH]


def _validate_archive(archive: zipfile.ZipFile, *, max_uncompressed_bytes: int) -> set[str]:
    entries = archive.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise ValueError("too many archive entries")
    total_size = 0
    names: set[str] = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if path.is_absolute() or ".." in path.parts or entry.flag_bits & 0x1:
            raise ValueError("unsafe archive member")
        total_size += entry.file_size
        if total_size > max_uncompressed_bytes:
            raise ValueError("archive expands beyond limit")
        if (
            entry.compress_size > 0
            and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO
        ):
            raise ValueError("suspicious compression ratio")
        names.add(entry.filename)
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise ValueError("macro-enabled archive")
    return names


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    raw = archive.read(name)
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("XML declarations are not allowed")
    return ET.fromstring(raw)


def _word_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        if element.tag == f"{{{W}}}t" and element.text:
            parts.append(element.text)
        elif element.tag == f"{{{W}}}tab":
            parts.append("\t")
        elif element.tag in {f"{{{W}}}br", f"{{{W}}}cr"}:
            parts.append("\n")
    return _normalize_text("".join(parts))


def _word_style_levels(archive: zipfile.ZipFile, names: set[str]) -> dict[str, int]:
    if "word/styles.xml" not in names:
        return {}
    root = _xml(archive, "word/styles.xml")
    levels: dict[str, int] = {}
    for style in root.findall(f".//{{{W}}}style"):
        style_id = style.get(f"{{{W}}}styleId")
        if not style_id:
            continue
        name_node = style.find(f"{{{W}}}name")
        name = "" if name_node is None else (name_node.get(f"{{{W}}}val") or "")
        outline = style.find(f".//{{{W}}}outlineLvl")
        outline_value = None if outline is None else outline.get(f"{{{W}}}val")
        match = re.search(r"heading\s*([1-9])", f"{style_id} {name}", re.IGNORECASE)
        if outline_value is not None and outline_value.isdigit() and int(outline_value) < 9:
            levels[style_id] = int(outline_value) + 1
        elif match:
            levels[style_id] = int(match.group(1))
    return levels


def _word_table_text(table: ET.Element) -> str:
    rows: list[str] = []
    for row in table.findall(f"./{{{W}}}tr"):
        cells: list[str] = []
        for cell in row.findall(f"./{{{W}}}tc"):
            value = " ".join(
                text
                for paragraph in cell.findall(f".//{{{W}}}p")
                if (text := _word_paragraph_text(paragraph))
            )
            cells.append(value)
        if any(cells):
            rows.append("\t".join(cells))
    return "\n".join(rows)


def _docx_contents(
    archive: zipfile.ZipFile,
    names: set[str],
    *,
    max_units: int,
    max_characters: int,
) -> list[dict[str, object]]:
    if "word/document.xml" not in names:
        raise ValueError("DOCX document part is missing")
    root = _xml(archive, "word/document.xml")
    body = root.find(f".//{{{W}}}body")
    if body is None:
        raise ValueError("DOCX body is missing")
    style_levels = _word_style_levels(archive, names)
    heading_path: list[str] = []
    current_title: str | None = None
    current_lines: list[str] = []
    sections: list[tuple[str, tuple[str, ...], str]] = []

    def flush() -> None:
        nonlocal current_lines
        content = _normalize_text("\n\n".join(current_lines))
        if content:
            title = current_title or _locator_text(content.splitlines()[0])
            path = tuple(heading_path) if heading_path else (title,)
            sections.append((title, path, content))
        current_lines = []

    for block in body:
        if block.tag == f"{{{W}}}p":
            text = _word_paragraph_text(block)
            style = block.find(f"./{{{W}}}pPr/{{{W}}}pStyle")
            style_id = None if style is None else style.get(f"{{{W}}}val")
            level = style_levels.get(style_id or "")
            if level is not None and text:
                flush()
                del heading_path[level - 1 :]
                while len(heading_path) < level - 1:
                    heading_path.append("未命名章节")
                locator_heading = _locator_text(text)
                heading_path.append(locator_heading)
                current_title = locator_heading
            if text:
                current_lines.append(text)
        elif block.tag == f"{{{W}}}tbl":
            text = _word_table_text(block)
            if text:
                current_lines.append(text)
    flush()

    if not sections:
        return []
    if len(sections) > max_units:
        raise OverflowError("DOCX_UNIT_LIMIT_EXCEEDED")
    if sum(len(section[2]) for section in sections) > max_characters:
        raise OverflowError("DOCX_TEXT_LIMIT_EXCEEDED")
    return [
        {
            "ordinal": ordinal,
            "content": content,
            "locator": {
                "kind": "heading",
                "position": ordinal,
                "title": title,
                "path": list(path),
            },
        }
        for ordinal, (title, path, content) in enumerate(sections, start=1)
    ]


def _relationships(archive: zipfile.ZipFile, name: str) -> dict[str, tuple[str, str]]:
    root = _xml(archive, name)
    relationships: dict[str, tuple[str, str]] = {}
    for relationship in root.findall(f".//{{{REL}}}Relationship"):
        relationship_id = relationship.get("Id")
        target = relationship.get("Target")
        rel_type = relationship.get("Type", "")
        if relationship_id and target and relationship.get("TargetMode") != "External":
            relationships[relationship_id] = (target, rel_type)
    return relationships


def _drawing_paragraphs(node: ET.Element) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in node.findall(f".//{{{A}}}p"):
        value = _normalize_text(
            "".join(text.text or "" for text in paragraph.findall(f".//{{{A}}}t"))
        )
        if value:
            paragraphs.append(value)
    return paragraphs


def _slide_text(root: ET.Element) -> list[str]:
    values: list[str] = []
    for shape in root.findall(f".//{{{P}}}sp"):
        values.extend(_drawing_paragraphs(shape))
    for frame in root.findall(f".//{{{P}}}graphicFrame"):
        values.extend(_drawing_paragraphs(frame))
    return list(dict.fromkeys(values))


def _notes_text(root: ET.Element) -> list[str]:
    values: list[str] = []
    for shape in root.findall(f".//{{{P}}}sp"):
        placeholder = shape.find(f"./{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
        if placeholder is not None and placeholder.get("type") == "body":
            values.extend(_drawing_paragraphs(shape))
    return values


def _resolve_part(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


def _pptx_contents(
    archive: zipfile.ZipFile,
    names: set[str],
    *,
    max_units: int,
    max_characters: int,
) -> list[dict[str, object]]:
    presentation_name = "ppt/presentation.xml"
    rels_name = "ppt/_rels/presentation.xml.rels"
    if presentation_name not in names or rels_name not in names:
        raise ValueError("PPTX presentation parts are missing")
    presentation = _xml(archive, presentation_name)
    presentation_rels = _relationships(archive, rels_name)
    slide_parts: list[str] = []
    for slide_id in presentation.findall(f".//{{{P}}}sldId"):
        rel_id = slide_id.get(f"{{{R}}}id")
        if rel_id in presentation_rels:
            slide_parts.append(_resolve_part(presentation_name, presentation_rels[rel_id][0]))
    if len(slide_parts) > max_units:
        raise OverflowError("PPTX_SLIDE_LIMIT_EXCEEDED")

    contents: list[dict[str, object]] = []
    extracted_characters = 0
    for slide_number, slide_part in enumerate(slide_parts, start=1):
        if slide_part not in names:
            raise ValueError("PPTX slide part is missing")
        slide = _xml(archive, slide_part)
        slide_values = _slide_text(slide)
        notes_values: list[str] = []
        slide_rels_name = posixpath.join(
            posixpath.dirname(slide_part),
            "_rels",
            f"{posixpath.basename(slide_part)}.rels",
        )
        if slide_rels_name in names:
            for target, rel_type in _relationships(archive, slide_rels_name).values():
                if rel_type.endswith("/notesSlide"):
                    notes_part = _resolve_part(slide_part, target)
                    if notes_part in names:
                        notes_values = _notes_text(_xml(archive, notes_part))
                    break
        raw_title = next((value for value in slide_values if not value.isdigit()), None)
        title = None if raw_title is None else _locator_text(raw_title)
        parts = slide_values[:]
        if notes_values:
            parts.extend(["演讲者备注", *notes_values])
        content = _normalize_text("\n\n".join(parts))
        extracted_characters += len(content)
        if extracted_characters > max_characters:
            raise OverflowError("PPTX_TEXT_LIMIT_EXCEEDED")
        contents.append(
            {
                "ordinal": slide_number,
                "content": content,
                "locator": {
                    "kind": "slide",
                    "position": slide_number,
                    "title": title,
                    "path": [],
                },
            }
        )
    return contents


def extract_office(
    source_path: Path,
    *,
    media_type: str,
    max_units: int,
    max_characters: int,
    max_uncompressed_bytes: int,
) -> dict[str, Any]:
    with zipfile.ZipFile(source_path) as archive:
        names = _validate_archive(archive, max_uncompressed_bytes=max_uncompressed_bytes)
        if media_type == DOCX_MEDIA_TYPE:
            contents = _docx_contents(
                archive,
                names,
                max_units=max_units,
                max_characters=max_characters,
            )
            kind = "DOCX"
        elif media_type == PPTX_MEDIA_TYPE:
            contents = _pptx_contents(
                archive,
                names,
                max_units=max_units,
                max_characters=max_characters,
            )
            kind = "PPTX"
        else:
            return {
                "error": {
                    "code": "UNSUPPORTED_DOCUMENT_TYPE",
                    "message": "解析器不支持该文件类型",
                }
            }
    if not contents or not any(str(content["content"]).strip() for content in contents):
        return {
            "error": {
                "code": f"{kind}_TEXT_NOT_FOUND",
                "message": f"未在 {kind} 中检测到可阅读文字",
            }
        }
    return {
        "parser_name": "ooxml-stdlib",
        "parser_version": PARSER_VERSION,
        "contents": contents,
    }


def main() -> None:
    if len(sys.argv) != 7:
        raise SystemExit(2)
    sys.addaudithook(_deny_network_access)
    source_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    media_type = sys.argv[3]
    try:
        result = extract_office(
            source_path,
            media_type=media_type,
            max_units=int(sys.argv[4]),
            max_characters=int(sys.argv[5]),
            max_uncompressed_bytes=int(sys.argv[6]),
        )
    except OverflowError as exc:
        code = str(exc)
        result = {
            "error": {
                "code": code,
                "message": "Office 文档内容超过处理上限，请拆分后重试",
            }
        }
    except Exception:
        kind = "DOCX" if media_type == DOCX_MEDIA_TYPE else "PPTX"
        result = {
            "error": {
                "code": f"{kind}_INVALID",
                "message": f"{kind} 文件损坏、结构异常或格式不受支持",
            }
        }
    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

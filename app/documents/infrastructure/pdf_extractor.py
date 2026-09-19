from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pypdf
from pypdf import PdfReader


def _normalized_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\x00", "").splitlines()).strip()


def extract_pdf(source_path: Path, *, max_pages: int, max_characters: int) -> dict[str, Any]:
    reader = PdfReader(source_path, strict=True)
    if reader.is_encrypted:
        return {"error": {"code": "PDF_ENCRYPTED", "message": "暂不支持加密 PDF"}}
    if len(reader.pages) > max_pages:
        return {
            "error": {
                "code": "PDF_PAGE_LIMIT_EXCEEDED",
                "message": f"PDF 页数不能超过 {max_pages} 页",
            }
        }

    pages: list[dict[str, object]] = []
    extracted_characters = 0
    for page_number, page in enumerate(reader.pages, start=1):
        content = _normalized_text(page.extract_text() or "")
        extracted_characters += len(content)
        if extracted_characters > max_characters:
            return {
                "error": {
                    "code": "PDF_TEXT_LIMIT_EXCEEDED",
                    "message": "PDF 可提取文字量超过处理上限",
                }
            }
        pages.append({"page_number": page_number, "content": content})

    if extracted_characters == 0:
        return {
            "error": {
                "code": "PDF_TEXT_NOT_FOUND",
                "message": "未检测到可提取文字；扫描版 PDF 需要 OCR",
            }
        }
    return {
        "parser_name": "pypdf",
        "parser_version": pypdf.__version__,
        "pages": pages,
    }


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(2)
    source_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    max_pages = int(sys.argv[3])
    max_characters = int(sys.argv[4])
    try:
        result = extract_pdf(
            source_path,
            max_pages=max_pages,
            max_characters=max_characters,
        )
    except Exception:
        result = {
            "error": {
                "code": "PDF_INVALID",
                "message": "PDF 文件损坏或格式不受支持",
            }
        }
    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

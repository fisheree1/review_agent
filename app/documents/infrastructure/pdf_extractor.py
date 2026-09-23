from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pypdf
from pypdf import PdfReader


def _deny_network_access(event: str, _: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise PermissionError("PDF parser subprocess cannot access the network")


def _normalized_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\x00", "").splitlines()).strip()


def _ocr_page(source_path: Path, page_number: int) -> str:
    """Rasterize one page at a bounded size, then recognize Chinese and English text."""
    with tempfile.TemporaryDirectory(prefix="review-agent-ocr-") as directory:
        image_path = Path(directory) / "page.png"
        output_prefix = str(Path(directory) / "page")
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-singlefile",
                "-scale-to",
                "2200",
                "-gray",
                "-png",
                str(source_path),
                output_prefix,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=35,
        )
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            check=True,
            capture_output=True,
            timeout=35,
        )
        return _normalized_text(result.stdout.decode("utf-8", errors="replace"))


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

    contents: list[dict[str, object]] = []
    extracted_characters = 0
    ocr_used = False
    for page_number, page in enumerate(reader.pages, start=1):
        content = _normalized_text(page.extract_text() or "")
        if len(content) < 20:
            try:
                recognized = _ocr_page(source_path, page_number)
            except FileNotFoundError:
                return {
                    "error": {
                        "code": "PDF_OCR_UNAVAILABLE",
                        "message": "OCR 服务不可用，请联系管理员",
                    }
                }
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return {
                    "error": {
                        "code": "PDF_OCR_FAILED",
                        "message": "扫描页识别失败，请重试或检查文件",
                    }
                }
            if len(recognized) > len(content):
                content = recognized
                ocr_used = True
        extracted_characters += len(content)
        if extracted_characters > max_characters:
            return {
                "error": {
                    "code": "PDF_TEXT_LIMIT_EXCEEDED",
                    "message": "PDF 可提取文字量超过处理上限",
                }
            }
        contents.append(
            {
                "ordinal": page_number,
                "content": content,
                "locator": {
                    "kind": "page",
                    "position": page_number,
                    "title": None,
                    "path": [],
                },
            }
        )

    if extracted_characters == 0:
        return {
            "error": {
                "code": "PDF_TEXT_NOT_FOUND",
                "message": "未识别到可阅读文字，请检查扫描质量",
            }
        }
    return {
        "parser_name": "pypdf+tesseract" if ocr_used else "pypdf",
        "parser_version": f"{pypdf.__version__}+ocr-v1",
        "contents": contents,
    }


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(2)
    sys.addaudithook(_deny_network_access)
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

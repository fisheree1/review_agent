from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pypdf
from pypdf import PageObject, PdfReader


def _deny_network_access(event: str, _: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise PermissionError("PDF parser subprocess cannot access the network")


def _normalized_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\x00", "").splitlines()).strip()


def _large_image(page: PageObject) -> bool:
    """Ignore small logos; inspect drawn image area without decoding image bytes."""
    page_area = float(page.mediabox.width) * float(page.mediabox.height)
    if page_area <= 0:
        return False
    resources = page.get("/Resources")
    if resources is None:
        return False
    resources = resources.get_object()
    images = resources.get("/XObject")
    images = images.get_object() if images is not None else {}
    found = False

    def inspect(operator: bytes, arguments: list[Any], matrix: list[float], _: list[float]) -> None:
        nonlocal found
        if operator != b"Do" or not arguments or len(matrix) < 4:
            return
        entry = images.get(arguments[0])
        if entry is None:
            return
        obj = entry.get_object()
        if obj.get("/Subtype") == "/Image":
            area = abs(matrix[0] * matrix[3] - matrix[1] * matrix[2])
            found = found or area / page_area >= 0.12

    page.extract_text(visitor_operand_before=inspect)
    return found


def _spatial_ocr(tsv: str) -> str:
    """Keep recognized labels grouped with bounded relative positions.

    Positions describe layout, not inferred arrows or semantic relationships.
    English-first bilingual OCR and sparse segmentation preserve diagram labels
    more reliably than treating a slide as a single dense text paragraph.
    """
    groups: dict[tuple[int, int], dict[str, Any]] = {}
    width = height = 1
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        level = int(row["level"])
        if level == 1:
            width, height = max(1, int(row["width"])), max(1, int(row["height"]))
        text = row.get("text", "").strip()
        if level != 5 or not text or float(row["conf"]) < 20:
            continue
        key = (int(row["block_num"]), int(row["par_num"]))
        group = groups.setdefault(
            key, {"left": int(row["left"]), "top": int(row["top"]), "lines": {}}
        )
        line = int(row["line_num"])
        group["lines"].setdefault(line, []).append(text)
    paragraphs: list[str] = []
    for group in sorted(groups.values(), key=lambda group: (group["top"], group["left"])):
        lines = [" ".join(words) for _, words in sorted(group["lines"].items())]
        left, top = round(100 * group["left"] / width), round(100 * group["top"] / height)
        paragraphs.append(f"[OCR region left={left}% top={top}%]\n" + "\n".join(lines))
    return "\n\n".join(paragraphs)


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
            ["tesseract", str(image_path), "stdout", "-l", "eng+chi_sim", "--psm", "11", "tsv"],
            check=True,
            capture_output=True,
            timeout=35,
        )
        return _spatial_ocr(result.stdout.decode("utf-8", errors="replace"))


def _ocr_supplement(native: str, recognized: str) -> str:
    """Keep new figure labels while avoiding duplicate native paragraphs."""
    normalized = re.sub(r"\s+", " ", native).casefold()
    additions: list[str] = []
    for block in recognized.split("\n\n"):
        text = block.split("\n", 1)[-1] if block.startswith("[OCR region ") else block
        candidate = re.sub(r"\s+", " ", text).strip().casefold()
        if candidate and candidate not in normalized:
            additions.append(block)
    return "\n\n".join(additions)


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
    mixed_pages = 0
    for page_number, page in enumerate(reader.pages, start=1):
        content = _normalized_text(page.extract_text() or "")
        image_only = len(content) < 20
        mixed = not image_only and len(content) < 1600 and mixed_pages < 64 and _large_image(page)
        if image_only or mixed:
            mixed_pages += int(not image_only)
            try:
                recognized = _ocr_page(source_path, page_number)
            except FileNotFoundError:
                return {
                    "error": {
                        "code": "PDF_OCR_UNAVAILABLE",
                        "message": "OCR 服务不可用，请联系管理员",
                    }
                }
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError):
                return {
                    "error": {
                        "code": "PDF_OCR_FAILED",
                        "message": "扫描页识别失败，请重试或检查文件",
                    }
                }
            if recognized and image_only:
                content = recognized
                ocr_used = True
            elif recognized and mixed:
                supplement = _ocr_supplement(content, recognized)
                if supplement:
                    content = content + "\n\n" + supplement
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
        "parser_version": f"{pypdf.__version__}+layout-ocr-v2",
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

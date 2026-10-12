"""Render a validated study answer as a private, reproducible PDF."""

from __future__ import annotations

from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

PDF_EXPORT_VERSION = "cited-study-notes-v2"
FONT_PATH = Path(__file__).with_name("fonts") / "NotoSansSC-Regular.ttf"


@lru_cache(maxsize=1)
def _register_font() -> None:
    pdfmetrics.registerFont(TTFont("StudyNoto", str(FONT_PATH)))


def render_study_pdf(
    title: str,
    answer: dict[str, Any],
    scope: list[dict[str, Any]],
    coverage: dict[str, int] | None,
) -> bytes:
    """Render persisted PDF points and the optional teaching explanation."""
    if not answer.get("claims"):
        raise ValueError("No cited claims available")
    _register_font()
    text = ParagraphStyle(
        "note-body",
        fontName="StudyNoto",
        fontSize=11,
        leading=18,
        textColor=colors.HexColor("#252b33"),
        wordWrap="CJK",
        alignment=TA_LEFT,
    )
    heading = ParagraphStyle(
        "note-title",
        parent=text,
        fontSize=19,
        leading=27,
        spaceAfter=16,
    )
    label = ParagraphStyle(
        "note-label",
        parent=text,
        fontSize=9,
        leading=14,
        textColor=colors.HexColor("#566477"),
    )
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=48,
        bottomMargin=48,
        title=title,
        author="Review Agent",
    )
    names = {item["document_id"]: item["filename"] for item in scope}
    story: list[Any] = [Paragraph(escape(title), heading)]
    if coverage:
        coverage_text = (
            f"抽样覆盖 {coverage['sampled_pages']} / {coverage['indexed_pages']} 个有正文的页面。"
            "请通过下方出处核对重要结论。"
        )
        if "processed_chunks" in coverage:
            coverage_text = (
                f"已阅读 {coverage['sampled_pages']} / {coverage['indexed_pages']} 个有正文的页面，"
                f"{coverage['processed_chunks']} / {coverage['indexed_chunks']} 个正文片段；"
                f"其中 {coverage['full_pages']} 页的索引正文已完整读取。"
                "阅读范围不等同于全部知识点均已提取，请结合原文核对。"
            )
            if coverage.get("budget_limited"):
                coverage_text += "本次整理已达到处理上限，可按章节继续整理。"
        story.append(Paragraph(coverage_text, label))
        story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#d4dce5")))
    story.append(Spacer(1, 16))
    for index, claim in enumerate(answer["claims"], start=1):
        if claim.get("title"):
            story.append(Paragraph(f"{index}. {escape(claim['title'])}", text))
        story.append(Paragraph(escape(claim["text"]), text))
        for paragraph in (claim.get("explanation") or "").split("\n"):
            if paragraph.strip():
                story.append(Paragraph(escape(paragraph), text))
        story.append(Spacer(1, 5))
        for citation in claim["citations"]:
            filename = names.get(citation.get("document_id", ""), "原文")
            source = f"{escape(filename)} · 第 {citation['unit']} 页"
            story.append(Paragraph(source, label))
            story.append(Paragraph(escape(citation["quote"][:160]), label))
        story.append(Spacer(1, 15))
    if explanation := answer.get("explanation"):
        for paragraph in explanation.split("\n"):
            if paragraph.strip():
                story.append(Paragraph(escape(paragraph), text))
                story.append(Spacer(1, 12))
    document.build(story)
    return buffer.getvalue()

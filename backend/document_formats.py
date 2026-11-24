"""Original file validation and extraction for the ingestion worker."""

import codecs
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid5
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from database import Chunk

MIME = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(ValueError):
    """Safe, user-visible import error."""


def validate(file, suffix):
    if suffix == ".pdf" and file.read(5) != b"%PDF-":
        raise DocumentError("文件缺少 PDF 标识。")
    if suffix == ".doc" and file.read(8) != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise DocumentError("文件不是二进制 Word 文档。")
    if suffix == ".docx":
        try:
            with ZipFile(file) as archive:
                body = archive.getinfo("word/document.xml")
                archive.getinfo("[Content_Types].xml")
                if body.file_size > 64 * 1024 * 1024:
                    raise DocumentError("Word 正文解压后超过 64 MiB。")
        except (BadZipFile, KeyError):
            raise DocumentError("文件缺少 DOCX 正文。") from None
    if suffix in (".md", ".markdown"):
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        try:
            while block := file.read(65536):
                if b"\x00" in block:
                    raise DocumentError("Markdown 必须是 UTF-8 文本。")
                decoder.decode(block)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise DocumentError("Markdown 必须是 UTF-8 文本。") from None


def fragments(doc_id, label, section, start, end, text, parent=None):
    text = text.strip()
    return [
        Chunk(
            id=uuid5(doc_id, f"{label}/{start}/{offset}"),
            document_id=doc_id,
            section=section,
            parent_id=parent,
            page_start=start,
            page_end=end,
            text=text[offset : offset + 1600],
        )
        for offset in range(0, len(text), 1600)
    ]


def markdown(path, doc_id):
    chunks = []
    section = "Markdown"
    parent = None
    lines = []
    size = start = end = 0
    fence = None

    def flush():
        chunks.extend(
            fragments(doc_id, "md", section, start, end, "\n".join(lines), parent)
        )
        lines.clear()

    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        heading = None if fence else re.match(r"^ {0,3}#{1,6}\s+(.+)", line)
        if heading or not line.strip() or size + len(line) > 1500:
            flush()
            size = 0
        if heading:
            section = heading[1].strip()
            parent = uuid5(doc_id, f"md-section/{number}")
        if marker := re.match(r"^ {0,3}(`{3,}|~{3,})", line):
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence):
                fence = None
        if line.strip():
            if not lines:
                start = number
            end = number
            lines.append(line)
            size += len(line) + 1
    flush()
    return chunks


def word_document(path, doc_id):
    if path.suffix == ".doc":
        command = (
            ["textutil", "-convert", "txt", "-stdout", str(path)]
            if sys.platform == "darwin"
            else ["antiword", "-m", "UTF-8.txt", str(path)]
        )
        text = run_parser(command).decode("utf-8")
        return [
            chunk
            for n, line in enumerate(text.splitlines(), 1)
            for chunk in fragments(doc_id, "doc", "Word 正文", n, n, line)
        ]
    with ZipFile(path) as archive:
        body = ElementTree.fromstring(archive.read("word/document.xml")).find(
            WORD + "body"
        )
    if body is None:
        raise DocumentError("Word 正文为空。")
    chunks = []
    section = "Word 正文"
    parent = None
    index = 0
    for block in body:
        if block.tag == WORD + "p":
            text = "".join(node.text or "" for node in block.iter(WORD + "t"))
            style = block.find("./" + WORD + "pPr/" + WORD + "pStyle")
            if style is not None and style.get(WORD + "val", "").lower().startswith(
                ("heading", "标题")
            ):
                section = text
                parent = uuid5(doc_id, f"word-section/{index}")
            parts = [text]
        elif block.tag == WORD + "tbl":
            rows = [
                [
                    " ".join(t.text or "" for t in cell.iter(WORD + "t"))
                    for cell in row.findall(WORD + "tc")
                ]
                for row in block.findall(WORD + "tr")
            ]
            parts = [
                " | ".join(
                    f"{rows[0][j]}: {v}" if i and j < len(rows[0]) else v
                    for j, v in enumerate(row)
                )
                for i, row in enumerate(rows)
            ]
        else:
            continue
        for part in parts:
            index += 1
            chunks.extend(
                fragments(doc_id, "docx", section, index, index, part, parent)
            )
    return chunks


def run_parser(args):
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        raise DocumentError(
            "文档转换失败，请检查文件是否加密或损坏，以及 worker 的解析依赖。"
        ) from None


def pdf_chunks(path, doc_id, parse_text):
    pages = (
        run_parser(["pdftotext", "-enc", "UTF-8", str(path), "-"]).decode().split("\f")
    )
    if pages and not pages[-1]:
        pages.pop()
    scanned = {n for n, text in enumerate(pages, 1) if not text.strip()}
    chunks = parse_text(path, doc_id) if len(scanned) < len(pages) else []
    if scanned:
        chunks = [c for c in chunks if c.page_start not in scanned]
        with TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "page")
            for page in sorted(scanned):
                run_parser(
                    [
                        "pdftoppm",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-singlefile",
                        "-scale-to",
                        "3000",
                        "-png",
                        str(path),
                        prefix,
                    ]
                )
                text = run_parser(
                    ["tesseract", prefix + ".png", "stdout", "-l", "chi_sim+eng"]
                ).decode()
                chunks.extend(fragments(doc_id, "ocr", "扫描页 OCR", page, page, text))
    return sorted(chunks, key=lambda c: c.page_start)

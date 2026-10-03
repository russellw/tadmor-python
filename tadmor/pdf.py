"""A minimal PDF writer, enough for printable business documents.

Pages use the PDF "standard 14" Helvetica fonts, which every conforming
reader has built in, so no font program is embedded; only their advance
widths (pdf_metrics) are needed to measure text. Coordinates are in
points with the origin at the bottom left. Text is WinAnsi (CP1252), and
characters outside it render as '?'.
"""

import zlib

from .pdf_metrics import HELVETICA, HELVETICA_BOLD

REGULAR, BOLD = "/F1", "/F2"
_WIDTHS = {REGULAR: HELVETICA, BOLD: HELVETICA_BOLD}

A4 = (595.28, 841.89)


def _winansi(s):
    return s.encode("cp1252", errors="replace")


def width(font, size, s):
    w = _WIDTHS[font]
    return sum(w[b] for b in _winansi(s)) * size / 1000


def _num(v):
    """A coordinate without trailing zeros."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _string(s):
    b = _winansi(s)
    b = b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + b.replace(b"\n", b"\\n").replace(b"\r", b"\\r") + b")"


class Page:
    def __init__(self, w, h):
        self.width, self.height = w, h
        self.content = bytearray()

    def text(self, font, size, x, y, s, gray=0):
        self.content += f"BT {font} {_num(size)} Tf {_num(gray)} g {_num(x)} {_num(y)} Td ".encode()
        self.content += _string(s) + b" Tj ET\n"

    def line(self, x1, y1, x2, y2, w, gray):
        self.content += f"{_num(w)} w {_num(gray)} G {_num(x1)} {_num(y1)} m {_num(x2)} {_num(y2)} l S\n".encode()


class Document:
    def __init__(self):
        self.pages = []

    def add_page(self, w=A4[0], h=A4[1]):
        p = Page(w, h)
        self.pages.append(p)
        return p

    def bytes(self):
        """Serialize: 1 catalog, 2 page tree, 3/4 fonts, then a page and a
        compressed content stream per page."""
        buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []

        def obj(body):
            offsets.append(len(buf))
            buf.extend(f"{len(offsets)} 0 obj\n".encode() + body + b"\nendobj\n")

        kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(len(self.pages)))
        obj(b"<< /Type /Catalog /Pages 2 0 R >>")
        obj(f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>".encode())
        obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        for i, p in enumerate(self.pages):
            obj(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_num(p.width)} {_num(p.height)}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * i} 0 R >>".encode()
            )
            data = zlib.compress(bytes(p.content))
            obj(f"<< /Length {len(data)} /Filter /FlateDecode >>\nstream\n".encode() + data + b"\nendstream")
        xref = len(buf)
        buf += f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            buf += f"{off:010d} 00000 n \n".encode()
        buf += f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(buf)

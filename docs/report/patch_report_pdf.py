"""One-time precise patch of report.pdf, preserving the original 18-page layout.

Updates the summary retest label on p.2 and replaces the live Treasure Grid
replay paragraph on p.14 with a concise implementation explanation.
The embedded original HelveticaNeue is reused, with a reconstructed ASCII
cmap because Chromium PDF subsetting removed it.
"""
from io import BytesIO
from pathlib import Path

import fitz
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / "report.pdf"

with fitz.open(PDF) as doc:
    assert len(doc) == 18, "Unexpected PDF pagination"
    original_p2 = doc[1].get_text()
    original_p14 = doc[13].get_text()
    if "Kimi K3 post-fix retest" in original_p2 and "tryMove(dcol, drow)" in original_p14:
        print("PDF already updated")
        raise SystemExit(0)
    assert "latest Kimi K3 retest" in original_p2, "Unexpected p.2 content"
    assert "After the presentation we replayed" in original_p14, "Unexpected p.14 content"

    fontdata = None
    for entry in doc[13].get_fonts():
        if entry[3].endswith("+HelveticaNeue"):
            fontdata = doc.extract_font(entry[0])[3]
            break
    assert fontdata, "Original embedded HelveticaNeue missing"
    font = TTFont(BytesIO(fontdata))
    cmap = newTable("cmap")
    cmap.tableVersion = 0
    sub = CmapSubtable.newSubtable(4)
    sub.platformID, sub.platEncID, sub.language = 3, 1, 0
    glyphs = font.getGlyphOrder()
    sub.cmap = {cp: glyphs[3 if cp == 32 else cp - 25] for cp in range(32, 127)}
    cmap.tables = [sub]
    font["cmap"] = cmap
    buffer = BytesIO()
    font.save(buffer)
    fontdata = buffer.getvalue()

    page = doc[1]
    page.add_redact_annot(fitz.Rect(61, 279.2, 166, 290), fill=(1, 1, 1))
    page.apply_redactions(images=0, graphics=0, text=0)
    page.insert_font(fontname="HNEmbedded", fontbuffer=fontdata)
    page.insert_text((61.4998, 287.9945), "Kimi K3 post-fix retest",
                     fontname="HNEmbedded", fontsize=8.5948,
                     color=(0x4d/255, 0x66/255, 0x70/255))

    page = doc[13]
    page.add_redact_annot(fitz.Rect(51.4, 322.4, 547.0, 431), fill=(1, 1, 1))
    page.apply_redactions(images=0, graphics=0, text=0)
    page.insert_font(fontname="HNEmbedded", fontbuffer=fontdata)
    paragraph = (
        "Technical implementation and replay. In game.js, tryMove(dcol, drow) blocks movement into walls or "
        "outside the grid and ignores input after win or loss. Each collected treasure is removed and raises the "
        "score. Reaching the trap loses; the exit wins only after all three treasures are collected. Restart resets the "
        "game. A post-presentation browser replay used real key presses to confirm the initial layout, boundary "
        "and wall checks, score, early exit, win/loss, Restart and the absence of JavaScript errors or page "
        "scrolling. The replay checks the delivered game only, not the agent's verdict. The run's summary and "
        "traces/ were not stored here, so elapsed time and repair count are not reported."
    )
    spare = page.insert_textbox(fitz.Rect(51.75, 323.10, 545.25, 442),
                                paragraph, fontname="HNEmbedded",
                                fontsize=10.39475,
                                color=(0x13/255, 0x23/255, 0x2b/255),
                                align=fitz.TEXT_ALIGN_JUSTIFY,
                                lineheight=1.592)
    assert spare >= 0, "Added paragraph did not fit"
    doc.save(ROOT / "report-patched.pdf", garbage=4, deflate=True)

with fitz.open(ROOT / "report-patched.pdf") as patched:
    assert len(patched) == 18
    assert "Kimi K3 post-fix retest" in patched[1].get_text()
    assert "tryMove(dcol, drow)" in patched[13].get_text()
    assert "8 Current limitations" in patched[13].get_text()
(ROOT / "report-patched.pdf").replace(PDF)
print("Patched report.pdf: 18 pages, original embedded font and layout retained")

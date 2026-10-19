"""Render report/report.html to report/report.pdf with headless Chromium.

Run from the repository root:  python report/build_report.py
Animated SVG figures render as their final frame (reduced motion).
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
FIGURES = HERE / "figures"
# (svg, milliseconds into the animation when every element is shown)
FIGURE_FRAMES = [("readme-banner.svg", 13000), ("architecture.svg", 1500),
                 ("verification.svg", 10500), ("state-machine.svg", 13500)]

FOOTER = ('<div style="width:100%;font-size:8px;color:#6b8790;padding:0 18mm;display:flex;'
          'justify-content:space-between;font-family:Helvetica,Arial,sans-serif">'
          '<span>PE6203 Group Project 1 · A Coding Agent That Builds a Game</span>'
          '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


def make_figures(browser) -> None:
    """Static high-resolution frames of the animated README artwork."""
    FIGURES.mkdir(exist_ok=True)
    for name, ms in FIGURE_FRAMES:
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        holder = FIGURES / "_frame.html"
        holder.write_text(f"<body style='margin:0'><img id=f src='{(ASSETS / name).as_uri()}' "
                          "style='width:1280px;display:block'></body>", encoding="utf-8")
        page.goto(holder.as_uri())
        page.wait_for_timeout(ms)
        page.locator("#f").screenshot(path=str(FIGURES / name.replace(".svg", ".png")))
        page.close()
        holder.unlink()


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        make_figures(browser)
        page = browser.new_page()
        page.emulate_media(reduced_motion="reduce", media="print")
        page.goto((HERE / "report.html").as_uri())
        page.wait_for_load_state("networkidle")
        page.pdf(path=str(HERE / "report.pdf"), format="A4", print_background=True,
                 display_header_footer=True, header_template="<span></span>", footer_template=FOOTER,
                 margin={"top": "18mm", "bottom": "18mm", "left": "18mm", "right": "18mm"})
        browser.close()
    print(f"wrote {HERE / 'report.pdf'}")


if __name__ == "__main__":
    main()

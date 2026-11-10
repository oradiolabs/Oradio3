#!/usr/bin/env bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on November 10, 2026
# @author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:     Stichting Oradio
# @license:       GNU General Public License (GPL)
# @organization:  Stichting Oradio
# @version:       1
# @email:         info@stichtingoradio.nl
# @status:        Development
# @Reference:
#	https://docs.reportlab.com/reportlab/userguide/ch6_paragraphs/
# @Purpose:
#	Writes a PDF overview of the music on the USB stick
#	- table of contents with every folder under <USB>/Muziek, clickable
#	- one section per folder listing its mp3 files
#	- PDF bookmarks per folder and a 'terug naar inhoud' link per section
#	The PDF is saved in the root of the USB stick as Muziek_overzicht.pdf
#
#	The paths are not arguments: the music folder is USB_MOUNT_POINT from
#	constants.env plus /Muziek, the same way Main/constants.py builds
#	USB_MUSIC. The script fails if that folder does not exist, which is also
#	what happens when no stick is mounted
#
# @Usage:
#	bash ./USB_Muziek2pdf.sh

##### Initialize #####################

# Stop on errors (-e), catch unset variables (-u), catch failures in any part of a pipeline (-o pipefail)
set -euo pipefail

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

#---------- Ensure using bash ----------

# The script uses bash constructs
if [ -z "${BASH:-}" ]; then
	echo -e "${RED}This script requires bash${NC}"
	exit 1
fi

#---------- Locate the helper scripts and constants ----------

# pkg-helper.sh sits next to this script, constants.env one level up
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSTANTS="$SCRIPT_DIR/../constants.env"

#---------- Config ----------

USB_ROOT="$(sed -n 's/^USB_MOUNT_POINT=//p' "$CONSTANTS")"
if [ -z "$USB_ROOT" ]; then
	echo -e "${RED}USB_MOUNT_POINT not found in $CONSTANTS${NC}"
	exit 1
fi
MUSIC_DIR="$USB_ROOT/Muziek"
PDF_FILE="$USB_ROOT/Muziek_overzicht.pdf"

#---------- Check music folder ----------

# Before installing anything: no point fetching packages for a stick that is not there
if [ ! -d "$MUSIC_DIR" ]; then
	echo -e "${RED}Folder '$MUSIC_DIR' not found: is the USB stick mounted?${NC}"
	exit 1
fi

#---------- Ensure required packages are installed and up to date ----------

# python3-reportlab builds the PDF. fonts-dejavu-core gives it a TrueType font
# with accented characters (é, ü, ø) for folder and file names; the built-in
# PDF fonts only cover Latin-1
bash "$SCRIPT_DIR/pkg-helper.sh" python3-reportlab fonts-dejavu-core

#---------- Build the PDF ----------

echo -e "${YELLOW}Building $PDF_FILE ...${NC}"

# System interpreter: python3-reportlab is an apt package. ~/.venv is created
# with --system-site-packages, so it would find it as well
/usr/bin/python3 - "$MUSIC_DIR" "$PDF_FILE" <<'PYTHON'
import os
import sys
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MUSIC_DIR, PDF_FILE = sys.argv[1], sys.argv[2]
EXTENSIONS = (".mp3",)
SKIP_DIRS = ("System Volume Information", "$RECYCLE.BIN")
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
ACCENT = "#1F3A5F"


def pick_fonts():
    """DejaVu when installed, else the built-in Helvetica."""
    regular = os.path.join(FONT_DIR, "DejaVuSans.ttf")
    bold = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
    if os.path.exists(regular) and os.path.exists(bold):
        pdfmetrics.registerFont(TTFont("Base", regular))
        pdfmetrics.registerFont(TTFont("Base-Bold", bold))
        pdfmetrics.registerFontFamily("Base", normal="Base", bold="Base-Bold")
        return "Base", "Base-Bold"
    return "Helvetica", "Helvetica-Bold"


def collect(root):
    """List of (relative path, depth, [mp3 files]), folders in alphabetical order."""
    result = []
    for path, dirs, files in os.walk(root):
        dirs[:] = sorted((d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS),
                         key=str.lower)
        mp3s = sorted((f for f in files
                       if f.lower().endswith(EXTENSIONS) and not f.startswith("._")),
                      key=str.lower)
        rel = os.path.relpath(path, root)
        if rel == ".":
            if mp3s:  # loose files directly in Muziek
                result.append(("(Muziek)", 0, mp3s))
            continue
        result.append((rel, rel.count(os.sep), mp3s))
    return result


class Doc(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        # Bookmark in the reader's side panel for every folder heading
        info = getattr(flowable, "bookmark", None)
        if info:
            key, title, level = info
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level=level, closed=True)


folders = collect(MUSIC_DIR)
if not folders:
    print(f"No folders or mp3 files in {MUSIC_DIR}")
    sys.exit(1)

font, font_bold = pick_fonts()
ss = getSampleStyleSheet()
s_title = ParagraphStyle("title", parent=ss["Title"], fontName=font_bold, fontSize=22)
s_head = ParagraphStyle("head", parent=ss["Heading2"], fontName=font_bold,
                        textColor=colors.HexColor(ACCENT), spaceAfter=4)
s_path = ParagraphStyle("path", parent=ss["Normal"], fontName=font, fontSize=8,
                        textColor=colors.grey)
s_body = ParagraphStyle("body", parent=ss["Normal"], fontName=font, fontSize=10, leading=13)
s_index = ParagraphStyle("index", parent=s_body, alignment=2)  # right-aligned
s_toc = ParagraphStyle("toc", parent=s_body, fontSize=10.5, leading=15)
s_back = ParagraphStyle("back", parent=s_body, fontSize=8, alignment=2)

total = sum(len(m) for _, _, m in folders)
story = [
    Paragraph('<a name="toc"/>Muziek', s_title),
    Paragraph(f"{len(folders)} mappen &middot; {total} mp3-bestanden", s_path),
    Spacer(1, 0.4 * cm),
    Paragraph("Inhoudsopgave", s_head),
]

# Table of contents, sub folders indented
for i, (rel, depth, mp3s) in enumerate(folders):
    name = escape(os.path.basename(rel) or rel)
    style = ParagraphStyle(f"toc{depth}", parent=s_toc, leftIndent=depth * 0.6 * cm)
    story.append(Paragraph(f'<a href="#dir{i}" color="{ACCENT}">{name}</a> '
                           f'<font color="grey" size="8">({len(mp3s)})</font>', style))

# One section per folder
for i, (rel, depth, mp3s) in enumerate(folders):
    story.append(PageBreak())
    name = os.path.basename(rel) or rel
    head = Paragraph(f'<a name="dir{i}"/>{escape(name)}', s_head)
    head.bookmark = (f"dir{i}", name, depth)
    story += [head, Paragraph(escape(rel), s_path), Spacer(1, 0.3 * cm)]

    if mp3s:
        # Index column as wide as the largest number plus cell padding, so
        # 100., 1000. and up never wrap
        index_width = pdfmetrics.stringWidth(f"{len(mp3s)}.", font, s_body.fontSize) + 14
        rows = [[Paragraph(f"{n}.", s_index), Paragraph(escape(f), s_body)]
                for n, f in enumerate(mp3s, 1)]
        table = Table(rows, colWidths=[max(index_width, 1.2 * cm), None])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#F2F5F9")]),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("<i>Geen mp3-bestanden in deze map.</i>", s_path))

    story += [Spacer(1, 0.4 * cm),
              Paragraph(f'<a href="#toc" color="{ACCENT}">&#8593; terug naar inhoud</a>', s_back)]


def page_number(canv, doc):
    canv.saveState()
    canv.setFont(font, 8)
    canv.setFillColor(colors.grey)
    canv.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"pagina {doc.page}")
    canv.restoreState()


doc = Doc(PDF_FILE, pagesize=A4, title="Muziek - overzicht",
          leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm)
doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
print(f"{len(folders)} folders, {total} mp3 files")
PYTHON

# The stick is mounted without sync (see USB_MOUNT_OPTIONS in constants.env):
# force the PDF out so it survives pulling the stick right after this script
sync -f "$PDF_FILE"

echo -e "${GREEN}Saved: $PDF_FILE${NC}"

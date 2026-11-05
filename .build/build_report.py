import html
import re
import struct
import sys
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Group
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, Preformatted, KeepTogether, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).resolve().parents[1]
INK = colors.HexColor('#17212D')
PALE = colors.HexColor('#EDF2F6')
GRID = colors.HexColor('#D9D9D9')
COMPACT = '--compact' in sys.argv
MARGIN = 52 if COMPACT else 72
WIDTH = A4[0] - MARGIN * 2
DIAGRAM_WIDTH = A4[0] - 104
for font_name, filename in [('TNR', 'times.ttf'), ('TNR-Bold', 'timesbd.ttf'), ('TNR-Italic', 'timesi.ttf'), ('TNR-BoldItalic', 'timesbi.ttf')]:
    pdfmetrics.registerFont(TTFont(font_name, str(Path('C:/Windows/Fonts') / filename)))
pdfmetrics.registerFontFamily('TNR', normal='TNR', bold='TNR-Bold', italic='TNR-Italic', boldItalic='TNR-BoldItalic')
font_bytes = Path('C:/Windows/Fonts/times.ttf').read_bytes()
table_count = struct.unpack_from('>H', font_bytes, 4)[0]
font_tables = {font_bytes[12+i*16:16+i*16].decode(): struct.unpack_from('>II', font_bytes, 20+i*16) for i in range(table_count)}
units_per_em = struct.unpack_from('>H', font_bytes, font_tables['head'][0]+18)[0]
win_ascent, win_descent = struct.unpack_from('>HH', font_bytes, font_tables['OS/2'][0]+74)
SINGLE = 12 * (win_ascent + win_descent) / units_per_em
DOUBLE = 2 * SINGLE
styles = getSampleStyleSheet()
styles.add(ParagraphStyle('ReportTitle', fontName='TNR-Bold', fontSize=12, leading=SINGLE*1.07917, spaceAfter=8, textColor=colors.black))
styles.add(ParagraphStyle('ReportHeading', fontName='TNR-Bold', fontSize=12, leading=SINGLE*1.07917, spaceBefore=0, spaceAfter=8, firstLineIndent=35.45, alignment=TA_JUSTIFY, keepWithNext=True, textColor=colors.black))
styles.add(ParagraphStyle('ReportBody', fontName='TNR', fontSize=12, leading=DOUBLE, firstLineIndent=36, alignment=TA_JUSTIFY, spaceBefore=0, spaceAfter=0, textColor=colors.black, allowWidows=0, allowOrphans=0))
styles.add(ParagraphStyle('ReportCell', fontName='TNR', fontSize=12, leading=SINGLE, alignment=TA_JUSTIFY, textColor=colors.black))
styles.add(ParagraphStyle('ReportHeader', parent=styles['ReportCell'], fontName='TNR-Bold', alignment=TA_LEFT, textColor=colors.black))
styles.add(ParagraphStyle('ReportCode', fontName='Courier', fontSize=8.1, leading=11, spaceBefore=5, spaceAfter=10, leftIndent=8))
styles.add(ParagraphStyle('FigureNumber', fontName='TNR-Bold', fontSize=12, leading=DOUBLE, alignment=TA_LEFT, spaceAfter=0, keepWithNext=True))
styles.add(ParagraphStyle('FigureCaption', fontName='TNR-Italic', fontSize=12, leading=DOUBLE, alignment=TA_LEFT, spaceAfter=8, keepWithNext=True))
styles.add(ParagraphStyle('ReportReference', fontName='Helvetica', fontSize=9, leading=12, spaceAfter=5, textColor=INK, allowWidows=0, allowOrphans=0))
styles['ReportCell'].alignment = TA_LEFT
if COMPACT:
    for name in ('ReportBody', 'ReportCell', 'ReportHeader'):
        styles[name].fontName = 'Helvetica-Bold' if name == 'ReportHeader' else 'Helvetica'
        styles[name].fontSize = 10
        styles[name].leading = 14 if name == 'ReportBody' else 12
        styles[name].firstLineIndent = 0
    styles['ReportBody'].spaceAfter = 7
    for name in ('ReportTitle', 'ReportHeading'):
        styles[name].fontName = 'Helvetica-Bold'
        styles[name].fontSize = 23 if name == 'ReportTitle' else 13
        styles[name].leading = 28 if name == 'ReportTitle' else 17
        styles[name].firstLineIndent = 0
    styles['ReportHeading'].spaceBefore = 10
    for name in ('FigureNumber', 'FigureCaption'):
        styles[name].fontName = 'Helvetica-Bold' if name == 'FigureNumber' else 'Helvetica-Oblique'
        styles[name].fontSize = 9
        styles[name].leading = 12


def box(drawing, x, y, w, h, title, lines=()):
    drawing.add(Rect(x, y, w, h, fillColor=PALE, strokeColor=colors.HexColor('#92A3B3'), strokeWidth=.7, rx=3, ry=3))
    drawing.add(String(x + w/2, y + h - 16, title, textAnchor='middle', fontName='Helvetica-Bold', fontSize=9, fillColor=INK))
    for i, line in enumerate(lines):
        drawing.add(String(x + 10, y + h - 31 - i*12, line, fontName='Helvetica', fontSize=8.2, fillColor=INK))


def arrow(drawing, x1, y1, x2, y2, label=None):
    import math
    drawing.add(Line(x1, y1, x2, y2, strokeColor=INK, strokeWidth=.8))
    angle = math.atan2(y2-y1, x2-x1)
    p = [(x2, y2)] + [(x2-6*math.cos(angle+a), y2-6*math.sin(angle+a)) for a in (-.5, .5)]
    drawing.add(Polygon([v for pair in p for v in pair], fillColor=INK, strokeColor=INK))
    if label:
        drawing.add(String((x1+x2)/2+4, (y1+y2)/2+4, label, fontName='Helvetica', fontSize=8, fillColor=INK))


def architecture():
    d = Drawing(DIAGRAM_WIDTH, 330)
    box(d, 125, 245, 240, 35, 'Customers and warehouse operators')
    box(d, 125, 180, 240, 42, 'InventoryClient or SQL sessions', ['Primary commands and WAL-aware reads'])
    box(d, 10, 90, 210, 68, 'PostgreSQL primary :55434',
        ['Stock, reservations, transfers', 'Atomic inventory event records', 'Dedicated replication role and slot'])
    box(d, 270, 90, 210, 68, 'Synchronous standby :55435',
        ['Acknowledges durable WAL receipt', 'Replay may still lag', 'Fresh or advisory inventory reads'])
    box(d, 0, 180, 105, 42, 'Expiry worker', ['Automatic cleanup'])
    arrow(d, 245, 245, 245, 222)
    arrow(d, 180, 180, 150, 158)
    arrow(d, 320, 180, 365, 158)
    arrow(d, 52, 180, 52, 158)
    arrow(d, 220, 124, 270, 124)
    d.add(String(229, 134, 'WAL', fontName='Helvetica', fontSize=8, fillColor=INK))
    box(d, 10, 5, 210, 48, 'Outbox relay worker', ['Recoverable leases; retry after failure'])
    box(d, 270, 5, 210, 48, 'Projection database :55436', ['Durable inbox and movement totals'])
    arrow(d, 115, 90, 115, 53)
    arrow(d, 220, 29, 270, 29)
    d.add(String(224, 39, 'events', fontName='Helvetica', fontSize=8, fillColor=INK))
    return d


def erd():
    d = Drawing(DIAGRAM_WIDTH, 300)
    box(d, 0, 235, 210, 60, 'PRODUCTS', ['PK product_id', 'sku unique, name'])
    box(d, 280, 235, 210, 60, 'WAREHOUSES', ['PK warehouse_id', 'name, city'])
    box(d, 135, 125, 220, 85, 'INVENTORY', ['PK FK warehouse_id, product_id', 'on_hand, reserved, available', 'version, updated_at'])
    box(d, 0, 5, 145, 85, 'RESERVATIONS', ['PK order_id', 'request_items, status', 'expires_at, completed_at'])
    box(d, 173, 5, 145, 85, 'RESERVATION ITEMS', ['PK FK order_id', 'PK FK warehouse_id', 'PK FK product_id, quantity'])
    box(d, 345, 5, 145, 85, 'INVENTORY EVENTS', ['PK event_id', 'FK warehouse_id, product_id', 'operation_id, deltas, version'])
    arrow(d, 105, 235, 185, 210)
    arrow(d, 385, 235, 305, 210)
    d.add(String(110, 214, '1 : many', fontSize=8, fillColor=INK))
    d.add(String(344, 214, '1 : many', fontSize=8, fillColor=INK))
    arrow(d, 245, 125, 245, 90, '1 : many')
    arrow(d, 145, 46, 173, 46)
    d.add(String(147, 54, '1 : n', fontSize=7.5, fillColor=INK))
    arrow(d, 355, 150, 420, 90, '1 : many')
    combined = Drawing(DIAGRAM_WIDTH, 540)
    core = Group(*d.contents)
    core.translate(0, 240)
    combined.add(core)
    box(combined, 0, 180, 145, 50, 'INVENTORY (reference)', ['PK warehouse_id, product_id'])
    box(combined, 173, 180, 145, 50, 'PRODUCTS (reference)', ['PK product_id'])
    box(combined, 345, 180, 145, 50, 'WAREHOUSES (reference)', ['PK warehouse_id'])
    box(combined, 0, 45, 210, 85, 'STOCK ADJUSTMENTS',
        ['PK operation_id', 'FK warehouse_id, product_id', 'delta, reason, created_at'])
    box(combined, 280, 45, 210, 85, 'WAREHOUSE TRANSFERS',
        ['PK transfer_id, FK product_id', 'FK source and destination warehouse', 'quantity, status, dispatch / receipt times'])
    arrow(combined, 72, 180, 72, 130, '1 : many')
    arrow(combined, 245, 180, 300, 130, '1 : many')
    arrow(combined, 395, 180, 365, 130)
    arrow(combined, 450, 180, 440, 130)
    combined.add(String(348, 157, 'source', fontName='Helvetica', fontSize=7, fillColor=INK))
    combined.add(String(440, 151, 'destination', fontName='Helvetica', fontSize=7, fillColor=INK))
    combined.add(String(0, 15, 'Reference boxes repeat existing entities to keep all foreign-key relationships readable.', fontName='Helvetica', fontSize=8, fillColor=INK))
    return combined


def transfer_lifecycle():
    d = Drawing(DIAGRAM_WIDTH, 210)
    box(d, 0, 130, 140, 70, 'REQUESTED', ['Fixed transfer contents', 'Stock unchanged'])
    box(d, 175, 130, 140, 70, 'IN TRANSIT', ['Source minus quantity', 'Destination still pending'])
    box(d, 350, 130, 140, 70, 'RECEIVED', ['Verified physical receipt', 'Destination plus quantity'])
    box(d, 0, 10, 140, 65, 'CANCELLED', ['Before dispatch', 'No stock movement'])
    box(d, 175, 10, 140, 65, 'LOST', ['After dispatch', 'Source stays deducted'])
    arrow(d, 140, 162, 175, 162)
    arrow(d, 315, 162, 350, 162)
    d.add(String(143, 176, 'dispatch', fontName='Helvetica', fontSize=7.5, fillColor=INK))
    d.add(String(318, 176, 'receive', fontName='Helvetica', fontSize=7.5, fillColor=INK))
    arrow(d, 70, 130, 70, 75, 'cancel')
    arrow(d, 245, 130, 245, 75, 'lose')
    return d


def markup(text):
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`([^`]+)`', r'<font name="Courier">\1</font>', text)
    text = re.sub(r'(https://\S+)', r'<link href="\1" color="#245780">\1</link>', text)
    return text


def footer(canvas, document):
    canvas.setFont('Helvetica' if COMPACT else 'TNR', 8 if COMPACT else 12)
    canvas.setFillColor(colors.black)
    canvas.drawRightString(A4[0]-MARGIN, A4[1]-36, str(document.page))
    if COMPACT:
        canvas.drawString(MARGIN, A4[1]-36, 'Database Design | Problem 2 | Verified 5 November 2026')


lines = (ROOT / 'REPORT.md').read_text(encoding='utf-8').splitlines()
story = []
i = 0
figure = 0
table_number = 0
while i < len(lines):
    line = lines[i]
    if not line.strip():
        i += 1
        continue
    if line.startswith('# '):
        story.append(Paragraph(markup(line[2:]), styles['ReportTitle']))
    elif line.startswith('## '):
        heading = line[3:]
        if heading.startswith(('Appendix ', '12 Implementation')):
            story.append(PageBreak())
        heading = re.sub(r'^(\d+) ', r'\1  ', heading)
        story.append(Paragraph(markup(heading), styles['ReportHeading']))
    elif line.startswith('```'):
        language = line[3:]
        block = []
        i += 1
        while i < len(lines) and not lines[i].startswith('```'):
            block.append(lines[i])
            i += 1
        if language == 'mermaid':
            figure += 1
            graphic = {1: architecture, 2: erd, 3: transfer_lifecycle}[figure]()
            scale = WIDTH / graphic.width * (0.97 if figure == 2 else 1)
            graphic.scale(scale, scale)
            graphic.width *= scale
            graphic.height *= scale
            graphic.hAlign = 'LEFT'
            caption = {1: 'Implemented primary and read-replica architecture',
                       2: 'Schema relationships and supporting records',
                       3: 'Warehouse transfer lifecycle'}[figure]
            story.append(KeepTogether([Paragraph(f'Figure {figure}', styles['FigureNumber']), Paragraph(caption, styles['FigureCaption']), graphic, Spacer(1, 8)]))
        else:
            story.append(KeepTogether([Preformatted('\n'.join(block), styles['ReportCode'])]))
    elif line.startswith('|'):
        rows = []
        while i < len(lines) and lines[i].startswith('|'):
            cells = [cell.strip() for cell in lines[i].strip('|').split('|')]
            if not all(re.fullmatch(r'[-: ]+', cell) for cell in cells):
                rows.append(cells)
            i += 1
        i -= 1
        if len(rows[0]) == 2:
            widths = [WIDTH*.29, WIDTH*.71]
        elif rows[0][0] == 'Step':
            widths = [WIDTH*.1, WIDTH*.4, WIDTH*.5]
        elif rows[0][0] == 'Suite':
            widths = [WIDTH*.56, WIDTH*.24, WIDTH*.20]
        elif rows[0][0] == 'Check':
            widths = [WIDTH*.12, WIDTH*.73, WIDTH*.15]
        else:
            widths = [WIDTH*.24, WIDTH*.31, WIDTH*.45]
        content = [[Paragraph(markup(cell), styles['ReportHeader'] if ri == 0 else styles['ReportCell']) for cell in row] for ri, row in enumerate(rows)]
        table = Table(content, colWidths=widths, repeatRows=1, hAlign='CENTER')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.white),
            ('GRID', (0,0), (-1,-1), .5, colors.black),
            ('LINEABOVE', (0,0), (-1,0), .75, colors.black),
            ('LINEBELOW', (0,-1), (-1,-1), .75, colors.black),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 4),
            ('RIGHTPADDING', (0,0), (-1,-1), 4),
            ('TOPPADDING', (0,0), (-1,-1), 3.5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3.5)
        ]))
        if rows[0][0] == 'Check' and not COMPACT:
            table.setStyle(TableStyle([
                ('TOPPADDING', (0,0), (-1,-1), 1.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 1.5)
            ]))
        table_number += 1
        table_title = {'Table': 'Database tables and keys',
                       'Failure': 'Failure scenarios and recovery behavior',
                       'Step': 'Competing transactions for the last item',
                       'Suite': 'Observed test-suite results',
                       'Check': 'Individual verification results'}.get(rows[0][0], 'Design decisions and measured results')
        story.extend([Paragraph(f'Table {table_number}', styles['FigureNumber']), Paragraph(table_title, styles['FigureCaption'])])
        story.extend([table, Spacer(1, 10)])
    else:
        paragraph = [line]
        while i+1 < len(lines) and lines[i+1].strip() and not lines[i+1].startswith(('#', '|', '```')) and not re.match(r'^\d+\. ', lines[i+1]):
            i += 1
            paragraph.append(lines[i])
        paragraph_style = styles['ReportReference'] if re.match(r'^\[\d+\]', line) else styles['ReportBody']
        story.append(Paragraph(markup(' '.join(paragraph)), paragraph_style))
    i += 1

class ReportDocument(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == 'ReportHeading':
            heading = flowable.getPlainText()
            self.canv.bookmarkPage(heading)
            self.canv.addOutlineEntry(heading, heading, level=0)


output = ROOT / ('Inventory_Management_Report.pdf' if COMPACT else 'Inventory_Management_Report_Styled.pdf')
document = ReportDocument(
    str(output), pagesize=A4,
    rightMargin=MARGIN, leftMargin=MARGIN, topMargin=72 if not COMPACT else 58, bottomMargin=MARGIN,
    title='Inventory Management Database Design', author='',
    subject='Database design, measured capacity, synchronous replication and tested recovery'
)
document.addPageTemplates(PageTemplate(id='Body', frames=Frame(MARGIN, MARGIN, WIDTH, A4[1]-MARGIN-document.topMargin, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0), onPage=footer))
document.build(story)
print(output)

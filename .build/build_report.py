import html
import re
import struct
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, Preformatted, KeepTogether
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).resolve().parents[1]
INK = colors.HexColor('#17212D')
PALE = colors.HexColor('#EDF2F6')
GRID = colors.HexColor('#D9D9D9')
WIDTH = A4[0] - 144
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
    d = Drawing(DIAGRAM_WIDTH, 255)
    box(d, 150, 215, 190, 35, 'Customers and operators')
    box(d, 150, 158, 190, 40, 'Inventory API and connection pool')
    box(d, 160, 93, 170, 45, 'PostgreSQL primary', ['Stock, reservations, event outbox'])
    box(d, 0, 94, 130, 44, 'Expiry worker', ['Short write transactions'])
    box(d, 365, 157, 125, 42, 'Cache and read replicas', ['Advisory browsing'])
    box(d, 0, 10, 145, 48, 'Synchronous standby', ['Durable failover candidate'])
    box(d, 175, 10, 145, 48, 'Outbox publisher', ['At least once delivery'])
    box(d, 350, 10, 140, 48, 'Broker and consumers', ['Cache and integrations'])
    arrow(d, 245, 215, 245, 198)
    arrow(d, 245, 158, 245, 138)
    arrow(d, 130, 116, 160, 116)
    arrow(d, 340, 179, 365, 179)
    arrow(d, 330, 118, 426, 157)
    arrow(d, 191, 93, 83, 58)
    arrow(d, 245, 93, 245, 58)
    arrow(d, 320, 34, 350, 34)
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
    return d


def markup(text):
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`([^`]+)`', r'<font name="Courier">\1</font>', text)
    text = re.sub(r'(https://\S+)', r'<link href="\1" color="#245780">\1</link>', text)
    return text


def footer(canvas, document):
    canvas.setFont('TNR', 12)
    canvas.setFillColor(colors.black)
    canvas.drawRightString(A4[0]-72, A4[1]-36, str(document.page))


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
            graphic = architecture() if figure == 1 else erd()
            scale = WIDTH / graphic.width * (0.97 if figure == 2 else 1)
            graphic.scale(scale, scale)
            graphic.width *= scale
            graphic.height *= scale
            graphic.hAlign = 'LEFT'
            caption = 'Inventory service architecture' if figure == 1 else 'Core inventory and reservation relationships'
            story.append(KeepTogether([Paragraph(f'Figure {figure}', styles['FigureNumber']), Paragraph(caption, styles['FigureCaption']), graphic, Spacer(1, 8)]))
        else:
            story.append(Preformatted('\n'.join(block), styles['ReportCode']))
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
        table_number += 1
        table_title = {1: 'Database tables and keys', 2: 'Failure scenarios and recovery behavior', 3: 'Competing transactions for the last item'}[table_number]
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

document = BaseDocTemplate(
    str(ROOT / 'Inventory_Management_Report_Styled.pdf'), pagesize=A4,
    rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=72,
    title='Inventory Management Database Design', author='',
    subject='Problem 2 Database Design Assignment'
)
document.addPageTemplates(PageTemplate(id='Body', frames=Frame(72, 72, WIDTH, A4[1]-144, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0), onPage=footer))
document.build(story)
print(ROOT / 'Inventory_Management_Report_Styled.pdf')

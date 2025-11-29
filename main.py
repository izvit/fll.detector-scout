from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QSizePolicy, QHBoxLayout, QToolButton
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPixmap, QPolygonF, QIcon, QBrush
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import Qt, QPointF, Signal, QObject, Slot, QRectF, QSize, QTimer
import threading
import re
import serial
import sys
import random


class GridCanvas(QWidget):
    def __init__(self, parent=None, cell_size: int = 70):
        super().__init__(parent)
        self.cell_size = cell_size
        self.grid_color = QColor(200, 200, 200)
        self.background_color = QColor(255, 255, 255)
        self.setMinimumSize(400, 300)
        # Pins to render: list of (row_letter, col_number)
        self.pins = []
        # Rectangles drawn by user: list of QRectF in widget coordinates
        self.rectangles: list[QRectF] = []
        self.drawing_mode = False
        self._drag_start = None
        self._current_rect = None
        self.setMouseTracking(True)
        # prepare striped brush for rectangles (red diagonal stripe)
        try:
            pattern_size = 8
            pix = QPixmap(pattern_size, pattern_size)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            pen = QPen(QColor(200, 30, 30))
            pen.setWidth(1)
            p.setPen(pen)
            # draw a diagonal line across the tile
            p.drawLine(0, pattern_size - 1, pattern_size - 1, 0)
            p.end()
            self._stripe_brush = QBrush(p)
        except Exception:
            self._stripe_brush = QBrush(QColor(200, 30, 30, 80))

        # lighter stripe brush for in-progress rectangle (more transparent)
        try:
            pix2 = QPixmap(pattern_size, pattern_size)
            pix2.fill(Qt.transparent)
            p2 = QPainter(pix2)
            pen2 = QPen(QColor(200, 30, 30, 120))
            pen2.setWidth(1)
            p2.setPen(pen2)
            p2.drawLine(0, pattern_size - 1, pattern_size - 1, 0)
            p2.end()
            self._stripe_brush_light = QBrush(pix2)
        except Exception:
            self._stripe_brush_light = QBrush(QColor(200, 30, 30, 40))

        # quadcopter SVG renderer (optional) and animation timer
        try:
            self._quad_renderer = QSvgRenderer("assets/quadcopter.svg")
        except Exception:
            self._quad_renderer = None

        # animated quadcopters: list of dicts {'pos': QPointF, 'vel': QPointF, 'size': float}
        self.quadcopters = []
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(50)  # ~20 FPS
        self._anim_timer.timeout.connect(self._tick)
        # visited grid cells (set of (row_idx, col_idx) using 1-based indices for content area)
        self.visited = set()
        # purple special drone and purple pindrops
        self.purple_drone = None  # dict like quad: {'pos','vel','size','battery'}
        self.purple_pins: list[tuple[str, int]] = []

    def add_pin(self, row_letter: str, col_num: int):
        """Add a pin if not already present. row_letter is like 'B' or 'AA', col_num is integer."""
        try:
            key = (row_letter.upper(), int(col_num))
        except Exception:
            return
        if key in self.pins:
            return
        self.pins.append(key)
        self.update()

    def set_drawing_mode(self, enabled: bool):
        self.drawing_mode = bool(enabled)
        if self.drawing_mode:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def clear_rectangles(self):
        """Remove all user-drawn rectangles and repaint."""
        if self.rectangles:
            self.rectangles.clear()
            self.update()

    # Mouse events for rectangle drawing
    def mousePressEvent(self, event):
        if self.drawing_mode and event.button() == Qt.LeftButton:
            self._drag_start = event.position() if hasattr(event, 'position') else event.localPos()
            self._current_rect = QRectF(self._drag_start.x(), self._drag_start.y(), 0, 0)
            self.update()
            return
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing_mode and self._drag_start is not None:
            pos = event.position() if hasattr(event, 'position') else event.localPos()
            x0 = self._drag_start.x()
            y0 = self._drag_start.y()
            x1 = pos.x()
            y1 = pos.y()
            left = min(x0, x1)
            top = min(y0, y1)
            w = abs(x1 - x0)
            h = abs(y1 - y0)
            self._current_rect = QRectF(left, top, w, h)
            self.update()
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drawing_mode and event.button() == Qt.LeftButton and self._current_rect is not None:
            # finalize rectangle
            self.rectangles.append(QRectF(self._current_rect))
            self._current_rect = None
            self._drag_start = None
            self.update()
            return
        return super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.background_color)

        pen = QPen(self.grid_color)
        pen.setWidth(1)
        painter.setPen(pen)

        w = self.width()
        h = self.height()
        cs = self.cell_size
        # Draw grid lines (including header lines at 0..)
        x = 0
        while x <= w:
            painter.drawLine(x, 0, x, h)
            x += cs

        y = 0
        while y <= h:
            painter.drawLine(0, y, w, y)
            y += cs

        # Header area: first row (top) and first column (left) reserved for labels
        # Column numbers in the top row (exclude the top-left corner at x=0)
        painter.setPen(QColor(120, 120, 120))
        font = painter.font()
        # Increase header font size slightly, proportional to cell size (cs)
        # Use a sensible minimum so it remains readable on small cells
        try:
            font.setPointSizeF(max(8.0, cs * 0.22))
        except Exception:
            try:
                font.setPointSize(max(8, int(cs * 0.22)))
            except Exception:
                pass
        font.setBold(True)
        painter.setFont(font)

        total_cols = max(1, w // cs)
        total_rows = max(1, h // cs)

        # Draw column numbers (1-based) in the top header row
        for i in range(1, total_cols):
            col_num = i
            cx = i * cs
            rect = (cx, 0, cs, cs)
            painter.drawText(rect[0], rect[1], rect[2], rect[3], Qt.AlignCenter, str(col_num))

        # Draw row letters (A, B, C...) in the left header column
        for j in range(1, total_rows):
            row_idx = j
            letter = self._index_to_letters(row_idx)
            cy = j * cs
            rect = (0, cy, cs, cs)
            painter.drawText(rect[0], rect[1], rect[2], rect[3], Qt.AlignCenter, letter)

        # Draw visited markers (light green squares) in the content area
        try:
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(180, 255, 180, 140))
            for (r_idx, c_idx) in self.visited:
                # ensure indices are within current grid bounds
                if r_idx >= 1 and c_idx >= 1 and c_idx < total_cols and r_idx < total_rows:
                    vx = c_idx * cs
                    vy = r_idx * cs
                    painter.drawRect(vx, vy, cs, cs)
            painter.restore()
        except Exception:
            pass

        # Draw pins on top of grid
        for (r_letter, c_num) in self.pins:
            try:
                r = self._letters_to_index(r_letter)
                c = int(c_num)
            except Exception:
                continue
            # top-left of cell
            cell_x = c * cs
            cell_y = r * cs
            cx = cell_x + cs / 2
            cy = cell_y + cs / 2

            # Draw a simple map pin: circle head and triangular tail
            painter.save()
            pin_color = QColor(220, 50, 50)
            painter.setPen(QPen(pin_color.darker(110)))
            painter.setBrush(pin_color)

            head_radius = cs * 0.18
            # head center slightly above center
            head_cx = cx
            head_cy = cy - head_radius * 0.2
            painter.drawEllipse(int(head_cx - head_radius), int(head_cy - head_radius), int(head_radius * 2), int(head_radius * 2))

            # tail - small triangle pointing down
            # Slightly smaller tail: reduce height and top width
            tail_height = cs * 0.16
            p1 = QPointF(head_cx - head_radius * 0.85, head_cy + head_radius * 0.3)
            p2 = QPointF(head_cx + head_radius * 0.85, head_cy + head_radius * 0.3)
            p3 = QPointF(head_cx, head_cy + head_radius * 0.4 + tail_height)
            poly = QPolygonF([p1, p2, p3])
            painter.drawPolygon(poly)

            # small white center dot
            painter.setBrush(QColor(255, 255, 255))
            inner_r = head_radius * 0.45
            painter.drawEllipse(int(head_cx - inner_r), int(head_cy - inner_r), int(inner_r * 2), int(inner_r * 2))

            painter.restore()

        # Draw purple pindrops (distinct color)
        for (r_letter, c_num) in getattr(self, 'purple_pins', []):
            try:
                r = self._letters_to_index(r_letter)
                c = int(c_num)
            except Exception:
                continue
            cell_x = c * cs
            cell_y = r * cs
            cx = cell_x + cs / 2
            cy = cell_y + cs / 2
            painter.save()
            pcol = QColor(155, 50, 200)
            painter.setPen(QPen(pcol.darker(110)))
            painter.setBrush(pcol)
            pr = cs * 0.16
            painter.drawEllipse(int(cx - pr), int(cy - pr), int(pr * 2), int(pr * 2))
            # small tail
            t1 = QPointF(cx - pr * 0.7, cy + pr * 0.25)
            t2 = QPointF(cx + pr * 0.7, cy + pr * 0.25)
            t3 = QPointF(cx, cy + pr * 0.9)
            poly2 = QPolygonF([t1, t2, t3])
            painter.drawPolygon(poly2)
            painter.restore()

        # Draw finalized rectangles
        # Draw finalized rectangles (red striped)
        rect_pen = QPen(QColor(160, 20, 20))
        rect_pen.setWidth(2)
        painter.setPen(rect_pen)
        painter.setBrush(self._stripe_brush)
        for r in self.rectangles:
            painter.drawRect(r)

        # Draw quadcopters on top of everything (so they appear above pins/rects)
        for q in self.quadcopters:
            pos = q.get('pos')
            size = q.get('size', self.cell_size * 0.9)
            if pos is None:
                continue
            try:
                if self._quad_renderer:
                    rect = QRectF(pos.x() - size / 2.0, pos.y() - size / 2.0, size, size)
                    self._quad_renderer.render(painter, rect)
                else:
                    painter.save()
                    painter.setPen(QPen(QColor(60, 140, 200).darker(110)))
                    painter.setBrush(QColor(80, 160, 240, 220))
                    painter.drawEllipse(QRectF(pos.x() - size / 2.0, pos.y() - size / 2.0, size, size))
                    painter.restore()
                # Draw small battery indicator at top-left of quad (visual only)
                try:
                    bat = q.get('battery', None)
                    if bat is not None:
                        # small bar inside/near quad
                        bar_w = max(12, int(size * 0.28))
                        bar_h = max(6, int(size * 0.12))
                        tl_x = pos.x() - size / 2.0 + 4
                        tl_y = pos.y() - size / 2.0 + 4
                        # outer border
                        painter.save()
                        border_pen = QPen(QColor(30, 30, 30))
                        border_pen.setWidth(1)
                        painter.setPen(border_pen)
                        painter.setBrush(QColor(40, 40, 40, 200))
                        painter.drawRect(tl_x, tl_y, bar_w, bar_h)
                        # cap on the right
                        cap_w = max(2, int(bar_w * 0.08))
                        cap_rect = QRectF(tl_x + bar_w, tl_y + bar_h * 0.18, cap_w, bar_h * 0.64)
                        painter.drawRect(cap_rect)
                        # fill according to battery level
                        pct = max(0.0, min(100.0, float(bat))) / 100.0
                        inner_w = max(1, int((bar_w - 2) * pct))
                        if pct > 0.6:
                            fill_col = QColor(120, 220, 120)
                        elif pct > 0.3:
                            fill_col = QColor(240, 220, 80)
                        else:
                            fill_col = QColor(220, 80, 80)
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(fill_col)
                        painter.drawRect(tl_x + 1, tl_y + 1, inner_w, bar_h - 2)
                        painter.restore()
                except Exception:
                    pass
            except Exception:
                # swallow drawing errors for robustness
                continue
        # Draw the special purple drone (if present) so it's clearly visible
        pd = getattr(self, 'purple_drone', None)
        if pd is not None:
            try:
                pos = pd.get('pos')
                size = pd.get('size', self.cell_size * 0.9)
                if pos is not None:
                    # draw a distinct purple ellipse for the special drone
                    # Prefer rendering a tinted SVG for the purple drone if an SVG renderer is available.
                    drawn = False
                    try:
                        if getattr(self, '_quad_renderer', None) is not None:
                            # render the SVG into a temporary pixmap at the desired size
                            pix = QPixmap(int(size), int(size))
                            pix.fill(Qt.transparent)
                            ptmp = QPainter(pix)
                            try:
                                # render the source SVG (quadcopter) into the pixmap
                                self._quad_renderer.render(ptmp, QRectF(0, 0, size, size))
                                # tint it using SourceIn composition so the shape keeps its alpha
                                ptmp.setCompositionMode(QPainter.CompositionMode_SourceIn)
                                tint = QColor(155, 50, 200, 220)
                                ptmp.fillRect(QRectF(0, 0, size, size), tint)
                                ptmp.end()
                                # draw the tinted pixmap centered at the drone position
                                painter.drawPixmap(int(pos.x() - size / 2.0), int(pos.y() - size / 2.0), pix)
                                drawn = True
                            finally:
                                if ptmp.isActive():
                                    ptmp.end()
                    except Exception:
                        drawn = False

                    if not drawn:
                        try:
                            painter.save()
                            painter.setPen(QPen(QColor(100, 30, 120).darker(110)))
                            painter.setBrush(QColor(155, 50, 200, 220))
                            painter.drawEllipse(QRectF(pos.x() - size / 2.0, pos.y() - size / 2.0, size, size))
                            painter.restore()
                        except Exception:
                            pass

                    # small battery indicator near the purple drone (visual only)
                    try:
                        bat = pd.get('battery', None)
                        if bat is not None:
                            bar_w = max(12, int(size * 0.28))
                            bar_h = max(6, int(size * 0.12))
                            tl_x = pos.x() - size / 2.0 + 4
                            tl_y = pos.y() - size / 2.0 + 4
                            painter.save()
                            border_pen = QPen(QColor(30, 30, 30))
                            border_pen.setWidth(1)
                            painter.setPen(border_pen)
                            painter.setBrush(QColor(40, 40, 40, 200))
                            painter.drawRect(tl_x, tl_y, bar_w, bar_h)
                            cap_w = max(2, int(bar_w * 0.08))
                            cap_rect = QRectF(tl_x + bar_w, tl_y + bar_h * 0.18, cap_w, bar_h * 0.64)
                            painter.drawRect(cap_rect)
                            pct = max(0.0, min(100.0, float(bat))) / 100.0
                            inner_w = max(1, int((bar_w - 2) * pct))
                            if pct > 0.6:
                                fill_col = QColor(160, 120, 240)
                            elif pct > 0.3:
                                fill_col = QColor(220, 180, 120)
                            else:
                                fill_col = QColor(200, 80, 120)
                            painter.setPen(Qt.NoPen)
                            painter.setBrush(fill_col)
                            painter.drawRect(tl_x + 1, tl_y + 1, inner_w, bar_h - 2)
                            painter.restore()
                    except Exception:
                        pass
            except Exception:
                pass
        # Draw current rectangle (dashed outline, lighter striped fill)
        if self._current_rect is not None:
            dash_pen = QPen(QColor(180, 30, 30))
            dash_pen.setStyle(Qt.DashLine)
            dash_pen.setWidth(2)
            painter.setPen(dash_pen)
            painter.setBrush(self._stripe_brush_light)
            painter.drawRect(self._current_rect)

    def _index_to_letters(self, n: int) -> str:
        # Convert 1-based index to Excel-style column letters: 1->A, 27->AA
        result = []
        while n > 0:
            n, rem = divmod(n - 1, 26)
            result.append(chr(ord('A') + rem))
        return ''.join(reversed(result))

    # --- quadcopter animation control ---
    def start_quads(self, count: int = 3):
        """Start (or restart) `count` quadcopters moving inside the drawable grid area."""
        try:
            self.stop_quads()
            w = max(1, self.width())
            h = max(1, self.height())
            cs = self.cell_size
            area_x0 = cs
            area_y0 = cs
            area_x1 = max(area_x0 + 10, w - cs)
            area_y1 = max(area_y0 + 10, h - cs)
            # place quadcopters avoiding existing rectangles
            for _ in range(max(0, int(count))):
                size = cs * 0.9
                placed = False
                attempts = 0
                while not placed and attempts < 40:
                    attempts += 1
                    x = random.uniform(area_x0 + 10, max(area_x0 + 11, area_x1 - 10))
                    y = random.uniform(area_y0 + 10, max(area_y0 + 11, area_y1 - 10))
                    bbox = QRectF(x - size / 2.0, y - size / 2.0, size, size)
                    if not self._bbox_intersects_rects(bbox):
                        vx = random.uniform(-2.2, 2.2) * 1.3
                        vy = random.uniform(-2.2, 2.2) * 1.3
                        # battery percentage (visual only)
                        bat = random.uniform(60.0, 100.0)
                        self.quadcopters.append({'pos': QPointF(x, y), 'vel': QPointF(vx, vy), 'size': size, 'battery': bat})
                        placed = True
                # if we failed to find non-overlapping position, accept last one (fallback)
                if not placed:
                    x = random.uniform(area_x0 + 10, max(area_x0 + 11, area_x1 - 10))
                    y = random.uniform(area_y0 + 10, max(area_y0 + 11, area_y1 - 10))
                    vx = random.uniform(-2.2, 2.2) * 1.3
                    vy = random.uniform(-2.2, 2.2) * 1.3
                    bat = random.uniform(60.0, 100.0)
                    self.quadcopters.append({'pos': QPointF(x, y), 'vel': QPointF(vx, vy), 'size': size, 'battery': bat})

            # also create a single purple drone (special)
            try:
                # place purple drone avoiding rectangles too
                p_size = cs * 0.9
                placed = False
                attempts = 0
                while not placed and attempts < 60:
                    attempts += 1
                    px = random.uniform(area_x0 + 10, max(area_x0 + 11, area_x1 - 10))
                    py = random.uniform(area_y0 + 10, max(area_y0 + 11, area_y1 - 10))
                    pbbox = QRectF(px - p_size / 2.0, py - p_size / 2.0, p_size, p_size)
                    if not self._bbox_intersects_rects(pbbox):
                        pvx = random.uniform(-2.2, 2.2) * 1.3
                        pvy = random.uniform(-2.2, 2.2) * 1.3
                        pbat = random.uniform(80.0, 100.0)
                        self.purple_drone = {'pos': QPointF(px, py), 'vel': QPointF(pvx, pvy), 'size': p_size, 'battery': pbat}
                        placed = True
                if not placed:
                    # fallback
                    self.purple_drone = {'pos': QPointF(area_x0 + 20, area_y0 + 20), 'vel': QPointF(1.3, -1.3), 'size': p_size, 'battery': 90.0}
            except Exception:
                self.purple_drone = None

            if self.quadcopters:
                self._anim_timer.start()
                self.update()
        except Exception:
            pass

    def stop_quads(self):
        try:
            if self._anim_timer.isActive():
                self._anim_timer.stop()
        except Exception:
            pass
        try:
            if self.quadcopters:
                self.quadcopters.clear()
                self.update()
        except Exception:
            pass
        # clear purple drone and purple pins
        try:
            self.purple_drone = None
            if hasattr(self, 'purple_pins') and self.purple_pins:
                self.purple_pins.clear()
        except Exception:
            pass

    def _tick(self):
        # advance quadcopters positions and bounce off content-area edges
        if not self.quadcopters:
            return
        try:
            w = max(1, self.width())
            h = max(1, self.height())
            cs = self.cell_size
            area_x0 = cs
            area_y0 = cs
            area_x1 = max(area_x0 + 10, w - cs)
            area_y1 = max(area_y0 + 10, h - cs)

            for q in self.quadcopters:
                pos = q['pos']
                vel = q['vel']
                size = q.get('size', cs * 0.9)
                # propose new positions separately for X and Y to avoid rectangle zones
                half = size / 2.0

                # check X movement
                next_x_pos = QPointF(pos.x() + vel.x(), pos.y())
                bbox_x = QRectF(next_x_pos.x() - half, next_x_pos.y() - half, size, size)
                if self._bbox_intersects_rects(bbox_x):
                    # reverse X velocity to avoid entering rect
                    vel.setX(-vel.x())
                else:
                    pos.setX(next_x_pos.x())

                # check Y movement
                next_y_pos = QPointF(pos.x(), pos.y() + vel.y())
                bbox_y = QRectF(next_y_pos.x() - half, next_y_pos.y() - half, size, size)
                if self._bbox_intersects_rects(bbox_y):
                    vel.setY(-vel.y())
                else:
                    pos.setY(next_y_pos.y())

                # if still overlapping after movement (rare), attempt small nudge or randomize
                bbox_now = QRectF(pos.x() - half, pos.y() - half, size, size)
                if self._bbox_intersects_rects(bbox_now):
                    # try a few nudges along reversed velocity
                    nudged = False
                    for _ in range(6):
                        pos.setX(pos.x() + vel.x())
                        pos.setY(pos.y() + vel.y())
                        bbox_now = QRectF(pos.x() - half, pos.y() - half, size, size)
                        if not self._bbox_intersects_rects(bbox_now):
                            nudged = True
                            break
                    if not nudged:
                        # randomize velocity to try escape (30% faster)
                        vel.setX(random.uniform(-2.2, 2.2) * 1.3)
                        vel.setY(random.uniform(-2.2, 2.2) * 1.3)

                # maintain bounds (bounce)
                if pos.x() - half < area_x0:
                    pos.setX(area_x0 + half)
                    # reflect X velocity and add small random perturbation
                    new_vx = -vel.x() + random.uniform(-0.6, 0.6)
                    if abs(new_vx) < 0.15:
                        new_vx = 0.15 * (1 if new_vx >= 0 else -1)
                    vel.setX(new_vx)
                if pos.x() + half > area_x1:
                    pos.setX(area_x1 - half)
                    new_vx = -vel.x() + random.uniform(-0.6, 0.6)
                    if abs(new_vx) < 0.15:
                        new_vx = 0.15 * (1 if new_vx >= 0 else -1)
                    vel.setX(new_vx)
                if pos.y() - half < area_y0:
                    pos.setY(area_y0 + half)
                    new_vy = -vel.y() + random.uniform(-0.6, 0.6)
                    if abs(new_vy) < 0.15:
                        new_vy = 0.15 * (1 if new_vy >= 0 else -1)
                    vel.setY(new_vy)

                # slowly drain battery for visual effect (does not affect behavior)
                try:
                    if 'battery' in q:
                        q['battery'] = max(0.0, q.get('battery', 100.0) - 0.02)
                except Exception:
                    pass
                if pos.y() + half > area_y1:
                    pos.setY(area_y1 - half)
                    new_vy = -vel.y() + random.uniform(-0.6, 0.6)
                    if abs(new_vy) < 0.15:
                        new_vy = 0.15 * (1 if new_vy >= 0 else -1)
                    vel.setY(new_vy)

            # request repaint on the GUI thread
            self.update()
        except Exception:
            pass

        # After movement, mark visited grid cells for each quad
        try:
            total_cols = max(1, w // cs)
            total_rows = max(1, h // cs)
            for q in self.quadcopters:
                pos = q.get('pos')
                if pos is None:
                    continue
                col_idx = int(pos.x() // cs)
                row_idx = int(pos.y() // cs)
                # only consider content area (skip header row/col at index 0)
                if row_idx >= 1 and col_idx >= 1 and col_idx < total_cols and row_idx < total_rows:
                    self.visited.add((row_idx, col_idx))
        except Exception:
            pass

        # move purple drone (if present) and mark visited
        try:
            pd = self.purple_drone
            if pd is not None:
                pos = pd['pos']
                vel = pd['vel']
                size = pd.get('size', cs * 0.9)
                half = size / 2.0

                # X
                next_x = QPointF(pos.x() + vel.x(), pos.y())
                bbox_x = QRectF(next_x.x() - half, next_x.y() - half, size, size)
                if self._bbox_intersects_rects(bbox_x):
                    vel.setX(-vel.x())
                else:
                    pos.setX(next_x.x())

                # Y
                next_y = QPointF(pos.x(), pos.y() + vel.y())
                bbox_y = QRectF(next_y.x() - half, next_y.y() - half, size, size)
                if self._bbox_intersects_rects(bbox_y):
                    vel.setY(-vel.y())
                else:
                    pos.setY(next_y.y())

                # bounds bounce with small perturbation
                if pos.x() - half < area_x0:
                    pos.setX(area_x0 + half)
                    vel.setX(-vel.x() + random.uniform(-0.6, 0.6))
                if pos.x() + half > area_x1:
                    pos.setX(area_x1 - half)
                    vel.setX(-vel.x() + random.uniform(-0.6, 0.6))
                if pos.y() - half < area_y0:
                    pos.setY(area_y0 + half)
                    vel.setY(-vel.y() + random.uniform(-0.6, 0.6))
                if pos.y() + half > area_y1:
                    pos.setY(area_y1 - half)
                    vel.setY(-vel.y() + random.uniform(-0.6, 0.6))

                # slight battery drain
                try:
                    pd['battery'] = max(0.0, pd.get('battery', 100.0) - 0.03)
                except Exception:
                    pass

                # mark visited cell
                try:
                    col_idx = int(pos.x() // cs)
                    row_idx = int(pos.y() // cs)
                    if row_idx >= 1 and col_idx >= 1 and col_idx < total_cols and row_idx < total_rows:
                        self.visited.add((row_idx, col_idx))
                except Exception:
                    pass
        except Exception:
            pass

    def _bbox_intersects_rects(self, bbox: QRectF) -> bool:
        """Return True if bbox intersects any user-drawn rectangle."""
        try:
            for r in self.rectangles:
                if bbox.intersects(r):
                    return True
        except Exception:
            return False
        return False

    def place_purple_pindrop(self):
        """Place a purple pindrop at the purple drone's current grid cell (if any)."""
        try:
            pd = self.purple_drone
            if pd is None:
                return
            pos = pd.get('pos')
            if pos is None:
                return
            cs = self.cell_size
            col_idx = int(pos.x() // cs)
            row_idx = int(pos.y() // cs)
            if row_idx < 1 or col_idx < 1:
                return
            # convert to letter index and 1-based column
            row_letter = self._index_to_letters(row_idx)
            col_num = int(col_idx)
            key = (row_letter.upper(), col_num)
            if key in getattr(self, 'purple_pins', []):
                return
            self.purple_pins.append(key)
            self.update()
        except Exception:
            pass

    def _letters_to_index(self, s: str) -> int:
        # Convert letters like 'A' or 'AA' to 1-based index
        s = s.upper().strip()
        val = 0
        for ch in s:
            if 'A' <= ch <= 'Z':
                val = val * 26 + (ord(ch) - ord('A') + 1)
        return val


class SerialReceiver(QObject):
    """Background serial reader that emits parsed pin coordinates.

    Expects ASCII lines like: 'B,9' or 'A1' or 'C 12'. Emits (row_letters, col_int) via `got_pin`.
    """
    got_pin = Signal(str, int)

    def __init__(self, port: str = "COM5", baud: int = 9600, timeout: float = 1.0):
        super().__init__()
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._thread = None
        self._stop = threading.Event()
        self._pattern = re.compile(r"^\s*([A-Za-z]+)\s*,?\s*(\d+)\s*$")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=2.0)

    def _run(self):
        try:
            ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        except Exception as e:
            # could not open serial port; just exit thread
            print(f"SerialReceiver: failed to open {self.port}: {e}")
            return

        with ser:
            while not self._stop.is_set():
                try:
                    raw = ser.readline()
                    if not raw:
                        continue
                    try:
                        line = raw.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        line = str(raw).strip()
                    if not line:
                        continue
                    m = self._pattern.match(line)
                    if not m:
                        continue
                    row = m.group(1).upper()
                    col = int(m.group(2))
                    try:
                        self.got_pin.emit(row, col)
                    except Exception:
                        pass
                except Exception:
                    # ignore read errors and continue
                    continue


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DetectorScout — Just Team Pi #57294")
        self.canvas = GridCanvas(self)

        # Serial receiver (Bluetooth SPP mapped to a COM port). Update `port` as needed.
        self.serial_receiver = SerialReceiver(port="COM8", baud=9600)
        self.serial_receiver.got_pin.connect(self._on_serial_pin)
        # Do NOT start automatically; controlled by Play button

        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)

        # Header widget above the canvas: left logo + centered title
        header_widget = QWidget()
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_widget.setLayout(header_layout)
        header_widget.setFixedHeight(48)
        header_widget.setStyleSheet("background-color: #9B30FF;")

        # Load and render SVG logo into a pixmap
        logo_label = QLabel()
        logo_size = 36
        try:
            renderer = QSvgRenderer("assets/quadcopter.svg")
            pix = QPixmap(logo_size, logo_size)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            renderer.render(p)
            p.end()
            logo_label.setPixmap(pix)
        except Exception:
            # fallback: empty placeholder
            logo_label.setText("")

        logo_label.setFixedSize(logo_size, logo_size)

        title = QLabel("Detector Scout")
        title.setAlignment(Qt.AlignCenter)
        header_font = QFont()
        header_font.setPointSize(18)
        header_font.setBold(True)
        title.setFont(header_font)
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        title.setFixedHeight(48)
        title.setStyleSheet("color: white;")

        # Play toggle button (start/stop serial receiver)
        play_btn = QToolButton()
        play_btn.setCheckable(True)
        play_btn.setToolTip("start search")
        play_btn.setText("▶")
        play_btn.setFixedSize(44, 44)
        play_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: white; font-size: 20px; font-weight: 600; }"
            "QToolButton:checked { background-color: rgba(0,0,0,0.18); border-radius: 4px; font-size: 14px; }"
        )
        def _update_play_text(checked: bool):
            play_btn.setText("❚❚" if checked else "▶")
        play_btn.toggled.connect(_update_play_text)
        play_btn.toggled.connect(self._on_play_toggled)

        # Rectangle drawing toggle button on the right
        rect_btn = QToolButton()
        rect_btn.setCheckable(True)
        rect_btn.setToolTip("Rectangle draw mode")
        icon_size = 20
        try:
            renderer2 = QSvgRenderer("assets/rect.svg")
            pix2 = QPixmap(icon_size, icon_size)
            pix2.fill(Qt.transparent)
            p2 = QPainter(pix2)
            renderer2.render(p2)
            p2.end()
            rect_btn.setIcon(QIcon(pix2))
            rect_btn.setIconSize(QSize(icon_size, icon_size))
        except Exception:
            rect_btn.setText("▭")

        rect_btn.toggled.connect(self.canvas.set_drawing_mode)
        rect_btn.setFixedSize(40, 40)
        # Visual feedback: darker background when toggled
        rect_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: white; }"
            "QToolButton:checked { background-color: rgba(0,0,0,0.18); border-radius: 4px; }"
        )

        # Pindrop button (icon + fallback)
        pin_btn = QToolButton()
        pin_btn.setToolTip("Pindrop")
        pin_btn.setFixedSize(40, 40)
        try:
            renderer_pin = QSvgRenderer("assets/pindrop.svg")
            pix_pin = QPixmap(20, 20)
            pix_pin.fill(Qt.transparent)
            p_pin = QPainter(pix_pin)
            renderer_pin.render(p_pin)
            p_pin.end()
            pin_btn.setIcon(QIcon(pix_pin))
            pin_btn.setIconSize(QSize(20, 20))
        except Exception:
            pin_btn.setText("📍")

        pin_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: white; }"
            "QToolButton:hover { background-color: rgba(255,255,255,0.06); border-radius: 4px; }"
        )
        # when pressed, have the purple drone drop a pindrop
        try:
            pin_btn.clicked.connect(lambda: self.canvas.place_purple_pindrop())
        except Exception:
            pass

        # Clear rectangles button
        clear_btn = QToolButton()
        clear_btn.setToolTip("Clear rectangles")
        clear_btn.setText("Clear")
        clear_btn.setFixedSize(60, 40)
        clear_btn.clicked.connect(self.canvas.clear_rectangles)

        header_layout.addWidget(logo_label)
        header_layout.addWidget(title)
        header_layout.addWidget(play_btn)
        header_layout.addWidget(rect_btn)
        header_layout.addWidget(pin_btn)
        header_layout.addWidget(clear_btn)

        main_layout.addWidget(header_widget)
        main_layout.addWidget(self.canvas)

        self.setCentralWidget(main_widget)

    def closeEvent(self, event):
        # stop background watcher cleanly
        # stop serial receiver
        try:
            # stop quadcopter animation
            try:
                self.canvas.stop_quads()
            except Exception:
                pass

            self.serial_receiver.stop()
        except Exception:
            pass
        super().closeEvent(event)

    @Slot(bool)
    def _on_play_toggled(self, checked: bool):
        # Start or stop the serial receiver depending on toggle state.
        try:
            if checked:
                self.serial_receiver.start()
                # also start the quadcopter simulation (3 quads)
                try:
                    self.canvas.start_quads(3)
                except Exception:
                    pass
            else:
                # stop simulation first
                try:
                    self.canvas.stop_quads()
                except Exception:
                    pass
                self.serial_receiver.stop()
        except Exception:
            pass

    @Slot(str, int)
    def _on_serial_pin(self, row: str, col: int):
        # Called on GUI thread when serial delivers a pin coordinate
        try:
            # Treat any serial message as a notification to place a purple pindrop
            # at the purple drone's current location (do not add a regular pin).
            try:
                self.canvas.place_purple_pindrop()
                # optional: brief debug print to console
                # print(f"Serial: placed purple pindrop for message {row},{col}")
            except Exception:
                pass
        except Exception:
            pass


def main(argv=None):
    app = QApplication(argv or sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

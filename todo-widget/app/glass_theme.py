"""
Frosted-Glass Card Theme for Desktop Widgets
============================================

A dark frosted-glass panel that shows the blurred wallpaper, a cyan glow round a rounded
border, pill buttons, letter-spaced uppercase labels and bright text with a soft dark halo.

This module keeps that look in one place so that every widget card matches it.

Educational Highlights:
- "Frosted glass" is a trick: the card is not really see-through. We load the wallpaper,
  resize it exactly as Windows does, blur and darken it, then paint the piece that sits
  behind the card. See `WallpaperGlass`.
- Qt cannot smooth the edge of a clipped picture, so the rounded card is filled with a
  "texture brush" (a picture used as paint) instead. See `WallpaperGlass.fill`.
- The glow is five see-through cyan outlines of shrinking width drawn on top of each
  other. See `paint_card`.
- Text is drawn ten times: eight dark copies one pixel away in every direction (the halo),
  a drop shadow, and finally the real text. See `draw_text`.
"""

from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import sys
from typing import NamedTuple, Optional

import paths
from PIL import Image, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QScreen,
    QTransform,
)

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import winreg

log = logging.getLogger(__name__)

# --- Colours (hex strings) ---
ACCENT = "#22D3EE"      # cyan: the glow, active pills, focus rings
EDGE = "#67E8F9"        # lighter cyan: the crisp border line
TINT = "#0B0D12"        # near-black laid over the blurred wallpaper
FLAT_FILL = "#101218"   # card colour when the wallpaper cannot be used
C_VALUE = "#F4F7FB"     # brightest text: values and task text
C_TEXT2 = "#D0D7E3"     # secondary text
C_LABEL = "#AEB8CC"     # section labels such as "CPU" or "TASKS"
C_FAINT = "#8A93A6"     # finished or disabled text
EMERALD = "#34D399"     # good / done
AMBER = "#F59E0B"       # warning
RED = "#EF4444"         # danger
RED_SOFT = "#FCA5A5"    # danger text on a dark background

# --- Sizes in pixels ---
GLOW = 8                # see-through margin round the card where the glow fades out
EDGE_PAD = GLOW + 14    # distance from the window edge to the card's content
CORNER_RADIUS = 14
HEADER_H = 18
GAP = 10
LABEL_H = 14
PILL_STRIP_H = 22       # height reserved for the row of pills at the top
PILL_H = 20             # height of one pill in that row
BUTTON_H = 26           # taller pills used as buttons
PILL_PAD = 20           # a pill is this much wider than its text

# Windows uses the Segoe UI family. On Linux the list is empty, which keeps Qt's default sans font.
FONT_FAMILIES = ["Segoe UI Variable", "Segoe UI"] if sys.platform == "win32" else []

# (pen width, alpha) for each glow outline, widest and faintest first.
GLOW_STROKES = ((11.0, 12), (8.0, 22), (5.5, 40), (3.5, 70), (2.0, 110))

# Brightness curve applied after blurring: it squeezes highlights so a bright wallpaper
# cannot wash out the text. Input 0..255 maps to roughly 0..117.
TONE_CURVE = [int(130 * (1 - math.exp(-level / 110.0))) for level in range(256)]
DESATURATION = 0.3      # how far the blurred wallpaper is pulled towards grey

DEFAULT_TINT_OPACITY = 0.5

# Windows wallpaper constants.
SPI_GETDESKWALLPAPER = 0x0073
STYLE_FILL = 10
STYLE_STRETCH = 2


# ----------------------------------------------------------------------------------------
# Fonts and text
# ----------------------------------------------------------------------------------------

def qt_font(size_pt: int, bold: bool = False, letter_spacing: float = 0.0, strike_out: bool = False) -> QFont:
    """Builds the widget font (Segoe UI Variable) at a point size."""
    font = QFont()
    if FONT_FAMILIES:
        font.setFamilies(FONT_FAMILIES)
    font.setPointSize(size_pt)
    font.setBold(bold)
    font.setStrikeOut(strike_out)
    if letter_spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    return font


def draw_cross(painter: QPainter, centre: QPointF, size: float, colour: QColor) -> None:
    """Paints an X from two lines, so no font has to contain a cross glyph (used on Linux)."""
    half = size / 4.0
    pen = QPen(colour, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.save()
    painter.setPen(pen)
    painter.drawLine(QPointF(centre.x() - half, centre.y() - half), QPointF(centre.x() + half, centre.y() + half))
    painter.drawLine(QPointF(centre.x() - half, centre.y() + half), QPointF(centre.x() + half, centre.y() - half))
    painter.restore()


def draw_check(painter: QPainter, centre: QPointF, size: float, colour: QColor) -> None:
    """Paints a tick mark from a path, so no font has to contain a check glyph (used on Linux)."""
    unit = size / 10.0
    path = QPainterPath()
    path.moveTo(centre.x() - 3.0 * unit, centre.y() + 0.2 * unit)
    path.lineTo(centre.x() - 0.8 * unit, centre.y() + 2.6 * unit)
    path.lineTo(centre.x() + 3.4 * unit, centre.y() - 2.6 * unit)
    pen = QPen(colour, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.save()
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


def label_font() -> QFont:
    """Font for uppercase section labels: 9 pt with the letters spread out by 1 px."""
    return qt_font(9, letter_spacing=1.0)


def pill_font() -> QFont:
    """Font for the text inside pills: 8 pt bold, slightly spread out."""
    return qt_font(8, bold=True, letter_spacing=0.8)


def centred_baseline(top: float, height: float, metrics: QFontMetrics) -> float:
    """Returns the text baseline that centres one line vertically inside a box."""
    return top + (height + metrics.ascent() - metrics.descent()) / 2


def draw_text(
    painter: QPainter,
    x: float,
    baseline: float,
    text: str,
    font: QFont,
    color: str | QColor,
    align_right: bool = False,
    max_width: Optional[float] = None,
) -> int:
    """Draws one line of haloed text and returns its width in pixels.

    `x` is the left edge of the text, or the right edge when `align_right` is True.
    Text wider than `max_width` is cut short with an ellipsis.
    """
    painter.setFont(font)
    metrics = QFontMetrics(font)
    if max_width is not None:
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(max_width))
    width = metrics.horizontalAdvance(text)
    left = int(x - width) if align_right else int(x)
    base = int(baseline)

    # Halo: eight dark copies, one pixel away in every direction.
    painter.setPen(QColor(0, 0, 0, 90))
    for offset_x, offset_y in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
        painter.drawText(left + offset_x, base + offset_y, text)
    # Drop shadow, slightly below and to the right.
    painter.setPen(QColor(0, 0, 0, 140))
    painter.drawText(left + 1, base + 2, text)
    # The real text on top.
    painter.setPen(QColor(color))
    painter.drawText(left, base, text)
    return width


def pill_width(text: str) -> float:
    """Returns how wide a pill needs to be to hold `text`."""
    return QFontMetrics(pill_font()).horizontalAdvance(text) + PILL_PAD


def draw_pill(
    painter: QPainter,
    rect: QRectF,
    text: str,
    active: bool = False,
    color: str = ACCENT,
    hovered: bool = False,
    enabled: bool = True,
) -> None:
    """Draws a rounded "pill" button with its text centred.

    active   filled with the tint colour (the selected tab, or a switch that is on)
    hovered  a little brighter, so the mouse gets feedback
    enabled  False draws it dimmed, for a button that has nothing to do right now
    """
    tint = QColor(color)
    radius = rect.height() / 2

    if active:
        fill = QColor(tint)
        fill.setAlpha(95 if hovered else 70)
    elif not enabled:
        fill = QColor(255, 255, 255, 8)
    else:
        fill = QColor(255, 255, 255, 30 if hovered else 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawRoundedRect(rect, radius, radius)

    border = QColor(tint)
    if active:
        border.setAlpha(220)
    elif not enabled:
        border.setAlpha(45)
    else:
        border.setAlpha(150 if hovered else 90)
    painter.setPen(QPen(border, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, radius, radius)

    font = pill_font()
    metrics = QFontMetrics(font)
    text_width = metrics.horizontalAdvance(text)
    baseline = centred_baseline(rect.top(), rect.height(), metrics)
    if active:
        text_color = "#FFFFFF"
    elif not enabled:
        text_color = C_FAINT
    else:
        text_color = C_TEXT2
    draw_text(painter, rect.center().x() - text_width / 2, baseline, text, font, text_color)


# ----------------------------------------------------------------------------------------
# The card itself
# ----------------------------------------------------------------------------------------

def card_body_rect(width: float, height: float) -> QRectF:
    """Returns the visible card inside a window of the given size (the rest is glow)."""
    return QRectF(GLOW + 0.5, GLOW + 0.5, width - 2 * GLOW - 1, height - 2 * GLOW - 1)


def card_path(width: float, height: float) -> QPainterPath:
    """Returns the rounded outline of the card as a path Qt can fill and stroke."""
    path = QPainterPath()
    path.addRoundedRect(card_body_rect(width, height), CORNER_RADIUS, CORNER_RADIUS)
    return path


def paint_card(
    painter: QPainter,
    path: QPainterPath,
    glass: Optional["WallpaperGlass"],
    window_origin: QPointF,
    tint_opacity: float,
) -> None:
    """Paints the card background: frosted wallpaper, dark tint, cyan glow and border.

    window_origin  where the window's top-left corner is on the desktop, so the right
                   piece of wallpaper is shown behind the card
    tint_opacity   0.2 (very see-through) to 1.0 (solid dark)
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if glass is None or not glass.fill(painter, path, window_origin):
        painter.fillPath(path, QColor(FLAT_FILL))

    tint = QColor(TINT)
    tint.setAlpha(int(255 * max(0.0, min(1.0, tint_opacity))))
    painter.fillPath(path, tint)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    for pen_width, alpha in GLOW_STROKES:
        glow = QColor(ACCENT)
        glow.setAlpha(alpha)
        painter.setPen(QPen(glow, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)

    edge = QColor(EDGE)
    edge.setAlpha(235)
    painter.setPen(QPen(edge, 1.3))
    painter.drawPath(path)
    painter.restore()


# ----------------------------------------------------------------------------------------
# How see-through the card is
# ----------------------------------------------------------------------------------------

def opacity_fraction(raw: object, default: float = DEFAULT_TINT_OPACITY) -> float:
    """Turns a saved opacity setting into a fraction between 0.2 and 1.0.

    The setting may be a fraction (0.35) or a percentage (35); both mean the same.
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if math.isnan(value):
        return default
    if value > 1.0:
        value /= 100.0
    return min(1.0, max(0.2, value))


# ----------------------------------------------------------------------------------------
# Shared Widget Background Config (Custom Image + Blur + Brightness + Saturation)
# ----------------------------------------------------------------------------------------

# One background setting shared by every Desktop Widgets app, so they all show the same picture.
WIDGET_BG_CONFIG_PATH = paths.widget_background_file()
# Older builds kept the same file in a different folder. It is only ever READ, and only when
# the new file does not exist yet; every save goes to WIDGET_BG_CONFIG_PATH.
# Linux has no legacy location, so the fallback is switched off there.
LEGACY_BG_CONFIG_PATH: Optional[str] = os.path.join(paths.appdata_dir(), "TomTodoList", "widget_background.json")
if paths.is_linux():
    LEGACY_BG_CONFIG_PATH = None

DEFAULT_BG_CONFIG = {
    "image_path": "",          # Empty or missing = fall back to Windows desktop wallpaper
    "blur_radius": 6.0,        # 0 = crisp image, 6 = subtle blur, 18 = heavy frosted glass
    "tint_opacity": 0.35,      # Dark overlay opacity (0.0 to 1.0)
    "brightness": 0.85,        # 1.0 = full image brightness, 0.5 = half brightness
    "saturation": 0.90,        # 1.0 = full colour, 0.0 = grayscale
    "fit_mode": "per_widget",  # "per_widget" (crop/fill each card) or "span_both" (span across both widgets)
}


class CustomBackgroundSettings(NamedTuple):
    """Custom background image and visual effect settings shared by both widgets."""

    image_path: str
    image_mtime: float
    config_mtime: float
    blur_radius: float
    tint_opacity: float
    brightness: float
    saturation: float
    fit_mode: str


def _source_file(config_path: Optional[str], legacy_path: Optional[str]) -> tuple[str, Optional[str]]:
    """Works out which background file to read.

    Returns (path_to_write, path_to_read_or_None). With no explicit path the shared file is
    used, falling back to the legacy file when the shared one does not exist yet. A caller
    that names its own `config_path` (a test, say) never touches the legacy location unless it
    also names a `legacy_path`.
    """
    if config_path is None:
        config_path = WIDGET_BG_CONFIG_PATH
        if legacy_path is None:
            legacy_path = LEGACY_BG_CONFIG_PATH
    if os.path.isfile(config_path):
        return config_path, config_path
    if legacy_path and os.path.isfile(legacy_path):
        return config_path, legacy_path
    return config_path, None


def load_widget_bg_config(
    config_path: Optional[str] = None, legacy_path: Optional[str] = None
) -> Optional[CustomBackgroundSettings]:
    """Reads the shared widget background config if an active custom image is set."""
    _, config_path = _source_file(config_path, legacy_path)
    if config_path is None:
        return None
    try:
        cfg_mtime = os.path.getmtime(config_path)
        with open(config_path, "r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            return None
        image_path = str(raw.get("image_path") or "").strip()
        if not image_path or not os.path.isfile(image_path):
            return None
        img_mtime = os.path.getmtime(image_path)
        blur_radius = max(0.0, min(60.0, float(raw.get("blur_radius", DEFAULT_BG_CONFIG["blur_radius"]))))
        tint_opacity = max(0.0, min(0.95, float(raw.get("tint_opacity", DEFAULT_BG_CONFIG["tint_opacity"]))))
        brightness = max(0.1, min(1.5, float(raw.get("brightness", DEFAULT_BG_CONFIG["brightness"]))))
        saturation = max(0.0, min(1.5, float(raw.get("saturation", DEFAULT_BG_CONFIG["saturation"]))))
        fit_mode = str(raw.get("fit_mode") or DEFAULT_BG_CONFIG["fit_mode"]).strip().lower()
        if fit_mode not in ("per_widget", "span_both", "desktop"):
            fit_mode = "per_widget"
        return CustomBackgroundSettings(
            image_path=image_path,
            image_mtime=img_mtime,
            config_mtime=cfg_mtime,
            blur_radius=blur_radius,
            tint_opacity=tint_opacity,
            brightness=brightness,
            saturation=saturation,
            fit_mode=fit_mode,
        )
    except Exception:
        return None


def save_widget_bg_config(
    updates: dict, config_path: Optional[str] = None, legacy_path: Optional[str] = None
) -> dict:
    """Updates and saves the shared widget background configuration.

    Existing settings are read from the shared file (or the legacy file when the shared one
    is missing), but the result is always written to the shared file only.
    """
    target, source = _source_file(config_path, legacy_path)
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    data = dict(DEFAULT_BG_CONFIG)
    if source is not None:
        try:
            with open(source, "r", encoding="utf-8-sig") as handle:
                existing = json.load(handle)
            if isinstance(existing, dict):
                data.update(existing)
        except Exception:
            pass
    data.update(updates)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return data


def apply_custom_effects(
    image: Image.Image,
    blur_radius: float,
    brightness: float,
    saturation: float,
    device_pixel_ratio: float = 1.0,
) -> Image.Image:
    """Applies configurable blur, brightness, and saturation to a background image."""
    from PIL import ImageEnhance

    result = image.convert("RGB")
    if blur_radius > 0.1:
        scaled_radius = max(0.5, min(80.0, blur_radius * device_pixel_ratio))
        result = result.filter(ImageFilter.GaussianBlur(radius=scaled_radius))
    if abs(brightness - 1.0) > 0.01:
        result = ImageEnhance.Brightness(result).enhance(brightness)
    if abs(saturation - 1.0) > 0.01:
        result = ImageEnhance.Color(result).enhance(saturation)
    return result


# ----------------------------------------------------------------------------------------
# Wallpaper
# ----------------------------------------------------------------------------------------

class WallpaperSettings(NamedTuple):
    """What Windows is currently showing as the desktop background."""

    path: str
    style: int        # 10 = Fill, 2 = Stretch, 6 = Fit, 0 = Centre, 22 = Span
    tiled: bool
    modified: float   # the picture file's last-modified time


def wallpaper_settings() -> Optional[WallpaperSettings]:
    """Asks Windows for the wallpaper picture and how it is fitted to the screen."""
    if not IS_WINDOWS:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(520)
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, len(buffer), buffer, 0):
            return None
        path = buffer.value
        if not path or not os.path.isfile(path):
            return None
        style, tiled = STYLE_FILL, False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
                style = int(winreg.QueryValueEx(key, "WallpaperStyle")[0])
                tiled = str(winreg.QueryValueEx(key, "TileWallpaper")[0]) == "1"
        except (OSError, ValueError):
            pass  # keep the defaults: Fill, not tiled
        return WallpaperSettings(path, style, tiled, os.path.getmtime(path))
    except OSError:
        return None


def fit_wallpaper(image: Image.Image, width: int, height: int, style: int, tiled: bool) -> Optional[Image.Image]:
    """Resizes the wallpaper to a screen the way Windows does.

    Returns None for fitting styles this function does not copy (Fit, Centre, Span);
    the card then falls back to a plain dark fill.
    """
    if width <= 0 or height <= 0 or image.width <= 0 or image.height <= 0:
        return None
    if tiled:
        canvas = Image.new("RGB", (width, height))
        for top in range(0, height, image.height):
            for left in range(0, width, image.width):
                canvas.paste(image, (left, top))
        return canvas
    if style == STYLE_FILL:
        # Scale until the screen is covered, then cut off whatever sticks out, centred.
        scale = max(width / image.width, height / image.height)
        scaled_w = max(width, round(image.width * scale))
        scaled_h = max(height, round(image.height * scale))
        scaled = image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        left = (scaled_w - width) // 2
        top = (scaled_h - height) // 2
        return scaled.crop((left, top, left + width, top + height))
    if style == STYLE_STRETCH:
        return image.resize((width, height), Image.Resampling.LANCZOS)
    return None


def frost(image: Image.Image, device_pixel_ratio: float) -> Image.Image:
    """Blurs, darkens and slightly greys a picture: the frosted-glass look."""
    radius = max(18, min(40, int(18 * device_pixel_ratio)))
    blurred = image.filter(ImageFilter.GaussianBlur(radius=radius))
    blurred = blurred.point(TONE_CURVE * 3)  # the same curve for red, green and blue
    grey = blurred.convert("L").convert("RGB")
    return Image.blend(blurred, grey, DESATURATION)


class WallpaperGlass:
    """Supplies the blurred background (custom widget image or desktop wallpaper) painted behind a card.

    When a custom image is configured in `%APPDATA%\\DesktopWidgets\\widget_background.json`,
    it renders that image behind the card with configurable blur, brightness, and saturation.
    Otherwise, one frosted desktop wallpaper picture is prepared per monitor and cached.
    """

    def __init__(self, bg_config_path: Optional[str] = None) -> None:
        self._bg_config_path = bg_config_path
        self._custom: Optional[CustomBackgroundSettings] = None
        self._settings: Optional[WallpaperSettings] = None
        self._checked = False
        self._cache: dict[tuple, Optional[QPixmap]] = {}

    @property
    def custom_settings(self) -> Optional[CustomBackgroundSettings]:
        """Returns the currently active custom background settings, if any."""
        if not self._checked:
            self.refresh()
        return self._custom

    def refresh(self) -> bool:
        """Re-reads custom background and wallpaper settings. Returns True when changed."""
        custom = load_widget_bg_config(self._bg_config_path)
        settings = wallpaper_settings()
        changed = self._checked and (custom != self._custom or settings != self._settings)
        if changed or not self._checked:
            self._custom = custom
            self._settings = settings
            self._cache.clear()
        self._checked = True
        return changed

    def _custom_card_pixmap(self, card_w: int, card_h: int, ratio: float) -> Optional[QPixmap]:
        """Builds and caches the custom background fitted directly to the card dimensions."""
        custom = self._custom
        if custom is None or card_w <= 0 or card_h <= 0:
            return None
        key = ("custom_card", custom.image_path, custom.image_mtime, custom.config_mtime, card_w, card_h, ratio)
        if key not in self._cache:
            try:
                with Image.open(custom.image_path) as source:
                    raw_img = source.convert("RGB")
                phys_w = max(1, round(card_w * ratio))
                phys_h = max(1, round(card_h * ratio))
                fitted = fit_wallpaper(raw_img, phys_w, phys_h, STYLE_FILL, False)
                if fitted is None:
                    self._cache[key] = None
                    return None
                processed = apply_custom_effects(
                    fitted,
                    blur_radius=custom.blur_radius,
                    brightness=custom.brightness,
                    saturation=custom.saturation,
                    device_pixel_ratio=ratio,
                )
                pixels = processed.tobytes("raw", "RGB")
                qimg = QImage(pixels, processed.width, processed.height, 3 * processed.width, QImage.Format.Format_RGB888)
                self._cache[key] = QPixmap.fromImage(qimg.copy())
            except Exception:
                log.exception("Could not prepare custom widget background from %s", custom.image_path)
                self._cache[key] = None
        return self._cache[key]

    def _frosted_pixmap(self, screen: QScreen) -> Optional[QPixmap]:
        """Returns the frosted wallpaper for one monitor, building it on first use."""
        if not self._checked:
            self.refresh()
        geometry = screen.geometry()
        ratio = screen.devicePixelRatio()
        key = ("screen", geometry.x(), geometry.y(), geometry.width(), geometry.height(), ratio)
        if key not in self._cache:
            self._cache[key] = self._build_pixmap(geometry.width(), geometry.height(), ratio)
        return self._cache[key]

    def _build_pixmap(self, logical_w: int, logical_h: int, ratio: float) -> Optional[QPixmap]:
        custom = self._custom
        if custom is not None and custom.fit_mode == "desktop":
            try:
                with Image.open(custom.image_path) as source:
                    wallpaper = source.convert("RGB")
                fitted = fit_wallpaper(wallpaper, round(logical_w * ratio), round(logical_h * ratio), STYLE_FILL, False)
                if fitted is None:
                    return None
                processed = apply_custom_effects(
                    fitted,
                    blur_radius=custom.blur_radius,
                    brightness=custom.brightness,
                    saturation=custom.saturation,
                    device_pixel_ratio=ratio,
                )
                pixels = processed.tobytes("raw", "RGB")
                image = QImage(pixels, processed.width, processed.height, 3 * processed.width, QImage.Format.Format_RGB888)
                return QPixmap.fromImage(image.copy())
            except Exception:
                log.exception("Could not prepare custom desktop background from %s", custom.image_path)

        settings = self._settings
        if settings is None:
            return None
        try:
            with Image.open(settings.path) as source:
                wallpaper = source.convert("RGB")
            # Work in real screen pixels, which on a scaled display is more than Qt's units.
            fitted = fit_wallpaper(wallpaper, round(logical_w * ratio), round(logical_h * ratio),
                                   settings.style, settings.tiled)
            if fitted is None:
                return None
            frosted = frost(fitted, ratio)
            pixels = frosted.tobytes("raw", "RGB")
            image = QImage(pixels, frosted.width, frosted.height, 3 * frosted.width, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(image.copy())  # copy(): Qt keeps its own pixels after `pixels` is freed
        except Exception:
            log.exception("Could not prepare the frosted wallpaper from %s", settings.path)
            return None

    def fill(self, painter: QPainter, path: QPainterPath, window_origin: QPointF) -> bool:
        """Fills `path` (in window coordinates) with the custom background or wallpaper behind it.

        Returns False without painting when no background is available.
        """
        if not self._checked:
            self.refresh()

        bounds = path.boundingRect()
        origin = QPointF(window_origin)
        bounds_on_desktop = bounds.translated(origin)

        # If a custom widget background is active in per_widget mode, fit it directly to the card!
        if self._custom is not None and self._custom.fit_mode != "desktop":
            ratio = 1.0
            for screen in QGuiApplication.screens():
                if QRectF(screen.geometry()).intersects(bounds_on_desktop):
                    ratio = screen.devicePixelRatio()
                    break
            win_w = max(1, round(bounds.right() + GLOW))
            win_h = max(1, round(bounds.bottom() + GLOW))
            pixmap = self._custom_card_pixmap(win_w, win_h, ratio)
            if pixmap is not None:
                placement = QTransform()
                placement.scale(1.0 / ratio, 1.0 / ratio)
                brush = QBrush(pixmap)
                brush.setTransform(placement)
                painter.fillPath(path, brush)
                return True

        covered: list[tuple[QScreen, QPixmap]] = []
        for screen in QGuiApplication.screens():
            if not QRectF(screen.geometry()).intersects(bounds_on_desktop):
                continue
            pixmap = self._frosted_pixmap(screen)
            if pixmap is None:
                return False
            covered.append((screen, pixmap))
        if not covered:
            return False

        for screen, pixmap in covered:
            screen_rect = QRectF(screen.geometry())
            # Line the picture up with the desktop: slide it to where this monitor is,
            # relative to our window, and shrink it back from real pixels to Qt's units.
            placement = QTransform()
            placement.translate(screen_rect.x() - origin.x(), screen_rect.y() - origin.y())
            placement.scale(1.0 / screen.devicePixelRatio(), 1.0 / screen.devicePixelRatio())
            brush = QBrush(pixmap)
            brush.setTransform(placement)

            target = path
            if not screen_rect.contains(bounds_on_desktop):
                # The card straddles two monitors: paint only the part on this one.
                on_this_screen = QPainterPath()
                on_this_screen.addRect(screen_rect.translated(-origin.x(), -origin.y()))
                target = path.intersected(on_this_screen)
            painter.fillPath(target, brush)
        return True


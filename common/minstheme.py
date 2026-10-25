# Общая тема для mins*-приложений.
# CssProvider = запасная палитра + @import файла палитры + style.css приложения.
# Следит за каталогом палитры и перезагружается при её смене (в том числе
# при подмене симлинка), так что окна перекрашиваются сразу.
#
# Файл палитры — $MINS_THEME, по умолчанию ~/.config/hypr/theme/current.css.

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

DEFAULT_THEME_CSS = Path(os.environ.get(
    "MINS_THEME", Path.home() / ".config/hypr/theme/current.css"))

# Палитра по умолчанию — на случай, если current.css отсутствует.
# Стоит до @import, так что переменные темы её перекрывают.
FALLBACK_PALETTE = """
@define-color bg #1e1e2e;
@define-color bg-alt #313244;
@define-color fg #cdd6f4;
@define-color fg-alt #a6adc8;
@define-color accent #a6e3a1;
@define-color red #f38ba8;
@define-color green #a6e3a1;
@define-color yellow #f9e2af;
@define-color blue #89b4fa;
@define-color purple #cba6f7;
@define-color cyan #94e2d5;
@define-color border #45475a;
"""


class Theme:
    def __init__(self, name, style_css, theme_css=DEFAULT_THEME_CSS):
        self.name = name
        self.style_css = Path(style_css)
        self.theme_css = Path(theme_css)
        self.provider = Gtk.CssProvider()
        self.provider.connect("parsing-error", self._on_error)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self._pending = 0
        self._monitors = []
        # switch.sh делает `ln -sf` — меняется запись в каталоге, а не файл,
        # поэтому смотрим на каталог. style.css — чтобы правка стилей тоже
        # подхватывалась без перезапуска.
        for path, is_dir in ((self.theme_css.parent, True), (self.style_css, False)):
            f = Gio.File.new_for_path(str(path))
            try:
                mon = (f.monitor_directory if is_dir else f.monitor_file)(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error as e:
                print(f"{name}: не могу следить за {path}: {e.message}", file=sys.stderr)
                continue
            mon.connect("changed", self._on_changed)
            self._monitors.append(mon)
        self.load()

    def load(self):
        css = FALLBACK_PALETTE
        if self.theme_css.exists():
            css += f'@import url("{self.theme_css.resolve().as_uri()}");\n'
        css += self.style_css.read_text()
        self.provider.load_from_string(css)

    def _on_changed(self, *_):
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(150, self._reload)

    def _reload(self):
        self._pending = 0
        self.load()
        return GLib.SOURCE_REMOVE

    def _on_error(self, _provider, section, error):
        loc = section.get_start_location()
        print(f"{self.name}: css {section.get_file() and section.get_file().get_path()}"
              f":{loc.lines + 1}: {error.message}", file=sys.stderr)

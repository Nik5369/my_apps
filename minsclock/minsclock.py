#!/usr/bin/env python3
# minsclock — часы в стиле minscal/minsplay: крупное время, дата и тонкая
# линия минуты. Цвета берёт из системной темы (~/.config/hypr/theme/current.css)
# и перекрашивается вживую.

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from minstheme import Theme  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

APP_ID = "dev.natsu.minsclock"
HERE = Path(__file__).resolve().parent
STYLE_CSS = HERE / "style.css"
SETTINGS_FILE = Path(GLib.get_user_config_dir()) / "minsclock" / "settings.json"
# классы размера окна для адаптивного CSS: (мин. ширина, мин. высота, класс)
SIZES = [(820, 360, "size-l"), (520, 220, "size-m"), (0, 0, "size-s")]

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница",
                 "суббота", "воскресенье"]


def read_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_settings(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, SETTINGS_FILE)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="minsclock",
                         default_width=340, default_height=150)
        # Hyprland не даёт серверных декораций — без этого GTK нарисует свой
        # заголовок с кнопками, который выбивается из стиля.
        self.set_decorated(False)
        self.add_css_class("minsclock")

        self.settings = read_settings()
        self.size_class = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["content"])
        root.append(Gtk.Box(vexpand=True))

        time_row = Gtk.Box(halign=Gtk.Align.CENTER)
        self.hours = Gtk.Label(css_classes=["digits"])
        self.colon = Gtk.Label(label=":", css_classes=["digits", "colon"])
        self.minutes = Gtk.Label(css_classes=["digits"])
        self.seconds = Gtk.Label(css_classes=["seconds"])
        for w in (self.hours, self.colon, self.minutes, self.seconds):
            w.set_valign(Gtk.Align.BASELINE)
            time_row.append(w)
        root.append(time_row)

        self.date = Gtk.Label(css_classes=["date"])
        root.append(self.date)
        root.append(Gtk.Box(vexpand=True))

        # линия минуты: заполняется за 60 секунд
        self.minute_bar = Gtk.ProgressBar(css_classes=["position"])
        root.append(self.minute_bar)

        # пустая подложка на всё окно: её resize = resize окна, по нему адаптив
        backdrop = Gtk.DrawingArea(hexpand=True, vexpand=True)
        backdrop.connect("resize", lambda _a, w, h: self._on_size(w, h))
        overlay = Gtk.Overlay(child=backdrop)
        overlay.add_overlay(root)
        self.set_child(overlay)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.toggle_seconds())
        time_row.add_controller(click)

        self._apply_seconds()
        self._tick()
        # 10 раз в секунду: линия минуты идёт плавно, цифры меняются вовремя
        GLib.timeout_add(100, self._tick)

    def _tick(self):
        now = datetime.now()
        self.hours.set_text(f"{now.hour:02d}")
        self.minutes.set_text(f"{now.minute:02d}")
        self.seconds.set_text(f"{now.second:02d}")
        self.date.set_text(f"{WEEKDAYS_FULL[now.weekday()]}, {now.day} {MONTHS_GEN[now.month - 1]}")
        self.minute_bar.set_fraction((now.second + now.microsecond / 1e6) / 60)
        # без секунд двоеточие мигает, чтобы было видно, что часы идут
        blink = not self.settings.get("seconds", True) and now.microsecond >= 500_000
        (self.colon.add_css_class if blink else self.colon.remove_css_class)("off")
        return GLib.SOURCE_CONTINUE

    def toggle_seconds(self):
        self.settings["seconds"] = not self.settings.get("seconds", True)
        write_settings(self.settings)
        self._apply_seconds()

    def _apply_seconds(self):
        self.seconds.set_visible(self.settings.get("seconds", True))

    def _on_size(self, width, height):
        cls = next(c for w, h, c in SIZES if width >= w and height >= h)
        if cls != self.size_class:
            # класс меняем вне аллокации, иначе GTK ругается на relayout
            self.size_class = cls
            GLib.idle_add(self._apply_size, cls)

    def _apply_size(self, cls):
        for _w, _h, c in SIZES:
            self.remove_css_class(c)
        self.add_css_class(cls)
        return GLib.SOURCE_REMOVE

    def _on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_s:
            self.toggle_seconds()
        elif keyval == Gdk.KEY_Escape:
            self.close()
        else:
            return False
        return True


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.theme = None

    def do_activate(self):
        if self.theme is None:
            self.theme = Theme("minsclock", STYLE_CSS)
        # повторный запуск закрывает окно — удобно вешать на клик в waybar
        win = self.get_active_window()
        if win:
            win.close()
            return
        Window(self).present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

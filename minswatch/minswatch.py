#!/usr/bin/env python3
# minswatch — секундомер в стиле minscal/minsplay/minsclock.
# Делит состояние с waybar-модулем custom/stopwatch
# (~/.config/waybar/scripts/stopwatch.sh): тот же файл
# $XDG_RUNTIME_DIR/waybar-stopwatch.state в формате "RUNNING ACCUM START"
# и тот же сигнал waybar, так что бар и окно всегда показывают одно и то же.

import os
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from minstheme import Theme  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

APP_ID = "dev.natsu.minswatch"
HERE = Path(__file__).resolve().parent
STYLE_CSS = HERE / "style.css"
STATE_FILE = Path(os.environ.get(
    "MINSWATCH_STATE",
    Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "waybar-stopwatch.state"))
WAYBAR_SIGNAL = 8  # должно совпадать с "signal" у custom/stopwatch в config.jsonc
# классы размера окна для адаптивного CSS: (мин. ширина, мин. высота, класс)
SIZES = [(820, 360, "size-l"), (520, 220, "size-m"), (0, 0, "size-s")]

ICON = "󰈸"
ICON_PLAY, ICON_PAUSE, ICON_RESET = "󰐊", "󰏤", "󰑓"


class Stopwatch:
    """Состояние в формате stopwatch.sh: целые секунды, START — unix time."""

    def __init__(self):
        self.running, self.accum, self.start = False, 0, 0
        self.load()

    def load(self):
        try:
            running, accum, start = STATE_FILE.read_text().split()[:3]
            self.running, self.accum, self.start = running == "1", int(accum), int(start)
        except (OSError, ValueError):
            self.running, self.accum, self.start = False, 0, 0

    def save(self):
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(f"{int(self.running)} {self.accum} {self.start}\n")
        os.replace(tmp, STATE_FILE)
        # waybar перерисует модуль сразу, не дожидаясь своего interval
        try:
            Gio.Subprocess.new(["pkill", f"-RTMIN+{WAYBAR_SIGNAL}", "waybar"],
                               Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            pass

    def elapsed(self):
        """Прошедшее время в секундах (float — для плавной линии)."""
        if self.running:
            return self.accum + max(time.time() - self.start, 0)
        return float(self.accum)

    def toggle(self):
        now = int(time.time())
        if self.running:
            self.accum += now - self.start
            self.running = False
        else:
            self.start = now
            self.running = True
        self.save()

    def reset(self):
        self.running, self.accum, self.start = False, 0, 0
        self.save()


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="minswatch",
                         default_width=360, default_height=170)
        # Hyprland не даёт серверных декораций — без этого GTK нарисует свой
        # заголовок с кнопками, который выбивается из стиля.
        self.set_decorated(False)
        self.add_css_class("minswatch")

        self.watch = Stopwatch()
        self.size_class = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["content"])
        root.append(Gtk.Box(vexpand=True))

        time_row = Gtk.Box(halign=Gtk.Align.CENTER)
        self.icon = Gtk.Label(label=ICON, css_classes=["icon"])
        self.hours = Gtk.Label(css_classes=["digits", "hours"])
        self.rest = Gtk.Label(css_classes=["digits"])
        # цифры — по базовой линии, огонёк — по центру строки
        self.icon.set_valign(Gtk.Align.CENTER)
        time_row.append(self.icon)
        for w in (self.hours, self.rest):
            w.set_valign(Gtk.Align.BASELINE)
            time_row.append(w)
        root.append(time_row)

        self.status = Gtk.Label(css_classes=["status"])
        root.append(self.status)
        root.append(Gtk.Box(vexpand=True))

        controls = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        reset = Gtk.Button(label=ICON_RESET, css_classes=["ctl"], tooltip_text="r — сброс")
        reset.connect("clicked", lambda *_: self._do(self.watch.reset))
        self.play_btn = Gtk.Button(css_classes=["ctl", "play"],
                                   tooltip_text="пробел — старт/пауза")
        self.play_btn.connect("clicked", lambda *_: self._do(self.watch.toggle))
        controls.append(reset)
        controls.append(self.play_btn)
        root.append(controls)

        # линия текущей минуты, как в minsclock
        self.minute_bar = Gtk.ProgressBar(css_classes=["position"])
        root.append(self.minute_bar)

        # пустая подложка на всё окно: её resize = resize окна, по нему адаптив
        backdrop = Gtk.DrawingArea(hexpand=True, vexpand=True)
        backdrop.connect("resize", lambda _a, w, h: self._on_size(w, h))
        overlay = Gtk.Overlay(child=backdrop)
        overlay.add_overlay(root)
        self.set_child(overlay)

        # как в waybar-модуле: клик по времени — старт/пауза, правый — сброс
        click = Gtk.GestureClick(button=0)
        click.connect("released", self._on_click)
        time_row.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        # клики по модулю в waybar меняют файл — подхватываем их
        self.monitor = Gio.File.new_for_path(str(STATE_FILE)).monitor_file(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        self.monitor.connect("changed", lambda *_: (self.watch.load(), self._tick()))

        self._tick()
        GLib.timeout_add(100, self._tick)

    def _do(self, action):
        action()
        self._tick()

    def _tick(self):
        w = self.watch
        total = w.elapsed()
        s = int(total)
        h, m, sec = s // 3600, s % 3600 // 60, s % 60
        self.hours.set_text(f"{h:02d}:")
        self.rest.set_text(f"{m:02d}:{sec:02d}")
        # пока часов нет, «00:» приглушён — взгляд цепляется за минуты
        (self.hours.add_css_class if h == 0 else self.hours.remove_css_class)("zero")
        self.minute_bar.set_fraction((total % 60) / 60)
        self.play_btn.set_label(ICON_PAUSE if w.running else ICON_PLAY)
        if w.running:
            self.status.set_text("идёт")
        elif w.accum:
            self.status.set_text("пауза")
        else:
            self.status.set_text("пробел — старт")
        for cls, on in (("running", w.running), ("paused", not w.running and w.accum > 0)):
            (self.add_css_class if on else self.remove_css_class)(cls)
        return GLib.SOURCE_CONTINUE

    def _on_click(self, gesture, *_):
        if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            self._do(self.watch.reset)
        else:
            self._do(self.watch.toggle)

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
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._do(self.watch.toggle)
        elif keyval in (Gdk.KEY_r, Gdk.KEY_BackSpace):
            self._do(self.watch.reset)
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
            self.theme = Theme("minswatch", STYLE_CSS)
        # повторный запуск закрывает окно — удобно вешать на клик в waybar
        win = self.get_active_window()
        if win:
            win.close()
            return
        Window(self).present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

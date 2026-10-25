#!/usr/bin/env python3
# minsplay — мини-плеер в стиле minscal: трек, управление через MPRIS
# (playerctl), визуализатор на cava и громкость колесом мыши (wpctl).
# Цвета берёт из системной темы (~/.config/hypr/theme/current.css)
# и перекрашивается вживую.

import re
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Playerctl", "2.0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from minstheme import Theme  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Playerctl  # noqa: E402

APP_ID = "dev.natsu.minsplay"
HERE = Path(__file__).resolve().parent
STYLE_CSS = HERE / "style.css"
CAVA_CONF = HERE / "cava.conf"
SINK = "@DEFAULT_AUDIO_SINK@"
VOLUME_STEP = 5   # % за один щелчок колеса
VOLUME_MAX = 1.5  # wpctl -l: потолок 150%, всё выше 100% подсвечивается
# классы размера окна для адаптивного CSS: (мин. ширина, мин. высота, класс)
SIZES = [(820, 360, "size-l"), (560, 220, "size-m"), (0, 0, "size-s")]
BARS = 48         # должно совпадать с bars в cava.conf

ICON_PREV, ICON_NEXT = "󰒮", "󰒭"
ICON_PLAY, ICON_PAUSE = "󰐊", "󰏤"
ICON_VOL, ICON_MUTE = "󰕾", "󰖁"

class Cava:
    """cava в raw-режиме: каждая строка stdout — кадр «v1;v2;...;vN;»
    со значениями 0..1000. Отдаёт кадры в on_frame как список 0.0..1.0."""

    def __init__(self, on_frame):
        self.on_frame = on_frame
        self.proc = None
        try:
            self.proc = Gio.Subprocess.new(
                ["cava", "-p", str(CAVA_CONF)],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as e:
            print(f"minsplay: cava не запустилась: {e.message}", file=sys.stderr)
            return
        self.stream = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self.cancel = Gio.Cancellable()
        self._read()

    def _read(self):
        self.stream.read_line_async(GLib.PRIORITY_DEFAULT, self.cancel, self._on_line)

    def _on_line(self, stream, result):
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            return  # отменено при выходе или cava упала
        if line is None:
            return  # EOF
        vals = [int(v) / 1000 for v in line.split(";") if v.isdigit()]
        if vals:
            self.on_frame(vals)
        self._read()

    def stop(self):
        if self.proc:
            self.cancel.cancel()
            self.proc.force_exit()


class Volume:
    """Громкость default sink через wpctl. Всё асинхронно, чтобы не
    подвешивать интерфейс."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.level = None
        self.muted = False
        self.refresh()
        # внешние изменения (клавиши, pavucontrol) — опрашиваем раз в 2 с
        GLib.timeout_add_seconds(2, lambda: (self.refresh(), True)[1])

    def _run(self, args, then=None):
        try:
            proc = Gio.Subprocess.new(["wpctl", *args],
                                      Gio.SubprocessFlags.STDOUT_PIPE
                                      | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as e:
            print(f"minsplay: wpctl: {e.message}", file=sys.stderr)
            return
        proc.communicate_utf8_async(None, None, self._done, then)

    def _done(self, proc, result, then):
        try:
            _, out, _ = proc.communicate_utf8_finish(result)
        except GLib.Error:
            return
        if then:
            then(out or "")

    def refresh(self):
        self._run(["get-volume", SINK], self._parse)

    def _parse(self, out):
        # "Volume: 0.65" или "Volume: 0.65 [MUTED]"
        m = re.search(r"Volume:\s*([\d.]+)", out)
        if not m:
            return
        level, muted = round(float(m.group(1)) * 100), "[MUTED]" in out
        if (level, muted) != (self.level, self.muted):
            self.level, self.muted = level, muted
            self.on_change()

    def step(self, direction):
        sign = "+" if direction > 0 else "-"
        self._run(["set-volume", "-l", str(VOLUME_MAX), SINK, f"{VOLUME_STEP}%{sign}"],
                  lambda _out: self.refresh())

    def toggle_mute(self):
        self._run(["set-mute", SINK, "toggle"], lambda _out: self.refresh())


class Players:
    """Следит за MPRIS-плеерами; активный — играющий, иначе первый."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.manager = Playerctl.PlayerManager()
        self.manager.connect("name-appeared", lambda _m, name: self._manage(name))
        self.manager.connect("player-vanished", lambda *_: self.on_change())
        # без уведомлений: окно ещё не сохранило ссылку на нас
        for name in self.manager.props.player_names:
            self._manage(name, notify=False)

    def _manage(self, name, notify=True):
        try:
            player = Playerctl.Player.new_from_name(name)
        except GLib.Error as e:
            print(f"minsplay: {name.name}: {e.message}", file=sys.stderr)
            return
        player.connect("playback-status", lambda *_: self.on_change())
        player.connect("metadata", lambda *_: self.on_change())
        self.manager.manage_player(player)
        if notify:
            self.on_change()

    @property
    def active(self):
        players = self.manager.props.players
        for p in players:
            if p.props.playback_status == Playerctl.PlaybackStatus.PLAYING:
                return p
        return players[0] if players else None


def call(player, method):
    # плеер мог исчезнуть между кликом и вызовом
    if player:
        try:
            getattr(player, method)()
        except GLib.Error as e:
            print(f"minsplay: {method}: {e.message}", file=sys.stderr)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="minsplay",
                         default_width=420, default_height=140)
        # Hyprland не даёт серверных декораций — без этого GTK нарисует свой
        # заголовок с кнопками, который выбивается из стиля.
        self.set_decorated(False)
        self.add_css_class("minsplay")

        self.bars = [0.0] * BARS

        self.viz = Gtk.DrawingArea(css_classes=["viz"], hexpand=True, vexpand=True)
        self.viz.set_draw_func(self._draw_viz)
        # viz занимает всё окно, так что его resize = resize окна
        self.viz.connect("resize", self._on_resize)
        self.size_class = None

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["content"])
        self.title_label = Gtk.Label(xalign=0, css_classes=["track"],
                                     ellipsize=Pango.EllipsizeMode.END)
        self.artist_label = Gtk.Label(xalign=0, css_classes=["artist"],
                                      ellipsize=Pango.EllipsizeMode.END)
        content.append(self.title_label)
        content.append(self.artist_label)
        content.append(Gtk.Box(vexpand=True))

        controls = Gtk.Box(spacing=2, css_classes=["controls"])
        prev = Gtk.Button(label=ICON_PREV, css_classes=["ctl"], tooltip_text="← назад")
        prev.connect("clicked", lambda *_: call(self.players.active, "previous"))
        self.play_btn = Gtk.Button(label=ICON_PLAY, css_classes=["ctl", "play"],
                                   tooltip_text="пробел — пауза")
        self.play_btn.connect("clicked", lambda *_: call(self.players.active, "play_pause"))
        nxt = Gtk.Button(label=ICON_NEXT, css_classes=["ctl"], tooltip_text="→ вперёд")
        nxt.connect("clicked", lambda *_: call(self.players.active, "next"))
        for w in (prev, self.play_btn, nxt):
            controls.append(w)
        self.player_label = Gtk.Label(css_classes=["source"], xalign=0,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.vol_btn = Gtk.Button(css_classes=["volume"],
                                  tooltip_text="колесо — громкость, клик — mute")
        self.vol_btn.connect("clicked", lambda *_: self.volume.toggle_mute())
        # CenterBox держит управление ровно по центру окна, сколько бы
        # места ни занимали подпись плеера слева и громкость справа
        bottom = Gtk.CenterBox(start_widget=self.player_label, center_widget=controls,
                               end_widget=self.vol_btn)
        content.append(bottom)

        self.progress = Gtk.ProgressBar(css_classes=["position"])
        content.append(self.progress)

        overlay = Gtk.Overlay(child=self.viz)
        overlay.add_overlay(content)
        self.set_child(overlay)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL
            | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.players = Players(self.refresh)
        self.volume = Volume(self._render_volume)
        self.cava = Cava(self._on_frame)
        self.connect("close-request", lambda *_: self.cava.stop() or False)

        GLib.timeout_add(500, self._tick_position)
        self.refresh()
        self._render_volume()

    # --- отрисовка ---

    def refresh(self):
        p = self.players.active
        if p is None:
            self.title_label.set_text("ничего не играет")
            self.artist_label.set_text("")
            self.player_label.set_text("")
            self.play_btn.set_label(ICON_PLAY)
            self.progress.set_fraction(0)
            self.add_css_class("idle")
            return
        self.remove_css_class("idle")
        self.title_label.set_text(p.get_title() or "без названия")
        self.artist_label.set_text(p.get_artist() or "")
        self.player_label.set_text(p.props.player_name)
        playing = p.props.playback_status == Playerctl.PlaybackStatus.PLAYING
        self.play_btn.set_label(ICON_PAUSE if playing else ICON_PLAY)
        self._tick_position()

    def _render_volume(self):
        v = self.volume
        if v.level is None:
            self.vol_btn.set_label(ICON_VOL)
            return
        self.vol_btn.set_label(f"{ICON_MUTE if v.muted else ICON_VOL} {v.level}%")
        for cls, on in (("muted", v.muted), ("boost", v.level > 100)):
            (self.vol_btn.add_css_class if on else self.vol_btn.remove_css_class)(cls)

    def _on_resize(self, _area, width, height):
        cls = next(c for w, h, c in SIZES if width >= w and height >= h)
        if cls != self.size_class:
            # класс меняем вне аллокации, иначе GTK ругается на relayout
            GLib.idle_add(self._apply_size, cls)

    def _apply_size(self, cls):
        if self.size_class:
            self.remove_css_class(self.size_class)
        self.size_class = cls
        self.add_css_class(cls)
        return GLib.SOURCE_REMOVE

    def _tick_position(self):
        p = self.players.active
        if p is not None:
            pos = length = 0
            try:
                length = int(p.print_metadata_prop("mpris:length") or 0)
                pos = p.get_position()
            except (GLib.Error, ValueError):
                pass  # плеер не отдаёт позицию — линия остаётся пустой
            self.progress.set_fraction(min(pos / length, 1) if length else 0)
        return GLib.SOURCE_CONTINUE

    def _on_frame(self, vals):
        self.bars = vals
        self.viz.queue_draw()

    def _draw_viz(self, area, cr, width, height):
        n = len(self.bars)
        if not n:
            return
        color = area.get_color()  # = CSS color у .viz, т.е. @accent темы
        gap = 2
        bar_w = max((width - gap * (n - 1)) / n, 1)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        for i, v in enumerate(self.bars):
            h = max(v * height, 1.5)  # в тишине остаётся тонкая полоска
            cr.rectangle(i * (bar_w + gap), height - h, bar_w, h)
        cr.fill()

    # --- ввод ---

    def _on_scroll(self, _ctl, _dx, dy):
        if dy:
            self.volume.step(-dy)
        return True

    def _on_key(self, _ctl, keyval, _code, _state):
        p = self.players.active
        if keyval == Gdk.KEY_space:
            call(p, "play_pause")
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_h):
            call(p, "previous")
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_l):
            call(p, "next")
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_k):
            self.volume.step(1)
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_j):
            self.volume.step(-1)
        elif keyval == Gdk.KEY_m:
            self.volume.toggle_mute()
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
            self.theme = Theme("minsplay", STYLE_CSS)
        # повторный запуск закрывает окно — удобно вешать на клик в waybar
        win = self.get_active_window()
        if win:
            win.close()
            return
        Window(self).present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

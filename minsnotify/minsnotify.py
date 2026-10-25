#!/usr/bin/env python3
# minsnotify — демон уведомлений + окно истории в стиле остальных mins*-приложений.
#
#   minsnotify --daemon   фоновый сервер org.freedesktop.Notifications:
#                         всплывашки справа сверху (layer-shell) и история
#   minsnotify            открыть/закрыть окно истории (запустит демон,
#                         если он ещё не работает)
#
# Цвета берёт из системной темы (~/.config/hypr/theme/current.css)
# и перекрашивается вживую.

import os
import sys

# gtk4-layer-shell должен загрузиться раньше libwayland-client, иначе
# всплывашки не станут layer-поверхностями — перезапускаемся с LD_PRELOAD.
# Ищем по имени, а не по пути: на разных дистрибутивах lib/lib64/multiarch.
import ctypes.util  # noqa: E402

_LAYER_LIB = ctypes.util.find_library("gtk4-layer-shell")
if _LAYER_LIB and "gtk4-layer-shell" not in os.environ.get("LD_PRELOAD", ""):
    env = dict(os.environ, LD_PRELOAD=":".join(
        p for p in (_LAYER_LIB, os.environ.get("LD_PRELOAD")) if p))
    os.execve(sys.executable, [sys.executable, *sys.argv], env)

import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from minstheme import Theme  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell, Pango  # noqa: E402

# переменные окружения — чтобы тестировать рядом с работающим демоном
APP_ID = os.environ.get("MINSNOTIFY_APP_ID", "dev.natsu.minsnotify")
BUS_NAME = os.environ.get("MINSNOTIFY_BUS_NAME", "org.freedesktop.Notifications")
HERE = Path(__file__).resolve().parent
STYLE_CSS = HERE / "style.css"
DATA_DIR = Path(os.environ.get(
    "MINSNOTIFY_DATA", Path(GLib.get_user_data_dir()) / "minsnotify"))
HISTORY_FILE = DATA_DIR / "history.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
IMAGE_DIR = Path(GLib.get_user_cache_dir()) / "minsnotify"

PATH = "/org/freedesktop/Notifications"
IFACE = "org.freedesktop.Notifications"
HISTORY_LIMIT = 200
MAX_POPUPS = 4
# expire_timeout = -1 → время по срочности (мс); 0 — не исчезает
DEFAULT_TIMEOUT = {0: 4000, 1: 6000, 2: 0}
# классы размера окна для адаптивного CSS: (мин. ширина, мин. высота, класс)
SIZES = [(820, 600, "size-l"), (560, 420, "size-m"), (0, 0, "size-s")]

# причины NotificationClosed по спецификации
EXPIRED, DISMISSED, CLOSED_BY_CALL = 1, 2, 3

INTROSPECTION = f"""
<node>
  <interface name="{IFACE}">
    <method name="GetCapabilities"><arg type="as" direction="out"/></method>
    <method name="Notify">
      <arg type="s" name="app_name" direction="in"/>
      <arg type="u" name="replaces_id" direction="in"/>
      <arg type="s" name="app_icon" direction="in"/>
      <arg type="s" name="summary" direction="in"/>
      <arg type="s" name="body" direction="in"/>
      <arg type="as" name="actions" direction="in"/>
      <arg type="a{{sv}}" name="hints" direction="in"/>
      <arg type="i" name="expire_timeout" direction="in"/>
      <arg type="u" direction="out"/>
    </method>
    <method name="CloseNotification"><arg type="u" name="id" direction="in"/></method>
    <method name="GetServerInformation">
      <arg type="s" direction="out"/><arg type="s" direction="out"/>
      <arg type="s" direction="out"/><arg type="s" direction="out"/>
    </method>
    <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
    <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
  </interface>
</node>
"""


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def body_markup(body):
    """Подмножество разметки из спецификации → безопасная Pango-разметка."""
    body = re.sub(r"<img[^>]*>", "", body or "").strip()
    # одиночный & ломает разбор всей разметки — экранируем только его
    body = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", body)
    try:
        Pango.parse_markup(body, -1, "\0")
        return body
    except GLib.Error:
        return GLib.markup_escape_text(body)


def save_image_data(nid, value):
    """hint image-data (iiibiiay) → PNG в кэше; путь — чтобы пережить перезапуск."""
    try:
        w, h, stride, alpha, _bps, channels, data = value
        fmt = Gdk.MemoryFormat.R8G8B8A8 if alpha else Gdk.MemoryFormat.R8G8B8
        if channels not in (3, 4) or len(data) < stride * (h - 1) + w * channels:
            return None
        tex = Gdk.MemoryTexture.new(w, h, fmt, GLib.Bytes.new(bytes(data)), stride)
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = IMAGE_DIR / f"{int(time.time())}-{nid}.png"
        tex.save_to_png(str(path))
        return str(path)
    except (ValueError, TypeError, GLib.Error):
        return None


def resolve_image(nid, app_icon, hints):
    """Картинка уведомления: image-data > image-path > app_icon > иконка .desktop.
    Возвращает путь к файлу или имя иконки."""
    for key in ("image-data", "image_data", "icon_data"):
        if key in hints:
            path = save_image_data(nid, hints[key])
            if path:
                return path
    for candidate in (hints.get("image-path"), hints.get("image_path"), app_icon):
        if candidate:
            return candidate.removeprefix("file://")
    entry = hints.get("desktop-entry")
    if entry:
        try:
            info = Gio.DesktopAppInfo.new(f"{entry}.desktop")
        except TypeError:  # такого .desktop нет — PyGObject бросает на NULL
            return None
        icon = info.get_icon()
        if isinstance(icon, Gio.ThemedIcon):
            return icon.get_names()[0]
        if isinstance(icon, Gio.FileIcon):
            return icon.get_file().get_path()
    return None


def image_widget(image, css_class):
    img = Gtk.Image(css_classes=[css_class])
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    if image and image.startswith("/") and os.path.exists(image):
        img.set_from_file(image)
    elif image and not image.startswith("/") and theme.has_icon(image):
        img.set_from_icon_name(image)
    else:
        img.set_from_icon_name("dialog-information")
    return img


def when(ts):
    t = datetime.fromtimestamp(ts)
    now = datetime.now()
    delta = now - t
    if delta < timedelta(minutes=1):
        return "сейчас"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} мин"
    if t.date() == now.date():
        return t.strftime("%H:%M")
    if t.date() == (now - timedelta(days=1)).date():
        return t.strftime("вчера %H:%M")
    return t.strftime("%d.%m %H:%M")


class Store:
    """История уведомлений + слушатели. Новые — в начале списка."""

    def __init__(self):
        self.items = read_json(HISTORY_FILE, [])
        for n in self.items:
            n["live"] = False  # отправители прошлых сессий уже не услышат ActionInvoked
        self.next_id = max((n["id"] for n in self.items), default=0) + 1
        self.listeners = []

    def get(self, nid):
        return next((n for n in self.items if n["id"] == nid), None)

    def add(self, n):
        old = self.get(n["id"])
        if old:
            self.items.remove(old)
        self.items.insert(0, n)
        del self.items[HISTORY_LIMIT:]
        self._changed()

    def remove(self, nid):
        n = self.get(nid)
        if n:
            self.items.remove(n)
            self._changed()
        return n

    def _changed(self):
        write_json_atomic(HISTORY_FILE, [n for n in self.items if not n.get("transient")])
        for cb in self.listeners:
            cb()


class Card(Gtk.Box):
    """Карточка уведомления — и во всплывашке, и в истории."""

    def __init__(self, server, n, popup=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,
                         css_classes=["card", f"urgency-{n['urgency']}"])
        self.server, self.n, self.popup = server, n, popup

        row = Gtk.Box(spacing=12)
        row.append(image_widget(n.get("image"), "note-image"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=2)

        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label=n["app"] or "уведомление", css_classes=["app"],
                              xalign=0, ellipsize=Pango.EllipsizeMode.END))
        self.time_label = Gtk.Label(label=when(n["time"]), css_classes=["time"],
                                    xalign=0, hexpand=True)
        head.append(self.time_label)
        close = Gtk.Button(label="✕", css_classes=["close"], valign=Gtk.Align.START,
                           tooltip_text="закрыть")
        close.connect("clicked", lambda *_: self._close())
        head.append(close)
        text.append(head)

        if n["summary"]:
            text.append(Gtk.Label(label=n["summary"], css_classes=["summary"], xalign=0,
                                  wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                  lines=2, ellipsize=Pango.EllipsizeMode.END))
        if n["body"]:
            body = Gtk.Label(css_classes=["body"], xalign=0, wrap=True, use_markup=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR)
            body.set_markup(n["body"])
            if popup:
                body.set_lines(4)
                body.set_ellipsize(Pango.EllipsizeMode.END)
            text.append(body)

        # кнопки — только пока отправитель жив (иначе ActionInvoked некому слышать)
        actions = [a for a in n["actions"] if a[0] != "default"] if n.get("live") else []
        if actions:
            bar = Gtk.Box(spacing=4, css_classes=["actions"])
            for key, label in actions:
                b = Gtk.Button(label=label, css_classes=["action"], hexpand=True)
                b.connect("clicked", lambda *_, k=key: self.server.invoke(self.n["id"], k))
                bar.append(b)
            text.append(bar)
        row.append(text)
        self.append(row)

        if popup:
            self.timer = Gtk.ProgressBar(css_classes=["position"], fraction=1)
            self.append(self.timer)

        # клик по карточке — действие по умолчанию, если оно есть
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)

    def _on_click(self, gesture, _n, _x, _y):
        # клик по кнопке внутри карточки уже обработан самой кнопкой
        target = self.pick(_x, _y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not self:
            if isinstance(target, Gtk.Button):
                return
            target = target.get_parent()
        if self.n.get("live") and any(a[0] == "default" for a in self.n["actions"]):
            self.server.invoke(self.n["id"], "default")
        elif self.popup:
            self.server.popups.hide(self.n["id"])

    def _close(self):
        if self.popup:
            # крестик во всплывашке — только убрать с экрана, история остаётся
            self.server.popups.hide(self.n["id"])
        else:
            self.server.dismiss(self.n["id"])

    def refresh_time(self):
        self.time_label.set_text(when(self.n["time"]))


class Popups:
    """Стопка всплывашек справа сверху — одна layer-shell поверхность."""

    def __init__(self, server):
        self.server = server
        self.cards = {}    # id → (card, timeout_ms, remaining_ms, hovered)
        self.window = None
        self.box = None
        GLib.timeout_add(50, self._tick)

    def _ensure_window(self):
        if self.window:
            return
        win = Gtk.Window(css_classes=["minsnotify-popups"], decorated=False,
                         resizable=False)
        Gtk4LayerShell.init_for_window(win)
        Gtk4LayerShell.set_namespace(win, "minsnotify")
        Gtk4LayerShell.set_layer(win, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_keyboard_mode(win, Gtk4LayerShell.KeyboardMode.NONE)
        for edge, margin in ((Gtk4LayerShell.Edge.TOP, 12), (Gtk4LayerShell.Edge.RIGHT, 12)):
            Gtk4LayerShell.set_anchor(win, edge, True)
            Gtk4LayerShell.set_margin(win, edge, margin)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                           css_classes=["popup-stack"])
        win.set_child(self.box)
        self.window = win

    def show(self, n):
        timeout = n["timeout"]
        if self.server.dnd and n["urgency"] < 2:
            return
        self.hide(n["id"], quiet=True)  # replaces_id — заменяем на месте
        self._ensure_window()
        card = Card(self.server, n, popup=True)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._hover(n["id"], True))
        motion.connect("leave", lambda *_: self._hover(n["id"], False))
        card.add_controller(motion)
        self.box.prepend(card)
        self.cards[n["id"]] = [card, timeout, timeout, False]
        while len(self.cards) > MAX_POPUPS:
            oldest = next(iter(self.cards))
            self.hide(oldest)
        self.window.present()

    def _hover(self, nid, on):
        if nid in self.cards:
            self.cards[nid][3] = on

    def hide(self, nid, quiet=False):
        entry = self.cards.pop(nid, None)
        if entry:
            self.box.remove(entry[0])
        if not self.cards and self.window and not quiet:
            # пересоздаём окно: layer-поверхность GTK4 не умеет ужиматься обратно
            self.window.destroy()
            self.window = None

    def _tick(self):
        for nid, entry in list(self.cards.items()):
            card, timeout, remaining, hovered = entry
            if timeout <= 0:
                card.timer.set_visible(False)
                continue
            if not hovered:
                remaining -= 50
                entry[2] = remaining
            card.timer.set_fraction(max(remaining, 0) / timeout)
            if remaining <= 0:
                self.server.expire(nid)
        return GLib.SOURCE_CONTINUE


class HistoryWindow(Gtk.ApplicationWindow):
    def __init__(self, app, server):
        super().__init__(application=app, title="minsnotify",
                         default_width=420, default_height=520)
        self.set_decorated(False)
        self.add_css_class("minsnotify")
        self.server = server
        self.size_class = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       css_classes=["content"])
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label="уведомления", css_classes=["title"], xalign=0))
        self.count = Gtk.Label(css_classes=["dim"], xalign=0, hexpand=True)
        head.append(self.count)
        self.dnd_btn = Gtk.Button(css_classes=["text-btn"], tooltip_text="d")
        self.dnd_btn.connect("clicked", lambda *_: server.set_dnd(not server.dnd))
        head.append(self.dnd_btn)
        clear = Gtk.Button(label="очистить", css_classes=["text-btn"],
                           tooltip_text="shift+del")
        clear.connect("clicked", lambda *_: server.dismiss_all())
        head.append(clear)
        root.append(head)

        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.empty = Gtk.Label(label="тихо", css_classes=["dim"], vexpand=True,
                               valign=Gtk.Align.CENTER)
        scroll = Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        root.append(self.empty)
        root.append(scroll)

        # пустая подложка на всё окно: её resize = resize окна, по нему адаптив
        backdrop = Gtk.DrawingArea(hexpand=True, vexpand=True)
        backdrop.connect("resize", lambda _a, w, h: self._on_size(w, h))
        overlay = Gtk.Overlay(child=backdrop)
        overlay.add_overlay(root)
        self.set_child(overlay)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        server.store.listeners.append(self.render)
        server.dnd_listeners.append(self._render_dnd)
        self.connect("close-request", self._on_close)
        self._clock = GLib.timeout_add_seconds(30, self._refresh_times)
        self.render()
        self._render_dnd()

    def render(self):
        child = self.list.get_first_child()
        while child:
            self.list.remove(child)
            child = self.list.get_first_child()
        for n in self.server.store.items:
            self.list.append(Card(self.server, n))
        count = len(self.server.store.items)
        self.count.set_text(str(count) if count else "")
        self.empty.set_visible(count == 0)

    def _render_dnd(self):
        self.dnd_btn.set_label("✓ не беспокоить" if self.server.dnd else "не беспокоить")
        (self.dnd_btn.add_css_class if self.server.dnd
         else self.dnd_btn.remove_css_class)("accent")

    def _refresh_times(self):
        child = self.list.get_first_child()
        while child:
            child.refresh_time()
            child = child.get_next_sibling()
        return GLib.SOURCE_CONTINUE

    def _on_close(self, *_):
        self.server.store.listeners.remove(self.render)
        self.server.dnd_listeners.remove(self._render_dnd)
        GLib.source_remove(self._clock)
        return False

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

    def _on_key(self, _ctl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
        elif keyval == Gdk.KEY_d:
            self.server.set_dnd(not self.server.dnd)
        elif keyval == Gdk.KEY_Delete and state & Gdk.ModifierType.SHIFT_MASK:
            self.server.dismiss_all()
        else:
            return False
        return True


class Server:
    """org.freedesktop.Notifications поверх Store и Popups."""

    def __init__(self, conn):
        self.conn = conn
        self.store = Store()
        self.popups = Popups(self)
        self.settings = read_json(SETTINGS_FILE, {})
        self.dnd = self.settings.get("dnd", False)
        self.dnd_listeners = []
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        conn.register_object(PATH, node.interfaces[0], self._on_call, None, None)
        # REPLACE — забрать имя у демона, который это разрешает; сами отнять
        # себя не даём, чтобы случайно поднятый swaync не перехватил уведомления
        self.owner_id = Gio.bus_own_name_on_connection(
            conn, BUS_NAME, Gio.BusNameOwnerFlags.REPLACE,
            lambda *_: print(f"minsnotify: владею {BUS_NAME}", file=sys.stderr),
            lambda *_: print(f"minsnotify: {BUS_NAME} занят другим демоном "
                             "(swaync?) — жду, пока освободится", file=sys.stderr))

    # --- D-Bus ---

    def _on_call(self, conn, sender, path, iface, method, params, invocation):
        # любая ошибка — D-Bus-ошибкой отправителю, а не вечным ожиданием ответа
        try:
            self._dispatch(sender, method, params, invocation)
        except Exception as e:  # noqa: BLE001
            print(f"minsnotify: {method}: {e!r}", file=sys.stderr)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def _dispatch(self, sender, method, params, invocation):
        if method == "GetCapabilities":
            invocation.return_value(GLib.Variant("(as)", (
                ["body", "body-markup", "body-hyperlinks", "actions",
                 "icon-static", "persistence"],)))
        elif method == "GetServerInformation":
            invocation.return_value(GLib.Variant("(ssss)",
                                                 ("minsnotify", "natsu", "0.1", "1.2")))
        elif method == "Notify":
            nid = self.notify(sender, *params.unpack())
            invocation.return_value(GLib.Variant("(u)", (nid,)))
        elif method == "CloseNotification":
            (nid,) = params.unpack()
            self.close(nid, CLOSED_BY_CALL)
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _emit(self, signal, sig, args):
        self.conn.emit_signal(None, PATH, IFACE, signal, GLib.Variant(sig, args))

    # --- логика ---

    def notify(self, sender, app, replaces_id, app_icon, summary, body, actions,
               hints, expire_timeout):
        nid = replaces_id if replaces_id and self.store.get(replaces_id) else self.store.next_id
        if nid == self.store.next_id:
            self.store.next_id += 1
        urgency = int(hints.get("urgency", 1))
        urgency = urgency if urgency in (0, 1, 2) else 1
        timeout = DEFAULT_TIMEOUT[urgency] if expire_timeout < 0 else expire_timeout
        n = {
            "id": nid,
            "app": app,
            "summary": summary,
            "body": body_markup(body),
            "image": resolve_image(nid, app_icon, hints),
            # actions приходят плоским списком [key, label, key, label, ...]
            "actions": [list(p) for p in zip(actions[0::2], actions[1::2])],
            "urgency": urgency,
            "timeout": timeout,
            "transient": bool(hints.get("transient", False)),
            "resident": bool(hints.get("resident", False)),
            "time": time.time(),
            "sender": sender,
            "live": True,
        }
        self.store.add(n)
        self.popups.show(n)
        return nid

    def expire(self, nid):
        """Таймер всплывашки истёк: прячем; transient — удаляем совсем."""
        self.popups.hide(nid)
        n = self.store.get(nid)
        if n and n.get("transient"):
            self.store.remove(nid)
            self._emit("NotificationClosed", "(uu)", (nid, EXPIRED))

    def close(self, nid, reason):
        self.popups.hide(nid)
        if self.store.remove(nid):
            self._emit("NotificationClosed", "(uu)", (nid, reason))

    def dismiss(self, nid):
        self.close(nid, DISMISSED)

    def dismiss_all(self):
        for n in list(self.store.items):
            self.popups.hide(n["id"])
            if n.get("live"):
                self._emit("NotificationClosed", "(uu)", (n["id"], DISMISSED))
        self.store.items.clear()
        self.store._changed()

    def invoke(self, nid, key):
        n = self.store.get(nid)
        if not n or not n.get("live"):
            return
        self._emit("ActionInvoked", "(us)", (nid, key))
        # по спецификации после действия уведомление закрывается,
        # если отправитель не попросил resident
        if n.get("resident"):
            self.popups.hide(nid)
        else:
            self.dismiss(nid)

    def set_dnd(self, on):
        self.dnd = on
        self.settings["dnd"] = on
        write_json_atomic(SETTINGS_FILE, self.settings)
        if on:
            for nid in list(self.popups.cards):
                if self.store.get(nid) and self.store.get(nid)["urgency"] < 2:
                    self.popups.hide(nid)
        for cb in self.dnd_listeners:
            cb()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.server = None
        self.theme = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()  # демон живёт и без окон
        self.theme = Theme("minsnotify", STYLE_CSS)
        self.server = Server(self.get_dbus_connection())

    def do_command_line(self, cmdline):
        if "--daemon" in cmdline.get_arguments():
            return 0
        # обычный запуск — открыть/закрыть окно истории
        win = next((w for w in self.get_windows() if isinstance(w, HistoryWindow)), None)
        if win:
            win.close()
        else:
            HistoryWindow(self, self.server).present()
        return 0


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

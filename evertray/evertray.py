#!/usr/bin/env python3
# evertray — системный трей в стиле evercal/everplay/everclock/everwatch.
# StatusNotifierHost: берёт иконки у org.kde.StatusNotifierWatcher (его держит
# kded6 или waybar), меню рисует сам через com.canonical.dbusmenu.
# Цвета берёт из системной темы (~/.config/hypr/theme/current.css)
# и перекрашивается вживую.

import os
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from evertheme import Theme  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "dev.natsu.evertray"
HERE = Path(__file__).resolve().parent
STYLE_CSS = HERE / "style.css"
THEME_CSS = Path(os.environ.get(
    "EVERTRAY_THEME", Path.home() / ".config/hypr/theme/current.css"))
# классы размера окна для адаптивного CSS: (мин. ширина, мин. высота, класс)
SIZES = [(820, 360, "size-l"), (520, 220, "size-m"), (0, 0, "size-s")]

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
TIMEOUT = 2000  # мс на D-Bus-вызов: зависшее приложение не должно вешать трей


def bus_call(conn, service, path, iface, method, args, reply_type, callback=None):
    """Асинхронный D-Bus-вызов; callback(result_tuple | None, error | None)."""
    def done(c, res):
        try:
            out = c.call_finish(res)
        except GLib.Error as e:
            if callback:
                callback(None, e)
            return
        if callback:
            callback(out.unpack() if out else (), None)
    conn.call(service, path, iface, method, args,
              GLib.VariantType(reply_type) if reply_type else None,
              Gio.DBusCallFlags.NONE, TIMEOUT, None, done)


def argb_to_texture(width, height, data):
    """IconPixmap: ARGB32 в сетевом порядке байт → RGBA-текстура."""
    if len(data) < width * height * 4:
        return None
    rgba = bytearray(len(data))
    rgba[0::4] = data[1::4]
    rgba[1::4] = data[2::4]
    rgba[2::4] = data[3::4]
    rgba[3::4] = data[0::4]
    return Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8,
                                 GLib.Bytes.new(bytes(rgba)), width * 4)


def strip_mnemonic(label):
    # "_Открыть" → "Открыть", "a__b" → "a_b"
    return label.replace("__", "\0").replace("_", "").replace("\0", "_")


class TrayItem:
    """Один StatusNotifierItem: свойства + действия."""

    def __init__(self, conn, key, on_change):
        self.conn = conn
        self.key = key
        # ":1.53/org/ayatana/..." или просто "org.foo.Bar" (путь по умолчанию)
        i = key.find("/")
        self.service, self.path = (key[:i], key[i:]) if i > 0 else (key, "/StatusNotifierItem")
        self.on_change = on_change
        self.props = {}
        self._sub = conn.signal_subscribe(
            self.service, ITEM_IFACE, None, self.path, None,
            Gio.DBusSignalFlags.NONE, lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        bus_call(self.conn, self.service, self.path, PROPS_IFACE, "GetAll",
                 GLib.Variant("(s)", (ITEM_IFACE,)), "(a{sv})", self._on_props)

    def _on_props(self, out, err):
        if err:
            print(f"evertray: {self.key}: {err.message}", file=sys.stderr)
            return
        self.props = out[0]
        self.on_change(self)

    def close(self):
        self.conn.signal_unsubscribe(self._sub)

    @property
    def title(self):
        tip = self.props.get("ToolTip")
        return (self.props.get("Title") or (tip[2] if tip else "")
                or self.props.get("Id") or self.service)

    @property
    def status(self):
        return self.props.get("Status", "Active")

    @property
    def menu_path(self):
        return self.props.get("Menu")

    def icon(self):
        """(icon_name, None) | (None, texture) | (None, None)."""
        attention = self.status == "NeedsAttention"
        name = (attention and self.props.get("AttentionIconName")) or self.props.get("IconName")
        pixmaps = (attention and self.props.get("AttentionIconPixmap")) or self.props.get("IconPixmap")
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        extra = self.props.get("IconThemePath")
        if extra and extra not in theme.get_search_path():
            theme.add_search_path(extra)
        if name:
            if name.startswith("/") and os.path.exists(name):
                return None, Gdk.Texture.new_from_filename(name)
            if theme.has_icon(name):
                return name, None
        if pixmaps:
            w, h, data = max(pixmaps, key=lambda p: p[0] * p[1])
            return None, argb_to_texture(w, h, data)
        return (name or "application-x-executable"), None

    def call(self, method, *args, on_error=None):
        sig = {"Activate": "(ii)", "SecondaryActivate": "(ii)", "Scroll": "(is)"}[method]
        bus_call(self.conn, self.service, self.path, ITEM_IFACE, method,
                 GLib.Variant(sig, args), None,
                 lambda _out, err: err and on_error and on_error(err))


class Menu(Gtk.Popover):
    """dbusmenu, нарисованное своими виджетами; подменю — страницы в Stack."""

    def __init__(self, item, parent):
        super().__init__(has_arrow=False, css_classes=["tray-menu"],
                         position=Gtk.PositionType.BOTTOM)
        self.item = item
        self.set_parent(parent)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=120, vhomogeneous=False,
                               interpolate_size=True)
        self.set_child(self.stack)
        self.connect("closed", lambda *_: GLib.idle_add(self.unparent))
        self.pages = []

    def open(self):
        conn, svc, path = self.item.conn, self.item.service, self.item.menu_path
        # AboutToShow даёт приложению обновить меню; ошибку игнорируем —
        # многие его не реализуют
        bus_call(conn, svc, path, MENU_IFACE, "AboutToShow",
                 GLib.Variant("(i)", (0,)), None,
                 lambda *_: bus_call(conn, svc, path, MENU_IFACE, "GetLayout",
                                     GLib.Variant("(iias)", (0, -1, [])),
                                     "(u(ia{sv}av))", self._on_layout))

    def _on_layout(self, out, err):
        if err:
            print(f"evertray: меню {self.item.key}: {err.message}", file=sys.stderr)
            self.unparent()
            return
        _rev, root = out
        self._push(root, None)
        self.popup()

    def _push(self, node, title):
        _id, _props, children = node
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["menu-page"])
        if title is not None:
            back = Gtk.Button(css_classes=["menu-back"])
            back.set_child(Gtk.Label(label=f"‹  {title}", xalign=0))
            back.connect("clicked", lambda *_: self._pop())
            page.append(back)
            page.append(Gtk.Separator(css_classes=["menu-sep"]))
        for child in children:
            row = self._row(child)
            if row:
                page.append(row)
        name = f"p{len(self.pages)}"
        self.pages.append(name)
        self.stack.add_named(page, name)
        self.stack.set_visible_child_name(name)

    def _pop(self):
        old = self.pages.pop()
        self.stack.set_visible_child_name(self.pages[-1])
        page = self.stack.get_child_by_name(old)
        GLib.timeout_add(200, lambda: (self.stack.remove(page), False)[1])

    def _row(self, node):
        mid, props, children = node
        if not props.get("visible", True):
            return None
        if props.get("type") == "separator":
            return Gtk.Separator(css_classes=["menu-sep"])
        label = strip_mnemonic(props.get("label", ""))
        btn = Gtk.Button(css_classes=["menu-item"], sensitive=props.get("enabled", True))
        box = Gtk.Box(spacing=8)
        toggle = props.get("toggle-type", "")
        mark = ""
        if toggle == "checkmark":
            mark = "✓" if props.get("toggle-state") == 1 else ""
        elif toggle == "radio":
            mark = "●" if props.get("toggle-state") == 1 else "○"
        box.append(Gtk.Label(label=mark, css_classes=["menu-mark"], width_chars=1))
        box.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        submenu = props.get("children-display") == "submenu" or children
        if submenu:
            box.append(Gtk.Label(label="›", css_classes=["menu-mark"]))
        btn.set_child(box)
        if submenu:
            btn.connect("clicked", lambda *_: self._open_sub(mid, node, label))
        else:
            btn.connect("clicked", lambda *_: self._click(mid))
        return btn

    def _open_sub(self, mid, node, label):
        bus_call(self.item.conn, self.item.service, self.item.menu_path, MENU_IFACE,
                 "AboutToShow", GLib.Variant("(i)", (mid,)), None)
        self._push(node, label)

    def _click(self, mid):
        bus_call(self.item.conn, self.item.service, self.item.menu_path, MENU_IFACE,
                 "Event", GLib.Variant("(isvu)", (mid, "clicked", GLib.Variant("i", 0),
                                                  int(time.time()) & 0xFFFFFFFF)), None)
        self.popdown()


class Tile(Gtk.FlowBoxChild):
    def __init__(self, item):
        super().__init__(css_classes=["tile"])
        self.item = item
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.image = Gtk.Image(css_classes=["tray-icon"])
        self.label = Gtk.Label(css_classes=["name"], ellipsize=Pango.EllipsizeMode.END,
                               max_width_chars=10, width_chars=8)
        box.append(self.image)
        box.append(self.label)
        self.set_child(box)

        click = Gtk.GestureClick(button=0)
        click.connect("released", self._on_click)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.BOTH_AXES
            | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        self.update()

    def update(self):
        it = self.item
        name, texture = it.icon()
        if texture:
            self.image.set_from_paintable(texture)
        else:
            self.image.set_from_icon_name(name)
        self.label.set_text(it.title)
        self.set_tooltip_text(it.title)
        for cls, on in (("attention", it.status == "NeedsAttention"),
                        ("passive", it.status == "Passive")):
            (self.add_css_class if on else self.remove_css_class)(cls)

    def activate_item(self):
        it = self.item
        if it.props.get("ItemIsMenu") and it.menu_path:
            return self.show_menu()
        # не поддерживает Activate — показываем меню
        it.call("Activate", 0, 0, on_error=lambda _e: self.show_menu())

    def show_menu(self):
        if self.item.menu_path:
            Menu(self.item, self).open()

    def _on_click(self, gesture, *_):
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_SECONDARY:
            self.show_menu()
        elif button == Gdk.BUTTON_MIDDLE:
            self.item.call("SecondaryActivate", 0, 0)
        else:
            self.activate_item()

    def _on_scroll(self, _ctl, dx, dy):
        if dy:
            self.item.call("Scroll", int(-dy), "vertical")
        elif dx:
            self.item.call("Scroll", int(dx), "horizontal")
        return True


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="evertray",
                         default_width=380, default_height=160)
        # Hyprland не даёт серверных декораций — без этого GTK нарисует свой
        # заголовок с кнопками, который выбивается из стиля.
        self.set_decorated(False)
        self.add_css_class("evertray")
        self.size_class = None
        self.items = {}   # key → TrayItem
        self.tiles = {}   # key → Tile

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       css_classes=["content"])
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label="трей", css_classes=["title"], xalign=0))
        self.count = Gtk.Label(css_classes=["dim"], xalign=0, hexpand=True)
        head.append(self.count)
        root.append(head)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                homogeneous=True, max_children_per_line=12,
                                row_spacing=4, column_spacing=4,
                                valign=Gtk.Align.START)
        self.flow.set_sort_func(lambda a, b: (a.item.title.lower() > b.item.title.lower())
                                - (a.item.title.lower() < b.item.title.lower()))
        self.empty = Gtk.Label(label="в трее пусто", css_classes=["dim"],
                               vexpand=True, valign=Gtk.Align.CENTER)
        scroll = Gtk.ScrolledWindow(child=self.flow, vexpand=True,
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

        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._subs = [
            self.conn.signal_subscribe(WATCHER, WATCHER, sig, WATCHER_PATH, None,
                                       Gio.DBusSignalFlags.NONE, handler)
            for sig, handler in (
                ("StatusNotifierItemRegistered", lambda *a: self._add(a[5][0])),
                ("StatusNotifierItemUnregistered", lambda *a: self._remove(a[5][0])))]
        self._register_host()
        bus_call(self.conn, WATCHER, WATCHER_PATH, PROPS_IFACE, "Get",
                 GLib.Variant("(ss)", (WATCHER, "RegisteredStatusNotifierItems")),
                 "(v)", self._on_items)
        self.connect("close-request", self._on_close)
        self._render_count()

    def _register_host(self):
        # хостов может быть несколько (waybar, kded) — регистрируемся своим
        # именем, чтобы приложения считали, что трей есть
        self.host_name = f"org.kde.StatusNotifierHost-{os.getpid()}"
        self.host_id = Gio.bus_own_name_on_connection(
            self.conn, self.host_name, Gio.BusNameOwnerFlags.NONE,
            lambda *_: bus_call(self.conn, WATCHER, WATCHER_PATH, WATCHER,
                                "RegisterStatusNotifierHost",
                                GLib.Variant("(s)", (self.host_name,)), None),
            None)

    def _on_items(self, out, err):
        if err:
            self.empty.set_text("нет StatusNotifierWatcher")
            print(f"evertray: {err.message}", file=sys.stderr)
            return
        for key in out[0]:
            self._add(key)

    def _add(self, key):
        if key not in self.items:
            self.items[key] = TrayItem(self.conn, key, self._on_item)

    def _on_item(self, item):
        tile = self.tiles.get(item.key)
        if tile is None:
            tile = self.tiles[item.key] = Tile(item)
            self.flow.append(tile)
        else:
            tile.update()
            tile.changed()  # пересортировать, если сменился заголовок
        self._render_count()

    def _remove(self, key):
        item = self.items.pop(key, None)
        if item:
            item.close()
        tile = self.tiles.pop(key, None)
        if tile:
            self.flow.remove(tile)
        self._render_count()

    def _render_count(self):
        n = len(self.tiles)
        self.count.set_text(str(n) if n else "")
        self.empty.set_visible(n == 0)

    def _on_close(self, *_):
        for sub in self._subs:
            self.conn.signal_unsubscribe(sub)
        for item in self.items.values():
            item.close()
        Gio.bus_unown_name(self.host_id)
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

    def _focused_tile(self):
        w = self.get_focus()
        while w is not None and not isinstance(w, Tile):
            w = w.get_parent()
        return w

    def _on_key(self, _ctl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            tile = self._focused_tile()
            if tile:
                tile.activate_item()
        elif keyval == Gdk.KEY_Menu or keyval == Gdk.KEY_m:
            tile = self._focused_tile()
            if tile:
                tile.show_menu()
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
            self.theme = Theme("evertray", STYLE_CSS, THEME_CSS)
        # повторный запуск закрывает окно — удобно вешать на клик в waybar
        win = self.get_active_window()
        if win:
            win.close()
            return
        Window(self).present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

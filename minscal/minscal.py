#!/usr/bin/env python3
# minscal — локальный календарь с событиями в стиле waycal.
# Цвета берёт из системной темы (~/.config/hypr/theme/current.css,
# её переключает theme/scripts/switch.sh) и перекрашивается вживую.

import json
import os
import re
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from minstheme import Theme  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

APP_ID = "dev.natsu.minscal"
HERE = Path(__file__).resolve().parent
STYLE_CSS = HERE / "style.css"
DATA_FILE = Path(GLib.get_user_data_dir()) / "minscal" / "events.json"
SETTINGS_FILE = Path(GLib.get_user_config_dir()) / "minscal" / "settings.json"

MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
          "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн",
                "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница",
                 "суббота", "воскресенье"]
COLORS = ["accent", "red", "green", "yellow", "blue", "purple", "cyan"]
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
PICK = 12  # ячеек в сетке выбора месяца/года (3×4)


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as e:
        # Битый файл не перезаписываем молча — откладываем в сторону.
        backup = path.with_name(f"{path.name}.broken-{int(time.time())}")
        print(f"minscal: {path}: {e}; сохранён как {backup}", file=sys.stderr)
        try:
            path.rename(backup)
        except OSError:
            pass
        return default


def with_month(d, year, month):
    # день, которого нет в новом месяце (31 → февраль), прижимаем к концу
    for day in range(d.day, 27, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


def add_months(d, n):
    m = d.month - 1 + n
    return with_month(d, d.year + m // 12, m % 12 + 1)


def time_key(ev):
    return (not ev.get("all_day"), ev.get("start") or "", ev.get("title", ""))


def set_class(widget, cls, on):
    (widget.add_css_class if on else widget.remove_css_class)(cls)


class Store:
    """events.json: {"YYYY-MM-DD": [{id, title, start, end, all_day, color, note, done}]}"""

    def __init__(self, path):
        self.path = path
        self.events = read_json(path, {})

    def day(self, d):
        return sorted(self.events.get(d.isoformat(), []), key=time_key)

    def upsert(self, d, ev):
        lst = self.events.setdefault(d.isoformat(), [])
        for i, old in enumerate(lst):
            if old["id"] == ev["id"]:
                lst[i] = ev
                break
        else:
            lst.append(ev)
        self.save()

    def delete(self, d, ev_id):
        key = d.isoformat()
        self.events[key] = [e for e in self.events.get(key, []) if e["id"] != ev_id]
        if not self.events[key]:
            del self.events[key]
        self.save()

    def save(self):
        write_json_atomic(self.path, self.events)


def to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def from_min(total):
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


class TimeField(Gtk.Button):
    """Кнопка «ЧЧ:ММ» с попапом: сетка часов и сетка минут (шаг 5).
    Колесо над кнопкой — ±15 минут, с Shift — ±1 час."""

    def __init__(self, placeholder, on_change=None, clearable=False):
        super().__init__(css_classes=["time-field"])
        self.placeholder = placeholder
        self.on_change = on_change
        self.value = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(Gtk.Label(label="часы", xalign=0, css_classes=["tp-caption"]))
        self.hours = self._grid(box, [f"{h:02d}" for h in range(24)], 6, self._on_hour)
        box.append(Gtk.Separator(css_classes=["tp-sep"]))
        box.append(Gtk.Label(label="минуты", xalign=0, css_classes=["tp-caption"]))
        self.minutes = self._grid(box, [f"{m:02d}" for m in range(0, 60, 5)], 6,
                                  self._on_minute)
        if clearable:
            clear = Gtk.Button(label="без конца", css_classes=["text-btn"],
                               halign=Gtk.Align.END)
            clear.connect("clicked", lambda *_: (self.set_value(None, notify=True),
                                                 self.popover.popdown()))
            box.append(clear)
        self.popover = Gtk.Popover(child=box, has_arrow=False,
                                   css_classes=["time-popover"])
        self.popover.set_parent(self)
        self.connect("clicked", lambda *_: self._open())

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        self._sync()

    def _grid(self, box, labels, cols, handler):
        grid = Gtk.Grid(row_spacing=2, column_spacing=2, column_homogeneous=True)
        buttons = []
        for i, text in enumerate(labels):
            b = Gtk.Button(label=text, css_classes=["tp"], focusable=False)
            b.connect("clicked", handler, text)
            grid.attach(b, i % cols, i // cols, 1, 1)
            buttons.append(b)
        box.append(grid)
        return buttons

    def set_value(self, value, notify=False):
        self.value = value
        self._sync()
        if notify and self.on_change:
            self.on_change(self)

    def _sync(self):
        self.set_label(self.value or self.placeholder)
        set_class(self, "empty", self.value is None)
        h, m = self.value.split(":") if self.value else (None, None)
        for b in self.hours:
            set_class(b, "selected", b.get_label() == h)
        for b in self.minutes:
            set_class(b, "selected", b.get_label() == m)

    def _open(self):
        self._sync()
        self.popover.popup()

    def _on_hour(self, _b, hh):
        mm = self.value.split(":")[1] if self.value else "00"
        self.set_value(f"{hh}:{mm}", notify=True)

    def _on_minute(self, _b, mm):
        hh = self.value.split(":")[0] if self.value else "09"
        self.set_value(f"{hh}:{mm}", notify=True)
        self.popover.popdown()

    def _on_scroll(self, ctl, _dx, dy):
        if not self.get_sensitive() or dy == 0:
            return False
        shift = ctl.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK
        step = 60 if shift else 15
        cur = to_min(self.value or self.placeholder)
        # к сетке шага: 09:07 → 09:15 вверх, 09:00 вниз
        if dy < 0:
            new = (cur // step + 1) * step
        else:
            new = (cur - 1) // step * step
        self.set_value(from_min(new), notify=True)
        return True


class EventForm(Gtk.Box):
    def __init__(self, on_save, on_cancel):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add_css_class("form")
        self.on_save = on_save
        self.on_cancel = on_cancel
        self.ev_id = None
        self.ev_done = False

        self.title = Gtk.Entry(placeholder_text="название")
        self.title.connect("activate", lambda *_: self._save())
        self.append(self.title)

        times = Gtk.Box(spacing=6)
        self.start = TimeField("09:00", on_change=self._on_start)
        self.end = TimeField("10:00", clearable=True)
        self.all_day = Gtk.CheckButton(label="весь день", hexpand=True,
                                       halign=Gtk.Align.END)
        self.all_day.connect("toggled", self._sync_all_day)
        times.append(self.start)
        times.append(Gtk.Label(label="–", css_classes=["dim"]))
        times.append(self.end)
        times.append(self.all_day)
        self.append(times)

        swatches = Gtk.Box(spacing=6)
        self.swatches = {}
        group = None
        for c in COLORS:
            b = Gtk.ToggleButton(css_classes=["swatch", f"c-{c}"], tooltip_text=c,
                                 valign=Gtk.Align.CENTER)
            if group:
                b.set_group(group)
            group = group or b
            swatches.append(b)
            self.swatches[c] = b
        self.append(swatches)

        self.note = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 top_margin=6, bottom_margin=6,
                                 left_margin=8, right_margin=8)
        scroll = Gtk.ScrolledWindow(child=self.note, vexpand=True,
                                    css_classes=["note"])
        self.append(scroll)

        self.error = Gtk.Label(css_classes=["error"], xalign=0)
        self.append(self.error)

        buttons = Gtk.Box(spacing=4, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="отмена", css_classes=["text-btn"])
        cancel.connect("clicked", lambda *_: self.on_cancel())
        save = Gtk.Button(label="сохранить", css_classes=["text-btn", "accent"])
        save.connect("clicked", lambda *_: self._save())
        buttons.append(cancel)
        buttons.append(save)
        self.append(buttons)

    def fill(self, ev=None):
        ev = ev or {}
        self.ev_id = ev.get("id")
        self.ev_done = ev.get("done", False)
        self.title.set_text(ev.get("title", ""))
        self.start.set_value(ev.get("start"))
        self.end.set_value(ev.get("end"))
        self.all_day.set_active(ev.get("all_day", False))
        self.swatches[ev.get("color", "accent")].set_active(True)
        self.note.get_buffer().set_text(ev.get("note", ""))
        self.error.set_text("")
        self._sync_all_day()
        self.title.grab_focus()

    def _sync_all_day(self, *_):
        on = not self.all_day.get_active()
        self.start.set_sensitive(on)
        self.end.set_sensitive(on)

    def _on_start(self, field):
        # конец не задан или уехал раньше начала — ставим +1 час
        end = self.end.value
        if end is None or to_min(end) <= to_min(field.value):
            self.end.set_value(from_min(min(to_min(field.value) + 60, 23 * 60 + 55)))
        self.error.set_text("")

    def popdown(self):
        """Закрыть открытый попап времени; True — если он был открыт."""
        for f in (self.start, self.end):
            if f.popover.get_visible():
                f.popover.popdown()
                return True
        return False

    def _save(self):
        title = self.title.get_text().strip()
        if not title:
            return self.error.set_text("нужно название")
        all_day = self.all_day.get_active()
        start = self.start.value or ""
        end = self.end.value or ""
        if not all_day:
            if not start:
                all_day = True
            elif not TIME_RE.match(start) or (end and not TIME_RE.match(end)):
                return self.error.set_text("время в формате ЧЧ:ММ")
            elif end and end.zfill(5) < start.zfill(5):
                return self.error.set_text("конец раньше начала")
        buf = self.note.get_buffer()
        color = next(c for c, b in self.swatches.items() if b.get_active())
        self.on_save({
            "id": self.ev_id or uuid.uuid4().hex,
            "title": title,
            "all_day": all_day,
            "start": None if all_day else start.zfill(5),
            "end": None if all_day or not end else end.zfill(5),
            "color": color,
            "note": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip(),
            "done": self.ev_done,
        })


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="minscal",
                         default_width=760, default_height=440)
        # Hyprland не даёт серверных декораций — без этого GTK нарисует свой
        # заголовок с кнопками, который выбивается из стиля.
        self.set_decorated(False)
        self.add_css_class("minscal")

        self.store = Store(DATA_FILE)
        self.settings = read_json(SETTINGS_FILE, {})
        self.set_style(self.settings.get("style", "rounded"), save=False)

        self.today = date.today()
        self.selected = self.today
        self.view = "days"            # days | months | years
        self.pick_year = self.today.year

        root = Gtk.Box(css_classes=["root"])
        root.append(self._build_calendar())
        root.append(self._build_sidebar())
        root.append(self._build_agenda())
        self.set_child(root)
        self.set_agenda(self.settings.get("agenda", True), save=False, animate=False)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        # полночь, пока окно открыто — переносим «сегодня»
        GLib.timeout_add_seconds(60, self._tick)
        self.render()

    # --- построение ---

    def _build_calendar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      css_classes=["calendar"], hexpand=True)

        header = Gtk.Box()
        prev = Gtk.Button(label="‹", css_classes=["nav"])
        prev.connect("clicked", lambda *_: self.step(-1))
        nxt = Gtk.Button(label="›", css_classes=["nav"])
        nxt.connect("clicked", lambda *_: self.step(1))
        title = Gtk.Box(hexpand=True, halign=Gtk.Align.CENTER)
        self.month_btn = Gtk.Button(css_classes=["title-btn"])
        self.month_btn.connect("clicked", lambda *_: self.open_picker("months"))
        self.year_btn = Gtk.Button(css_classes=["title-btn"])
        self.year_btn.connect("clicked", lambda *_: self.open_picker("years"))
        self.range_label = Gtk.Label(css_classes=["title"])
        title.append(self.month_btn)
        title.append(self.year_btn)
        title.append(self.range_label)
        header.append(prev)
        header.append(title)
        header.append(nxt)
        box.append(header)

        self.cal_stack = Gtk.Stack(vexpand=True, transition_duration=120,
                                   transition_type=Gtk.StackTransitionType.CROSSFADE)

        days = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        weekdays = Gtk.Box(homogeneous=True)
        for name in WEEKDAYS:
            weekdays.append(Gtk.Label(label=name, css_classes=["weekday"]))
        days.append(weekdays)
        grid = Gtk.Grid(row_spacing=2, column_spacing=2, vexpand=True,
                        row_homogeneous=True, column_homogeneous=True)
        self.cells = []
        for i in range(42):
            num = Gtk.Label(css_classes=["num"])
            dot = Gtk.Label(label="•", css_classes=["dot"])
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            valign=Gtk.Align.CENTER)
            inner.append(num)
            inner.append(dot)
            btn = Gtk.Button(child=inner, css_classes=["day"], focusable=False)
            btn.connect("clicked", self._on_cell, i)
            grid.attach(btn, i % 7, i // 7, 1, 1)
            self.cells.append((btn, num, dot))
        self.cell_dates = [None] * 42
        days.append(grid)
        self.cal_stack.add_named(days, "days")

        # одна сетка 3×4 на оба пикера: месяцы года или 12 лет подряд
        pick = Gtk.Grid(row_spacing=4, column_spacing=4,
                        row_homogeneous=True, column_homogeneous=True)
        self.pick_cells = []
        for i in range(PICK):
            btn = Gtk.Button(css_classes=["pick"], focusable=False)
            btn.connect("clicked", self._on_pick, i)
            pick.attach(btn, i % 3, i // 3, 1, 1)
            self.pick_cells.append(btn)
        self.cal_stack.add_named(pick, "picker")
        box.append(self.cal_stack)
        return box

    def _build_sidebar(self):
        # узкая полоса между календарём и событиями: в свёрнутом виде
        # остаётся только она — кнопка разворота, точки событий дня и «+»
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      css_classes=["rail"])
        self.toggle_btn = Gtk.Button(css_classes=["nav", "toggle"],
                                     tooltip_text="события (a)")
        self.toggle_btn.connect("clicked", lambda *_: self.set_agenda(not self.agenda_open))
        bar.append(self.toggle_btn)
        self.mini = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                            vexpand=True)
        bar.append(self.mini)
        self.mini_add = Gtk.Button(label="+", css_classes=["nav"],
                                   tooltip_text="новое событие (n)")
        self.mini_add.connect("clicked", lambda *_: self.edit(None))
        bar.append(self.mini_add)
        return bar

    def _build_agenda(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      css_classes=["agenda"], width_request=260)
        head = Gtk.Box(spacing=8)
        self.day_label = Gtk.Label(xalign=0, css_classes=["title"])
        self.weekday_label = Gtk.Label(xalign=0, css_classes=["dim"],
                                       valign=Gtk.Align.BASELINE)
        head.append(self.day_label)
        head.append(self.weekday_label)
        box.append(head)

        self.stack = Gtk.Stack(vexpand=True, transition_duration=120,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)

        list_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.listbox = Gtk.ListBox(css_classes=["events"],
                                   selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", lambda _lb, row: self.edit(row.ev))
        self.empty = Gtk.Label(label="пусто", css_classes=["dim"], xalign=0)
        scroll = Gtk.ScrolledWindow(child=self.listbox, vexpand=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        list_page.append(self.empty)
        list_page.append(scroll)
        add = Gtk.Button(label="+ событие", css_classes=["text-btn"],
                         halign=Gtk.Align.START)
        add.connect("clicked", lambda *_: self.edit(None))
        list_page.append(add)
        self.stack.add_named(list_page, "list")

        self.form = EventForm(self._on_form_save, self.show_list)
        self.stack.add_named(self.form, "form")
        box.append(self.stack)

        # hexpand=False явно: иначе он наследуется от растягивающихся детей
        # формы, и свёрнутая панель забирает половину свободного места.
        self.revealer = Gtk.Revealer(child=box, transition_duration=180, hexpand=False,
                                     transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)
        return self.revealer

    def _event_row(self, ev):
        row = Gtk.ListBoxRow(css_classes=["event"])
        row.ev = ev
        set_class(row, "done", ev.get("done", False))
        hbox = Gtk.Box(spacing=8)
        check = Gtk.CheckButton(active=ev.get("done", False), valign=Gtk.Align.START,
                                css_classes=[f"ev-{ev.get('color', 'accent')}"],
                                tooltip_text="выполнено (пробел)")
        check.connect("toggled", self._on_done, row)
        hbox.append(check)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(Gtk.Label(label=ev["title"], xalign=0, wrap=True,
                              css_classes=["ev-title"]))
        if ev.get("all_day"):
            when = "весь день"
        else:
            when = ev["start"] + (f"–{ev['end']}" if ev.get("end") else "")
        meta = when + (f" · {ev['note']}" if ev.get("note") else "")
        text.append(Gtk.Label(label=meta, xalign=0, wrap=True,
                              css_classes=["ev-meta"]))
        hbox.append(text)
        rm = Gtk.Button(label="✕", css_classes=["remove"], valign=Gtk.Align.START,
                        tooltip_text="удалить (del)")
        rm.connect("clicked", lambda *_: self.delete(ev))
        hbox.append(rm)
        row.set_child(hbox)
        return row

    # --- отрисовка ---

    def render(self):
        if self.view == "days":
            self._render_days()
        else:
            self._render_picker()
        self.render_day()

    def _render_days(self):
        first = self.selected.replace(day=1)
        start = first - timedelta(days=first.weekday())
        self._set_title(MONTHS[first.month - 1], str(first.year))
        for i, (btn, num, dot) in enumerate(self.cells):
            d = start + timedelta(days=i)
            self.cell_dates[i] = d
            num.set_text(str(d.day))
            events = self.store.day(d)
            dot.set_opacity(1 if events else 0)
            set_class(dot, "all-done", bool(events) and all(e.get("done") for e in events))
            set_class(btn, "other-month", d.month != first.month)
            set_class(btn, "today", d == self.today)
            set_class(btn, "selected", d == self.selected)

    def _render_picker(self):
        if self.view == "months":
            self._set_title(None, str(self.pick_year))
            for i, btn in enumerate(self.pick_cells):
                btn.set_label(MONTHS_SHORT[i])
                set_class(btn, "today", (self.pick_year, i + 1) == (self.today.year, self.today.month))
                set_class(btn, "selected", (self.pick_year, i + 1) == (self.selected.year, self.selected.month))
        else:
            first = self._years_start()
            self._set_title(None, None, f"{first} – {first + PICK - 1}")
            for i, btn in enumerate(self.pick_cells):
                y = first + i
                btn.set_label(str(y))
                set_class(btn, "today", y == self.today.year)
                set_class(btn, "selected", y == self.selected.year)

    def _set_title(self, month, year, rng=None):
        self.month_btn.set_visible(month is not None)
        self.year_btn.set_visible(year is not None)
        self.range_label.set_visible(rng is not None)
        if month:
            self.month_btn.set_label(month)
        if year:
            self.year_btn.set_label(year)
        if rng:
            self.range_label.set_text(rng)

    def _years_start(self):
        return self.pick_year - self.pick_year % PICK

    def render_day(self):
        d = self.selected
        self.day_label.set_text(f"{d.day} {MONTHS_GEN[d.month - 1]}")
        self.weekday_label.set_text(
            "сегодня" if d == self.today else WEEKDAYS_FULL[d.weekday()])
        self.listbox.remove_all()
        events = self.store.day(d)
        for ev in events:
            self.listbox.append(self._event_row(ev))
        self.empty.set_visible(not events)
        self._render_mini(events)

    def _render_mini(self, events):
        child = self.mini.get_first_child()
        while child:
            self.mini.remove(child)
            child = self.mini.get_first_child()
        for ev in events:
            dot = Gtk.Box(css_classes=["mini-dot", f"c-{ev.get('color', 'accent')}"],
                          halign=Gtk.Align.CENTER, tooltip_text=ev["title"])
            set_class(dot, "done", ev.get("done", False))
            self.mini.append(dot)

    # --- действия ---

    def select(self, d):
        self.selected = d
        self.view = "days"
        self.cal_stack.set_visible_child_name("days")
        self.show_list()
        self.render()

    def step(self, n):
        """‹ / › в заголовке: месяц, год или страница лет — смотря что открыто."""
        if self.view == "days":
            self.select(add_months(self.selected, n))
        else:
            self.pick_year += n * (1 if self.view == "months" else PICK)
            self._render_picker()

    def open_picker(self, view):
        self.view = view
        self.pick_year = self.selected.year
        self.cal_stack.set_visible_child_name("picker")
        self._render_picker()

    def close_picker(self):
        self.view = "days"
        self.cal_stack.set_visible_child_name("days")
        self._render_days()

    def set_agenda(self, open_, save=True, animate=True):
        self.agenda_open = open_
        self.revealer.set_transition_duration(180 if animate else 0)
        self.revealer.set_reveal_child(open_)
        self.mini.set_visible(not open_)
        self.mini_add.set_visible(not open_)
        self.toggle_btn.set_label("›" if open_ else "‹")
        if not open_:
            self.show_list()
        self.settings["agenda"] = open_
        if save:
            write_json_atomic(SETTINGS_FILE, self.settings)

    def show_list(self):
        self.stack.set_visible_child_name("list")

    def edit(self, ev):
        if not self.agenda_open:
            self.set_agenda(True)
        self.form.fill(ev)
        self.stack.set_visible_child_name("form")

    def delete(self, ev):
        self.store.delete(self.selected, ev["id"])
        self.render()

    def toggle_done(self, ev, done=None):
        ev = dict(ev, done=not ev.get("done", False) if done is None else done)
        self.store.upsert(self.selected, ev)
        return ev

    def set_style(self, style, save=True):
        for s in ("sharp", "rounded"):
            self.remove_css_class(s)
        self.add_css_class(style)
        self.settings["style"] = style
        if save:
            write_json_atomic(SETTINGS_FILE, self.settings)

    def _selected_row(self):
        return self.listbox.get_selected_row() or self.listbox.get_row_at_index(0)

    # --- обработчики ---

    def _on_cell(self, _btn, i):
        d = self.cell_dates[i]
        if d == self.selected and self.stack.get_visible_child_name() == "list":
            return self.edit(None)  # повторный клик по дню — новое событие
        self.select(d)

    def _on_pick(self, _btn, i):
        if self.view == "months":
            self.select(with_month(self.selected, self.pick_year, i + 1))
        else:
            self.view = "months"
            self.pick_year = self._years_start() + i
            self._render_picker()

    def _on_done(self, check, row):
        # строку не пересобираем — галочка и фокус остаются на месте
        row.ev = self.toggle_done(row.ev, check.get_active())
        set_class(row, "done", row.ev["done"])
        self._render_days() if self.view == "days" else None
        self._render_mini(self.store.day(self.selected))

    def _on_form_save(self, ev):
        self.store.upsert(self.selected, ev)
        self.show_list()
        self.render()

    def _on_key(self, _ctl, keyval, _code, state):
        if self.stack.get_visible_child_name() == "form":
            if keyval == Gdk.KEY_Escape:
                # Esc сначала закрывает попап времени, потом уже форму
                if not self.form.popdown():
                    self.show_list()
                return True
            return False

        if self.view != "days":
            if keyval in (Gdk.KEY_Left, Gdk.KEY_h, Gdk.KEY_Page_Up):
                self.step(-1)
            elif keyval in (Gdk.KEY_Right, Gdk.KEY_l, Gdk.KEY_Page_Down):
                self.step(1)
            elif keyval in (Gdk.KEY_Escape, Gdk.KEY_m):
                self.close_picker()
            else:
                return False
            return True

        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        moves = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1, Gdk.KEY_Up: -7, Gdk.KEY_Down: 7,
                 Gdk.KEY_h: -1, Gdk.KEY_l: 1, Gdk.KEY_k: -7, Gdk.KEY_j: 7}
        if keyval in moves:
            self.select(self.selected + timedelta(days=moves[keyval]))
        elif keyval in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            step = -1 if keyval == Gdk.KEY_Page_Up else 1
            self.select(add_months(self.selected, step * (12 if shift else 1)))
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_t):
            self.select(date.today())
        elif keyval == Gdk.KEY_m:
            self.open_picker("months")
        elif keyval == Gdk.KEY_y:
            self.open_picker("years")
        elif keyval in (Gdk.KEY_a, Gdk.KEY_Tab):
            self.set_agenda(not self.agenda_open)
        elif keyval == Gdk.KEY_n:
            self.edit(None)
        elif keyval in (Gdk.KEY_e, Gdk.KEY_Delete, Gdk.KEY_space, Gdk.KEY_x):
            row = self._selected_row()
            if row is None:
                return True
            if keyval == Gdk.KEY_e:
                self.edit(row.ev)
            elif keyval == Gdk.KEY_Delete:
                self.delete(row.ev)
            else:
                check = row.get_child().get_first_child()
                check.set_active(not check.get_active())
        elif keyval == Gdk.KEY_s:
            cur = self.settings.get("style", "rounded")
            self.set_style("sharp" if cur == "rounded" else "rounded")
        elif keyval == Gdk.KEY_Escape:
            self.close()
        else:
            return False
        return True

    def _tick(self):
        if date.today() != self.today:
            self.today = date.today()
            self.render()
        return GLib.SOURCE_CONTINUE


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.theme = None

    def do_activate(self):
        if self.theme is None:
            self.theme = Theme("minscal", STYLE_CSS)
        win = self.get_active_window() or Window(self)
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

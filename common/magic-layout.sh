#!/usr/bin/env bash
# Собирает раскладку mins*-приложений на magic layer (SUPER+S), dwindle:
#
#   ┌───────────┬────────────┬───────────┐
#   │           │ minsnotify │           │
#   │  minscal  ├────────────┤           │
#   │           │  minsplay  │ obsidian  │
#   ├───────────┼─────┬──────┤           │
#   │ minswatch │tray │clock │           │
#   └───────────┴─────┴──────┴───────────┘
#
# Если obsidian не установлен, правой колонки нет — остаются две.
#
# Dwindle делит окно в фокусе, поэтому окна открываются по очереди:
# перед каждым фокусируем «соседа», задаём preselect, ждём появления окна
# и сразу выставляем пропорции. Уже открытое окно (например, obsidian
# на другом рабочем столе) не запускается заново, а переносится сюда.
#
# Запускается из autostart.lua; руками — только на пустом magic layer.

set -u
BIN="$HOME/.local/bin"

disp() { hyprctl dispatch "$1" >/dev/null; }

exists() { hyprctl clients -j | grep -q "\"class\": \"$1\""; }

# ждём окно класса $1 до 15 секунд (obsidian/electron стартует небыстро)
wait_for() {
    for _ in $(seq 150); do
        exists "$1" && { sleep 0.15; return 0; }
        sleep 0.1
    done
    echo "magic-layout: не дождался окна $1" >&2
    return 1
}

active() { hyprctl activewindow -j | grep -q "\"class\": \"$1\""; }

# focus КЛАСС_REGEX [КЛАСС] — фокус и ожидание, пока окно правда станет
# активным: перенесённое окно (obsidian) может забрать фокус с задержкой,
# и тогда следующее окно разделило бы не тот тайл
focus() {
    disp "hl.dsp.focus({ window = \"class:^$1\$\" })"
    [ -n "${2:-}" ] || return 0
    for _ in $(seq 20); do
        active "$2" && return 0
        sleep 0.05
        disp "hl.dsp.focus({ window = \"class:^$1\$\" })"
    done
    echo "magic-layout: не удалось сфокусировать $2" >&2
}

# place КЛАСС КОМАНДА [СОСЕД НАПРАВЛЕНИЕ]
place() {
    local class="$1" cmd="$2" target="${3:-}" dir="${4:-}"
    local re="${class//./\\\\.}"
    if [ -n "$target" ]; then
        focus "${target//./\\\\.}" "$target"
        disp "hl.dsp.layout(\"preselect $dir\")"
    fi
    if exists "$class"; then
        disp "hl.dsp.window.move({ workspace = \"special:magic\", window = \"class:^$re\$\" })"
        sleep 0.5  # дать перенесённому окну отработать свою активацию
    else
        setsid -f $cmd >/dev/null 2>&1
    fi
    wait_for "$class"
}

# ratio КЛАСС ЧИСЛО — пропорция split'а, в котором лежит окно
# (1.0 — пополам, 0.667 — первое окно занимает треть)
ratio() {
    focus "${1//./\\\\.}" "$1"
    disp "hl.dsp.layout(\"splitratio $2 exact\")"
}

# magic должен быть открыт, чтобы фокус и preselect работали внутри него
if ! hyprctl monitors -j | grep -q '"name": "special:magic"'; then
    disp 'hl.dsp.workspace.toggle_special("magic")'
fi

place dev.natsu.minscal    "$BIN/minscal"
place dev.natsu.minsnotify "$BIN/minsnotify" dev.natsu.minscal    r
# obsidian — необязательная третья колонка: без него две колонки пополам
if exists obsidian || command -v obsidian >/dev/null 2>&1; then
    place obsidian         "obsidian"        dev.natsu.minsnotify r
    ratio dev.natsu.minscal 0.667                 # три равные колонки
fi
place dev.natsu.minswatch  "$BIN/minswatch"  dev.natsu.minscal    d
ratio dev.natsu.minscal 1.39                      # календарь — ~70% высоты
place dev.natsu.minsplay   "$BIN/minsplay"   dev.natsu.minsnotify d
ratio dev.natsu.minsnotify 0.66                   # уведомления — треть высоты
place dev.natsu.minstray   "$BIN/minstray"   dev.natsu.minsplay   d
place dev.natsu.minsclock  "$BIN/minsclock"  dev.natsu.minstray   r

focus 'dev\\.natsu\\.minscal'
# на старте прячем magic обратно — открывается по SUPER+S
[ "${1:-}" = "--hide" ] && disp 'hl.dsp.workspace.toggle_special("magic")'
exit 0

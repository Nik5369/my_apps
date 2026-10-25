#!/usr/bin/env bash
# Собирает раскладку ever*-приложений на magic layer (SUPER+S), dwindle:
#
#   ┌───────────┬────────────┬───────────┐
#   │           │ evernotify │           │
#   │  evercal  ├────────────┤           │
#   │           │  everplay  │ obsidian  │
#   ├───────────┼─────┬──────┤           │
#   │ everwatch │tray │clock │           │
#   └───────────┴─────┴──────┴───────────┘
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

focus() { disp "hl.dsp.focus({ window = \"class:^$1\$\" })"; }

# place КЛАСС КОМАНДА [СОСЕД НАПРАВЛЕНИЕ]
place() {
    local class="$1" cmd="$2" target="${3:-}" dir="${4:-}"
    local re="${class//./\\\\.}"
    if [ -n "$target" ]; then
        focus "${target//./\\\\.}"
        disp "hl.dsp.layout(\"preselect $dir\")"
    fi
    if exists "$class"; then
        disp "hl.dsp.window.move({ workspace = \"special:magic\", window = \"class:^$re\$\" })"
    else
        setsid -f $cmd >/dev/null 2>&1
    fi
    wait_for "$class"
}

# ratio КЛАСС ЧИСЛО — пропорция split'а, в котором лежит окно
# (1.0 — пополам, 0.667 — первое окно занимает треть)
ratio() {
    focus "${1//./\\\\.}"
    disp "hl.dsp.layout(\"splitratio $2 exact\")"
}

# magic должен быть открыт, чтобы фокус и preselect работали внутри него
if ! hyprctl monitors -j | grep -q '"name": "special:magic"'; then
    disp 'hl.dsp.workspace.toggle_special("magic")'
fi

place dev.natsu.evercal    "$BIN/evercal"
place dev.natsu.evernotify "$BIN/evernotify" dev.natsu.evercal    r
place obsidian             "obsidian"        dev.natsu.evernotify r
ratio dev.natsu.evercal 0.667                     # три равные колонки
place dev.natsu.everwatch  "$BIN/everwatch"  dev.natsu.evercal    d
ratio dev.natsu.evercal 1.39                      # календарь — ~70% высоты
place dev.natsu.everplay   "$BIN/everplay"   dev.natsu.evernotify d
ratio dev.natsu.evernotify 0.66                   # уведомления — треть высоты
place dev.natsu.evertray   "$BIN/evertray"   dev.natsu.everplay   d
place dev.natsu.everclock  "$BIN/everclock"  dev.natsu.evertray   r

focus 'dev\\.natsu\\.evercal'
# на старте прячем magic обратно — открывается по SUPER+S
[ "${1:-}" = "--hide" ] && disp 'hl.dsp.workspace.toggle_special("magic")'
exit 0

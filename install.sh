#!/usr/bin/env bash
# Установка mins*-приложений из этого репозитория.
#
#   ./install.sh               ссылки в ~/.local/bin, .desktop-файлы,
#                              D-Bus-активация демона уведомлений
#   ./install.sh --uninstall   убрать всё это (код и данные не трогаются)
#
# Ссылки ведут в текущее место репозитория — после переезда репозитория
# достаточно запустить ./install.sh ещё раз.

set -eu

REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
BIN="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
DBUS_DIR="$HOME/.local/share/dbus-1/services"
DBUS_FILE="$DBUS_DIR/org.freedesktop.Notifications.service"

# имя|описание|иконка|категории
APPS=(
    "minscal|Календарь с событиями|x-office-calendar|Office;Calendar;"
    "minsplay|Мини-плеер с визуализатором|multimedia-audio-player|AudioVideo;Audio;Player;"
    "minsclock|Часы|preferences-system-time|Utility;Clock;"
    "minswatch|Секундомер|chronometer|Utility;Clock;"
    "minstray|Системный трей|preferences-desktop-notification|Utility;"
    "minsnotify|История уведомлений|preferences-desktop-notification-bell|Utility;"
)

uninstall() {
    for entry in "${APPS[@]}"; do
        name="${entry%%|*}"
        rm -f "$BIN/$name" "$APPS_DIR/$name.desktop"
    done
    rm -f "$BIN/mins-magic-layout"
    # D-Bus-файл удаляем, только если он наш
    if grep -q "minsnotify" "$DBUS_FILE" 2>/dev/null; then
        rm -f "$DBUS_FILE"
    fi
    echo "удалено: ссылки в $BIN, .desktop-файлы, D-Bus-активация minsnotify"
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

mkdir -p "$BIN" "$APPS_DIR" "$DBUS_DIR"

for entry in "${APPS[@]}"; do
    IFS='|' read -r name comment icon categories <<<"$entry"
    chmod +x "$REPO/$name/$name.py"
    ln -sfn "$REPO/$name/$name.py" "$BIN/$name"
    cat > "$APPS_DIR/$name.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Comment=$comment
Exec=$BIN/$name
Icon=$icon
Terminal=false
Categories=$categories
StartupWMClass=dev.natsu.$name
EOF
    echo "  $name → $BIN/$name"
done

chmod +x "$REPO/common/magic-layout.sh"
ln -sfn "$REPO/common/magic-layout.sh" "$BIN/mins-magic-layout"
echo "  раскладка magic layer → $BIN/mins-magic-layout"

# Если демона уведомлений нет, первое же уведомление поднимет minsnotify
# (файл в ~/.local/share важнее системных, например swaync).
cat > "$DBUS_FILE" <<EOF
[D-BUS Service]
Name=org.freedesktop.Notifications
Exec=$BIN/minsnotify --daemon
EOF
echo "  D-Bus-активация уведомлений → $DBUS_FILE"

# --- проверка зависимостей: только предупреждения ---

missing=()
python3 - <<'EOF' || missing+=("python-gobject / gtk4 / gtk4-layer-shell / playerctl (GI-библиотеки)")
import gi
for ns, ver in (("Gtk", "4.0"), ("Gtk4LayerShell", "1.0"), ("Playerctl", "2.0")):
    gi.require_version(ns, ver)
from gi.repository import Gtk, Gtk4LayerShell, Playerctl  # noqa: F401
EOF
for cmd in cava playerctl wpctl hyprctl; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
# minsnotify подгружает её через LD_PRELOAD — ищем так же, по имени
python3 -c 'import ctypes.util, sys; sys.exit(not ctypes.util.find_library("gtk4-layer-shell"))' \
    || missing+=("libgtk4-layer-shell (gtk4-layer-shell)")
fc-list | grep -qi "JetBrains Mono" || missing+=("шрифт JetBrains Mono")
fc-list | grep -qi "FiraCode Nerd Font" || missing+=("шрифт FiraCode Nerd Font")
theme="${MINS_THEME:-$HOME/.config/hypr/theme/current.css}"
[ -e "$theme" ] || \
    missing+=("палитра $theme — без неё встроенные цвета (см. palette.example.css в README)")

echo
if [ ${#missing[@]} -gt 0 ]; then
    echo "не хватает:"
    printf '  - %s\n' "${missing[@]}"
    echo
fi

cat <<EOF
готово. Что ещё сделать руками (один раз на новой системе):
  - правила окон и блюра:     вставить $REPO/hypr/rules.lua в ~/.config/hypr/modules/rules.lua
  - автозапуск:               вставить $REPO/hypr/autostart.lua в hl.on("hyprland.start", ...)
  - убрать swaync/другой демон уведомлений из автозапуска
EOF

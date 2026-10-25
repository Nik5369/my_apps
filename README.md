# my_apps

Маленькие GTK4-приложения для Hyprland в одном минималистичном стиле. Цвета берутся
из системной темы (`~/.config/hypr/theme/current.css`), и при её смене окна
перекрашиваются сразу, без перезапуска.

| Приложение | Что делает |
|---|---|
| `evercal` | календарь с событиями, отметкой «выполнено», выбором месяца и года |
| `everplay` | мини-плеер MPRIS: трек, управление, визуализатор cava, громкость колесом (до 150 %) |
| `everclock` | часы с линией текущей минуты |
| `everwatch` | секундомер; общее состояние с waybar-модулем `custom/stopwatch` |
| `evertray` | системный трей (StatusNotifierItem) с меню dbusmenu |
| `evernotify` | демон уведомлений вместо swaync: всплывашки и окно истории |

`common/` — общий модуль темы (`evertheme.py`) и скрипт раскладки magic layer
(`magic-layout.sh`). Приложения ищут `common/` рядом с собой, поэтому структуру
папок нужно сохранять.

## Установка

```sh
git clone <репозиторий> ~/main/my_apps
cd ~/main/my_apps
./install.sh
```

`install.sh` создаёт ссылки в `~/.local/bin` (`evercal`, …, `ever-magic-layout`),
`.desktop`-файлы для лаунчера и файл D-Bus-активации, чтобы уведомления поднимали
evernotify. В конце он проверяет зависимости и сообщает, чего не хватает.
После переезда репозитория достаточно запустить `./install.sh` ещё раз.

`./install.sh --uninstall` убирает ссылки, `.desktop`-файлы и D-Bus-файл.
Код и данные остаются.

### Зависимости (Arch)

```sh
sudo pacman -S python-gobject gtk4 gtk4-layer-shell playerctl cava wireplumber libnotify \
               ttf-jetbrains-mono ttf-firacode-nerd
```

### Hyprland

Эти части живут в `~/.config/hypr`, а не здесь. На новой системе их нужно вставить один раз:

- `hypr/rules.lua` → в `~/.config/hypr/modules/rules.lua` (окна на magic layer, блюр всплывашек);
- `hypr/autostart.lua` → внутрь `hl.on("hyprland.start", ...)`. Swaync и obsidian из
  остального автозапуска нужно убрать: obsidian запускает скрипт раскладки.

Система тем (`~/.config/hypr/theme`: палитры, `current.css`, `switch.sh`) тоже
не входит в репозиторий. Без неё приложения работают в запасной палитре.

## Данные

| Где | Что |
|---|---|
| `~/.local/share/evercal/events.json` | события календаря |
| `~/.local/share/evernotify/history.json` | история уведомлений |
| `~/.config/evercal/`, `~/.config/everclock/` | настройки (углы, секунды и т. п.) |
| `$XDG_RUNTIME_DIR/waybar-stopwatch.state` | состояние секундомера |

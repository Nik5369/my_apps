-- Фрагмент для ~/.config/hypr/modules/autostart.lua — вставить внутрь
-- hl.on("hyprland.start", function() ... end). Пути — через ~/.local/bin,
-- их создаёт install.sh, так что от места репозитория они не зависят.

    -- демон уведомлений (вместо swaync)
    hl.exec_cmd("~/.local/bin/minsnotify --daemon")
    -- magic layer (SUPER+S): mins*-приложения + obsidian, раскладку по очереди
    -- собирает скрипт; --hide — спрятать magic после сборки.
    -- obsidian запускает сам скрипт — из остального автозапуска его убрать.
    hl.exec_cmd("~/.local/bin/mins-magic-layout --hide")

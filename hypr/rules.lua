-- Фрагмент для ~/.config/hypr/modules/rules.lua — ever*-приложения на magic layer.
-- Раскладку тайлов собирает ~/.local/bin/ever-magic-layout (common/magic-layout.sh).

hl.window_rule({
    name      = "obsidian",
    match     = { class = "^obsidian$" },
    -- obsidian — правая колонка дашборда на magic layer
    -- (раскладку собирает ~/.local/bin/ever-magic-layout из ~/main/my_apps)
    workspace = "special:magic silent",
})

-- ever*-приложения живут на magic layer (SUPER+S) тайлами — раскладку
-- делает Hyprland, а сами окна подстраивают интерфейс под размер тайла.

-- evercal (~/main/my_apps/evercal). Фон берётся из @bg темы, так что
-- в glass-темах окно полупрозрачное и общий decoration.blur размывает то,
-- что под ним.
hl.window_rule({
    name   = "evercal",
    match  = { class = "^dev\\.natsu\\.evercal$" },
    workspace = "special:magic silent",
})

-- everplay (~/main/my_apps/everplay) — мини-плеер.
hl.window_rule({
    name   = "everplay",
    match  = { class = "^dev\\.natsu\\.everplay$" },
    workspace = "special:magic silent",
})

-- everclock (~/main/my_apps/everclock) — часы.
hl.window_rule({
    name   = "everclock",
    match  = { class = "^dev\\.natsu\\.everclock$" },
    workspace = "special:magic silent",
})

-- everwatch (~/main/my_apps/everwatch) — секундомер.
hl.window_rule({
    name   = "everwatch",
    match  = { class = "^dev\\.natsu\\.everwatch$" },
    workspace = "special:magic silent",
})

-- evertray (~/main/my_apps/evertray) — системный трей.
hl.window_rule({
    name   = "evertray",
    match  = { class = "^dev\\.natsu\\.evertray$" },
    workspace = "special:magic silent",
})

-- evernotify (~/main/my_apps/evernotify) — окно истории уведомлений.
hl.window_rule({
    name   = "evernotify",
    match  = { class = "^dev\\.natsu\\.evernotify$" },
    workspace = "special:magic silent",
})

-- всплывашки evernotify — layer-поверхность, блюр как у swaync
hl.layer_rule({
    name         = "evernotify-blur",
    match        = { namespace = "^evernotify$" },
    blur         = true,
    ignore_alpha = 0.4,
})


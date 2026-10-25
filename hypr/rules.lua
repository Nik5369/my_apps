-- Фрагмент для ~/.config/hypr/modules/rules.lua — mins*-приложения на magic layer.
-- Раскладку тайлов собирает ~/.local/bin/mins-magic-layout (common/magic-layout.sh).

hl.window_rule({
    name      = "obsidian",
    match     = { class = "^obsidian$" },
    -- obsidian — правая колонка дашборда на magic layer
    -- (раскладку собирает ~/.local/bin/mins-magic-layout)
    workspace = "special:magic silent",
})

-- mins*-приложения живут на magic layer (SUPER+S) тайлами — раскладку
-- делает Hyprland, а сами окна подстраивают интерфейс под размер тайла.

-- minscal. Фон берётся из @bg темы, так что
-- в glass-темах окно полупрозрачное и общий decoration.blur размывает то,
-- что под ним.
hl.window_rule({
    name   = "minscal",
    match  = { class = "^dev\\.natsu\\.minscal$" },
    workspace = "special:magic silent",
})

-- minsplay — мини-плеер.
hl.window_rule({
    name   = "minsplay",
    match  = { class = "^dev\\.natsu\\.minsplay$" },
    workspace = "special:magic silent",
})

-- minsclock — часы.
hl.window_rule({
    name   = "minsclock",
    match  = { class = "^dev\\.natsu\\.minsclock$" },
    workspace = "special:magic silent",
})

-- minswatch — секундомер.
hl.window_rule({
    name   = "minswatch",
    match  = { class = "^dev\\.natsu\\.minswatch$" },
    workspace = "special:magic silent",
})

-- minstray — системный трей.
hl.window_rule({
    name   = "minstray",
    match  = { class = "^dev\\.natsu\\.minstray$" },
    workspace = "special:magic silent",
})

-- minsnotify — окно истории уведомлений.
hl.window_rule({
    name   = "minsnotify",
    match  = { class = "^dev\\.natsu\\.minsnotify$" },
    workspace = "special:magic silent",
})

-- всплывашки minsnotify — layer-поверхность, блюр как у swaync
hl.layer_rule({
    name         = "minsnotify-blur",
    match        = { namespace = "^minsnotify$" },
    blur         = true,
    ignore_alpha = 0.4,
})


"""Оформление: палитра и шрифты. Палитра тёмная «графит» с фиолетово-синим градиентом.
QML получает её словарём QML как P (P.card, P.accent …, см. app.create) — цвета задаются только здесь."""

BG, RAIL, CARD, CARD_H, FIELD = "#111114", "#16161b", "#1c1c22", "#23232b", "#24242c"
NAV_H = "#1b1b21"  # пункт боковой панели под курсором
BORDER, BORDER_LIGHT = "#2a2a33", "#4a4a56"
ACCENT, ACCENT_H, ACCENT_TX, ACCENT_DIM = "#6d5dfc", "#5b4ce0", "#a99fff", "#3b3570"
GRAD1, GRAD2 = "#6d5dfc", "#3b82f6"
# текст и значки на акцентном цвете и градиенте: обычный, второстепенный, у недоступной кнопки
ON_ACCENT, ON_ACCENT_MUTED, ON_ACCENT_DISABLED = "#ffffff", "#e4e1ff", "#9b93d9"
SECOND, SECOND_H = "#24242c", "#2e2e38"
TEXT, MUTED, DISABLED_TX, LOG_TX = "#f2f2f5", "#8d8d99", "#5a5a66", "#c9c9d3"
OKC, OK_BG, OK_DIM = "#34d399", "#15302a", "#1f5a46"
WARN, WARN_DIM = "#fbbf24", "#5a4a1a"
DANGER, DANGER_BG = "#f87171", "#3a1f22"
SHADE, SHADE_MODAL = "#80000000", "#99000000"  # затемнение окна под панелью приложения и под диалогом
AVATARS = ("#6d5dfc", "#3b82f6", "#10b981", "#f97316", "#ec4899", "#22c55e", "#0ea5e9", "#eab308")
FONT, FONT_MONO = "Segoe UI", "Consolas"

QML = {
    "bg": BG, "rail": RAIL, "card": CARD, "cardHover": CARD_H, "field": FIELD, "navHover": NAV_H, "border": BORDER,
    "borderLight": BORDER_LIGHT, "accent": ACCENT, "accentHover": ACCENT_H, "accentText": ACCENT_TX,
    "accentDim": ACCENT_DIM, "grad1": GRAD1, "grad2": GRAD2, "onAccent": ON_ACCENT, "onAccentMuted": ON_ACCENT_MUTED,
    "onAccentDisabled": ON_ACCENT_DISABLED, "second": SECOND, "secondHover": SECOND_H, "text": TEXT, "muted": MUTED,
    "disabled": DISABLED_TX, "logText": LOG_TX, "ok": OKC, "okBg": OK_BG, "okDim": OK_DIM, "warn": WARN,
    "warnDim": WARN_DIM, "danger": DANGER, "dangerBg": DANGER_BG, "shade": SHADE, "shadeModal": SHADE_MODAL,
    "font": FONT, "fontMono": FONT_MONO,
}

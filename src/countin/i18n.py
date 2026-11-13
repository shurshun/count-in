"""Interface language. Strings in the code are Russian and double as keys; EN holds their English versions.

A key missing from EN shows in Russian, so a forgotten translation is visible but never breaks the UI.
test_every_ui_string_has_an_english_version catches them.
"""

LANGUAGES = {"system": "Как в системе", "en": "English", "ru": "Русский"}

_lang = "ru"

EN = {
    # Window and menus
    "Файл": "File",
    "Открыть…": "Open…",
    "Сохранить": "Save",
    "Показать сохранённый файл": "Show Saved File",
    "Настройки…": "Settings…",
    "Воспроизведение": "Playback",
    "Прослушать / Стоп (Пробел)": "Preview / Stop (Space)",
    "Справка": "Help",
    "О программе Count-In": "About Count-In",
    "Проверить обновления": "Check for Updates",
    "Страница проекта": "Project Page",
    "▶  Прослушать": "▶  Preview",
    "■  Стоп": "■  Stop",
    "Пробел": "Space",
    "Перетащите сюда трек\n\nmp3, FLAC, ALAC, WAV": "Drop a track here\n\nmp3, FLAC, ALAC, WAV",
    "Отмена": "Cancel",
    "Тяните оранжевый маркер «1» на первую сильную долю. Колесо — масштаб, перетаскивание — сдвиг, "
    "⌥ — без прилипания к долям.":
        'Drag the orange "1" marker onto the first downbeat. Wheel zooms, dragging pans, '
        "⌥ places it without snapping to beats.",
    # Cards
    "Темп и первая доля": "Tempo and first downbeat",
    "Темп": "Tempo",
    "Доля «1»": 'Downbeat "1"',
    " с": " s",
    "Взять кандидата детектора": "Use the detector's candidate",
    "Отсчёт": "Count-in",
    "Рисунок": "Pattern",
    "Ровный": "Even",
    "Нарастание": "Build-up",
    "1 такт": "1 bar",
    "2 такта": "2 bars",
    "Длина": "Length",
    "Размер": "Meter",
    "Громкость": "Volume",
    "Бэктрек": "Backing track",
    "Гитара": "Guitar",
    "Вокал": "Vocals",
    "Обрезать тишину в начале": "Trim leading silence",
    "Формат": "Format",
    "Как у исходника": "Same as source",
    "{v:+d} дБ": "{v:+d} dB",
    "{v} дБ": "{v} dB",
    "как есть": "unchanged",
    "убрать": "remove",
    # Status and progress
    "Показать в Finder": "Show in Finder",
    "Показать в папке": "Show in Explorer",
    "Обновить до v{version}": "Update to v{version}",
    "Установлена последняя версия, v{version}": "You have the latest version, v{version}",
    "Это не аудиофайл: mp3, FLAC, ALAC или WAV": "Not an audio file: mp3, FLAC, ALAC or WAV",
    "Анализ…": "Analyzing…",
    "Анализ: {name}": "Analyzing: {name}",
    "Детектор: {bpm:.2f} BPM · «1» на {t} · уверенность {conf:.0%}":
        'Detector: {bpm:.2f} BPM · "1" at {t} · confidence {conf:.0%}',
    "детектор не уверен, проверьте маркер «1» на слух": 'the detector is unsure, check the "1" marker by ear',
    "{name}: загрузка модели": "{name}: loading the model",
    "Разделяю отрывок": "Separating the excerpt",
    "Сборка отрывка": "Building the excerpt",
    "{name}: чтение": "{name}: reading",
    "{name}: разделение": "{name}: separating",
    "{name}: запись": "{name}: writing",
    "осталось ~{m}:{s:02d}": "~{m}:{s:02d} left",
    "считаю время…": "estimating time…",
    "Отменено": "Cancelled",
    "Отменяю…": "Cancelling…",
    "Гитару и вокал можно менять на ходу": "Guitar and vocals change live",
    "Чтобы менять гитару и вокал на ходу, опустите ползунок до прослушивания":
        "To change guitar and vocals live, lower a slider before the preview",
    "Не удалось воспроизвести: {e}": "Playback failed: {e}",
    "Файл {name} уже есть. Заменить?": "{name} already exists. Replace it?",
    "Сохранено: {name}": "Saved: {name}",
    "Клики перегружают звук в {n} сэмплах, убавьте громкость отсчёта":
        "The clicks clip {n} samples, lower the count-in volume",
    "Трек": "Track",
    "Аудио ({exts})": "Audio ({exts})",
    "MIDI-шаблон": "MIDI pattern",
    # About
    "Барабанный отсчёт перед первой сильной долей и бэктреки без гитары или вокала.":
        "Drum count-in before the first downbeat and backing tracks without guitar or vocals.",
    # Settings
    "Настройки": "Settings",
    "Сохранение": "Saving",
    "Рядом с исходным файлом": "Next to the original file",
    "В папку": "To folder",
    "Выбрать…": "Choose…",
    "Раскладывать по папкам": "Sort into subfolders",
    "Исполнитель": "Artist",
    "Год": "Year",
    "Альбом": "Album",
    "Внешний вид": "Appearance",
    "Иконка приложения": "App icon",
    "Светлая": "Light",
    "Тёмная": "Dark",
    "Язык": "Language",
    "Как в системе": "System",
    "Язык сменится после перезапуска приложения": "The language changes after a restart",
    "Готово": "Done",
    "Папка для сохранения": "Output folder",
    "не выбрана": "not chosen",
    "Пример: …/{path}": "Example: …/{path}",
    "Файл ляжет рядом с исходником: {name}": "The file goes next to the original: {name}",
    "Песня": "Song",
    # Folder names and file description (core.io)
    "Неизвестный исполнитель": "Unknown Artist",
    "Без альбома": "Unknown Album",
    "Без года": "Unknown Year",
    "{n} бит": "{n}-bit",
    "{k:g} кГц": "{k:g} kHz",
    "моно": "mono",
    "стерео": "stereo",
    "{n} кан.": "{n} ch",
    "сжатие до {p:.0%}": "compressed to {p:.0%}",
}


def set_language(lang: str) -> str:
    """lang is "en", "ru" or "system"; returns the language in effect."""
    global _lang
    if lang not in ("en", "ru"):
        lang = system_language()
    _lang = lang

    # Numbers in spin boxes follow the interface: 80.89 in English, 80,89 in Russian.
    from PySide6.QtCore import QLocale

    QLocale.setDefault(QLocale(QLocale.English if lang == "en" else QLocale.Russian))
    return lang


def system_language() -> str:
    from PySide6.QtCore import QLocale

    return "ru" if QLocale.system().language() == QLocale.Russian else "en"


def language() -> str:
    return _lang


def _(text: str, **fmt) -> str:
    """Text in the current language, formatted with fmt when given."""
    s = EN.get(text, text) if _lang == "en" else text
    return s.format(**fmt) if fmt else s

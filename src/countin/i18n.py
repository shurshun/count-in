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
    "Проверить обновления…": "Check for Updates…",
    "Проверка обновлений…": "Checking for Updates…",
    "Загрузка обновления…": "Downloading the Update…",
    "Загрузка обновления… {p}%": "Downloading the Update… {p}%",
    "Перезапустить и обновить до v{version}": "Restart to Update to v{version}",
    "Доступна новая версия v{version}. Она скачается и установится автоматически.":
        "Version {version} is available. It will be downloaded and installed automatically.",
    "Не удалось загрузить обновление: {e}": "Could not download the update: {e}",
    "Дождитесь окончания сохранения": "Wait for the save to finish",
    "Страница проекта": "Project Page",
    "▶  Прослушать": "▶  Preview",
    "■  Стоп": "■  Stop",
    "Пробел": "Space",
    "Перетащите сюда трек\n\nmp3, FLAC, ALAC, WAV": "Drop a track here\n\nmp3, FLAC, ALAC, WAV",
    "Отмена": "Cancel",
    "Тяните оранжевый маркер «1» на первую сильную долю. Колесо — масштаб, перетаскивание — сдвиг, "
    "⌥ — без прилипания к долям. Двойной щелчок — играть отсюда. Пауза: выделите такты с ⇧ или по шкале времени, "
    "правый щелчок по паузе убирает её.":
        'Drag the orange "1" marker onto the first downbeat. Wheel zooms, dragging pans, '
        "⌥ places it without snapping to beats. Double-click plays from there. Pause: select bars with ⇧ or along "
        "the time ruler, right-click a pause to remove it.",
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
    "Выходной формат": "Output format",
    "Как у исходника": "Same as source",
    "{v:+d} дБ": "{v:+d} dB",
    "{v} дБ": "{v} dB",
    "как есть": "unchanged",
    "убрать": "remove",
    "Убрать гитару": "Remove guitar",
    "Заполнять паузы хай-хэтом": "Fill pauses with hi-hat",
    "Такты, где без убранных дорожек тихо, получают хай-хэт на каждую долю и отсчёт палочками за такт до паузы. Правый щелчок по паузе убирает её, рядом с паузой добавляет такт, края паузы тянутся мышью.": "Bars that go quiet without the removed parts get a hi-hat on every beat and a bar of stick count-in before the pause. Right-click a pause to remove it, next to one to add a bar; drag a pause edge to move it.",
    "Пауза {a}–{b}": "Pause {a}–{b}",
    "Долей в такте": "Beats per bar",
    "Удалить паузу": "Remove pause",
    "Порог паузы": "Pause threshold",
    "Насколько тише среднего уровня песни должен быть такт, чтобы считаться паузой. На волне этот уровень показан пунктиром.": "How far below the song's average level a bar must be to count as a pause. The waveform shows this level as a dashed line.",
    "Модели для Count-In": "Count-In models",
    "Нужно скачать модели": "The models need downloading",
    "Count-In находит доли и разделяет песню на дорожки нейросетями. Их веса ({mb:.0f} МБ) скачиваются один раз и потом не меняются, поэтому обновления приложения остаются маленькими. Если модели уже есть на флешке или в другой папке, укажите её. Папка assets рядом с приложением подхватывается сама.": "Count-In finds the beats and splits songs into parts with neural networks. Their weights ({mb:.0f} MB) download once and don't change afterwards, so app updates stay small. If you already have them on a flash drive or in another folder, choose it. An assets folder next to the app is picked up on its own.",
    "Позже": "Later",
    "Указать папку…": "Choose Folder…",
    "Скачать": "Download",
    "Не получилось: {e}": "That didn't work: {e}",
    "Скачано {d:.0f} из {n:.0f} МБ": "Downloaded {d:.0f} of {n:.0f} MB",
    "Скопировано {d:.0f} из {n:.0f} МБ": "Copied {d:.0f} of {n:.0f} MB",
    "Папка с моделями": "Folder with the models",
    "В этой папке нет моделей Count-In": "No Count-In models in this folder",
    "Без моделей трек не разобрать": "The track can't be analysed without the models",
    "Громкость вывода": "Output volume",
    "Закрыть": "Close",
    "Закрыть трек": "Close Track",
    "Раскладываю песню на дорожки, чтобы найти паузы без убранных инструментов. Это делается один раз на трек.": "Splitting the song into parts to find the pauses left without the removed instruments. This happens once per track.",
    "Готовлю отрывок: отсчёт и первые секунды песни с вашими настройками.": "Preparing the excerpt: the count-in and the first seconds of the song with your settings.",
    "Сохраняю трек с отсчётом и выбранным уровнем дорожек.": "Saving the track with the count-in and the part levels you chose.",
    "Барабанщик уже считает: раз, два, три, четыре…": "The drummer is already counting: one, two, three, four…",
    "Гитарист подкручивает колки": "The guitarist is tuning up",
    "Басист где-то рядом, его просто не слышно": "The bassist is around, the bass is just hard to hear",
    "Звукорежиссёр просит ещё немного вокала в мониторе": "The sound engineer wants a bit more vocal in the monitor",
    "Метроном не устаёт, в отличие от барабанщика": "The metronome never gets tired, unlike the drummer",
    "Первая доля — самая важная, мы её не пропустим": "The first downbeat matters most, we won't miss it",
    "Кто-то за кулисами ищет медиатор": "Someone backstage is looking for a pick",
    "Хай-хэт уже на подставке": "The hi-hat is on its stand",
    "В репетиционной пахнет кофе и проводами": "The rehearsal room smells of coffee and cables",
    "Вокалист распевается: ми-ми-ми": "The singer is warming up: mi-mi-mi",
    "Каждая нейросеть мечтает стать барабанщиком": "Every neural network dreams of becoming a drummer",
    "Темп держим, даже когда очень хочется ускориться": "We hold the tempo, even when we really want to rush",
    "Громкость прослушивания": "Preview volume",
    "Сохранить настройки как по умолчанию": "Save Settings as Default",
    "Отсчёт, дорожки, паузы и формат будут такими для каждого нового трека": "Count-in, parts, pauses and format start like this for every new track",
    "Настройки сохранены как настройки по умолчанию": "Settings saved as default",
    "Темп на слух": "Tap tempo",
    "Применить": "Apply",
    "Стучите пробелом в такт, клик с вашим темпом пойдёт сам": "Tap Space along with the song; a click at your tempo follows on its own",
    "Играет клик с настуканным темпом. Пробел, чтобы настучать заново": "The click plays at the tapped tempo. Space to tap again",
    "Tap…": "Tap…",
    "Настучать темп пробелом под играющую песню": "Tap the tempo with Space while the song plays",
    "Как в песне": "As detected",
    "Убрать ручной темп и размер: пауза снова идёт по долям, найденным в песне": "Drop the manual tempo and meter: the pause follows the detected beats again",
    "Готово": "Done",
    "Отмена": "Cancel",
    "пауз: {n}": "pauses: {n}",
    "{name}: поиск пауз": "{name}: finding pauses",
    "Поиск пауз": "Finding pauses",
    "Убрать вокал": "Remove vocals",
    "Бас": "Bass",
    "Барабаны": "Drums",
    "Пианино": "Piano",
    "Убрать бас": "Remove bass",
    "Убрать барабаны": "Remove drums",
    "Убрать пианино": "Remove piano",
    # Status and progress
    "Показать в Finder": "Show in Finder",
    "Показать в папке": "Show in Explorer",
    "Доступна v{version}, скачать": "v{version} is available, download it",
    "Установлена последняя версия, v{version}": "You have the latest version, v{version}",
    "Не удалось проверить обновления: {e}": "Could not check for updates: {e}",
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
    "Дорожки можно менять на ходу": "Parts change live while playing",
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
    "Открывать папку после сохранения": "Open the folder after saving",
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

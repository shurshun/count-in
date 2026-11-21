"""What changed in each release, newest first. The site's changelog page and the app's "What's New" window read it.

Each item is (English, Russian); **bold** marks the lead words. Standard library only, so the site builder loads
it by path.
"""

import html
import re

RELEASES = [
    ("0.11.0", "2026-11-22", [
        ("**Editable tempo track.** Review automatically detected sections, then click the BPM and meter badge "
         "to edit a section. Changes, grid offsets and hi-hat fills are saved with the song.",
         "**Темпотрек.** Проверьте автоматически найденные участки и нажмите на плашку BPM и размера, "
         "чтобы отредактировать участок. Правки, сдвиги сетки и заполнение хай-хэтом сохраняются с песней."),
        ("**Tap Tempo in a loop.** Each press of Space plays a click. After two missed beats, the metronome "
         "continues in the tapped tempo. Apply updates the selected passage.",
         "**Tap Tempo в петле.** Каждый пробел звучит как клик. Через две пропущенные доли метроном "
         "продолжает в настуканном темпе. «Применить» обновляет выбранный фрагмент."),
        ("**Pickups and playback.** Count-In checks the first full bar against later bars. Home moves to the "
         "first count-in click, and Play reuses the prepared audio when possible.",
         "**Затакт и воспроизведение.** Count-In проверяет первый полный такт по следующим тактам. "
         "Home возвращает к первому клику, а Play по возможности использует готовое аудио."),
        ("**Separate click sounds.** Count-in and whole-song metronome have independent sounds and levels. "
         "The defaults are sticks for count-in and closed hi-hat for the metronome, both at 0 dB.",
         "**Раздельный звук клика.** У отсчёта и метронома на всю песню свои звук и громкость. "
         "По умолчанию отсчёт звучит палочками, метроном — закрытым хай-хэтом, оба на 0 дБ."),
        ("**Manual control when detection is uncertain.** If a reliable rhythm cannot be found, the audio "
         "still opens and the BPM and first downbeat can be set manually.",
         "**Ручная настройка при слабом сигнале.** Если программа не нашла устойчивый ритм, аудио всё равно "
         "открывается. BPM и первую долю можно задать вручную."),
    ]),
    ("0.10.0", "2026-11-19", [
        ("**Loop and speed trainer.** Select bars and loop them; the trainer raises the speed by 5% every two passes "
         "up to 100%. Speed now goes down to 50%.",
         "**Петля и тренажёр скорости.** Выделите такты и зациклите их, а тренажёр поднимает скорость на 5% каждые "
         "два прохода до 100%. Скорость теперь опускается до 50%."),
        ("**Key.** The app detects the song's key and shows it in the header. The new Key control shifts the song "
         "by up to ±6 semitones and keeps the tempo.",
         "**Тональность.** Программа определяет тональность песни и показывает её в шапке. Новая настройка «Тон» "
         "сдвигает песню на ±6 полутонов и не меняет темп."),
        ("**Solo and +6 dB.** Every part has a solo button, and its slider now goes up to +6 dB. The click is its "
         "own part with mute and solo.",
         "**Соло и +6 дБ.** У каждой дорожки есть кнопка соло, а ползунок поднимается до +6 дБ. Клик стал отдельной "
         "дорожкой с выключением и соло."),
        ("**Markers and bars.** Bar numbers on the ruler, Go to Bar, named section markers (M, keys 1–9). "
         "Markers also go into tempo.mid.",
         "**Маркеры и такты.** Номера тактов на линейке, переход к такту, именованные маркеры частей "
         "(M, клавиши 1–9). Маркеры попадают и в tempo.mid."),
        ("**Song memory and undo.** Tempo, the 1, pauses, levels, speed, click and loop come back when a song "
         "reopens. Edits to tempo, the 1, pauses and markers can be undone.",
         "**Память песни и отмена.** Темп, «1», паузы, уровни, скорость, клик и петля возвращаются при повторном "
         "открытии песни. Правки темпа, «1», пауз и маркеров можно отменить."),
        ("**Set list.** Open several songs and switch with N and P or a page-turner pedal.",
         "**Сет-лист.** Откройте несколько песен и переключайтесь клавишами N и P или педалью-перелистывателем."),
        ("**Stage mix and even loudness.** A backing track with the click alone on the left channel, click.wav in "
         "the multitrack export, and an option to save at −14 LUFS.",
         "**Сценический микс и ровная громкость.** Бэктрек с кликом отдельно в левом канале, click.wav в "
         "многодорожечном экспорте и сохранение на −14 LUFS."),
        ("**Faster and smoother.** Seeking is instant, slowed playback no longer freezes the window, and the "
         "song click turns on and off during playback without a pause.",
         "**Быстрее и плавнее.** Перемотка мгновенная, замедление больше не подвешивает окно, а клик на всю песню "
         "включается и выключается на ходу без паузы."),
        ("**New header.** The song, its key and file, and the output volume with the file buttons sit in three "
         "cards. Recent files appear on the start screen.",
         "**Новая шапка.** Песня, её тональность и файл, громкость вывода и кнопки файла стоят в трёх карточках. "
         "На стартовом экране появились недавние файлы."),
        ("**Windows.** Separation runs on the graphics card with the most memory, and falls back to the processor "
         "if the card fails mid-song.",
         "**Windows.** Разделение идёт на видеокарте с наибольшей памятью, а если она сбоит посреди песни, работа "
         "переходит на процессор."),
        ("Help → What's New shows this list, and it opens once after an update.",
         "«Справка» → «Что нового» показывает этот список, а после обновления он открывается сам один раз."),
    ]),
    ("0.9.1", "2026-11-18", [
        ("**Click through the whole song.** A checkbox in the Count-in card keeps the click going on every beat to "
         "the end. Pick its sound (metronome, sticks, cowbell, ping or hi-hat) and level.",
         "**Клик на всю песню.** Флажок в карточке «Отсчёт» продолжает клик на каждую долю до конца песни. Можно "
         "выбрать звук (метроном, палочки, ковбелл, пинг или хай-хэт) и громкость."),
        ("**New build-up.** Eighths only over the second half of the last bar, each hit louder than the one before.",
         "**Новое нарастание.** Восьмые только во второй половине последнего такта, каждый удар громче предыдущего."),
        ("**Playback to the end.** The preview plays the whole song, and slowed playback is prepared about four "
         "times faster.",
         "**Прослушивание до конца.** Предпросмотр играет всю песню, а замедленное прослушивание готовится примерно "
         "в четыре раза быстрее."),
        ("**Clicks never clip**, and the audio output follows the system device without a restart.",
         "**Клики не перегружают звук**, а вывод звука следует за системным устройством без перезапуска."),
    ]),
    ("0.8.2", "2026-11-18", [
        ("**Separation works on macOS 14.** The app now asks for macOS 14 or later.",
         "**Разделение работает на macOS 14.** Программе нужна macOS 14 или новее."),
    ]),
    ("0.8.1", "2026-11-18", [
        ("**Signed and notarized for macOS.** No more trip to Privacy & Security on first launch.",
         "**Подпись и нотаризация для macOS.** При первом запуске больше не нужно идти в «Конфиденциальность и "
         "безопасность»."),
    ]),
    ("0.8.0", "2026-11-17", [
        ("**Multitrack export for your DAW.** Every part as a 32-bit WAV plus the click and a tempo map as MIDI, all "
         "starting at bar 1.",
         "**Многодорожечный экспорт для DAW.** Каждая дорожка в 32-битном WAV, клик и темп-карта в MIDI, всё "
         "начинается с первого такта."),
        ("**New save dialog** with the output format, readable buttons with any accent colour, and Help → Report a "
         "Problem.",
         "**Новый диалог сохранения** с форматом файла, читаемые кнопки при любом акцентном цвете и «Справка» → "
         "«Сообщить о проблеме»."),
    ]),
    ("0.7.2", "2026-11-15", [
        ("Download speed and time left while the models download.",
         "Скорость и оставшееся время при загрузке моделей."),
    ]),
    ("0.7.1", "2026-11-15", [
        ("**Model download fixed** on a Mac, and Help opens the user guide on the site.",
         "**Исправлена загрузка моделей** на Mac, а «Справка» открывает руководство на сайте."),
    ]),
    ("0.7.0", "2026-11-15", [
        ("**Pauses get a hi-hat** with a build-up into the band's entry.",
         "**В паузах звучит хай-хэт** с нарастанием к вступлению группы."),
        ("**Five parts.** Guitar, vocals, bass, drums and piano each get a slider and a mute button.",
         "**Пять дорожек.** Гитара, вокал, бас, барабаны и клавишные, у каждой ползунок и кнопка выключения."),
        ("**Slow it down** to 80% with the pitch kept.",
         "**Замедление** до 80% без изменения высоты."),
        ("**Separation on the GPU, in the background**, and a song separated once opens instantly next time.",
         "**Разделение на видеокарте и в фоне**, а уже разделённая песня в следующий раз открывается сразу."),
        ("**New editor** with a transport bar, mini-map and Help window, and a smaller app with models downloaded "
         "once.",
         "**Новый редактор** с панелью транспорта, мини-картой и справкой, а программа стала меньше, модели "
         "загружаются один раз."),
    ]),
    ("0.6.2", "2026-11-15", [
        ("**Mute buttons** beside the Guitar and Vocals sliders.",
         "**Кнопки выключения** рядом с ползунками гитары и вокала."),
    ]),
    ("0.6.1", "2026-11-15", [
        ("The system accent colour in light mode.",
         "Системный акцентный цвет в светлой теме."),
    ]),
    ("0.6.0", "2026-11-14", [
        ("**Automatic updates.** The app downloads a new version in the background and installs it on restart.",
         "**Автообновление.** Программа скачивает новую версию в фоне и ставит её при перезапуске."),
    ]),
    ("0.5.1", "2026-11-13", [
        ("The update badge sits neatly beside the version.",
         "Значок обновления аккуратно стоит рядом с версией."),
    ]),
    ("0.5.0", "2026-11-13", [
        ("The folder opens with the new file selected after a save.",
         "После сохранения открывается папка с выделенным файлом."),
    ]),
    ("0.4.1", "2026-11-13", [
        ("The update check works in the released app.",
         "Проверка обновлений работает в собранной программе."),
    ]),
    ("0.4.0", "2026-11-13", [
        ("**English and Russian interface**, light and dark app icon.",
         "**Английский и русский интерфейс**, светлая и тёмная иконка."),
    ]),
    ("0.3.0", "2026-11-13", [
        ("One track at a time with Open, Preview and Save, Space to play, and Preferences.",
         "Один трек за раз с кнопками «Открыть», «Прослушать» и «Сохранить», пробел для воспроизведения и "
         "настройки."),
    ]),
    ("0.2.0", "2026-11-13", [
        ("Light and dark app icons.",
         "Светлая и тёмная иконки программы."),
    ]),
    ("0.1.0", "2026-11-13", [
        ("**First release.** Drum count-in before the first downbeat, backing tracks without guitar or vocals, "
         "mp3, FLAC, ALAC and WAV with tags kept.",
         "**Первый релиз.** Барабанный отсчёт перед первой долей, минусовки без гитары и вокала, mp3, FLAC, ALAC и "
         "WAV с сохранением тегов."),
    ]),
]


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def rich(text: str) -> str:
    """Escapes text for HTML and turns **bold** into <b>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text, quote=False))


def since(seen: str, current: str) -> list:
    """Releases newer than `seen`, up to `current`."""
    return [r for r in RELEASES if version_key(seen) < version_key(r[0]) <= version_key(current)]

<p align="center"><img src="docs/screenshots/social-preview.png" alt="Count-In" width="820"></p>

# Count-In

[count-in.app](https://count-in.app) · [Telegram](https://t.me/count_in_app) · [English](#count-in) · [Русский](#русский)

Count-In finds a song's tempo and first downbeat and puts a drum count-in in front of it. The last click lands exactly one beat before the "one". The same song can become a backing track with the guitar and vocals removed or turned down.

- Count-in of 1 or 2 bars in 4/4, 3/4 or 6/8.
- Patterns: even, build-up (twice as dense in the second half, the last hits louder) or your own from a MIDI file.
- A neural network separates the song into guitar, vocals, bass, drums and piano in one pass; each has its own level and a mute button.
- Pauses: bars that go quiet once parts are removed get a hi-hat on every beat, as loud as the music around them, with an optional build-up into the band's entry.
- Speed: slow the song down to 80% with the pitch kept; the count-in and the hi-hat land on the slowed grid.
- Separation runs on the GPU: Core ML on a Mac, DirectML on Windows. A song separated once opens instantly next time.
- Input: mp3, FLAC, ALAC, WAV. Output: MP3 320 kbps, FLAC, ALAC or WAV, with tags and cover art kept.
- Works offline. The network is used for the one-time model download and for updates.

News, releases and feedback: the Telegram channel [@count_in_app](https://t.me/count_in_app).

The interface is in English and Russian; it follows the system language, and Settings can switch it. The [user guide](https://count-in.app/guide/) walks through every feature with screenshots; the app opens it from Help (F1).

## Download

Get the latest version from [Releases](https://github.com/shurshun/count-in/releases/latest).

| System | File |
|---|---|
| Windows 10/11, x64 | `Count-In-windows-x64.zip` |
| macOS 12+, Apple Silicon | `Count-In-macos-arm64.zip` |

The macOS build is signed and notarized by Apple. The Windows build is not signed yet, so Windows warns once.

- **Windows.** Unzip and run `Count-In.exe`. If SmartScreen appears, click "More info" → "Run anyway".
- **macOS.** Unzip, drag `Count-In.app` to Applications and open it.

## How to use

![Main window](docs/screenshots/main-light-en.png)

The app follows the system appearance and has a [dark theme](docs/screenshots/main-dark-en.png) too.

1. Drop a track onto the window or open it with ⌘O. The app analyzes it and starts separating the parts in the background right away; a card over the waveform shows the progress and tips.
2. Check the orange "1" marker on the waveform. If the detector got it wrong, drag the marker to the right beat or use the arrows in the Tempo card; the round arrow brings back what the detector found. The wheel zooms, dragging pans, the overview under the waveform shows the whole song.
3. Pick the count-in pattern, length and meter. Blue marks on the waveform show where the clicks will sound.
4. For a backing track, lower the part sliders in the Backing track card. With "Fill pauses with hi-hat" on, quiet stretches get a hi-hat; the silence threshold fits itself to the song, and the − and + buttons in the bar under the waveform remove or add a pause.
5. Press Play or Space. A click on the waveform or the overview sets the playhead, ← and → jump between pauses, and the speed control slows the song down. Levels change while it plays.
6. Press Save or ⌘S. By default the file goes next to the original, and Finder or Explorer opens with it selected; turn that off in Settings. Save asks for a backing track as one file, in the source format or as MP3, FLAC, ALAC or WAV, or a multitrack for a DAW: the parts as WAV plus the click grid and tempo map as MIDI, all starting on bar 1. The window shows the file or folder name before it saves.

In Settings (⌘,) you can pick one output folder and have files sorted into Artist / Year - Album subfolders from the source tags; each level is a checkbox.

![Settings](docs/screenshots/prefs-light-en.png)

The bottom bar shows the file format, bitrate or bit depth, sample rate and length, plus the app version.

When the detector is unsure, an orange hint appears above the waveform. ×2 and ÷2 fix a doubled or halved tempo; Tap sets the tempo by tapping.

### File names

| What was done | Name |
|---|---|
| Count-in only | `Song_countin.mp3` |
| Backing track | `Song_backing_guitar-off_vocals-6dB.mp3` |
| Slowed to 80% | `Song_backing_guitar-off_80pct.mp3` |

### Processing time

The count-in and analysis take seconds. Separation starts as soon as a track opens and runs on the GPU: about 50 seconds for a 3-minute song on an Apple Silicon Mac (Core ML), on Windows through DirectML on any DirectX 12 graphics card. You can listen and edit while it runs; a preview excerpt gets separated ahead of the rest. A song separated once comes from the cache next time; Settings show its size and clear it.

On first launch the app downloads its models once: about 780 MB on a Mac, plus a 712 MB GPU model on Windows. An `assets` folder next to the app works too, for a copy on a flash drive.

![Progress](docs/screenshots/progress-light-en.png)

### Updates

On start, the app checks GitHub for a newer version and downloads it in the background, checking its SHA-256 against the release. When it is ready, a round blue badge appears left of the version in the bottom right corner, and the menu offers "Restart to Update". Without a restart, the update installs when you quit. "Check for Updates…" sits in the app menu on macOS and under Help on Windows.

The app updates itself only from a folder it can write to. Run from Downloads on macOS without moving it, or from a protected folder, the badge opens the download page instead.

### Reporting a problem

Help → Report a Problem… sends your description to the author. You choose whether details about the computer and the app log go with it; no audio is sent, though the log may name the songs you opened.

---

## Development

[AGENTS.md](AGENTS.md) explains how the code works, why these technologies were chosen and where to change things.

### Environment

```bash
brew install ffmpeg            # or packaging/build_ffmpeg.sh, the LGPL build that goes into bin/
uv sync
packaging/fetch_models.sh      # beat_this final0 (81 MB), BS-RoFormer SW (700 MB)
```

### Run

```bash
uv run countin-gui [files...]                        # the app
uv run countin song.mp3 --style buildup --bars 2     # command line
uv run countin *.mp3 --dry-run                       # table: BPM, first downbeat, confidence
uv run countin-verify out.mp3 --last-click 2.22526   # where the last click actually landed
```

| Flag | What it does |
|---|---|
| `--bars 1\|2` | count-in length |
| `--meter 4/4\|3/4\|6/8` | meter; 6/8 clicks six eighths over two beats |
| `--style even\|buildup` | even count-in or build-up |
| `--pattern file.mid` | pattern from MIDI, length rounded up to whole bars |
| `--bpm`, `--first-beat SEC` | set tempo and first downbeat by hand |
| `--tempo-x2`, `--tempo-half` | fix a doubled or halved tempo |
| `--pre-roll keep\|trim` | `keep` puts clicks over the intro, `trim` first cuts leading silence |
| `--volume DB` | count-in volume |
| `--guitar-db DB`, `--vocals-db DB` | backing track levels, `--guitar-db=-inf` removes the guitar |
| `--out`, `--format mp3\|flac\|alac\|wav` | output format, defaults to the source format |

### Tests

```bash
uv run pytest -m 'not slow'    # fast
uv run pytest                  # including a neural network run
```

### Build and release

```bash
uv run pyinstaller packaging/countin.spec --noconfirm --distpath build/dist --workpath build/work  # app and CLI in one bundle
```

CI (`.github/workflows/build.yml`) builds Windows x64 and macOS arm64 for every pull request that touches code and runs the bundled CLI end to end; docs-only changes skip it. To release, bump `version` in `pyproject.toml` and tag `v<version>`. CI publishes both archives to Releases, and the in-app update check compares against that version.

## License

Count-In is released under the [MIT License](LICENSE). The bundled third-party components below keep their own licenses.

## Third-party components

| Component | License |
|---|---|
| [beat_this](https://github.com/CPJKU/beat_this) (beat detector, code and weights) | MIT |
| BS-RoFormer code from [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator), based on [lucidrains/BS-RoFormer](https://github.com/lucidrains/BS-RoFormer) | MIT |
| BS-RoFormer SW weights (jarredou) | author's terms, no explicit license |
| [FFmpeg](https://ffmpeg.org), built without GPL parts, with LAME | LGPL |
| [Qt for Python](https://doc.qt.io/qtforpython-6/) | LGPL |
| [PyTorch](https://pytorch.org) | BSD |
| [Signalsmith Stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch) via [python-stretch](https://github.com/gregogiudici/python-stretch) (slowing down) | MIT |
| [coremltools](https://github.com/apple/coremltools) (macOS) | BSD |
| [ONNX Runtime DirectML](https://onnxruntime.ai) (Windows) | MIT |

---

## Русский

Новости, релизы и обратная связь — в Telegram-канале [@count_in_app](https://t.me/count_in_app).

Count-In находит темп и первую сильную долю песни и вставляет перед ней барабанный отсчёт. Последний клик звучит ровно за долю до «раз». Из той же песни можно сделать бэктрек: убрать или приглушить гитару и вокал.

- Отсчёт на 1 или 2 такта, размер 4/4, 3/4 или 6/8.
- Рисунок: ровный, «нарастание» (во второй половине вдвое чаще, последние удары громче) или свой из MIDI-файла.
- Нейросеть за один проход разделяет песню на гитару, вокал, бас, барабаны и пианино; у каждой дорожки свой уровень и кнопка отключения.
- Паузы: такты, где без убранных инструментов тихо, получают хай-хэт на каждую долю, громкостью как музыка вокруг, и по желанию нарастание к вступлению группы.
- Скорость: песню можно замедлить до 80% без смены тона, отсчёт и хай-хэт встают на замедленную сетку.
- Разделение идёт на видеокарте: Core ML на Mac, DirectML на Windows. Песню, которую уже разделяли, программа открывает сразу.
- Вход: mp3, FLAC, ALAC, WAV. Выход: MP3 320 kbps, FLAC, ALAC или WAV, теги и обложка сохраняются.
- Работает без интернета. Сеть нужна для одной загрузки моделей и для обновлений.
- Интерфейс на русском и английском: язык берётся из системы, его можно сменить в настройках. [Справка](https://count-in.app/ru/guide/) со скриншотами описывает все возможности, программа открывает её из меню «Справка» (F1).

### Скачать

Последняя версия лежит на странице [Releases](https://github.com/shurshun/count-in/releases/latest).

| Система | Файл |
|---|---|
| Windows 10/11, x64 | `Count-In-windows-x64.zip` |
| macOS 12+, Apple Silicon | `Count-In-macos-arm64.zip` |

Сборка для macOS подписана и заверена Apple. Сборка для Windows пока не подписана, поэтому Windows один раз предупредит.

- **Windows.** Распакуйте архив и запустите `Count-In.exe`. Если появится SmartScreen, нажмите «Подробнее» → «Выполнить в любом случае».
- **macOS.** Распакуйте архив, перетащите `Count-In.app` в «Программы» и откройте.

### Как пользоваться

![Главное окно](docs/screenshots/main-light.png)

Приложение следует оформлению системы, есть и [тёмная тема](docs/screenshots/main-dark.png).

1. Перетащите трек в окно или откройте его через ⌘O. Приложение анализирует его и сразу начинает в фоне разделять на дорожки; карточка поверх волны показывает прогресс и советы.
2. Проверьте оранжевый маркер «1» на волне. Если детектор ошибся, перетащите маркер на нужную долю или сдвиньте стрелками в карточке «Темп»; круглая стрелка возвращает найденное детектором. Колесо меняет масштаб, перетаскивание сдвигает волну, обзор трека под волной показывает всю песню.
3. Выберите рисунок, длину и размер отсчёта. Синие отметки на волне показывают, где прозвучат клики.
4. Для бэктрека опустите ползунки в карточке «Бэктрек». С галочкой «Заполнять паузы хай-хэтом» тихие места получают хай-хэт; порог тишины подбирается сам, кнопки − и + под волной убирают и добавляют паузы.
5. Нажмите Play или пробел. Щелчок по волне или обзору трека ставит курсор, ← и → переходят по паузам, блок «Скорость» замедляет песню. Уровни меняются на ходу.
6. Нажмите «Сохранить» или ⌘S. По умолчанию файл ложится рядом с исходником, а Finder или Проводник открывается с ним; это отключается в настройках. Окно сохранения предлагает бэктрек одним файлом, в формате исходника, MP3, FLAC, ALAC или WAV, или мультитрек для DAW: партии в WAV, сетку кликов и темповую карту в MIDI, все с первого такта. Имя файла или папки видно до сохранения.

В настройках (⌘,) можно один раз выбрать папку для всех файлов и раскладывать их по подпапкам «Исполнитель / Год - Альбом» по тегам исходника, каждый уровень включается галочкой.

![Настройки](docs/screenshots/prefs-light.png)

В нижней панели видны формат файла, битрейт или разрядность, частота дискретизации, длительность и версия приложения.

Если детектор не уверен в результате, над волной появляется оранжевая подсказка. Кнопки ×2 и ÷2 исправляют удвоенный или половинный темп, Tap задаёт темп постукиванием.

#### Имена файлов

| Что сделано | Имя |
|---|---|
| Только отсчёт | `Песня_countin.mp3` |
| Бэктрек | `Песня_backing_guitar-off_vocals-6dB.mp3` |
| Замедлено до 80% | `Песня_backing_guitar-off_80pct.mp3` |

#### Время обработки

Отсчёт и анализ занимают секунды. Разделение начинается сразу после открытия трека и идёт на видеокарте: около 50 секунд на 3-минутную песню на Mac с Apple Silicon (Core ML), на Windows через DirectML на любой видеокарте с DirectX 12. Пока оно идёт, можно слушать и править; отрывок для прослушивания разделяется вне очереди. Песня, которую уже разделяли, берётся из кэша; его размер и очистка есть в настройках.

При первом запуске программа один раз скачивает модели: около 780 МБ на Mac и ещё 712 МБ модели для видеокарты на Windows. Подходит и папка `assets` рядом с программой, например на флешке.

![Прогресс](docs/screenshots/progress-light.png)

#### Обновления

При запуске приложение проверяет, нет ли новой версии на GitHub, и в фоне скачивает её, сверяя SHA-256 с релизом. Когда загрузка готова, справа внизу слева от версии появляется круглый голубой значок, а в меню — пункт «Перезапустить и обновить». Если не перезапускать, обновление установится при выходе. «Проверить обновления…» находится в меню приложения на macOS и в меню «Справка» на Windows.

Само обновляется только приложение из папки, куда можно писать. Если на macOS запускать его прямо из «Загрузок», не перенося, или из защищённой папки, значок открывает страницу загрузки.

#### Сообщить о проблеме

«Справка → Сообщить о проблеме…» отправляет автору ваше описание. Вы сами решаете, приложить ли сведения о компьютере и журнал программы; звук не уходит, а в журнале могут быть названия открытых песен.

---

### Разработка

Как устроен код, почему выбраны эти технологии и где что менять, описано в [AGENTS.md](AGENTS.md) (на английском).

#### Окружение

```bash
brew install ffmpeg            # или packaging/build_ffmpeg.sh, LGPL-сборка для бандла в bin/
uv sync
packaging/fetch_models.sh      # beat_this final0 (81 МБ), BS-RoFormer SW (700 МБ)
```

#### Запуск

```bash
uv run countin-gui [файлы...]                       # приложение
uv run countin song.mp3 --style buildup --bars 2     # командная строка
uv run countin *.mp3 --dry-run                       # таблица: BPM, первая доля, уверенность
uv run countin-verify out.mp3 --last-click 2.22526   # проверка, где оказался последний клик
```

| Флаг | Что делает |
|---|---|
| `--bars 1\|2` | длина отсчёта |
| `--meter 4/4\|3/4\|6/8` | размер; 6/8 щёлкает шесть восьмых на две доли |
| `--style even\|buildup` | ровный отсчёт или нарастание |
| `--pattern file.mid` | рисунок из MIDI, длина округляется до целых тактов |
| `--bpm`, `--first-beat SEC` | ручная правка темпа и первой доли |
| `--tempo-x2`, `--tempo-half` | исправить удвоенный или половинный темп |
| `--pre-roll keep\|trim` | `keep` кладёт клики поверх вступления, `trim` сначала срезает тишину в начале |
| `--volume DB` | громкость отсчёта |
| `--guitar-db DB`, `--vocals-db DB` | уровни для бэктрека, `--guitar-db=-inf` убирает гитару |
| `--out`, `--format mp3\|flac\|alac\|wav` | формат вывода, по умолчанию как у исходника |

#### Тесты

```bash
uv run pytest -m 'not slow'    # быстрые
uv run pytest                  # вместе с прогоном нейросети
```

#### Сборка и релиз

```bash
uv run pyinstaller packaging/countin.spec --noconfirm --distpath build/dist --workpath build/work  # приложение и CLI в одном бандле
```

CI (`.github/workflows/build.yml`) собирает Windows x64 и macOS arm64 для каждого PR с изменениями кода и прогоняет CLI из бандла от начала до конца; PR только с документацией сборку не запускают. Чтобы выпустить релиз, поднимите `version` в `pyproject.toml` и поставьте тег `v<версия>`. CI опубликует оба архива в Releases, а проверка обновлений в приложении сравнивает именно эту версию.

### Лицензия

Count-In распространяется по [лицензии MIT](LICENSE). Сторонние компоненты из таблицы ниже сохраняют свои лицензии.

### Сторонние компоненты

| Компонент | Лицензия |
|---|---|
| [beat_this](https://github.com/CPJKU/beat_this) (детектор долей, код и веса) | MIT |
| Код BS-RoFormer из [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator), на основе [lucidrains/BS-RoFormer](https://github.com/lucidrains/BS-RoFormer) | MIT |
| Веса BS-RoFormer SW (jarredou) | условия автора, явной лицензии нет |
| [FFmpeg](https://ffmpeg.org), сборка без GPL-частей, с LAME | LGPL |
| [Qt for Python](https://doc.qt.io/qtforpython-6/) | LGPL |
| [PyTorch](https://pytorch.org) | BSD |
| [Signalsmith Stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch) через [python-stretch](https://github.com/gregogiudici/python-stretch) (замедление) | MIT |
| [coremltools](https://github.com/apple/coremltools) (macOS) | BSD |
| [ONNX Runtime DirectML](https://onnxruntime.ai) (Windows) | MIT |

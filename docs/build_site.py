"""Builds index.html (English) and ru/index.html (Russian) from _page.html,
and download/{mac,windows}/index.html from _download.html.

    python3 docs/build_site.py

The template marks text with lang="en" / lang="ru"; each page keeps only its own language,
so search engines index two clean pages linked by hreflang.
"""

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent

PAGES = {
    "en": {
        "out": "index.html",
        "url": "https://count-in.app/",
        "title": "Count-In — drum count-in and backing tracks for any song",
        "description": "Count-In finds a song's tempo and first downbeat, adds a drum count-in in front of it and can turn guitar and vocals down for a backing track. Free for macOS and Windows, works offline.",
        "og_title": "Count-In — a count-in for every song",
        "og_description": "A drum count-in that lands exactly on the first downbeat, and backing tracks without guitar or vocals. Free for macOS and Windows.",
        "og_image_alt": "Count-In app window with the waveform and the count-in clicks",
        "ld_description": "Finds a song's tempo and first downbeat, adds a sample-accurate drum count-in and turns guitar and vocals down for backing tracks.",
        "locale": "en_US", "locale_alt": "ru_RU",
        "other_lang": "ru", "other_url": "ru/", "other_label": "RU",
    },
    "ru": {
        "out": "ru/index.html",
        "url": "https://count-in.app/ru/",
        "title": "Count-In — отсчёт перед песней и минусовки без гитары и вокала",
        "description": "Count-In находит темп и первую долю песни, добавляет перед ней отсчёт палочками и убирает гитару и вокал для минусовки. Бесплатно для macOS и Windows, работает без интернета.",
        "og_title": "Count-In — отсчёт перед любой песней",
        "og_description": "Отсчёт палочками, который точно попадает в первую долю, и минусовки без гитары и вокала. Бесплатно для macOS и Windows.",
        "og_image_alt": "Окно Count-In с волной песни и щелчками отсчёта",
        "ld_description": "Находит темп и первую долю песни, добавляет отсчёт с точностью до сэмпла и убирает гитару и вокал для минусовки.",
        "locale": "ru_RU", "locale_alt": "en_US",
        "other_lang": "en", "other_url": "../", "other_label": "EN",
    },
}

# Relative asset paths that need ../ on the /ru/ page
ASSET = re.compile(r'(?<=["\s,])(?=(?:screenshots/|demo/|download/|site\.css|site\.js|favicon\.png|apple-touch-icon\.png|icon\.png))')


def drop(html: str, lang: str) -> str:
    """Removes elements marked with the given lang, with their content."""
    html = re.sub(rf'<img\b[^>]*\blang="{lang}"[^>]*>\s*', "", html)
    return re.sub(rf'<(\w+)\b[^>]*\blang="{lang}"[^>]*>.*?</\1>\s*', "", html, flags=re.S)


def build() -> None:
    tpl = (DOCS / "_page.html").read_text()
    tpl = tpl.split("\n", 2)[0] + "\n" + tpl.split("\n", 2)[2]  # drop the template note
    for lang, p in PAGES.items():
        html = drop(tpl, "ru" if lang == "en" else "en")
        for k, v in p.items():
            html = html.replace("{{" + k + "}}", v)
        html = html.replace("{{lang}}", lang)
        assert "{{" not in html, "unfilled placeholder"
        if lang == "ru":
            html = ASSET.sub("../", html)
        html = html.replace("<!doctype html>\n", "<!doctype html>\n<!-- Generated from _page.html by build_site.py -->\n", 1)
        out = DOCS / p["out"]
        out.parent.mkdir(exist_ok=True)
        out.write_text(html)
        print("wrote", out.relative_to(DOCS))


DOWNLOADS = {
    "mac": {
        "os": "macOS", "asset": "Count-In-macos-arm64.zip", "req": "macOS 12+ · Apple Silicon",
        "other_os": "Windows", "other_slug": "windows",
        "steps": [
            ("Open the zip", "Откройте архив",
             "Double-click <code>Count-In-macos-arm64.zip</code> in Downloads. Finder unpacks <code>Count-In.app</code> next to it.",
             "Дважды щёлкните <code>Count-In-macos-arm64.zip</code> в «Загрузках». Finder распакует рядом <code>Count-In.app</code>."),
            ("Move it to Applications", "Перенесите в «Программы»",
             "Drag <code>Count-In.app</code> into Applications. From Downloads it can't update itself.",
             "Перетащите <code>Count-In.app</code> в «Программы». Из «Загрузок» программа не сможет обновляться."),
            ("Allow the first launch", "Разрешите первый запуск",
             "macOS blocks unsigned apps once. Open System Settings → Privacy &amp; Security and click <b>Open Anyway</b>.",
             "macOS один раз блокирует неподписанные программы. Откройте Системные настройки → Конфиденциальность и безопасность и нажмите <b>Всё равно открыть</b>."),
        ],
    },
    "windows": {
        "os": "Windows", "asset": "Count-In-windows-x64.zip", "req": "Windows 10/11 · x64",
        "other_os": "macOS", "other_slug": "mac",
        "steps": [
            ("Extract the zip", "Распакуйте архив",
             "Right-click <code>Count-In-windows-x64.zip</code> in Downloads and choose <b>Extract All</b>.",
             "Щёлкните правой кнопкой по <code>Count-In-windows-x64.zip</code> в «Загрузках» и выберите <b>Извлечь все</b>."),
            ("Keep the folder", "Положите папку на место",
             "Move the <code>Count-In</code> folder somewhere permanent, such as Documents, and run <code>Count-In.exe</code>. Updates need a folder you can write to.",
             "Перенесите папку <code>Count-In</code> в постоянное место, например в «Документы», и запустите <code>Count-In.exe</code>. Для обновлений нужна папка с правом записи."),
            ("Get past SmartScreen", "Пропустите SmartScreen",
             "Windows warns about unsigned apps once. Click <b>More info</b> → <b>Run anyway</b>.",
             "Windows один раз предупреждает о неподписанных программах. Нажмите <b>Подробнее</b> → <b>Выполнить в любом случае</b>."),
        ],
    },
}


def build_downloads() -> None:
    tpl = (DOCS / "_download.html").read_text()
    for slug, d in DOWNLOADS.items():
        steps = "\n".join(
            f'  <div class="card step"><div class="ico">{i}</div>'
            f'<h3><span lang="en">{te}</span><span lang="ru">{tr}</span></h3>'
            f'<p lang="en">{de}</p><p lang="ru">{dr}</p></div>'
            for i, (te, tr, de, dr) in enumerate(d["steps"], 1))
        fields = {**d, "steps": steps, "url": f"https://github.com/shurshun/count-in/releases/latest/download/{d['asset']}"}
        html = tpl
        for k, v in fields.items():
            html = html.replace("{{" + k + "}}", v)
        assert "{{" not in html, "unfilled placeholder"
        html = html.replace("<!doctype html>\n", "<!doctype html>\n<!-- Generated from _download.html by build_site.py -->\n", 1)
        out = DOCS / "download" / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
        print("wrote", out.relative_to(DOCS))


if __name__ == "__main__":
    build()
    build_downloads()

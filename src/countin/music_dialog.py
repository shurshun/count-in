"""The private music search dialog."""
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

from . import i18n, music


class MusicDialog(QDialog):
    def __init__(self, parent, token, job_class):
        super().__init__(parent)
        self.token, self.job_class = token, job_class
        self.job, self.path = None, None
        self.on_done = None
        self.quality_available = False
        self.ru = i18n.language() == "ru"
        self.setWindowTitle(self.tr_text("Music search", "Поиск музыки"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.artist = QLineEdit(self, placeholderText=self.tr_text("Artist", "Исполнитель"))
        self.song = QLineEdit(self, placeholderText=self.tr_text("Song", "Песня"))
        self.artists = QComboBox()
        self.tracks = QComboBox()
        self.artist_search = self.add_search(layout, self.artist, self.search_artists)
        layout.addWidget(self.artists)
        self.song_search = self.add_search(layout, self.song, self.search_tracks)
        layout.addWidget(self.tracks)
        self.quality = QLabel(wordWrap=True)
        self.quality.hide()
        layout.addWidget(self.quality)
        self.status = QLabel(wordWrap=True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.download = QPushButton(self.tr_text("Download and open", "Скачать и открыть"))
        self.download.setAutoDefault(False)
        self.download.clicked.connect(self.confirm)
        buttons.addWidget(self.download)
        cancel = QPushButton(self.tr_text("Close", "Закрыть"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.artists.currentIndexChanged.connect(self.artist_changed)
        self.tracks.currentIndexChanged.connect(self.track_changed)
        self.artist.textChanged.connect(self.update_buttons)
        self.song.textChanged.connect(self.update_buttons)
        self.update_buttons()

    def tr_text(self, en, ru):
        return ru if self.ru else en

    def add_search(self, layout, field, callback):
        row = QHBoxLayout()
        row.addWidget(field)
        button = QPushButton(self.tr_text("Search", "Найти"))
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        field.returnPressed.connect(callback)
        row.addWidget(button)
        layout.addLayout(row)
        return button

    def update_buttons(self, *_args):
        idle = self.job is None
        self.artist_search.setEnabled(idle and len(self.artist.text().strip()) >= 2)
        self.song_search.setEnabled(idle and self.artists.currentData() is not None and len(self.song.text().strip()) >= 2)
        self.download.setEnabled(idle and self.tracks.currentData() is not None and self.quality_available)
        for widget in (self.artist, self.song, self.artists, self.tracks):
            widget.setEnabled(idle)

    def artist_changed(self, *_args):
        self.tracks.clear()
        self.quality.hide()
        self.update_buttons()

    def search_artists(self):
        if self.artist_search.isEnabled():
            self.artists.clear()
            self.run_job(music.artists, self.artist.text().strip(), done=self.show_artists)

    def show_artists(self, rows):
        for row in rows:
            self.artists.addItem(" · ".join(filter(None, (row["name"], row["genre"]))), row["id"])
        self.status.setText(self.tr_text("Choose an artist and search for a song.", "Выбери исполнителя и найди песню.")
                            if rows else self.tr_text("No artists found.", "Исполнители не найдены."))

    def search_tracks(self):
        if self.song_search.isEnabled():
            self.tracks.clear()
            self.run_job(music.tracks, self.artists.currentData(), self.song.text().strip(), done=self.show_tracks)

    @staticmethod
    def track_label(row):
        seconds = row["duration_ms"] // 1000
        return f'{row["title"]} · {row["album"]} · {row["year"]} · {seconds // 60}:{seconds % 60:02d}'

    def show_tracks(self, rows):
        self.tracks.blockSignals(True)
        try:
            for row in rows:
                self.tracks.addItem(self.track_label(row), row)
            self.tracks.setCurrentIndex(-1)
        finally:
            self.tracks.blockSignals(False)
        self.status.setText(self.tr_text("Choose the recording to import.", "Выбери нужную запись.")
                            if rows else self.tr_text("No songs found.", "Песни не найдены."))

    def track_changed(self, *_args):
        self.quality_available = False
        track = self.tracks.currentData()
        self.quality.setVisible(track is not None)
        if track is not None:
            self.quality.setText(self.tr_text("Checking available formats…", "Проверяем доступные форматы…"))
            self.run_job(music.quality, track["id"], done=self.show_quality)
        self.update_buttons()

    def show_quality(self, result):
        self.quality_available = result["has_sources"]
        formats = result["formats"]
        if not self.quality_available:
            text = self.tr_text("No sources with seeders right now.", "Сейчас нет источников с сидами.")
        elif not formats:
            text = self.tr_text("Source format is unknown.", "Источник не указал формат.")
        else:
            text = self.tr_text("Reported quality", "Заявленное качество") + " · " + ", ".join(formats)
        self.quality.setText(text)
        self.status.clear()

    def confirm(self):
        track = self.tracks.currentData()
        if self.job or not track or not self.quality_available:
            return
        reply = QMessageBox.question(
            self, self.tr_text("Confirm recording", "Подтверди запись"),
            f'{track["artist"]} — {self.track_label(track)}\n{self.quality.text()}',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.run_job(music.import_track, track, done=self.open_result)

    def run_job(self, action, *args, done):
        self.status.setText(self.tr_text("Working…", "Подожди…"))
        self.progress.setRange(0, 0)
        self.progress.show()
        self.on_done = done
        self.checking_quality = action is music.quality
        self.job = self.job_class(action, self.token, *args)
        self.job.signals.done.connect(self.completed)
        self.job.signals.failed.connect(self.failed)
        self.job.signals.progress.connect(self.report)
        QThreadPool.globalInstance().start(self.job)
        self.update_buttons()

    def report(self, text, done, total):
        self.status.setText(text)
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def completed(self, result):
        callback, self.on_done = self.on_done, None
        if callback is None:
            return
        self.job = None
        self.progress.hide()
        callback(result)
        if self.path is None:
            self.update_buttons()

    def failed(self, message):
        self.on_done = None
        self.job = None
        self.progress.hide()
        self.status.setText(message)
        if self.checking_quality:
            self.quality.setText(self.tr_text("Could not check quality. Select the recording again.",
                                              "Не удалось проверить качество. Выбери запись заново."))
            self.tracks.blockSignals(True)
            self.tracks.setCurrentIndex(-1)
            self.tracks.blockSignals(False)
        self.update_buttons()

    def open_result(self, path):
        self.path = path
        self.accept()

    def reject(self):
        self.on_done = None
        if self.job:
            self.job.cancelled.set()
        super().reject()

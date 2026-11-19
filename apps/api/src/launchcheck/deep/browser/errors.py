"""Tarayıcı katmanı hataları (Playwright gerektirmeden import edilebilir)."""


class BotChallengeError(Exception):
    """Ana sayfa yerine bot doğrulama sayfası geldi: taramak yanıltıcı bulgular üretir."""

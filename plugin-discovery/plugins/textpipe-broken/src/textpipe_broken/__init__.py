import language_tables  # not installed: this plugin can't import

from textpipe import Filter


class Translate(Filter):
    def apply(self, text):
        return language_tables.translate(text)

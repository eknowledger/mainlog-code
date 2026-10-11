from textpipe import Casing


class Caps(Casing):
    def apply(self, text):
        return text.title()

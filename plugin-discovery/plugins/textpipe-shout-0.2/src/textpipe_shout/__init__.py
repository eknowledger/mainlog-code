from textpipe import Casing


class Shout(Casing):
    def apply(self, text):
        return text.upper() + " (0.2)"

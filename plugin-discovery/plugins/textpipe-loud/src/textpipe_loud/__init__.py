from textpipe import Casing


class Loud(Casing):
    def apply(self, text):
        return text.upper() + "!!!"

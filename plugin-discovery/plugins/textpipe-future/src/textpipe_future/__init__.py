from textpipe import Filter

print("textpipe-future: warming up the model cache")  # runs on import


class Summarize(Filter):
    api_version = "2.0"

    def apply(self, text):
        return text[:40]

"""A basic string operation and an optional formatting feature."""


def normalize(value):
    return value.strip()


def decorate(value):
    import optional_toolkit

    return optional_toolkit.decorate(normalize(value))

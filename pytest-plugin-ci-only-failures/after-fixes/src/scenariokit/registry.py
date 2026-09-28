def scenario(name):
    """Mark an async function as a named scenario."""
    def mark(fn):
        fn._scenario_name = name
        return fn
    return mark

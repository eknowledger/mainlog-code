"""README example: check that the dev server greets a new client.

Scenarios live in test files because that is how users run them: put them in a
test file and point pytest at a running server.

Run it against a running server:  pytest tests/examples --scenario-server HOST:PORT
"""
from scenariokit import scenario


@scenario("greeting")
async def greets_new_client(ctx):
    assert await ctx.request("HELLO") == "WELCOME"

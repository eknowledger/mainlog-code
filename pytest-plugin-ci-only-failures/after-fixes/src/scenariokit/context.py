import asyncio


class ScenarioContext:
    """What a scenario uses to talk to the development server."""

    def __init__(self, server):
        host, _, port = server.rpartition(":")
        self.host, self.port = host, int(port)

    async def request(self, line):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(line.encode() + b"\n")
            await writer.drain()
            return (await reader.readline()).decode().strip()
        finally:
            writer.close()
            await writer.wait_closed()

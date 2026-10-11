# The proposal's tool, with a stand-in @tool decorator so the module imports.
def tool(fn):
    return fn


@tool
async def transfer_call(destination: str) -> str:
    """Transfer the call to a human agent."""
    await self.endpoint.transfer(destination)
    return f"Transferred to {destination}"

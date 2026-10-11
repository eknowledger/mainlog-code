"""Outbound voice agent: appointment reminder."""

import os
from mf.pipeline import Pipeline
from mf.endpoint import Endpoint
from mf.session import Session
from mf.ai import STT, LLM, TTS, VAD, tool
from mf.nodes import OpusDecoder, OpusEncoder


@tool
async def transfer_call(ctx, destination: str) -> str:
    """Transfer the call to a human agent."""
    await ctx.endpoint.transfer(destination)
    return f"Transferred to {destination}"


@tool
async def end_call(ctx) -> str:
    """End the current call."""
    await ctx.endpoint.hangup()
    return "Call ended"


pipeline = Pipeline("outbound-agent")
ep = Endpoint.sip(trunk=os.environ["SIP_TRUNK"], to=os.environ["CALLEE"])

(
    ep.audio_in
    >> OpusDecoder()
    >> VAD()
    >> STT(provider="speech-provider")
    >> LLM(
        provider="llm-provider",
        model="some-model",
        system_prompt="You are a helpful appointment reminder agent.",
        tools=[transfer_call, end_call],
        greeting="Hi! This is a reminder about your appointment tomorrow.",
    )
    >> TTS(provider="voice-provider", voice="default")
    >> OpusEncoder()
    >> ep.audio_out
)

Session(pipeline).run()

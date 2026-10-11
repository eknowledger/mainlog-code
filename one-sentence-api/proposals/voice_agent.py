# Proposal 01, reconstructed with generic names. It imports from a package
# (mf_all) that doesn't exist, so it can't run as written.
from mf_all import Pipeline, Session, Endpoint, VoiceAgent, AgentSession
from mf_all.nodes.audio import OpusDecoder, OpusEncoder
from mf_all.nodes.ai import STTNode, TTSNode, LLMNode, VADNode
from mf_all.tools import tool


@tool
async def transfer_call(destination: str) -> str:
    """Transfer the call to a human agent."""
    await self.endpoint.transfer(destination)
    return f"Transferred to {destination}"


@tool
async def end_call() -> str:
    """End the current call."""
    await self.session.hangup()
    return "Call ended"


class OutboundAgent(VoiceAgent):
    def build(self) -> Pipeline:
        p = Pipeline("outbound-agent")
        ep = Endpoint.sip(trunk="my-trunk", to="+15551234567")
        (
            ep.audio_in
            >> OpusDecoder()
            >> VADNode()
            >> STTNode(provider="speech-provider")
            >> LLMNode(
                provider="llm-provider",
                model="some-model",
                system_prompt="You are a helpful appointment reminder agent.",
                tools=[transfer_call, end_call],
            )
            >> TTSNode(
                provider="voice-provider",
                voice="default",
            )
            >> OpusEncoder()
            >> ep.audio_out
        )
        return p

    def on_call_started(self, endpoint):
        self.agent_session.say(
            "Hi! This is a reminder about your appointment tomorrow."
        )


OutboundAgent().run()

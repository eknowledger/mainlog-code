# Proposal 02, reconstructed with generic names. Scaling turns the app into a
# function that receives a JobContext and is started by a Worker.
from mf_all import Pipeline, Endpoint, Worker, JobContext
from mf_all.nodes.audio import OpusDecoder, OpusEncoder
from mf_all.nodes.ai import SpeechToText, TextToSpeech, LLMProcessor, VAD
from mf_all.worker import WorkerOptions


async def entrypoint(ctx: JobContext):
    call = ctx.metadata
    pipeline = ctx.pipeline
    ep = Endpoint.sip(trunk=call["sip_trunk"], to=call["number"])
    (
        ep.audio_in
        >> OpusDecoder()
        >> VAD()
        >> SpeechToText(
            language="en-US",
        )
        >> LLMProcessor(
            system_prompt=call["prompt"],
            tools=call.get("tools", []),
        )
        >> TextToSpeech(voice="default")
        >> OpusEncoder()
        >> ep.audio_out
    )
    ctx.add_shutdown_callback(lambda: print(f"Call {call['call_id']} ended"))
    await ctx.session.run_until_complete()


if __name__ == "__main__":
    worker = Worker(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            max_concurrent=5,
            name="voice-agent",
        )
    )
    worker.run()

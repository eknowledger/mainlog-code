"""A small model of where audio gets decoded in a multi-party call.

rtp       -- RTP header packing with the RFC 6464 audio-level extension
e2e       -- a toy end-to-end frame cipher (for the demo only, not real crypto)
boxes     -- a selective forwarder, a mixer, and an agent endpoint
counts    -- decode/encode/hop counts per topology and agent placement
"""

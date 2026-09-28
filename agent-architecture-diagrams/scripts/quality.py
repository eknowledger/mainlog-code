"""Numeric contract shared by routing requests and the independent SVG audit.

Distances are centerline distances in source SVG pixels. Parallel separation
applies when the shared span is strictly greater than PARALLEL_SHARED_SPAN;
actual stroke overlap is forbidden at every positive span.
"""

MIN_PARALLEL_GAP = 20.0
PARALLEL_SHARED_SPAN = 10.0
NODE_CLEARANCE = 16.0

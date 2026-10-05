"""ai-starter: a conversational agent that answers customer questions from website content."""

import os

# onnxruntime (loaded by fastembed, and by importing qdrant_client or langchain_qdrant) starts a
# telemetry uploader thread that sends usage data to Microsoft. On macOS that thread can abort
# Python at exit ("recursive_mutex lock failed"), after everything else has finished. It must be
# off before onnxruntime is imported, which is why it is set here.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

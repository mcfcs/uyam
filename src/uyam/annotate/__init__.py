"""LLM-based annotation pipeline for the Uyám sarcasm dataset.

Turns the raw collected corpus (data/raw/**.jsonl) into a labeled training
dataset for the downstream model repository (leische):

  index -> select -> lid -> sentiment -> run (x3 annotators) -> aggregate
  -> adjudicate -> human review -> export

Heavy dependencies (ollama, transformers, fasttext) are imported lazily inside
the passes that need them so the base collector install keeps working.
"""

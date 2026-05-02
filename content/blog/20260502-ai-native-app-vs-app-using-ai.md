---
title: ai-native apps vs. apps that use ai
date: 2026-05-02
section: blog
---

Been reading through gbrain code for a few days. I may be late to the party but this looks like a new class of AI-native apps. 

What differentiates an AI-native app vs. an app that uses AI? Here's what I surmised from gbrain.

### 1. Machine-2-machine communication is in English

This is the most interesting design choice. In gbrain, human query -> agent -> resolver (index of skills in English) -> skills (how-to recipes in English) -> deterministic code (through MCP). Instead of using structured data & code to guide agent behavior, human language is the language of choice.

### 2. Non-deterministic agents call deterministic code

The agent is the user of gbrain, not humans. Therefore, it's the non-deterministic agent that calls on the deterministic tooling, not the other way around. Whereas a regular "app that uses AI" has deterministic code crafting & feeding queries into a stateless LLM. 

Note: I define agent as a LLM wrapped in a harness that manages context, maintains a persistent internal state & runtime.

### 3. Context-centric architecture

Context management isn't necessary for simple prompting, but becomes indispensable for large-scale & complex problems when you regularly hit the LLM's context-window limits. AI-native apps like gbrain have built-in RAG.

As a side-note, RAG seems to be the primary swappable tooling if you want to switch use cases (besides the skills obviously). 

gbrain's RAG is specifically set up for a VC. Examples: built-in graph edges such as "invested_in, founded", backlink retrieval weight boost that values relationship density & differentiates between data sources, timelines as a hardcoded concept in the schema / parsing / page-generation / basically everywhere.

If you want to use this in biomedical research, for example, you'd probably swap out text-embedding-3-large to something like a bioBERT, use biomedical ontology dictionaries to build the knowledge graph which would then be far more heavily weighted for retrieval than FTS / vector search, etc.
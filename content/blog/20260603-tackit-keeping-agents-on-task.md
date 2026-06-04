---
title: tackit - keeping agents on task
date: 2026-06-03
section: blog
---

After working on multiple large-scale projects with AI coding agents, I built a utility called [tackit](https://github.com/reedvoid/tackit) to help keep agents on task & improve context window efficiency. Here are the problems I'm trying to solve, 

- **Scattered records**: agents constantly create new files in different formats, scattered everywhere, to keep track of tasks which often get buried & lost. 
- **Context erosion**: every prompt re-reads lengthy planning docs, todo files, chat histories, triggering frequent auto-compaction and quality degradation.
- **Dependency hell**: dependency tracking is almost non-existent, relying on code-execution errors for dependency discovery, burying any where test coverage falls short.

I've been dog-fooding tackit for a week and fixed a ton of logical & efficiency issues, hopefully this little tool can be of help to others. 


### tackit keeps agents on track 

tackit is a deterministic app that helps keep agents on track with task management (in db with strict schema and pydantic validation), strongly nudges agents to resolve dependency cascades (strongly-bounded) upon edits with close-gates (can't close a task without resolving dependency cascades), minimal context bloat by surfacing & retrieving exactly the specs needed to complete tasks, and diff-based editing to minimize context & inference bloat. 

It also comes with a wide range of skill, mcp, and agent settings (e.g., claude.md) based nudges to improve coding agents' behaviors. Failing loud / ship-on-pain clauses help to revert agents' natural inclination to frequently ignore failures, assume degradation is acceptable, and silently moving forward. A fold-back behavior that strongly suggests folding learnings back into specific task's design & implementation specs post-implementation, as the process of writing code often leads to new discoveries. 

There are a ton more nuanced features & capabilities but these are the highlights. 


### opinionated spec<>task coupling

tackit is natively aware of the difference between design / schema specifications, vs. production (coding) tasks, vs. meta (discussion, notes, comments) tasks. Specifically tackit is designed to tightly couple specification <> production tasks. 

After using coding agents for a while, I got into the habit of keeping a set of design & schema specs that are constantly synced with implementation. This wasn't usually the case pre-ai, and the design spec written at the beginning of projects tend to to stale pretty quickly, relying on in-code notes & comments as reminders. With ai agents, I found if there wasn't a persistent, clearly laid out spec that is constantly updated, the agent drifts extremely fast and becomes extremely slow as it needs to ingest massive amounts of codebase & run tests to figure out what is happening. 

Hence one of tackit's primary goals is to keep specifications constantly in sync with implementation, including all dependencies. 


### getting started

There are two parts to the installation, one for the human, one for the agent. 

Step 1: Humans just run pypi to install the package, 

```bash
pip install tackit
```

Step 2: After the package is installed, go into the coding agent and ask it to wire up the mcp & skills. 

```bash
I've installed the `tackit` Python package. Run `tackit setup` and carry out the steps it prints to register it (MCP server + skill) for this project.
```

That's it, let me know what you think! 
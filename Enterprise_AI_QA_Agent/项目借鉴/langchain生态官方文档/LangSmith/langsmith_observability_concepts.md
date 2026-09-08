# Observability concepts - Docs by LangChain

**Source**: https://docs.langchain.com/langsmith/observability-concepts
**Description**: How LangSmith structures observability data as runs, traces, threads, and trajectories, and how to send traces.

---

> ## Documentation Index
> 
> Fetch the complete documentation index at: [/llms.txt](https://docs.langchain.com/langsmith/</llms.txt>)
> 
> Use this file to discover all available pages before exploring further.

Skip to main content

Interrupt is coming to NYC and London this fall. Join the builders, engineers, and teams shaping what's next for agents. [Get your tickets →](https://docs.langchain.com/langsmith/<https:/interrupt.langchain.com/>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

Monitor

Search...

⌘K

  * [Ask AI](https://docs.langchain.com/langsmith/<https:/chat.langchain.com/>)
  * [GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)
  * [Try LangSmith](https://docs.langchain.com/langsmith/<https:/smith.langchain.com/>)
  * [Try LangSmith](https://docs.langchain.com/langsmith/<https:/smith.langchain.com/>)

Search...

Navigation

Observability concepts

[Overview](https://docs.langchain.com/langsmith/</langsmith/observability>)[Trace](https://docs.langchain.com/langsmith/</langsmith/observability-quickstart>)[Debug](https://docs.langchain.com/langsmith/</langsmith/view-traces>)[Observe](https://docs.langchain.com/langsmith/</langsmith/dashboards>)[Reference](https://docs.langchain.com/langsmith/</langsmith/reference>)

  * [Quickstart](https://docs.langchain.com/langsmith/</langsmith/observability-quickstart>)

  * [Tutorial](https://docs.langchain.com/langsmith/</langsmith/observability-llm-tutorial>)

  * [Concepts](https://docs.langchain.com/langsmith/</langsmith/observability-concepts>)

  * [Chat](https://docs.langchain.com/langsmith/<https:/docs.langchain.com/langsmith/chat#observability>)

### Tracing setup

  * Integrations

  * Manual instrumentation

### Configuration & troubleshooting

  * Project & environment settings

  * [Cost tracking](https://docs.langchain.com/langsmith/</langsmith/cost-tracking>)
  * [Usage and billing](https://docs.langchain.com/langsmith/</langsmith/usage-and-billing>)
  * Advanced tracing techniques

  * Data & privacy

  * Troubleshooting guides

## On this page

  * How LangSmith structures and visualizes data
    * Runs
    * Traces
    * Threads
    * Trajectories
    * Compare traces, threads, and trajectories
    * Projects
  * Trace enrichment
    * Feedback
    * Tags
    * Metadata
  * Sending traces
    * Integrations
    * Manual instrumentation
  * Data retention

# Observability concepts

Copy pageCopy page

How LangSmith structures observability data as runs, traces, threads, and trajectories, and how to send traces.

Copy pageCopy page

LangSmith Observability lets you record, inspect, and analyze every step your AI agent takes. This page explains how that data is structured and visualized in LangSmith as well as how to start sending traces.

## 

​

How LangSmith structures and visualizes data

In LangSmith, every unit of work an agent performs, such as a model call, tool invocation, or information retrieval, is recorded as a _run_. The runs for a single operation are collected into a _trace_. You can link together traces from multi-turn sessions as a _thread_. A _trajectory_ is another way to structure and visualize that data. While a thread groups the traces of a session and keeps their nested structure, a trajectory flattens the entire session into an ordered list of messages that shows the path an agent took from start to finish. ![A thread groups a session's traces and keeps their nesting, while a trajectory flattens the same session into an ordered list of messages](https://mintcdn.com/langchain-5e9cc07a/_6XeQZT2NAQ4WqkK/langsmith/images/thread-trajectory-light.png?fit=max&auto=format&n=_6XeQZT2NAQ4WqkK&q=85&s=e770ae021710ef231582cd59aae3a403) ![A thread groups a session's traces and keeps their nesting, while a trajectory flattens the same session into an ordered list of messages](https://mintcdn.com/langchain-5e9cc07a/_6XeQZT2NAQ4WqkK/langsmith/images/thread-trajectory-dark.png?fit=max&auto=format&n=_6XeQZT2NAQ4WqkK&q=85&s=b60e198867da6e9a43877a66b0d90e54)

### 

​

Runs

A _run_ represents a single unit of work executed by an agent, such as calling an LLM, formatting a prompt, or retrieving documents. If you are familiar with [OpenTelemetry](https://docs.langchain.com/langsmith/<https:/opentelemetry.io/>), you can think of a run as a span.

### 

​

Traces

A _trace_ is a collection of runs for a single operation. For example, if a user request triggers an agent that calls a model, runs a tool, and then calls the model again, all of those runs belong to the same trace. Runs are bound to a trace by a unique trace ID.

Each trace is limited to a maximum of 25,000 runs. Once the trace reaches this limit, LangSmith will reject any additional runs that you send for that trace.

### 

​

Threads

A _thread_ is a sequence of traces representing a single multi-turn session. A turn is one exchange in that session: a user’s message and everything the agent does in response. Each turn is recorded as its own trace. To group traces into a thread, pass a `thread_id` metadata key with a unique value. [Learn how to configure threads](https://docs.langchain.com/langsmith/</langsmith/threads>).

### 

​

Trajectories

A _trajectory_ is a flat, ordered list of messages that shows the path an agent took from start to finish. In LangSmith, a trajectory is a projection over the traces in a thread. It contains the human, AI, and tool messages exchanged during the session, each appearing once, in the order it first appeared, with the nesting of runs removed.

The [Messages view](https://docs.langchain.com/langsmith/</langsmith/view-traces#messages-view>), which renders trajectories in the LangSmith UI, is in **[beta](https://docs.langchain.com/langsmith/</langsmith/release-stages>)**.

[Learn how trajectories render in the Messages view](https://docs.langchain.com/langsmith/</langsmith/messages-view-integrations>).

### 

​

Compare traces, threads, and trajectories

| Trace| Thread| Trajectory  
---|---|---|---  
Shape| Tree of runs| Sequence of traces| Flat, ordered list of messages  
Contains| Every run, with full inputs and outputs| Every run in every linked trace| Every message in every linked trace, deduplicated  
Reach for it when| You are debugging why one operation failed or ran slow| You are inspecting how the agent behaved across turns, with timing and nesting intact| You are reading what was exchanged in the session, without the execution detail  
  
Use **[Chat](https://docs.langchain.com/langsmith/</langsmith/chat>)** to analyze traces, runs, and threads. Chat helps you understand agent performance, debug issues, and gain insights from conversation threads without manually digging through data.

### 

​

Projects

A _project_ is a container for all the traces related to a single application or service. [Log traces to a project](https://docs.langchain.com/langsmith/</langsmith/log-traces-to-project>).

## 

​

Trace enrichment

### 

​

Feedback

_Feedback_ allows you to score an individual run based on certain criteria. Each feedback entry consists of a tag and a score, and is bound to a run by a unique run ID. Feedback can be continuous or discrete (categorical), and tags can be reused across runs within an organization. For more on how feedback is stored, refer to the [Feedback data format guide](https://docs.langchain.com/langsmith/</langsmith/feedback-data-format>).

### 

​

Tags

_Tags_ are strings you can attach to runs to categorize, filter, and group them in the LangSmith UI. [Learn how to attach tags to your traces](https://docs.langchain.com/langsmith/</langsmith/add-metadata-tags>).

### 

​

Metadata

_Metadata_ is a collection of key-value pairs you can attach to runs. For example, application version, environment, or any other contextual information. Similarly to tags, you can use metadata to filter and group runs. [Learn how to add metadata to your traces](https://docs.langchain.com/langsmith/</langsmith/add-metadata-tags>).

## 

​

Sending traces

There are two ways to send trace data to LangSmith.

### 

​

Integrations

LangSmith _integrations_ provide automatic tracing for popular LLM providers and agent frameworks (the equivalent of auto-instrumentation in general observability). When you use a supported framework such as LangChain, LangGraph, OpenAI, Anthropic, or CrewAI, the integration captures inputs, outputs, and metadata without requiring manual code changes. [Browse all integrations](https://docs.langchain.com/langsmith/</langsmith/integrations>).

### 

​

Manual instrumentation

_Manual instrumentation_ lets you add tracing to any code, regardless of the framework. Use it when you’re not using a supported integration or when you need granular control over what gets traced. LangSmith provides three mechanisms:

  * `@traceable` / `traceable`: a decorator to trace any function
  * `trace` context manager (Python): wrap specific code blocks
  * `RunTree` API: low-level, explicit trace construction

[Learn how to add manual instrumentation](https://docs.langchain.com/langsmith/</langsmith/annotate-code>).

## 

​

Data retention

LangSmith (SaaS) retains trace data for 180 days from ingestion. After that, traces are permanently deleted, with limited metadata retained for usage statistics. For details on retention tiers and pricing, refer to [Usage and billing: Data retention](https://docs.langchain.com/langsmith/</langsmith/usage-and-billing#data-retention>).

To keep data beyond the retention period, add it to a [dataset](https://docs.langchain.com/langsmith/</langsmith/manage-datasets>). Datasets persist indefinitely, even after the source trace is deleted.

To delete traces before their expiration date, see [Manage a trace](https://docs.langchain.com/langsmith/</langsmith/manage-trace#delete-a-trace>).

* * *

[Connect these docs](https://docs.langchain.com/langsmith/</use-these-docs>) to Claude, VSCode, and more via MCP for real-time answers.

[Edit this page on GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/edit/main/src/langsmith/observability-concepts.mdx>) or [file an issue](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/issues/new/choose>).

Was this page helpful?

YesNo

[Trace an LLM application tutorialPrevious](https://docs.langchain.com/langsmith/</langsmith/observability-llm-tutorial>)[LangSmith ChatNext](https://docs.langchain.com/langsmith/</langsmith/chat-observability>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)

Resources

[Forum](https://docs.langchain.com/langsmith/<https:/forum.langchain.com/>)[Changelog](https://docs.langchain.com/langsmith/<https:/changelog.langchain.com/>)[LangChain Academy](https://docs.langchain.com/langsmith/<https:/academy.langchain.com/>)[Contact Sales](https://docs.langchain.com/langsmith/<https:/www.langchain.com/contact-sales>)

Company

[Home](https://docs.langchain.com/langsmith/<https:/langchain.com/>)[Trust Center](https://docs.langchain.com/langsmith/<https:/trust.langchain.com/>)[Careers](https://docs.langchain.com/langsmith/<https:/langchain.com/careers>)[Blog](https://docs.langchain.com/langsmith/<https:/blog.langchain.com/>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)
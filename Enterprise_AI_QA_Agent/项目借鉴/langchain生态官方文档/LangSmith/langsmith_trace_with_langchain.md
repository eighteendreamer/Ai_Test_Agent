# Trace LangChain applications (Python and JS/TS) - Docs by LangChain

**Source**: https://docs.langchain.com/langsmith/trace-with-langchain

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

Agent frameworks

Trace LangChain applications (Python and JS/TS)

[Overview](https://docs.langchain.com/langsmith/</langsmith/observability>)[Trace](https://docs.langchain.com/langsmith/</langsmith/observability-quickstart>)[Debug](https://docs.langchain.com/langsmith/</langsmith/view-traces>)[Observe](https://docs.langchain.com/langsmith/</langsmith/dashboards>)[Reference](https://docs.langchain.com/langsmith/</langsmith/reference>)

  * [Quickstart](https://docs.langchain.com/langsmith/</langsmith/observability-quickstart>)

  * [Tutorial](https://docs.langchain.com/langsmith/</langsmith/observability-llm-tutorial>)

  * [Concepts](https://docs.langchain.com/langsmith/</langsmith/observability-concepts>)

  * [Chat](https://docs.langchain.com/langsmith/<https:/docs.langchain.com/langsmith/chat#observability>)

### Tracing setup

  * Integrations

    * [Overview](https://docs.langchain.com/langsmith/</langsmith/integrations>)
    * LLM providers

    * Agent frameworks

      * [AutoGen](https://docs.langchain.com/langsmith/</langsmith/trace-with-autogen>)
      * [Claude Agent SDK](https://docs.langchain.com/langsmith/</langsmith/trace-claude-agent-sdk>)
      * [Claude Managed Agents](https://docs.langchain.com/langsmith/</langsmith/trace-with-claude-managed-agents>)
      * [CrewAI](https://docs.langchain.com/langsmith/</langsmith/trace-with-crewai>)
      * [Deep Agents](https://docs.langchain.com/langsmith/</langsmith/trace-deep-agents>)
      * [Google ADK](https://docs.langchain.com/langsmith/</langsmith/trace-with-google-adk>)
      * [LangChain](https://docs.langchain.com/langsmith/</langsmith/trace-with-langchain>)
      * [LangGraph](https://docs.langchain.com/langsmith/</langsmith/trace-with-langgraph>)
      * [Mastra](https://docs.langchain.com/langsmith/</langsmith/trace-with-mastra>)
      * [Microsoft Agent Framework](https://docs.langchain.com/langsmith/</langsmith/trace-with-microsoft-agent-framework>)
      * [OpenAI Agents SDK](https://docs.langchain.com/langsmith/</langsmith/trace-with-openai-agents-sdk>)
      * [OpenTelemetry](https://docs.langchain.com/langsmith/</langsmith/trace-with-opentelemetry>)
      * [PydanticAI](https://docs.langchain.com/langsmith/</langsmith/trace-with-pydantic-ai>)
      * [Semantic Kernel](https://docs.langchain.com/langsmith/</langsmith/trace-with-semantic-kernel>)
      * [Strands Agents](https://docs.langchain.com/langsmith/</langsmith/trace-with-strands-agents>)
      * [Vercel AI SDK](https://docs.langchain.com/langsmith/</langsmith/trace-with-vercel-ai-sdk>)
    * Voice AI frameworks

    * Developer tools

  * Manual instrumentation

### Configuration & troubleshooting

  * Project & environment settings

  * [Cost tracking](https://docs.langchain.com/langsmith/</langsmith/cost-tracking>)
  * [Usage and billing](https://docs.langchain.com/langsmith/</langsmith/usage-and-billing>)
  * Advanced tracing techniques

  * Data & privacy

  * Troubleshooting guides

## On this page

  * Installation
  * Quick start
    * 1\. Configure your environment
    * 2\. Log a trace
    * 3\. View your trace
  * Trace selectively
  * Log to a specific project
    * Statically
    * Dynamically
  * Add metadata and tags to traces
  * Customize run name
  * Override model name in traces
  * Customize run ID
  * Access run (span) ID for LangChain invocations
  * Ensure all traces are submitted before exiting
  * Trace without setting environment variables
  * Distributed tracing with LangChain (Python)
  * Interoperability between LangChain (Python) and LangSmith SDK
  * Interoperability between LangChain.JS and LangSmith SDK
    * Tracing LangChain objects inside traceable (JS only)
    * Tracing LangChain child runs via traceable / RunTree API (JS only)

[Tracing setup](https://docs.langchain.com/langsmith/</langsmith/integrations>)

[Integrations](https://docs.langchain.com/langsmith/</langsmith/integrations>)

[Agent frameworks](https://docs.langchain.com/langsmith/</langsmith/trace-with-autogen>)

# Trace LangChain applications (Python and JS/TS)

Copy pageCopy page

Copy pageCopy page

LangSmith integrates seamlessly with LangChain (Python and JavaScript), the popular open-source framework for building LLM applications.

## 

​

Installation

Install the following for Python or JS (the code snippets use the OpenAI integration). For a full list of packages available, see the [LangChain docs](https://docs.langchain.com/langsmith/</oss/python/integrations/providers/overview>).

pip

yarn

npm

pnpm
[code]
    pip install langchain_openai
    
[/code]
[code]
    yarn add @langchain/openai @langchain/core
    
[/code]
[code]
    npm install @langchain/openai @langchain/core
    
[/code]
[code]
    pnpm add @langchain/openai @langchain/core
    
[/code]

## 

​

Quick start

### 

​

1\. Configure your environment
[code] 
    export LANGSMITH_TRACING=true
    export LANGSMITH_API_KEY=<your-api-key>
    # This example uses OpenAI, but you can use any LLM provider of choice
    export OPENAI_API_KEY=<your-openai-api-key>
    # For LangSmith API keys linked to multiple workspaces, set the LANGSMITH_WORKSPACE_ID environment variable to specify which workspace to use.
    export LANGSMITH_WORKSPACE_ID=<your-workspace-id>
    
[/code]

If your account is in a region other than US (the default), also set `LANGSMITH_ENDPOINT` to the API URL for your region. Without this, your API key won’t be recognized and requests will fail to authenticate.Region  
---  
GCP US  
GCP EU  
GCP APAC  
AWS US  
For example, EU accounts: `export LANGSMITH_ENDPOINT="https://eu.api.smith.langchain.com"`. Do not add a trailing slash to the URL, as this can cause authentication errors.

If you are using LangChain.js with LangSmith and are not in a serverless environment, we also recommend setting the following explicitly to reduce latency:`export LANGCHAIN_CALLBACKS_BACKGROUND=true`If you are in a serverless environment, we recommend setting the reverse to allow tracing to finish before your function ends:`export LANGCHAIN_CALLBACKS_BACKGROUND=false`

### 

​

2\. Log a trace

No extra code is needed to log a trace to LangSmith. Just run your LangChain code as you normally would.

Python

TypeScript
[code]
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant. Please respond to the user's request only based on the given context."),
        ("user", "Question: {question}\nContext: {context}")
    ])
    
    model = ChatOpenAI(model="gpt-5.4-mini")
    output_parser = StrOutputParser()
    chain = prompt | model | output_parser
    
    question = "Can you summarize this morning's meetings?"
    context = "During this morning's meeting, we solved all world conflict."
    
    chain.invoke({"question": question, "context": context})
    
[/code]
[code]
    import { ChatOpenAI } from "@langchain/openai";
    import { ChatPromptTemplate } from "@langchain/core/prompts";
    import { StringOutputParser } from "@langchain/core/output_parsers";
    
    const prompt = ChatPromptTemplate.fromMessages([
      ["system", "You are a helpful assistant. Please respond to the user's request only based on the given context."],
      ["user", "Question: {question}\nContext: {context}"],
    ]);
    
    const model = new ChatOpenAI({ modelName: "gpt-5.4-mini" });
    const outputParser = new StringOutputParser();
    const chain = prompt.pipe(model).pipe(outputParser);
    
    const question = "Can you summarize this morning's meetings?"
    const context = "During this morning's meeting, we solved all world conflict."
    
    await chain.invoke({ question: question, context: context });
    
[/code]

### 

​

3\. View your trace

By default, the trace will be logged to the project with the name `default`.

## 

​

Trace selectively

The previous section showed how to trace all invocations of a LangChain runnables within your applications by setting a single environment variable. While this is a convenient way to get started, you may want to trace only specific invocations or parts of your application. There are two ways to do this in Python: by manually passing in a `LangChainTracer` instance as a [callback](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain_core/callbacks/>), or by using the [`tracing_context` context manager](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langsmith/observability/sdk/run_helpers/#langsmith.run_helpers.tracing_context>). In JS/TS, you can pass a [`LangChainTracer`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/javascript/classes/_langchain_core.tracers_tracer_langchain.LangChainTracer.html>) instance as a callback.

Python

TypeScript
[code]
    # You can opt-in to specific invocations..
    import langsmith as ls
    
    with ls.tracing_context(enabled=True):
        chain.invoke({"question": "Am I using a callback?", "context": "I'm using a callback"})
    
    # This will NOT be traced (assuming LANGSMITH_TRACING is not set)
    chain.invoke({"question": "Am I being traced?", "context": "I'm not being traced"})
    
    # This would not be traced, even if LANGSMITH_TRACING=true
    with ls.tracing_context(enabled=False):
        chain.invoke({"question": "Am I being traced?", "context": "I'm not being traced"})
    
[/code]
[code]
    // You can configure a LangChainTracer instance to trace a specific invocation.
    import { LangChainTracer } from "@langchain/core/tracers/tracer_langchain";
    
    const tracer = new LangChainTracer();
    await chain.invoke(
      {
        question: "Am I using a callback?",
        context: "I'm using a callback"
      },
      { callbacks: [tracer] }
    );
    
[/code]

## 

​

Log to a specific project

### 

​

Statically

As mentioned in the [tracing conceptual guide](https://docs.langchain.com/langsmith/</langsmith/observability-concepts>) LangSmith uses the concept of a Project to group traces. If left unspecified, the tracer project is set to default. You can set the `LANGSMITH_PROJECT` environment variable to configure a custom project name for an entire application run. This should be done before executing your application.
[code] 
    export LANGSMITH_PROJECT=my-project
    
[/code]

The `LANGSMITH_PROJECT` flag is only supported in JS SDK versions >= 0.2.16, use `LANGCHAIN_PROJECT` instead if you are using an older version.

### 

​

Dynamically

This largely builds off of the previous section and allows you to set the project name for a specific `LangChainTracer` instance or as parameters to the `tracing_context` context manager in Python.

Python

TypeScript
[code]
    # You can set the project name using the project_name parameter.
    import langsmith as ls
    
    with ls.tracing_context(project_name="My Project", enabled=True):
        chain.invoke({"question": "Am I using a context manager?", "context": "I'm using a context manager"})
    
[/code]
[code]
    // You can set the project name for a specific tracer instance:
    import { LangChainTracer } from "@langchain/core/tracers/tracer_langchain";
    
    const tracer = new LangChainTracer({ projectName: "My Project" });
    await chain.invoke(
      {
        question: "Am I using a callback?",
        context: "I'm using a callback"
      },
      { callbacks: [tracer] }
    );
    
[/code]

## 

​

Add metadata and tags to traces

You can annotate your traces with arbitrary metadata and tags by providing them in the [`RunnableConfig`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain_core/runnables/?h=runnablecon#langchain_core.runnables.RunnableConfig>). This is useful for associating additional information with a trace, such as the environment in which it was executed, or the user who initiated it. For information on how to query traces and runs by metadata and tags, see [Query traces (SDK)](https://docs.langchain.com/langsmith/</langsmith/export-traces>)

When you attach metadata or tags to a runnable (either through the [`RunnableConfig`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain-core/runnables/config/RunnableConfig>) or at runtime with invocation params), they are inherited by all child runnables of that runnable.

Python

TypeScript
[code]
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful AI."),
        ("user", "{input}")
    ])
    
    # The tag "model-tag" and metadata {"model-key": "model-value"} will be attached to the ChatOpenAI run only
    chat_model = ChatOpenAI().with_config({"tags": ["model-tag"], "metadata": {"model-key": "model-value"}})
    output_parser = StrOutputParser()
    
    # Tags and metadata can be configured with RunnableConfig
    chain = (prompt | chat_model | output_parser).with_config({"tags": ["config-tag"], "metadata": {"config-key": "config-value"}})
    
    # Tags and metadata can also be passed at runtime
    chain.invoke({"input": "What is the meaning of life?"}, {"tags": ["invoke-tag"], "metadata": {"invoke-key": "invoke-value"}})
    
[/code]
[code]
    import { ChatOpenAI } from "@langchain/openai";
    import { ChatPromptTemplate } from "@langchain/core/prompts";
    import { StringOutputParser } from "@langchain/core/output_parsers";
    
    const prompt = ChatPromptTemplate.fromMessages([
        ["system", "You are a helpful AI."],
        ["user", "{input}"]
    ])
    
    // The tag "model-tag" and metadata {"model-key": "model-value"} will be attached to the ChatOpenAI run only
    const model = new ChatOpenAI().withConfig({ tags: ["model-tag"], metadata: { "model-key": "model-value" } });
    const outputParser = new StringOutputParser();
    
    // Tags and metadata can be configured with RunnableConfig
    const chain = (prompt.pipe(model).pipe(outputParser)).withConfig({"tags": ["config-tag"], "metadata": {"config-key": "top-level-value"}});
    
    // Tags and metadata can also be passed at runtime
    await chain.invoke({input: "What is the meaning of life?"}, {tags: ["invoke-tag"], metadata: {"invoke-key": "invoke-value"}})
    
[/code]

## 

​

Customize run name

You can customize the name of a given run when invoking or streaming your LangChain code by providing it in the [Config](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain_core/runnables/?h=runnablecon#langchain_core.runnables.RunnableConfig>). This name is used to identify the run in LangSmith and can be used to filter and group runs. The name is also used as the title of the run in the LangSmith UI. This can be done by setting a `run_name` in the [`RunnableConfig`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain-core/runnables/config/RunnableConfig>) object at construction or by passing a `run_name` in the invocation parameters in JS/TS.

Python

TypeScript
[code]
    # When tracing within LangChain, run names default to the class name of the traced object (e.g., 'ChatOpenAI').
    configured_chain = chain.with_config({"run_name": "MyCustomChain"})
    configured_chain.invoke({"input": "What is the meaning of life?"})
    
    # You can also configure the run name at invocation time, like below
    chain.invoke({"input": "What is the meaning of life?"}, {"run_name": "MyCustomChain"})
    
[/code]
[code]
    // When tracing within LangChain, run names default to the class name of the traced object (e.g., 'ChatOpenAI').
    const configuredChain = chain.withConfig({ runName: "MyCustomChain" });
    await configuredChain.invoke({ input: "What is the meaning of life?" });
    
    // You can also configure the run name at invocation time, like below
    await chain.invoke({ input: "What is the meaning of life?" }, {runName: "MyCustomChain"})
    
[/code]

The `run_name` parameter only changes the name of the runnable you invoke (e.g., a chain, function). It does not rename the nested run automatically created when you invoke an LLM object like [`ChatOpenAI`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI>) (`gpt-5.4-mini`). In the example, the enclosing run will appear in LangSmith as `MyCustomChain`, while the nested LLM run still shows the model’s default name.To give the LLM run a more meaningful name, you can either:

  * Wrap the model in another runnable and assign a `run_name` to that step.
  * Use a tracing decorator or helper (e.g., `@traceable` in Python, or `traceable` from `langsmith` in JS/TS) to create a custom run around the model call.

## 

​

Override model name in traces

When tracing LangChain model calls, LangSmith automatically captures the model identifier used in the API call. However, you may want to display a different, more descriptive name in traces for organizational purposes or to distinguish between different model configurations. You can do this by passing the `ls_model_name` [metadata parameter](https://docs.langchain.com/langsmith/</langsmith/ls-metadata-parameters#ls_model_name>) when constructing or configuring your LangChain model. This is particularly useful when:

  * Working with self-hosted or local models where the model ID might not be descriptive.
  * Using the same model with different configurations and wanting to distinguish them in traces.
  * Creating aliases for models to make traces more readable for your team.
  * Standardizing model names across different deployment environments.

Python

TypeScript
[code]
    from langchain_openai import ChatOpenAI
    from langchain_ollama import ChatOllama
    
    # Override model name for a local model
    llm = ChatOllama(
        model="llama2:13b-chat",  # Actual model ID
        metadata={"ls_model_name": "llama2-13b-production"}  # Name shown in LangSmith
    )
    
    # Or with OpenAI to distinguish configurations
    llm_creative = ChatOpenAI(
        model="gpt-5.5",
        temperature=0.9,
        metadata={"ls_model_name": "gpt-5.4-creative"}
    )
    
    llm_factual = ChatOpenAI(
        model="gpt-5.5",
        temperature=0.1,
        metadata={"ls_model_name": "gpt-5.4-factual"}
    )
    
    # The metadata is inherited when the model is used in a chain
    result = llm.invoke("What is the meaning of life?")
    
[/code]
[code]
    import { ChatOpenAI } from "@langchain/openai";
    import { ChatOllama } from "@langchain/ollama";
    
    // Override model name for a local model
    const llm = new ChatOllama({
      model: "llama2:13b-chat",  // Actual model ID
      metadata: { ls_model_name: "llama2-13b-production" }  // Name shown in LangSmith
    });
    
    // Or with OpenAI to distinguish configurations
    const llmCreative = new ChatOpenAI({
      modelName: "gpt-5.5",
      temperature: 0.9,
      metadata: { ls_model_name: "gpt-5.4-creative" }
    });
    
    const llmFactual = new ChatOpenAI({
      modelName: "gpt-5.5",
      temperature: 0.1,
      metadata: { ls_model_name: "gpt-5.4-factual" }
    });
    
    // The metadata is inherited when the model is used in a chain
    const result = await llm.invoke("What is the meaning of life?");
    
[/code]

When you pass `ls_model_name` in the model’s metadata, this name will appear in the LangSmith UI for all traces involving that model instance. This works for any LangChain chat model or LLM and is inherited by all runs that use the model, including when it’s part of a chain.

The `ls_model_name` metadata parameter is also used for [cost tracking](https://docs.langchain.com/langsmith/</langsmith/cost-tracking>). When combined with the `ls_provider` parameter, LangSmith can automatically calculate costs for custom or self-hosted models. For more information about all available metadata parameters, see the [metadata parameters reference](https://docs.langchain.com/langsmith/</langsmith/ls-metadata-parameters>).

## 

​

Customize run ID

You can customize the ID of a given run when invoking or streaming your LangChain code by providing it in the [Config](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain_core/runnables/?h=runnablecon#langchain_core.runnables.RunnableConfig>). This ID is used to uniquely identify the run in LangSmith and can be used to query specific runs. The ID can be useful for linking runs across different systems or for implementing custom tracking logic. This can be done by setting a `run_id` in the [`RunnableConfig`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain-core/runnables/config/RunnableConfig>) object at construction or by passing a `run_id` in the invocation parameters.

This feature is not currently supported directly for LLM objects.

Python

TypeScript
[code]
    import uuid
    
    my_uuid = uuid.uuid4()
    
    # You can configure the run ID at invocation time:
    chain.invoke({"input": "What is the meaning of life?"}, {"run_id": my_uuid})
    
[/code]
[code]
    const myUuid = crypto.randomUUID();
    
    // You can configure the run ID at invocation time, like below
    await chain.invoke({ input: "What is the meaning of life?" }, { runId: myUuid });
    
[/code]

Note that if you do this at the **root** of a trace (i.e., the top-level run, that run ID will be used as the `trace_id`).

## 

​

Access run (span) ID for LangChain invocations

When you invoke a LangChain object, you can manually specify the run ID of the invocation. This run ID can be used to query the run in LangSmith. In JS/TS, you can use a `RunCollectorCallbackHandler` instance to access the run ID.

Python

TypeScript
[code]
    import uuid
    
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant. Please respond to the user's request only based on the given context."),
        ("user", "Question: {question}\n\nContext: {context}")
    ])
    model = ChatOpenAI(model="gpt-5.4-mini")
    output_parser = StrOutputParser()
    
    chain = prompt | model | output_parser
    
    question = "Can you summarize this morning's meetings?"
    context = "During this morning's meeting, we solved all world conflict."
    my_uuid = uuid.uuid4()
    result = chain.invoke({"question": question, "context": context}, {"run_id": my_uuid})
    print(my_uuid)
    
[/code]
[code]
    import { ChatOpenAI } from "@langchain/openai";
    import { ChatPromptTemplate } from "@langchain/core/prompts";
    import { StringOutputParser } from "@langchain/core/output_parsers";
    import { RunCollectorCallbackHandler } from "@langchain/core/tracers/run_collector";
    
    const prompt = ChatPromptTemplate.fromMessages([
      ["system", "You are a helpful assistant. Please respond to the user's request only based on the given context."],
      ["user", "Question: {question}\n\nContext: {context}"],
    ]);
    const model = new ChatOpenAI({ modelName: "gpt-5.4-mini" });
    const outputParser = new StringOutputParser();
    
    const chain = prompt.pipe(model).pipe(outputParser);
    const runCollector = new RunCollectorCallbackHandler();
    
    const question = "Can you summarize this morning's meetings?"
    const context = "During this morning's meeting, we solved all world conflict."
    await chain.invoke(
        { question: question, context: context },
        { callbacks: [runCollector] }
    );
    const runId = runCollector.tracedRuns[0].id;
    console.log(runId);
    
[/code]

## 

​

Ensure all traces are submitted before exiting

In LangChain Python, LangSmith’s tracing is done in a background thread to avoid obstructing your production application. This means that your process may end before all traces are successfully posted to LangSmith. This is especially prevalent in a serverless environment, where your VM may be terminated immediately once your chain or agent completes. You can make callbacks synchronous by setting the `LANGCHAIN_CALLBACKS_BACKGROUND` environment variable to `"false"`. For both languages, LangChain exposes methods to wait for traces to be submitted before exiting your application. Below is an example:

Python

TypeScript
[code]
    from langchain_openai import ChatOpenAI
    from langchain_core.tracers.langchain import wait_for_all_tracers
    
    llm = ChatOpenAI()
    
    try:
      llm.invoke("Hello, World!")
    finally:
      wait_for_all_tracers()
    
[/code]
[code]
    import { awaitAllCallbacks } from "@langchain/core/callbacks/promises";
    
    try {
        const llm = new ChatOpenAI();
        const response = await llm.invoke("Hello, World!");
    } catch (e) {
        // handle error
    } finally {
        await awaitAllCallbacks();
    }
    
[/code]

## 

​

Trace without setting environment variables

As mentioned in other guides, the following environment variables allow you to configure tracing enabled, the api endpoint, the api key, and the tracing project:

  * `LANGSMITH_TRACING`
  * `LANGSMITH_API_KEY`
  * `LANGSMITH_ENDPOINT`
  * `LANGSMITH_PROJECT`

However, in some environments, it is not possible to set environment variables. In these cases, you can set the tracing configuration programmatically. This largely builds off of the previous section.

Python

TypeScript
[code]
    import langsmith as ls
    
    # You can create a client instance with an api key and api url
    client = ls.Client(
        api_key="YOUR_API_KEY",  # This can be retrieved from a secrets manager
        api_url="https://api.smith.langchain.com",  # Self-hosted, GCP EU (`eu.api...`), GCP APAC (`apac.api...`), or AWS US (`aws.api...`) as needed
    )
    
    # You can pass the client and project_name to the tracing_context
    with ls.tracing_context(client=client, project_name="test-no-env", enabled=True):
        chain.invoke({"question": "Am I using a callback?", "context": "I'm using a callback"})
    
[/code]
[code]
    import { LangChainTracer } from "@langchain/core/tracers/tracer_langchain";
    import { Client } from "langsmith";
    
    // You can create a client instance with an api key and api url
    const client = new Client(
        {
            apiKey: "YOUR_API_KEY",
            apiUrl: "https://api.smith.langchain.com", // Self-hosted, GCP EU (`eu.api...`), GCP APAC (`apac.api...`), or AWS US (`aws.api...`) as needed
        }
    );
    
    // You can pass the client and project_name to the LangChainTracer instance
    const tracer = new LangChainTracer({client, projectName: "test-no-env"});
    await chain.invoke(
      {
        question: "Am I using a callback?",
        context: "I'm using a callback",
      },
      { callbacks: [tracer] }
    );
    
[/code]

## 

​

Distributed tracing with LangChain (Python)

LangSmith supports distributed tracing with LangChain Python. This allows you to link runs (spans) across different services and applications. The principles are similar to the [distributed tracing guide](https://docs.langchain.com/langsmith/</langsmith/distributed-tracing>) for the LangSmith SDK.
[code] 
    import langsmith
    from langchain_core.runnables import chain
    from langsmith.run_helpers import get_current_run_tree
    
    # -- This code should be in a separate file or service --
    @chain
    def child_chain(inputs):
        return inputs["test"] + 1
    
    def child_wrapper(x, headers):
        with langsmith.tracing_context(parent=headers):
            child_chain.invoke({"test": x})
    
    # -- This code should be in a separate file or service --
    @chain
    def parent_chain(inputs):
        rt = get_current_run_tree()
        headers = rt.to_headers()
        # ... make a request to another service with the headers
        # The headers should be passed to the other service, eventually to the child_wrapper function
    
    parent_chain.invoke({"test": 1})
    
[/code]

## 

​

Interoperability between LangChain (Python) and LangSmith SDK

If you are using LangChain for part of your application and the LangSmith SDK (see [Custom instrumentation](https://docs.langchain.com/langsmith/</langsmith/annotate-code>)) for other parts, you can still trace the entire application seamlessly. LangChain objects will be traced when invoked within a `traceable` function and be bound as a child run of the `traceable` function.
[code] 
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from langsmith import traceable
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant. Please respond to the user's request only based on the given context."),
        ("user", "Question: {question}\nContext: {context}")
    ])
    
    model = ChatOpenAI(model="gpt-5.4-mini")
    output_parser = StrOutputParser()
    chain = prompt | model | output_parser
    
    # The above chain will be traced as a child run of the traceable function
    @traceable(
        tags=["openai", "chat"],
        metadata={"foo": "bar"}
    )
    def invoke_runnnable(question, context):
        result = chain.invoke({"question": question, "context": context})
        return "The response is: " + result
    
    invoke_runnnable("Can you summarize this morning's meetings?", "During this morning's meeting, we solved all world conflict.")
    
[/code]

This will produce the following trace tree: ![Trace tree python interop](https://mintcdn.com/langchain-5e9cc07a/ImHGLQW1HnQYwnJV/langsmith/images/trace-tree-python-interop.png?fit=max&auto=format&n=ImHGLQW1HnQYwnJV&q=85&s=52c64fd784522c4b2d75886ae76f8c18)

## 

​

Interoperability between LangChain.JS and LangSmith SDK

### 

​

Tracing LangChain objects inside `traceable` (JS only)

Starting with `langchain@0.2.x`, LangChain objects are traced automatically when used inside `@traceable` functions, inheriting the client, tags, metadata and project name of the traceable function. For older versions of LangChain below `0.2.x`, you will need to manually pass an instance `LangChainTracer` created from the tracing context found in `@traceable`.
[code] 
    import { ChatOpenAI } from "@langchain/openai";
    import { ChatPromptTemplate } from "@langchain/core/prompts";
    import { StringOutputParser } from "@langchain/core/output_parsers";
    import { getLangchainCallbacks } from "langsmith/langchain";
    
    const prompt = ChatPromptTemplate.fromMessages([
      [
        "system",
        "You are a helpful assistant. Please respond to the user's request only based on the given context.",
      ],
      ["user", "Question: {question}\nContext: {context}"],
    ]);
    
    const model = new ChatOpenAI({ modelName: "gpt-5.4-mini" });
    const outputParser = new StringOutputParser();
    const chain = prompt.pipe(model).pipe(outputParser);
    
    const main = traceable(
      async (input: { question: string; context: string }) => {
        const callbacks = await getLangchainCallbacks();
        const response = await chain.invoke(input, { callbacks });
        return response;
      },
      { name: "main" }
    );
    
[/code]

### 

​

Tracing LangChain child runs via `traceable` / RunTree API (JS only)

We’re working on improving the interoperability between `traceable` and LangChain. The following limitations are present when using combining LangChain with `traceable`:

  1. Mutating RunTree obtained from `getCurrentRunTree()` of the RunnableLambda context will result in a no-op.
  2. It’s discouraged to traverse the RunTree obtained from RunnableLambda via `getCurrentRunTree()` as it may not contain all the RunTree nodes.
  3. Different child runs may have the same `execution_order` and `child_execution_order` value. Thus in extreme circumstances, some runs may end up in a different order, depending on the `start_time`.

In some uses cases, you might want to run `traceable` functions as part of the RunnableSequence or trace child runs of LangChain run imperatively via the `RunTree` API. Starting with LangSmith 0.1.39 and @langchain/core 0.2.18, you can directly invoke `traceable`-wrapped functions within RunnableLambda.
[code] 
    import { traceable } from "langsmith/traceable";
    import { RunnableLambda } from "@langchain/core/runnables";
    import { RunnableConfig } from "@langchain/core/runnables";
    
    const tracedChild = traceable((input: string) => `Child Run: ${input}`, {
      name: "Child Run",
    });
    
    const parrot = new RunnableLambda({
      func: async (input: { text: string }, config?: RunnableConfig) => {
        return await tracedChild(input.text);
      },
    });
    
[/code]

![Trace Tree](https://mintcdn.com/langchain-5e9cc07a/ImHGLQW1HnQYwnJV/langsmith/images/trace-tree-manual-tracing.png?fit=max&auto=format&n=ImHGLQW1HnQYwnJV&q=85&s=7b117d3aa9b419fe2a314ec6d9cc7c16) Alternatively, you can convert LangChain’s [`RunnableConfig`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain-core/runnables/config/RunnableConfig>) to a equivalent RunTree object by using `RunTree.fromRunnableConfig` or pass the [`RunnableConfig`](https://docs.langchain.com/langsmith/<https:/reference.langchain.com/python/langchain-core/runnables/config/RunnableConfig>) as the first argument of `traceable`-wrapped function.

Traceable

Run Tree
[code]
    import { traceable } from "langsmith/traceable";
    import { RunnableLambda } from "@langchain/core/runnables";
    import { RunnableConfig } from "@langchain/core/runnables";
    
    const tracedChild = traceable((input: string) => `Child Run: ${input}`, {
      name: "Child Run",
    });
    
    const parrot = new RunnableLambda({
      func: async (input: { text: string }, config?: RunnableConfig) => {
        // Pass the config to existing traceable function
        await tracedChild(config, input.text);
        return input.text;
      },
    });
    
[/code]
[code]
    import { RunTree } from "langsmith/run_trees";
    import { RunnableLambda } from "@langchain/core/runnables";
    import { RunnableConfig } from "@langchain/core/runnables";
    
    const parrot = new RunnableLambda({
      func: async (input: { text: string }, config?: RunnableConfig) => {
        // create the RunTree from the RunnableConfig of the RunnableLambda
        const childRunTree = RunTree.fromRunnableConfig(config, {
          name: "Child Run",
        });
    
        childRunTree.inputs = { input: input.text };
        await childRunTree.postRun();
    
        childRunTree.outputs = { output: `Child Run: ${input.text}` };
        await childRunTree.patchRun();
    
        return input.text;
      },
    });
    
[/code]

If you prefer a video tutorial, check out the [Alternative Ways to Trace video](https://docs.langchain.com/langsmith/<https:/academy.langchain.com/pages/intro-to-langsmith-preview>) from the Introduction to LangSmith Course.

* * *

[Connect these docs](https://docs.langchain.com/langsmith/</use-these-docs>) to Claude, VSCode, and more via MCP for real-time answers.

[Edit this page on GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/edit/main/src/langsmith/trace-with-langchain.mdx>) or [file an issue](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/issues/new/choose>).

Was this page helpful?

YesNo

[Trace Google ADK applicationsPrevious](https://docs.langchain.com/langsmith/</langsmith/trace-with-google-adk>)[Trace LangGraph applicationsNext](https://docs.langchain.com/langsmith/</langsmith/trace-with-langgraph>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)

Resources

[Forum](https://docs.langchain.com/langsmith/<https:/forum.langchain.com/>)[Changelog](https://docs.langchain.com/langsmith/<https:/changelog.langchain.com/>)[LangChain Academy](https://docs.langchain.com/langsmith/<https:/academy.langchain.com/>)[Contact Sales](https://docs.langchain.com/langsmith/<https:/www.langchain.com/contact-sales>)

Company

[Home](https://docs.langchain.com/langsmith/<https:/langchain.com/>)[Trust Center](https://docs.langchain.com/langsmith/<https:/trust.langchain.com/>)[Careers](https://docs.langchain.com/langsmith/<https:/langchain.com/careers>)[Blog](https://docs.langchain.com/langsmith/<https:/blog.langchain.com/>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)
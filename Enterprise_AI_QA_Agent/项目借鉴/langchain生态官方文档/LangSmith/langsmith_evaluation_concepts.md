# Evaluation concepts - Docs by LangChain

**Source**: https://docs.langchain.com/langsmith/evaluation-concepts

---

> ## Documentation Index
> 
> Fetch the complete documentation index at: [/llms.txt](https://docs.langchain.com/langsmith/</llms.txt>)
> 
> Use this file to discover all available pages before exploring further.

Skip to main content

Interrupt is coming to NYC and London this fall. Join the builders, engineers, and teams shaping what's next for agents. [Get your tickets →](https://docs.langchain.com/langsmith/<https:/interrupt.langchain.com/>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

Test

Search...

⌘K

  * [Ask AI](https://docs.langchain.com/langsmith/<https:/chat.langchain.com/>)
  * [GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)
  * [Try LangSmith](https://docs.langchain.com/langsmith/<https:/smith.langchain.com/>)
  * [Try LangSmith](https://docs.langchain.com/langsmith/<https:/smith.langchain.com/>)

Search...

Navigation

Evaluation concepts

[Get started](https://docs.langchain.com/langsmith/</langsmith/evaluation>)[Datasets & Experiments](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application>)[Evaluators](https://docs.langchain.com/langsmith/</langsmith/evaluation-types>)[Annotation Queues](https://docs.langchain.com/langsmith/</langsmith/annotation-queues>)[Test from Playground](https://docs.langchain.com/langsmith/</langsmith/test-from-playground>)[Test from Studio](https://docs.langchain.com/langsmith/</langsmith/studio>)

  * [Overview](https://docs.langchain.com/langsmith/</langsmith/evaluation>)

  * [Quickstart](https://docs.langchain.com/langsmith/</langsmith/evaluation-quickstart>)

  * [Concepts](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts>)

  * [Evaluation approaches](https://docs.langchain.com/langsmith/</langsmith/evaluation-approaches>)

## On this page

  * What to evaluate
  * Offline and online evaluations
    * Offline evaluations
    * Online evaluations
  * Evaluation lifecycle
    * 1\. Development with offline evaluation
    * 2\. Initial deployment with online evaluation
    * 3\. Continuous improvement
  * Core evaluation targets
    * Targets for offline evaluation
    * Datasets
    * Examples
    * Experiment
    * Targets for online evaluation
    * Runs
    * Threads
  * Evaluators
    * Attaching an evaluator to a tracing project or dataset
    * Evaluator inputs
    * Evaluator outputs
    * Evaluation techniques
    * Human
    * Code
    * LLM-as-judge
    * Pairwise
    * Reference-free vs reference-based evaluators
  * Evaluation types
  * Best practices
    * Building datasets
    * Dataset organization
    * Human feedback collection
    * Evaluations vs testing
  * Quick reference: Offline vs online evaluation

# Evaluation concepts

Copy pageCopy page

Copy pageCopy page

LLM outputs are non-deterministic, which makes response quality hard to assess. Evaluations (evals) are a way to breakdown what “good” looks like and measure it. LangSmith Evaluation provides a framework for measuring quality throughout the application lifecycle, from pre-deployment testing to production monitoring.

## 

​

What to evaluate

Before building evaluations, identify what matters for your application. Break down your system into its critical components—LLM calls, retrieval steps, tool invocations, output formatting—and determine quality criteria for each. **Start with manually curated examples.** Create 5-10 examples of what “good” looks like for each critical component. These examples serve as your ground truth and inform which evaluation approaches to use. For instance:

  * **RAG system** : Examples of good retrievals (relevant documents) and good answers (accurate, complete).
  * **Agent** : Examples of correct tool selection and proper argument formatting or trajectory that the agent took.
  * **Chatbot** : Examples of helpful, on-brand responses that address user intent.

Once you’ve defined “good” through examples, you can measure how often your system produces similar quality outputs.

## 

​

Offline and online evaluations

LangSmith supports two types of evaluations that serve different purposes in your development workflow:

### 

​

Offline evaluations

Use offline evaluations for **pre-deployment testing** :

  * **Benchmarking** : Compare multiple versions to find the best performer.
  * **Regression testing** : Ensure new versions don’t degrade quality.
  * **Unit testing** : Verify correctness of individual components.
  * **Backtesting** : Test new versions against historical data.

Offline evaluations target _examples_ from _datasets_: curated test cases with reference outputs that define what “good” looks like.

### 

​

Online evaluations

Use online evaluations for **production monitoring** :

  * **Real-time monitoring** : Track quality continuously on live traffic.
  * **Anomaly detection** : Flag unusual patterns or edge cases.
  * **Production feedback** : Identify issues to add to offline datasets.

Online evaluations target _runs_ and _threads_ from [tracing](https://docs.langchain.com/langsmith/</langsmith/observability-quickstart>): real production traces without reference outputs. This difference in targets determines what you can evaluate: offline evaluations can check correctness against expected answers, while online evaluations focus on quality patterns, safety, and real-world behavior.

## 

​

Evaluation lifecycle

As you develop and [deploy your application](https://docs.langchain.com/langsmith/</langsmith/deployment>), your evaluation strategy evolves from pre-deployment testing to production monitoring. During development and testing, offline evaluations validate functionality against curated datasets. After deployment, online evaluations monitor production behavior on live traffic. As applications mature, both evaluation types work together in an iterative feedback loop to improve quality continuously.

### 

​

1\. Development with offline evaluation

Before production deployment, use offline evaluations to validate functionality, benchmark different approaches, and build confidence. Follow the [quickstart](https://docs.langchain.com/langsmith/</langsmith/evaluation-quickstart>) to run your first offline evaluation.

### 

​

2\. Initial deployment with online evaluation

After deployment, use online evaluations to monitor production quality, detect unexpected issues, and collect real-world data. Learn how to [configure online evaluations](https://docs.langchain.com/langsmith/</langsmith/online-evaluations-llm-as-judge>) for production monitoring.

### 

​

3\. Continuous improvement

Use both evaluation types together in an iterative feedback loop. Online evaluations surface issues that become offline test cases, offline evaluations validate fixes, and online evaluations confirm production improvements.

## 

​

Core evaluation targets

Evaluations run on different targets depending on whether they are offline or online.

### 

​

Targets for offline evaluation

Offline evaluations run on datasets and examples. The presence of reference outputs enables comparison between expected and actual results.

#### 

​

Datasets

A dataset is a _collection of examples_ used for evaluating an application. An example is a test input, reference output pair. ![List of datasets on the Examples tab in the LangSmith UI.](https://mintcdn.com/langchain-5e9cc07a/VoZdM8AAAwg3DXcJ/langsmith/images/datasets-light.png?fit=max&auto=format&n=VoZdM8AAAwg3DXcJ&q=85&s=63d27de8050bedc78eb18ccc0d1cad35) ![List of datasets on the Examples tab in the LangSmith UI.](https://mintcdn.com/langchain-5e9cc07a/VoZdM8AAAwg3DXcJ/langsmith/images/datasets-dark.png?fit=max&auto=format&n=VoZdM8AAAwg3DXcJ&q=85&s=e8d5d874df75cdf0471bcbfb1d29e12c)

#### 

​

Examples

Each example consists of:

  * **Inputs** : a dictionary of input variables to pass to your application.
  * **Reference outputs** (optional): a dictionary of reference outputs. These do not get passed to your application, they are only used in evaluators.
  * **Metadata** (optional): a dictionary of additional information that can be used to create filtered views of a dataset.

![Example in the LangSmith UI.](https://mintcdn.com/langchain-5e9cc07a/FQbj5E6QVw8CM9M2/langsmith/images/example-light.png?fit=max&auto=format&n=FQbj5E6QVw8CM9M2&q=85&s=f6d052b31a7f05ad79ed6e7911dfce32) ![Example in the LangSmith UI.](https://mintcdn.com/langchain-5e9cc07a/FQbj5E6QVw8CM9M2/langsmith/images/example-dark.png?fit=max&auto=format&n=FQbj5E6QVw8CM9M2&q=85&s=aed71efcd061f31f2ee308ef56a44606) Learn more about [managing datasets](https://docs.langchain.com/langsmith/</langsmith/manage-datasets>).

#### 

​

Experiment

An _experiment_ represents the results of evaluating a specific application version on a dataset. Each experiment captures outputs, evaluator scores, and execution traces for every example in the dataset. ![Experiment view](https://mintcdn.com/langchain-5e9cc07a/0B2PFrFBMRWNccee/langsmith/images/experiment-view.png?fit=max&auto=format&n=0B2PFrFBMRWNccee&q=85&s=89c78822157136d0e28e9a110dbdbfd5) Multiple experiments typically run on a given dataset to test different application configurations (e.g., different prompts or LLMs). LangSmith displays all experiments associated with a dataset and supports [comparing multiple experiments](https://docs.langchain.com/langsmith/</langsmith/compare-experiment-results>) side-by-side. Learn [how to analyze experiment results](https://docs.langchain.com/langsmith/</langsmith/analyze-an-experiment>).

### 

​

Targets for online evaluation

Online evaluations run on runs and threads from production traffic. Without reference outputs, evaluators focus on detecting issues, anomalies, and quality degradation in real-time.

#### 

​

Runs

A _run_ is a single execution trace from your [deployed application](https://docs.langchain.com/langsmith/</langsmith/deployment>). Each run contains:

  * **Inputs** : The actual user inputs your application received.
  * **Outputs** : What your application actually returned.
  * **Intermediate steps** : All the child runs (tool calls, LLM calls, and so on).
  * **Metadata** : Tags, user feedback, latency metrics, etc.

Unlike examples in datasets, runs do not include reference outputs. Online evaluators must assess quality without knowing what the “correct” answer should be, relying instead on quality heuristics, safety checks, and reference-free evaluation techniques. Learn more about [runs and traces in the Observability concepts](https://docs.langchain.com/langsmith/</langsmith/observability-concepts#runs>).

#### 

​

Threads

_Threads_ are collections of related runs representing multi-turn conversations. Online evaluators can run at the thread level to evaluate entire conversations rather than individual turns. This enables assessment of conversation-level properties like coherence across turns, topic maintenance, and user satisfaction throughout an interaction.

## 

​

Evaluators

_Evaluators_ are workspace-level resources that score application performance. They provide the measurement layer for both offline and online evaluation, adapting their inputs based on what data is available. Because evaluators are scoped to the workspace, you can attach a single evaluator to multiple tracing projects and datasets without recreating it each time. Run evaluators using any of the following:

  * The [Evaluators](https://docs.langchain.com/langsmith/</langsmith/evaluators>) page, to attach them to tracing projects or datasets
  * The [Playground](https://docs.langchain.com/langsmith/</langsmith/prompt-engineering-concepts#playground>)
  * The LangSmith SDK ([Python](https://docs.langchain.com/langsmith/<https:/docs.smith.langchain.com/reference/python/reference>) and [TypeScript](https://docs.langchain.com/langsmith/<https:/docs.smith.langchain.com/reference/js>))
  * [Rules](https://docs.langchain.com/langsmith/</langsmith/rules>), to run them automatically on tracing projects or datasets

### 

​

Attaching an evaluator to a tracing project or dataset

A single evaluator can be attached to many tracing projects and datasets. Configuration like sampling rate, filters, and [spend limits](https://docs.langchain.com/langsmith/</langsmith/evaluator-spend>) is set per attached project or dataset, not per evaluator. View an evaluator’s attached projects and datasets under its **Projects & Datasets** tab.

### 

​

Evaluator inputs

Evaluator inputs differ based on evaluation type: **Offline evaluators** receive:

  * Example: The example from your dataset, containing inputs, reference outputs, and metadata.
  * [Run](https://docs.langchain.com/langsmith/</langsmith/observability-concepts#runs>): The actual outputs and intermediate steps from running the application on the example inputs.

**Online evaluators** receive:

  * [Run](https://docs.langchain.com/langsmith/</langsmith/observability-concepts#runs>): The production trace containing inputs, outputs, and intermediate steps (no reference outputs available).

### 

​

Evaluator outputs

Evaluators return **feedback** , which is the scores from evaluation. Feedback is a dictionary or list of dictionaries. Each dictionary contains:

  * `key`: The metric name.
  * `score` | `value`: The metric value (`score` for numerical metrics, `value` for categorical metrics).
  * `comment` (optional): Additional reasoning or explanation for the score.

### 

​

Evaluation techniques

LangSmith supports several evaluation approaches:

  * Human
  * Code
  * LLM-as-judge
  * Pairwise

#### 

​

Human

_Human evaluation_ involves manual review of application outputs and execution traces. This approach is [often an effective starting point for evaluation](https://docs.langchain.com/langsmith/<https:/hamel.dev/blog/posts/evals/#looking-at-your-traces>). LangSmith provides tools to review application outputs and traces (all intermediate steps). **Annotation queues** [Annotation queues](https://docs.langchain.com/langsmith/</langsmith/annotation-queues>) streamline structured collection of human feedback on runs. They complement [inline annotation](https://docs.langchain.com/langsmith/</langsmith/annotate-traces-inline>) by providing organized workflows with prescribed rubrics, team collaboration features, and progress tracking. LangSmith supports two queue types:

  * **Single-run queues** : Review one run at a time against custom rubric items. Useful for triaging issues or building datasets from production traces. Single-run queues also support [assertions](https://docs.langchain.com/langsmith/</langsmith/assertions>), free-form acceptance criteria that an offline evaluator can grade future runs against.
  * **Pairwise queues** : Compare two runs side-by-side to judge which is better. Designed for fast A/B comparisons between experiments.

Key features include configuring multiple reviewers per run, enabling reservations to prevent conflicts, and exporting annotated runs directly to datasets for future evaluations.

#### 

​

Code

_Code evaluators_ are deterministic, rule-based functions. They work well for checks such as verifying the structure of a chatbot’s response is not empty, that generated code compiles, or that a classification matches exactly.

#### 

​

LLM-as-judge

_LLM-as-judge evaluators_ use LLMs to score application outputs. The grading rules and criteria are typically encoded in the LLM prompt. These evaluators can be:

  * **Reference-free** : Check if output contains offensive content or adheres to specific criteria.
  * **Reference-based** : Compare output to a reference (e.g., check factual accuracy relative to the reference).

LLM-as-judge evaluators require careful review of scores and prompt tuning. Few-shot evaluators, which include examples of inputs, outputs, and expected grades in the grader prompt, often improve performance. Learn about [how to define an LLM-as-a-judge evaluator](https://docs.langchain.com/langsmith/</langsmith/llm-as-judge>).

#### 

​

Pairwise

_Pairwise evaluators_ compare outputs from two application versions using heuristics (e.g., which response is longer), LLMs (with pairwise prompts), or human reviewers. Pairwise evaluation works well when directly scoring an output is difficult but comparing two outputs is straightforward. For example, in summarization tasks, choosing the more informative of two summaries is often easier than assigning an absolute score to a single summary. Learn [how run pairwise evaluations](https://docs.langchain.com/langsmith/</langsmith/evaluate-pairwise>).

### 

​

Reference-free vs reference-based evaluators

Understanding whether an evaluator requires reference outputs is essential for determining when it can be used. **Reference-free evaluators** assess quality without comparing to expected outputs. These work for both offline and online evaluation:

  * **Safety checks** : Toxicity detection, PII detection, content policy violations
  * **Format validation** : JSON structure, required fields, schema compliance
  * **Quality heuristics** : Response length, latency, specific keywords
  * **Reference-free LLM-as-judge** : Clarity, coherence, helpfulness, tone

**Reference-based evaluators** require reference outputs and only work for offline evaluation:

  * **Correctness** : Semantic similarity to reference answer
  * **Factual accuracy** : Fact-checking against ground truth
  * **Exact match** : Classification tasks with known labels
  * **Reference-based LLM-as-judge** : Comparing output quality to a reference

When designing an evaluation strategy, reference-free evaluators provide consistency across both offline testing and online monitoring, while reference-based evaluators enable more precise correctness checks during development.

## 

​

Evaluation types

LangSmith supports various evaluation approaches for different stages of development and deployment. Understanding when to use each type helps build a comprehensive evaluation strategy. Offline and online evaluations serve different purposes:

  * **Offline evaluation types** test pre-deployment on curated datasets with reference outputs
  * **Online evaluation types** monitor production behavior on live traffic without reference outputs

Learn more about [evaluation types and when to use each](https://docs.langchain.com/langsmith/</langsmith/evaluation-types>).

## 

​

Best practices

### 

​

Building datasets

There are various strategies for building datasets: **Manually curated examples** This is the recommended starting point. Create 10–20 high-quality examples covering common scenarios and edge cases. These examples define what “good” looks like for your application. **Historical traces** Once in production, convert real traces into examples. For high-traffic applications:

  * **User feedback** : Add runs that received negative feedback to test against.
  * **Heuristics** : Identify interesting runs (e.g., long latency, errors).
  * **LLM feedback** : Use LLMs to detect noteworthy conversations.

**Synthetic data** Generate additional examples from existing ones. Works best when starting with several high-quality, hand-crafted examples as templates.

### 

​

Dataset organization

**Splits** Splits are named subsets of a dataset used to segment examples into separate groups. Common patterns include:

  * **ML-style splits** : divide examples into training, validation, and test sets to avoid overfitting, where a model performs well on training data but poorly on unseen data.
  * **Category-based splits** : evaluate different input types separately when a dataset spans multiple task categories.
  * **Staged rollout** : keep exploratory examples isolated until you’re ready to include them in the main evaluation set.

Splits differ from metadata: use splits for high-level organizational grouping for evaluation, and metadata for per-example information such as tags and provenance. In machine learning, best practice is for each example to belong to exactly one split. LangSmith allows examples to belong to multiple splits, which is useful when an example fits several evaluation categories. Learn how to [create and manage dataset splits](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application#create-and-manage-dataset-splits>). **Versions** LangSmith automatically creates dataset [versions](https://docs.langchain.com/langsmith/</langsmith/manage-datasets#version-a-dataset>) when examples change. [Tag versions](https://docs.langchain.com/langsmith/</langsmith/manage-datasets#tag-a-version>) to mark important milestones. Target specific versions in CI pipelines to ensure dataset updates don’t break workflows.

### 

​

Human feedback collection

Human feedback often provides the most valuable assessment, particularly for subjective quality dimensions. **Annotation queues** [Annotation queues](https://docs.langchain.com/langsmith/</langsmith/annotation-queues>) enable structured collection of human feedback. Flag specific runs for review, collect annotations in a streamlined interface, and transfer annotated runs to datasets for future evaluations. Annotation queues complement [inline annotation](https://docs.langchain.com/langsmith/</langsmith/annotate-traces-inline>) by offering additional capabilities: grouping runs, specifying criteria, and configuring reviewer permissions.

### 

​

Evaluations vs testing

Testing and evaluation are similar but distinct concepts. **Evaluation measures performance according to metrics.** Metrics can be fuzzy or subjective, and prove more useful in relative terms. They typically compare systems against each other. **Testing asserts correctness.** A system can only be deployed if it passes all tests. Evaluation metrics can be converted into tests. For example, regression tests can assert that new versions must outperform baseline versions on relevant metrics. Run tests and evaluations together for efficiency when systems are expensive to run. Evaluations can be written using standard testing tools like [pytest](https://docs.langchain.com/langsmith/</langsmith/pytest>) or [Vitest/Jest](https://docs.langchain.com/langsmith/</langsmith/vitest-jest>).

## 

​

Quick reference: Offline vs online evaluation

The following table summarizes the key differences between offline and online evaluations:

| **Offline Evaluation**| **Online Evaluation**  
---|---|---  
**Runs on**|  Dataset (Examples)| Tracing Project (Runs/Threads)  
**Data access**|  Inputs, Outputs, Reference Outputs| Inputs, Outputs only  
**When to use**|  Pre-deployment, during development| Production, post-deployment  
**Primary use cases**|  Benchmarking, unit testing, regression testing, backtesting| Real-time monitoring, production feedback, anomaly detection  
**Evaluation timing**|  Batch processing on curated test sets| Real-time or near real-time on live traffic  
**Setup location**|  Evaluation tab (SDK, UI, Playground)| [Observability tab](https://docs.langchain.com/langsmith/</langsmith/online-evaluations-llm-as-judge>) (automated rules)  
**Data requirements**|  Requires dataset curation| No dataset needed, evaluates live traces  
  
* * *

[Connect these docs](https://docs.langchain.com/langsmith/</use-these-docs>) to Claude, VSCode, and more via MCP for real-time answers.

[Edit this page on GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/edit/main/src/langsmith/evaluation-concepts.mdx>) or [file an issue](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/issues/new/choose>).

Was this page helpful?

YesNo

[Evaluation quickstartPrevious](https://docs.langchain.com/langsmith/</langsmith/evaluation-quickstart>)[Application-specific evaluation approachesNext](https://docs.langchain.com/langsmith/</langsmith/evaluation-approaches>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)

Resources

[Forum](https://docs.langchain.com/langsmith/<https:/forum.langchain.com/>)[Changelog](https://docs.langchain.com/langsmith/<https:/changelog.langchain.com/>)[LangChain Academy](https://docs.langchain.com/langsmith/<https:/academy.langchain.com/>)[Contact Sales](https://docs.langchain.com/langsmith/<https:/www.langchain.com/contact-sales>)

Company

[Home](https://docs.langchain.com/langsmith/<https:/langchain.com/>)[Trust Center](https://docs.langchain.com/langsmith/<https:/trust.langchain.com/>)[Careers](https://docs.langchain.com/langsmith/<https:/langchain.com/careers>)[Blog](https://docs.langchain.com/langsmith/<https:/blog.langchain.com/>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)
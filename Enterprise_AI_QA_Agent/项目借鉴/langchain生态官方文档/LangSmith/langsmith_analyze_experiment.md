# Analyze an experiment - Docs by LangChain

**Source**: https://docs.langchain.com/langsmith/analyze-an-experiment

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

Analyze experiment results

Analyze an experiment

[Get started](https://docs.langchain.com/langsmith/</langsmith/evaluation>)[Datasets & Experiments](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application>)[Evaluators](https://docs.langchain.com/langsmith/</langsmith/evaluation-types>)[Annotation Queues](https://docs.langchain.com/langsmith/</langsmith/annotation-queues>)[Test from Playground](https://docs.langchain.com/langsmith/</langsmith/test-from-playground>)[Test from Studio](https://docs.langchain.com/langsmith/</langsmith/studio>)

### Datasets

  * Create a dataset

  * [Manage datasets](https://docs.langchain.com/langsmith/</langsmith/manage-datasets>)
  * [Custom output rendering](https://docs.langchain.com/langsmith/</langsmith/custom-output-rendering>)

### Run an evaluation

  * [With the SDK](https://docs.langchain.com/langsmith/</langsmith/evaluate-llm-application>)
  * [With OpenTelemetry](https://docs.langchain.com/langsmith/</langsmith/evaluate-with-opentelemetry>)
  * [With the UI](https://docs.langchain.com/langsmith/</langsmith/run-evaluation-from-playground>)
  * [With the API](https://docs.langchain.com/langsmith/</langsmith/run-evals-api-only>)

### Evaluation techniques

  * Define evaluation target

  * Scoring methods

  * Experiment configuration

  * Multimodal evaluations

### Analyze experiment results

  * [Analyze an experiment](https://docs.langchain.com/langsmith/</langsmith/analyze-an-experiment>)
  * [Analyze with Chat](https://docs.langchain.com/langsmith/</langsmith/chat#evaluation>)
  * [Compare experiment results](https://docs.langchain.com/langsmith/</langsmith/compare-experiment-results>)
  * [Filter experiments in the UI](https://docs.langchain.com/langsmith/</langsmith/filter-experiments-ui>)
  * [Fetch performance metrics for an experiment](https://docs.langchain.com/langsmith/</langsmith/fetch-perf-metrics-experiment>)
  * [Upload experiments run outside of LangSmith](https://docs.langchain.com/langsmith/</langsmith/upload-existing-experiments>)

### Tutorials

  * [Evaluate a chatbot](https://docs.langchain.com/langsmith/</langsmith/evaluate-chatbot-tutorial>)
  * [Evaluate a RAG application](https://docs.langchain.com/langsmith/</langsmith/evaluate-rag-tutorial>)
  * [Test a ReAct agent with Pytest/Vitest and LangSmith](https://docs.langchain.com/langsmith/</langsmith/test-react-agent-pytest>)
  * [Evaluate a complex agent](https://docs.langchain.com/langsmith/</langsmith/evaluate-complex-agent>)
  * [Run backtests on a new version of an agent](https://docs.langchain.com/langsmith/</langsmith/run-backtests-new-agent>)

### Common data types

  * [Example data format](https://docs.langchain.com/langsmith/</langsmith/example-data-format>)
  * [Dataset prebuilt JSON schema types](https://docs.langchain.com/langsmith/</langsmith/dataset-json-types>)
  * [Dataset transformations](https://docs.langchain.com/langsmith/</langsmith/dataset-transformations>)

## On this page

  * Analyze a single experiment
    * Open the experiment view
    * View experiment results
    * Customize columns
    * Sort and filter
    * Table views
    * View the traces
    * View evaluator runs
    * Track experiment progress
    * Group results by metadata
    * Repetitions
    * Compare to another experiment
  * Set a baseline in the Experiments tab view
  * Filter and group by models, prompts, and tools in the Experiments tab view
  * Download experiment results as a CSV
  * Rename an experiment

[Analyze experiment results](https://docs.langchain.com/langsmith/</langsmith/analyze-an-experiment>)

# Analyze an experiment

Copy pageCopy page

Copy pageCopy page

This page describes some of the essential tasks for working with [_experiments_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#experiment>) in LangSmith:

  * **Analyze a single experiment** : View and interpret experiment results, customize columns, filter data, and compare runs.
  * **Set a baseline in the Experiments tab view** : Set a baseline for a dataset that you want to outperform.
  * **Filter and group by models, prompts, and tools in the Experiments tab view** : Use **Models** , **Prompts** , and **Tools** columns to filter and group experiments in the **Experiments** tab view.
  * **Download experiment results as a CSV** : Export your experiment data for external analysis and sharing.
  * **Rename an experiment** : Update experiment names in both the Playground and experiment view.

## 

​

Analyze a single experiment

After running an experiment, you can use LangSmith’s experiment view to analyze the results and draw insights about your experiment’s performance.

### 

​

Open the experiment view

To open the experiment view,

  1. Select the relevant [_dataset_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#datasets>) from the **Dataset & Experiments** page which opens the **Experiments** tab view.
  2. Click the row of the experiment you want to view.

![Open experiment view](https://mintcdn.com/langchain-5e9cc07a/Fr2lazPB4XVeEA7l/langsmith/images/select-experiment.png?fit=max&auto=format&n=Fr2lazPB4XVeEA7l&q=85&s=74207f0a2422f89fdc75b23f0a88c58f)

### 

​

View experiment results

#### 

​

Customize columns

By default, the experiment view shows the input, output, and reference output for each [example](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#examples>) in the dataset, feedback scores from evaluations and experiment metrics like cost, token counts, latency and status. You can customize the columns clicking the **Columns** icon at the top right of the view to make it easier to interpret experiment results:

  * **Break out fields from inputs, outputs, and reference outputs** into their own columns. This is especially helpful if you have long inputs/outputs/reference outputs and want to surface important fields.
  * **Hide and reorder columns** to create focused views for analysis.
  * **Control decimal precision on feedback scores**. By default, LangSmith surfaces numerical feedback scores with a decimal precision of 2, but you can customize this setting to be up to 6 decimals.
  * **Set the Heat Map threshold** to high, middle, and low for numeric feedback scores in your experiment, which affects the threshold at which score chips render as red or green:

![Column heatmap configuration](https://mintcdn.com/langchain-5e9cc07a/aKRoUGXX6ygp4DlC/langsmith/images/column-heat-map.png?fit=max&auto=format&n=aKRoUGXX6ygp4DlC&q=85&s=b0203a449f0f7df70900735ba540d712)

You can set default configurations for an entire dataset or temporarily save settings just for yourself.

#### 

​

Sort and filter

To sort rows by a feedback score, click the **Sort by** icon in the column header. ![Sort column](https://mintcdn.com/langchain-5e9cc07a/Tdk8epB4BZgbugRX/langsmith/images/column-sort.png?fit=max&auto=format&n=Tdk8epB4BZgbugRX&q=85&s=19a77a5df00a579f3bd953fd75c4ade9) To filter rows, click the  icon in the column header and configure your filter settings. ![Filter column](https://mintcdn.com/langchain-5e9cc07a/Tdk8epB4BZgbugRX/langsmith/images/column-filter.png?fit=max&auto=format&n=Tdk8epB4BZgbugRX&q=85&s=d76a7c51f386c511679ad84ad6ca4d86)

#### 

​

Table views

Select one of three table view icons at the top right of the experiment view:

  * **Compact** : Shows each run as a single row for quick score comparisons.
  * **Full** : Shows the full output for each run.
  * **Diff** : Shows the text difference between the reference output and the output for each run.

![Diff view](https://mintcdn.com/langchain-5e9cc07a/Tdk8epB4BZgbugRX/langsmith/images/diff-mode.png?fit=max&auto=format&n=Tdk8epB4BZgbugRX&q=85&s=800ecac6a9fc6d8ef426a41999c65a44)

#### 

​

View the traces

Click any row in the experiment view to open the details panel, which shows the trace alongside feedback, input, output, and attributes for that run. ![View trace](https://mintcdn.com/langchain-5e9cc07a/Tdk8epB4BZgbugRX/langsmith/images/view-trace.png?fit=max&auto=format&n=Tdk8epB4BZgbugRX&q=85&s=f39235b29c4fca4bcb3cf4d4788133ca) To view the entire tracing project, click on the **View Project** icon at the top right of the experiment view.

#### 

​

View evaluator runs

By hovering over the evaluator score, you can view additional details about that evaluator run. For [LLM-as-a-judge evaluators](https://docs.langchain.com/langsmith/</langsmith/llm-as-judge>), click the **Source** link to view the prompt used, or **Evaluator trace** to open the trace in a new browser tab. For experiments with [repetitions](https://docs.langchain.com/langsmith/</langsmith/repetition>), click the aggregate average score to view links to all individual runs. ![View evaluator runs](https://mintcdn.com/langchain-5e9cc07a/Tdk8epB4BZgbugRX/langsmith/images/evaluator-run.png?fit=max&auto=format&n=Tdk8epB4BZgbugRX&q=85&s=372dc06ba8bdb552b6afbdce0de1dee7)

#### 

​

Track experiment progress

For experiments run from the Playground or through the SDK, a progress bar in the experiment header tracks completion in real time. The same progress appears in the **Progress** column of the experiments table. Progress reflects both run and evaluation status. Hover over the progress bar to view the number of runs completed and runs evaluated.

Progress tracking for experiments run through the SDK requires:

  * Python: `langsmith>=0.8.16`
  * TypeScript: `langsmith>=0.7.8`

### 

​

Group results by metadata

You can add metadata to examples to categorize and organize them. For example, if you’re evaluating factual accuracy on a question answering dataset, the metadata might include which subject area each question belongs to. Metadata can be added either [via the UI](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application#edit-example-metadata>) or [via the SDK](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically#update-single-example>). To analyze results by metadata, use the **Group by** icon at the top right of the experiment view and select your desired metadata key. This displays average feedback scores, latency, total tokens, and cost for each metadata group.

You will only be able to group by example metadata on experiments created after February 20th, 2025. Any experiments before that date can still be grouped by metadata, but only if the metadata is on the experiment traces themselves.

### 

​

Repetitions

If you’ve run your experiment with [_repetitions_](https://docs.langchain.com/langsmith/</langsmith/repetition>), click any row to open the details panel. The **Repetition Summary** shows a metrics table, all feedback scores, and lets you toggle through outputs or view individual repetitions with their traces. ![Repetitions](https://mintcdn.com/langchain-5e9cc07a/Tdk8epB4BZgbugRX/langsmith/images/repetitions.png?fit=max&auto=format&n=Tdk8epB4BZgbugRX&q=85&s=15a7c9c1a20042fbcdf1d5f7adcc25de)

### 

​

Compare to another experiment

In the top right of the experiment view, you can select another experiment to compare to. This will open up a comparison view, where you can see how the two experiments compare. To learn more about the comparison view, see [how to compare experiment results](https://docs.langchain.com/langsmith/</langsmith/compare-experiment-results>).

## 

​

Set a baseline in the Experiments tab view

While you may run dozens of tests, you typically have a specific benchmark you are trying to outperform. Setting a _baseline_ anchors your results against this reference point, which allows you to identify improvements or regressions in a crowded experiment list. By designating a baseline, you can:

  * Highlight a reference: Explicitly mark your best-performing run so it remains visible at the top of the **Experiments** tab view as you iterate.
  * See instant diffs: View performance deltas across all experiments automatically, which means you don’t necessarily need to perform manual side-by-side selection.
  * Accelerate assessment: Quickly determine if new iterations meet or exceed your current performance standards.

![The Experiments tab view with an experiment marked as the baseline at the top of the table. Scores show against the baseline on the rows of other experiments.](https://mintcdn.com/langchain-5e9cc07a/IYnogEjgWaxIU1jM/langsmith/images/baseline-experiment-view-light.png?fit=max&auto=format&n=IYnogEjgWaxIU1jM&q=85&s=3d192616f7373ce0f7c5996a6c65cc5e) ![The Experiments tab view with an experiment marked as the baseline at the top of the table. Scores show against the baseline on the rows of other experiments.](https://mintcdn.com/langchain-5e9cc07a/PSSTIduMbnDkYvaS/langsmith/images/baseline-experiment-view-dark.png?fit=max&auto=format&n=PSSTIduMbnDkYvaS&q=85&s=fba425d88cce62f2948cf3e1e47582cc) To set a baseline for a dataset:

  1. In the [LangSmith UI](https://docs.langchain.com/langsmith/<https:/smith.langchain.com?utm_source=docs&utm_medium=cta&utm_campaign=langsmith-signup&utm_content=langsmith-analyze-an-experiment>), navigate to the **Datasets & Experiments** option in the left menu.
  2. Select the dataset that you want to work with from the table.
  3. In the **Experiments** tab view, hover over an experiment row to display the **Set baseline** button on the right end of the row. Click to select your baseline experiment.

Your baseline experiment will pin to the top of the table and have the **Baseline** tag next to its name. Once an experiment is set as a baseline, the table will display scores against the baseline on each experiment for each column. When you are selecting multiple experiments for comparison, the baseline experiment will be the default source experiment to be compared to.

## 

​

Filter and group by models, prompts, and tools in the Experiments tab view

The experiments table includes **Models** , **Prompts** , and **Tools** columns that show which models, prompts, and tools were used for each experiment, making it easier to understand what changed between runs at a glance. These columns are populated automatically when you run experiments from the Playground. When running experiments via the SDK, pass a `metadata` object with `models`, `prompts`, and `tools` keys to `evaluate()`:
[code] 
    results = client.evaluate(
        target,
        data="my-dataset",
        evaluators=[...],
        metadata={
            "models": "openai:gpt-5.4-mini",
            "prompts": ["my-org/my-prompt:abc12345"],
            "tools": [{"name": "web_search", "description": "Search the web for information"}],
        },
    )
    
[/code]

See [how to evaluate an LLM application](https://docs.langchain.com/langsmith/</langsmith/evaluate-llm-application#run-the-evaluation>) for an example using metadata. The columns only appear when at least one experiment in the dataset has the field set. Once populated, click on a value in these columns to filter or group experiments. ![The Experiments tab view with metadata columns for models, prompts, and tools.](https://mintcdn.com/langchain-5e9cc07a/4y4TUahoyWs6oiHd/langsmith/images/metadata-columns-light.png?fit=max&auto=format&n=4y4TUahoyWs6oiHd&q=85&s=c08271466c5a6b923c1132b6edbb21c8) ![The Experiments tab view with metadata columns for models, prompts, and tools.](https://mintcdn.com/langchain-5e9cc07a/4y4TUahoyWs6oiHd/langsmith/images/metadata-columns-dark.png?fit=max&auto=format&n=4y4TUahoyWs6oiHd&q=85&s=7706671f545f24691e7cd8bdf419f7e0) You can also filter and group by models, model providers, prompts, prompt commits, tools, and other experiment metadata at the top left of the **Experiments** tab view: ![The Experiments tab view with metadata columns for models, prompts, and tools.](https://mintcdn.com/langchain-5e9cc07a/4y4TUahoyWs6oiHd/langsmith/images/metadata-group-by-light.png?fit=max&auto=format&n=4y4TUahoyWs6oiHd&q=85&s=4240516c40a0673a458786727df3f68d) ![The Experiments tab view with metadata columns for models, prompts, and tools.](https://mintcdn.com/langchain-5e9cc07a/4y4TUahoyWs6oiHd/langsmith/images/metadata-group-by-dark.png?fit=max&auto=format&n=4y4TUahoyWs6oiHd&q=85&s=2d32c89b32f083c50cae746888fdbc83)

## 

​

Download experiment results as a CSV

LangSmith lets you download experiment results as a CSV file for external analysis and sharing. Click the **Download as CSV** icon at the top right of the experiment view.

The CSV export always includes all columns, regardless of any column customization, sorting, or filtering you have applied in the experiment view. Column visibility settings affect only the on-screen display and are not reflected in the downloaded file.

There is a 5,000 row download limit for experiment results.

## 

​

Rename an experiment

Experiment names must be unique per workspace.

You can rename an experiment in the LangSmith UI in the following places:

  * **Experiment view** : Rename an experiment by using the pencil icon beside the experiment name. ![Edit name in experiment view](https://mintcdn.com/langchain-5e9cc07a/4y4TUahoyWs6oiHd/langsmith/images/rename-in-experiment-view.png?fit=max&auto=format&n=4y4TUahoyWs6oiHd&q=85&s=9361c74c51c2e109f7bfd877959eb015)
  * **Playground** : A default name with the format `pg::prompt-name::model::uuid` (eg. `pg::gpt-5.4-mini::897ee630`) is automatically assigned. You can rename an experiment immediately after running it by editing its name in the Playground table header. ![Edit name in playground](https://mintcdn.com/langchain-5e9cc07a/Fr2lazPB4XVeEA7l/langsmith/images/rename-in-playground.png?fit=max&auto=format&n=Fr2lazPB4XVeEA7l&q=85&s=5b647ff1894376bbb727dabc4d73f039)

* * *

[Connect these docs](https://docs.langchain.com/langsmith/</use-these-docs>) to Claude, VSCode, and more via MCP for real-time answers.

[Edit this page on GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/edit/main/src/langsmith/analyze-an-experiment.mdx>) or [file an issue](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/issues/new/choose>).

Was this page helpful?

YesNo

[Run an evaluation with multimodal contentPrevious](https://docs.langchain.com/langsmith/</langsmith/evaluate-with-attachments>)[LangSmith ChatNext](https://docs.langchain.com/langsmith/</langsmith/chat-evaluation>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)

Resources

[Forum](https://docs.langchain.com/langsmith/<https:/forum.langchain.com/>)[Changelog](https://docs.langchain.com/langsmith/<https:/changelog.langchain.com/>)[LangChain Academy](https://docs.langchain.com/langsmith/<https:/academy.langchain.com/>)[Contact Sales](https://docs.langchain.com/langsmith/<https:/www.langchain.com/contact-sales>)

Company

[Home](https://docs.langchain.com/langsmith/<https:/langchain.com/>)[Trust Center](https://docs.langchain.com/langsmith/<https:/trust.langchain.com/>)[Careers](https://docs.langchain.com/langsmith/<https:/langchain.com/careers>)[Blog](https://docs.langchain.com/langsmith/<https:/blog.langchain.com/>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)
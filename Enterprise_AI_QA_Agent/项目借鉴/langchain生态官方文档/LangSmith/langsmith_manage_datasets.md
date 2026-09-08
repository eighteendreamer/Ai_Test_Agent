# Manage datasets - Docs by LangChain

**Source**: https://docs.langchain.com/langsmith/manage-datasets

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

Datasets

Manage datasets

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

  * Version a dataset
    * Create a new version of a dataset
    * Tag a version
  * Evaluate on a specific dataset version
    * Use list_examples
  * Evaluate on a split / filtered view of a dataset
    * Evaluate on a filtered view of a dataset
    * Evaluate on a dataset split
  * Share a dataset
    * Share a dataset publicly
    * Unshare a dataset
  * Export a dataset
  * Export filtered traces from experiment to dataset
    * View experiment traces

[Datasets](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application>)

# Manage datasets

Copy pageCopy page

Copy pageCopy page

LangSmith provides tools for managing and working with your [_datasets_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#datasets>). This page describes dataset operations including:

  * Versioning datasets to track changes over time.
  * Filtering and splitting datasets for evaluation.
  * Sharing datasets publicly.
  * Exporting datasets in various formats.

You’ll also learn how to export filtered traces from [experiments](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#experiment>) back to datasets for further analysis and iteration.

The [LangSmith Engine](https://docs.langchain.com/langsmith/</langsmith/engine>) can automatically generate ground truth dataset examples from your production traces.

## 

​

Version a dataset

In LangSmith, datasets are versioned. This means that every time you add, update, or delete examples in your dataset, a new version of the dataset is created.

### 

​

Create a new version of a dataset

Any time you add, update, or delete examples in your dataset, a new [version](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#dataset-organization>) of your dataset is created. This allows you to track changes to your dataset over time and understand how your dataset has evolved. By default, the version is defined by the timestamp of the change. When you click on a particular version of a dataset (by timestamp) in the **Examples** tab, you will find the state of the dataset at that point in time. ![Version Datasets](https://mintcdn.com/langchain-5e9cc07a/1RIJxfRpkszanJLL/langsmith/images/version-dataset.png?fit=max&auto=format&n=1RIJxfRpkszanJLL&q=85&s=da312a60576f449797be71e24229ea31) Note that examples are read-only when viewing a past version of the dataset. You will also see the operations that were between this version of the dataset and the latest version of the dataset.

By default, the latest version of the dataset is shown in the **Examples** tab and experiments from all versions are shown in the **Tests** tab.

In the **Tests** tab, you will find the results of tests run on the dataset at different versions. ![Version Datasets](https://mintcdn.com/langchain-5e9cc07a/1RIJxfRpkszanJLL/langsmith/images/version-dataset-tests.png?fit=max&auto=format&n=1RIJxfRpkszanJLL&q=85&s=42c5ac800ef7282fa65013f6de02e45a)

### 

​

Tag a version

You can also tag versions of your dataset to give them a more human-readable name, which can be useful for marking important milestones in your dataset’s history. For example, you might tag a version of your dataset as “prod” and use it to run tests against your LLM pipeline. You can tag a version of your dataset in the UI by clicking on **\+ Tag this version** in the **Examples** tab. ![Tagging Datasets](https://mintcdn.com/langchain-5e9cc07a/ImHGLQW1HnQYwnJV/langsmith/images/tag-this-version.png?fit=max&auto=format&n=ImHGLQW1HnQYwnJV&q=85&s=c3f7a97c92eb645f7b0888f4e35ffd48) You can also tag versions of your dataset using the SDK. Here’s an example of how to tag a version of a dataset using the [Python SDK](https://docs.langchain.com/langsmith/<https:/docs.smith.langchain.com/reference/python/reference>):
[code] 
    from langsmith import Client
    from datetime import datetime
    
    client = Client()
    initial_time = datetime(2024, 1, 1, 0, 0, 0) # The timestamp of the version you want to tag
    
    # You can tag a specific dataset version with a semantic name, like "prod"
    client.update_dataset_tag(
        dataset_name=toxic_dataset_name, as_of=initial_time, tag="prod"
    )
    
[/code]

To run an evaluation on a particular tagged version of a dataset, refer to the Evaluate on a specific dataset version section.

## 

​

Evaluate on a specific dataset version

You may find it helpful to refer to the following content before you read this section:

  * Version a dataset.
  * [Fetching examples](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically#fetch-examples>).

### 

​

Use `list_examples`

You can use `evaluate` / `aevaluate` to pass in an iterable of examples to evaluate on a particular version of a dataset. Use `list_examples` / `listExamples` to fetch examples from a particular version tag using `as_of` / `asOf` and pass that into the `data` argument.

Python

TypeScript

Java
[code]
    from langsmith import Client
    
    ls_client = Client()
    
    # Assumes actual outputs have a 'class' key.
    # Assumes example outputs have a 'label' key.
    def correct(outputs: dict, reference_outputs: dict) -> bool:
      return outputs["class"] == reference_outputs["label"]
    
    results = ls_client.evaluate(
        lambda inputs: {"class": "Not toxic"},
        # Pass in filtered data here:
        data=ls_client.list_examples(
          dataset_name="Toxic Queries",
          as_of="latest",  # specify version here
        ),
        evaluators=[correct],
    )
    
[/code]
[code]
    import { evaluate } from "langsmith/evaluation";
    
    await evaluate((inputs) => labelText(inputs["input"]), {
      data: langsmith.listExamples({
        datasetName: datasetName,
        asOf: "latest",
      }),
      evaluators: [correctLabel],
    });
    
[/code]
[code]
    import com.langchain.smith.models.examples.ExampleListParams;
    
    ExampleListParams listParams = ExampleListParams.builder()
        .datasetId(datasetId)
        .asOf("latest")
    var examples = client.examples().list(listParams);
    
[/code]

Learn more about how to fetch views of a dataset on the [Create and manage datasets programmatically](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically#fetch-datasets>) page.

## 

​

Evaluate on a split / filtered view of a dataset

You may find it helpful to refer to the following content before you read this section:

  * [Fetching examples](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically#fetch-examples>).
  * [Creating and managing dataset splits](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application#create-and-manage-dataset-splits>).

### 

​

Evaluate on a filtered view of a dataset

You can use the `list_examples` / `listExamples` method to [fetch](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically#fetch-examples>) a subset of examples from a dataset to evaluate on. One common workflow is to fetch examples that have a certain metadata key-value pair.

Python

TypeScript

Java
[code]
    from langsmith import evaluate
    
    results = evaluate(
        lambda inputs: label_text(inputs["text"]),
        data=client.list_examples(dataset_name=dataset_name, metadata={"desired_key": "desired_value"}),
        evaluators=[correct_label],
        experiment_prefix="Toxic Queries",
    )
    
[/code]
[code]
    import { evaluate } from "langsmith/evaluation";
    
    await evaluate((inputs) => labelText(inputs["input"]), {
      data: langsmith.listExamples({
        datasetName: datasetName,
        metadata: {"desired_key": "desired_value"},
      }),
      evaluators: [correctLabel],
      experimentPrefix: "Toxic Queries",
    });
    
[/code]
[code]
    import com.langchain.smith.models.examples.ExampleListParams;
    
    ExampleListParams listParams = ExampleListParams.builder()
        .datasetId(datasetId)
        .metadata("{\"desired_key\":\"desired_value\"}")
        .build();
    var examples = client.examples().list(listParams);
    
[/code]

For more filtering capabilities, refer to this [how-to guide](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically#list-examples-by-structured-filter>).

### 

​

Evaluate on a dataset split

You can use the `list_examples` / `listExamples` method to evaluate on one or multiple [splits](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#dataset-organization>) of your dataset. The `splits` parameter takes a list of the splits you would like to evaluate.

Python

TypeScript

Java
[code]
    from langsmith import evaluate
    
    results = evaluate(
        lambda inputs: label_text(inputs["text"]),
        data=client.list_examples(dataset_name=dataset_name, splits=["test", "training"]),
        evaluators=[correct_label],
        experiment_prefix="Toxic Queries",
    )
    
[/code]
[code]
    import { evaluate } from "langsmith/evaluation";
    
    await evaluate((inputs) => labelText(inputs["input"]), {
      data: langsmith.listExamples({
        datasetName: datasetName,
        splits: ["test", "training"],
      }),
      evaluators: [correctLabel],
      experimentPrefix: "Toxic Queries",
    });
    
[/code]
[code]
    import com.langchain.smith.models.examples.ExampleListParams;
    import java.util.Arrays;
    import java.util.List;
    
    List<String> splits = Arrays.asList("test", "training");
    
    ExampleListParams listParams = ExampleListParams.builder()
        .datasetId(datasetId)
        .splits(splits)
        .build();
    var examples = client.examples().list(listParams);
    
[/code]

For more details on fetching views of a dataset, refer to the guide on [fetching datasets](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically#fetch-datasets>).

## 

​

Share a dataset

### 

​

Share a dataset publicly

Sharing a dataset publicly will make the **dataset examples, experiments and associated runs, and feedback on this dataset accessible to anyone with the link** , even if they don’t have a LangSmith account. Make sure you’re not sharing sensitive information.This feature is only available in the cloud-hosted version of LangSmith.

From the **Dataset & Experiments** tab, select a dataset, click **⋮** (top right of the page), click **Share Dataset**. This will open a dialog where you can copy the link to the dataset. ![Share Dataset](https://mintcdn.com/langchain-5e9cc07a/ImHGLQW1HnQYwnJV/langsmith/images/share-dataset.gif?s=3788767dadf1c265968fe61d96bacc2d)

### 

​

Unshare a dataset

  1. Click on **Unshare** by clicking on **Public** in the upper right hand corner of any publicly shared dataset, then **Unshare** in the dialog. ![Unshare Dataset](https://mintcdn.com/langchain-5e9cc07a/1RIJxfRpkszanJLL/langsmith/images/unshare-dataset.png?fit=max&auto=format&n=1RIJxfRpkszanJLL&q=85&s=98e1807ba9f9a510f56a435b7f81287c)
  2. Navigate to your organization’s list of publicly shared datasets, by clicking on **Settings** -> **Shared URLs** or [this link](https://docs.langchain.com/langsmith/<https:/smith.langchain.com/settings/shared>), then click on **Unshare** next to the dataset you want to unshare.

![Unshare Trace List](https://mintcdn.com/langchain-5e9cc07a/1RIJxfRpkszanJLL/langsmith/images/unshare-trace-list.png?fit=max&auto=format&n=1RIJxfRpkszanJLL&q=85&s=8a7762947b85af17b36f2c71857badf7)

## 

​

Export a dataset

You can export your LangSmith dataset to a CSV, JSONL, or [OpenAI’s fine tuning format](https://docs.langchain.com/langsmith/<https:/platform.openai.com/docs/guides/fine-tuning#example-format>) from the LangSmith UI. From the **Dataset & Experiments** tab, select a dataset, click **⋮** (top right of the page), click **Download Dataset**. ![Export Dataset Button](https://mintcdn.com/langchain-5e9cc07a/0B2PFrFBMRWNccee/langsmith/images/export-dataset-button.gif?s=e71b7c55d70528df0a8985b8884f7597)

## 

​

Export filtered traces from experiment to dataset

After running an [offline evaluation](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#offline-evaluations>) in LangSmith, you may want to export [traces](https://docs.langchain.com/langsmith/</langsmith/observability-concepts#traces>) that met some evaluation criteria to a dataset.

### 

​

View experiment traces

![Export filtered traces](https://mintcdn.com/langchain-5e9cc07a/0B2PFrFBMRWNccee/langsmith/images/export-filtered-trace-to-dataset.png?fit=max&auto=format&n=0B2PFrFBMRWNccee&q=85&s=d05263ae403f7f04e8a00ab956313c01) To do so, first click on the arrow next to your experiment name. This will direct you to a project that contains the traces generated from your experiment. ![Export filtered traces](https://mintcdn.com/langchain-5e9cc07a/0B2PFrFBMRWNccee/langsmith/images/experiment-tracing-project.png?fit=max&auto=format&n=0B2PFrFBMRWNccee&q=85&s=6ef0c958b5af1f2fe113b8717e698584) From there, you can filter the traces based on your evaluation criteria. In this example, we’re filtering for all traces that received an accuracy score greater than 0.5. ![Export filtered traces](https://mintcdn.com/langchain-5e9cc07a/0B2PFrFBMRWNccee/langsmith/images/filtered-traces-from-experiment.png?fit=max&auto=format&n=0B2PFrFBMRWNccee&q=85&s=0a0edc120c230511d10285f80248dab0) After applying the filter on the project, we can multi-select runs to add to the dataset, and click **Add to Dataset**. ![Export filtered traces](https://mintcdn.com/langchain-5e9cc07a/Xbr8HuVd9jPi6qTU/langsmith/images/add-filtered-traces-to-dataset.png?fit=max&auto=format&n=Xbr8HuVd9jPi6qTU&q=85&s=d2488fe04acef624c3528ad01c5bedaa)

* * *

[Connect these docs](https://docs.langchain.com/langsmith/</use-these-docs>) to Claude, VSCode, and more via MCP for real-time answers.

[Edit this page on GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/edit/main/src/langsmith/manage-datasets.mdx>) or [file an issue](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/issues/new/choose>).

Was this page helpful?

YesNo

[How to create and manage datasets programmaticallyPrevious](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-programmatically>)[Custom output renderingNext](https://docs.langchain.com/langsmith/</langsmith/custom-output-rendering>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)

Resources

[Forum](https://docs.langchain.com/langsmith/<https:/forum.langchain.com/>)[Changelog](https://docs.langchain.com/langsmith/<https:/changelog.langchain.com/>)[LangChain Academy](https://docs.langchain.com/langsmith/<https:/academy.langchain.com/>)[Contact Sales](https://docs.langchain.com/langsmith/<https:/www.langchain.com/contact-sales>)

Company

[Home](https://docs.langchain.com/langsmith/<https:/langchain.com/>)[Trust Center](https://docs.langchain.com/langsmith/<https:/trust.langchain.com/>)[Careers](https://docs.langchain.com/langsmith/<https:/langchain.com/careers>)[Blog](https://docs.langchain.com/langsmith/<https:/blog.langchain.com/>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)
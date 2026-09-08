# Evaluation quickstart - Docs by LangChain

**Source**: https://docs.langchain.com/langsmith/evaluation-quickstart

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

Evaluation quickstart

[Get started](https://docs.langchain.com/langsmith/</langsmith/evaluation>)[Datasets & Experiments](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application>)[Evaluators](https://docs.langchain.com/langsmith/</langsmith/evaluation-types>)[Annotation Queues](https://docs.langchain.com/langsmith/</langsmith/annotation-queues>)[Test from Playground](https://docs.langchain.com/langsmith/</langsmith/test-from-playground>)[Test from Studio](https://docs.langchain.com/langsmith/</langsmith/studio>)

  * [Overview](https://docs.langchain.com/langsmith/</langsmith/evaluation>)

  * [Quickstart](https://docs.langchain.com/langsmith/</langsmith/evaluation-quickstart>)

  * [Concepts](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts>)

  * [Evaluation approaches](https://docs.langchain.com/langsmith/</langsmith/evaluation-approaches>)

## On this page

  * Prerequisites

# Evaluation quickstart

Copy pageCopy page

Copy pageCopy page

[ _Evaluations_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts>) are a quantitative way to measure the performance of LLM applications. LLMs can behave unpredictably, even small changes to prompts, models, or inputs can significantly affect results. Evaluations provide a structured way to identify failures, compare versions, and build more reliable AI applications. Running an evaluation in LangSmith requires three key components:

  * [_Dataset_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#datasets>): A set of test inputs (and optionally, expected outputs).
  * [_Target function_](https://docs.langchain.com/langsmith/</langsmith/define-target-function>): The part of your application you want to test—this might be a single LLM call with a new prompt, one module, or your entire workflow.
  * [_Evaluators_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#evaluators>): Functions that score your target function’s outputs.

This quickstart guides you through running a starter evaluation that checks the correctness of LLM responses, using either the LangSmith SDK or UI.

## 

​

Prerequisites

Before you begin, make sure you have:

  * **A LangSmith account** : Sign up or log in at [smith.langchain.com](https://docs.langchain.com/langsmith/<https:/smith.langchain.com?utm_source=docs&utm_medium=cta&utm_campaign=langsmith-signup&utm_content=langsmith-evaluation-quickstart>).
  * **A LangSmith API key** : Follow the [Create an API key](https://docs.langchain.com/langsmith/</langsmith/create-account-api-key>) guide.
  * **An OpenAI API key** : Generate this from the [OpenAI dashboard](https://docs.langchain.com/langsmith/<https:/platform.openai.com/account/api-keys>).

**Select the UI or SDK filter for instructions:**

  * UI

  * SDK

## 

​

1\. Set workspace secrets

In the [LangSmith UI](https://docs.langchain.com/langsmith/<https:/smith.langchain.com?utm_source=docs&utm_medium=cta&utm_campaign=langsmith-signup&utm_content=snippets-langsmith-set-workspace-secrets>), ensure that your API key is set as a [workspace secret](https://docs.langchain.com/langsmith/</langsmith/set-up-hierarchy#configure-workspace-settings>).

  1. Navigate to  **Settings** and then move to the **Secrets** tab.
  2. Select **Add secret** and enter the key environment variable (e.g.,`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) and your API key as the **Value**.
  3. Select **Save secret**.

When adding workspace secrets in the LangSmith UI, make sure the secret keys match the environment variable names expected by your model provider.

If your provider authenticates with OAuth2 `client_credentials`, configure the credentials on the model configuration instead. Workspace secrets are not required in that case. See [OAuth client credentials](https://docs.langchain.com/langsmith/</langsmith/model-configurations#oauth-client-credentials>).

## 

​

2\. Create a prompt

The [Playground](https://docs.langchain.com/langsmith/</langsmith/prompt-engineering-concepts#playground>) makes it possible to run evaluations over different prompts, new models, or test different model configurations.

  1. In the [LangSmith UI](https://docs.langchain.com/langsmith/<https:/smith.langchain.com?utm_source=docs&utm_medium=cta&utm_campaign=langsmith-signup&utm_content=langsmith-evaluation-quickstart>), click **Playground** in the sidebar.
  2. Under the **Prompts** panel, modify the **system** prompt to:
[code] Answer the following question accurately:
         
[/code]

Leave the **Human** message as is: `{question}`.

## 

​

3\. Create a dataset

  1. Click **Set up Evaluation** , which will open a **New Experiment** table at the bottom of the page.
  2. In the **Select or create a new dataset** dropdown, click the **\+ New** button to create a new dataset.

![Playground with the edited system prompt and new experiment with the dropdown for creating a new dataset.](https://mintcdn.com/langchain-5e9cc07a/hVPHwyb3hetqtQnG/langsmith/images/playground-system-prompt-light.png?fit=max&auto=format&n=hVPHwyb3hetqtQnG&q=85&s=b068f4407a83e31403da9a5473960fee)![Playground with the edited system prompt and new experiment with the dropdown for creating a new dataset.](https://mintcdn.com/langchain-5e9cc07a/hVPHwyb3hetqtQnG/langsmith/images/playground-system-prompt-dark.png?fit=max&auto=format&n=hVPHwyb3hetqtQnG&q=85&s=a114b1a83bf8d0a074b4ce2759207e4d)

  3. Add the following examples to the dataset:

Inputs| Reference Outputs  
---|---  
question: Which country is Mount Kilimanjaro located in?| output: Mount Kilimanjaro is located in Tanzania.  
question: What is Earth’s lowest point?| output: Earth’s lowest point is The Dead Sea.  
  
  4. Click **Save** and enter a name to save your newly created dataset.

## 

​

4\. Add an evaluator

  1. Click **\+ Evaluator** and select **Correctness** from the **Prebuilt Evaluator** options.
  2. In the **Correctness** panel, click **Save**.

## 

​

5\. Run your evaluation

  1. Select  **Start** on the top right to run your evaluation. This will create an [_experiment_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#experiment>) with a preview in the **New Experiment** table. You can view in full by clicking the experiment name.

![Full experiment view of the results that used the example dataset.](https://mintcdn.com/langchain-5e9cc07a/3SZlGm2zGXjJWzA5/langsmith/images/full-experiment-view-light.png?fit=max&auto=format&n=3SZlGm2zGXjJWzA5&q=85&s=efa004b4032d0e439a58d08567b75478)![Full experiment view of the results that used the example dataset.](https://mintcdn.com/langchain-5e9cc07a/3SZlGm2zGXjJWzA5/langsmith/images/full-experiment-view-dark.png?fit=max&auto=format&n=3SZlGm2zGXjJWzA5&q=85&s=34c2921eeadd1b7782ac64b579bcef6a)

## 

​

Next steps

To learn more about running experiments in LangSmith, read the [evaluation conceptual guide](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts>).

  * For more details on evaluations, refer to the [Evaluation documentation](https://docs.langchain.com/langsmith/</langsmith/evaluation>).
  * Learn how to [create and manage datasets in the UI](https://docs.langchain.com/langsmith/</langsmith/manage-datasets-in-application#create-a-dataset-and-add-examples>).
  * Learn how to [run an evaluation from the Playground](https://docs.langchain.com/langsmith/</langsmith/run-evaluation-from-playground>).

This guide uses prebuilt LLM-as-judge evaluators from the open-source [`openevals`](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/openevals>) package. OpenEvals includes a set of commonly used evaluators and is a great starting point if you’re new to evaluations. If you want greater flexibility in how you evaluate your apps, you can also [define completely custom evaluators](https://docs.langchain.com/langsmith/</langsmith/code-evaluator-ui>).

## 

​

1\. Install dependencies

In your terminal, create a directory for your project and install the dependencies in your environment:

Python

TypeScript
[code]
    mkdir ls-evaluation-quickstart && cd ls-evaluation-quickstart
    python -m venv .venv && source .venv/bin/activate
    python -m pip install --upgrade pip
    pip install -U langsmith openevals openai
    
[/code]
[code]
    mkdir ls-evaluation-quickstart-ts && cd ls-evaluation-quickstart-ts
    npm init -y
    npm install langsmith openevals openai
    npx tsc --init
    
[/code]

If you are using `yarn` as your package manager, you will also need to manually install `@langchain/core` as a peer dependency of `openevals`. This is not required for LangSmith evals in general, you may define evaluators [using arbitrary custom code](https://docs.langchain.com/langsmith/</langsmith/code-evaluator-ui>).

## 

​

2\. Set up environment variables

Set the following environment variables:

  * `LANGSMITH_TRACING`
  * `LANGSMITH_API_KEY`
  * `OPENAI_API_KEY` (or your LLM provider’s API key)
  * (optional) `LANGSMITH_WORKSPACE_ID`: If your LangSmith API key is linked to multiple [workspaces](https://docs.langchain.com/langsmith/</langsmith/administration-overview#workspaces>), set this variable to specify which workspace to use.

[code]
    export LANGSMITH_TRACING=true
    export LANGSMITH_API_KEY="<your-langsmith-api-key>"
    export OPENAI_API_KEY="<your-openai-api-key>"
    export LANGSMITH_WORKSPACE_ID="<your-workspace-id>"
    
[/code]

If you’re using Anthropic, use the [Anthropic wrapper](https://docs.langchain.com/langsmith/</langsmith/trace-anthropic>) to trace your calls. For other providers, use [the traceable wrapper](https://docs.langchain.com/langsmith/</langsmith/annotate-code#use-%40traceable-%2F-traceable>).

## 

​

3\. Create a dataset

  1. Create a file and add the following code, which will:
     * Import the `Client` to connect to LangSmith.
     * Create a dataset.
     * Define example [_inputs_ and _outputs_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#examples>).
     * Associate the input and output pairs with that dataset in LangSmith so they can be used in evaluations.

Python

TypeScript
[code]# dataset.py
    from langsmith import Client
    
    def main():
        client = Client()
    
        # Programmatically create a dataset in LangSmith
        dataset = client.create_dataset(
            dataset_name="Sample dataset",
            description="A sample dataset in LangSmith."
        )
    
        # Create examples
        examples = [
            {
                "inputs": {"question": "Which country is Mount Kilimanjaro located in?"},
                "outputs": {"answer": "Mount Kilimanjaro is located in Tanzania."},
            },
            {
                "inputs": {"question": "What is Earth's lowest point?"},
                "outputs": {"answer": "Earth's lowest point is The Dead Sea."},
            },
        ]
    
        # Add examples to the dataset
        client.create_examples(dataset_id=dataset.id, examples=examples)
        print("Created dataset:", dataset.name)
    
    if __name__ == "__main__":
        main()
    
    
[/code]
[code]// dataset.ts
    import { Client } from "langsmith";
    
    async function main() {
    const client = new Client();
    
    const dataset = await client.createDataset(
        "Sample dataset",
        { description: "A sample dataset in LangSmith." }
    );
    
    // Define examples
    const inputs = [
        { question: "Which country is Mount Kilimanjaro located in?" },
        { question: "What is Earth's lowest point?" },
    ];
    const outputs = [
        { answer: "Mount Kilimanjaro is located in Tanzania." },
        { answer: "Earth's lowest point is The Dead Sea." },
    ];
    
    await client.createExamples({
        datasetId: dataset.id,
        inputs,
        outputs,
    });
    
    console.log("Created dataset:", dataset.name);
    }
    
    if (require.main === module) {
    main().catch((e) => {
        console.error(e);
        process.exit(1);
    });
    }
    
[/code]

  2. In your terminal, run the `dataset` file to create the datasets you’ll use to evaluate your app:

Python

TypeScript
[code]python dataset.py
         
[/code]
[code]npx ts-node dataset.ts
         
[/code]

You’ll see the following output:
[code] Created dataset: Sample dataset
         
[/code]

## 

​

4\. Create your target function

Define a [target function](https://docs.langchain.com/langsmith/</langsmith/define-target-function>) that contains what you’re evaluating. In this guide, you’ll define a target function that contains a single LLM call to answer a question.Add the following to an `eval` file:

Python

TypeScript
[code]
    # eval.py
    from langsmith import Client, wrappers
    from openai import OpenAI
    
    # Wrap the OpenAI client for LangSmith tracing
    openai_client = wrappers.wrap_openai(OpenAI())
    
    # Define the application logic you want to evaluate inside a target function
    # The SDK will automatically send the inputs from the dataset to your target function
    def target(inputs: dict) -> dict:
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "Answer the following question accurately"},
                {"role": "user", "content": inputs["question"]},
            ],
        )
        return {"answer": response.choices[0].message.content.strip()}
    
[/code]
[code]
    // eval.ts
    import { evaluate } from "langsmith/evaluation";
    import { wrapOpenAI } from "langsmith/wrappers/openai";
    import OpenAI from "openai";
    
    const openaiClient = wrapOpenAI(new OpenAI());
    
    async function target(inputs: Record<string, any>): Promise<Record<string, any>> {
      const question = String(inputs.question ?? "");
      const resp = await openaiClient.chat.completions.create({
        model: "gpt-5-mini",
        messages: [
          { role: "system", content: "Answer the following question accurately" },
          { role: "user", content: question },
        ],
      });
      return { answer: resp.choices[0].message.content?.trim() ?? "" };
    }
    
[/code]

## 

​

5\. Define an evaluator

In this step, you’re telling LangSmith how to grade the answers your app produces.Import a prebuilt evaluation prompt (`CORRECTNESS_PROMPT`) from [`openevals`](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/openevals>) and a helper that wraps it into an [_LLM-as-judge evaluator_](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts#llm-as-judge>), which will score the application’s output.

`CORRECTNESS_PROMPT` is just an f-string with variables for `"inputs"`, `"outputs"`, and `"reference_outputs"`. See [customizing OpenEvals prompts](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/openevals#customizing-prompts>) for more information.

The evaluator compares:

  * `inputs`: what was passed into your target function (e.g., the question text).
  * `outputs`: what your target function returned (e.g., the model’s answer).
  * `reference_outputs`: the ground truth answers you attached to each dataset example in Step 3.

Add the following highlighted code to your `eval` file:

Python

TypeScript
[code]
    from langsmith import Client, wrappers
    from openai import OpenAI
    from openevals.llm import create_llm_as_judge
    from openevals.prompts import CORRECTNESS_PROMPT
    
    # Wrap the OpenAI client for LangSmith tracing
    openai_client = wrappers.wrap_openai(OpenAI())
    
    # Define the application logic you want to evaluate inside a target function
    # The SDK will automatically send the inputs from the dataset to your target function
    def target(inputs: dict) -> dict:
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "Answer the following question accurately"},
                {"role": "user", "content": inputs["question"]},
            ],
        )
        return {"answer": response.choices[0].message.content.strip()}
    
    def correctness_evaluator(inputs: dict, outputs: dict, reference_outputs: dict):
        evaluator = create_llm_as_judge(
            prompt=CORRECTNESS_PROMPT,
            model="openai:o3-mini",
            feedback_key="correctness",
        )
        return evaluator(
            inputs=inputs,
            outputs=outputs,
            reference_outputs=reference_outputs
        )
    
[/code]
[code]
    import { evaluate } from "langsmith/evaluation";
    import { wrapOpenAI } from "langsmith/wrappers/openai";
    import OpenAI from "openai";
    import { createLLMAsJudge, CORRECTNESS_PROMPT } from "openevals";
    
    const openaiClient = wrapOpenAI(new OpenAI());
    
    async function target(inputs: Record<string, any>): Promise<Record<string, any>> {
      const question = String(inputs.question ?? "");
      const resp = await openaiClient.chat.completions.create({
        model: "gpt-5-mini",
        messages: [
          { role: "system", content: "Answer the following question accurately" },
          { role: "user", content: question },
        ],
      });
      return { answer: resp.choices[0].message.content?.trim() ?? "" };
    }
    
    const judge = createLLMAsJudge({
      prompt: CORRECTNESS_PROMPT,
      model: "openai:o3-mini",
      feedbackKey: "correctness",
    });
    
    async function correctnessEvaluator(run: {
      inputs: Record<string, any>;
      outputs: Record<string, any>;
      referenceOutputs?: Record<string, any>;
    }) {
      return judge({
        inputs: run.inputs,
        outputs: run.outputs,
        // OpenEvals expects snake_case here:
        reference_outputs: run.referenceOutputs,
      });
    }
    
[/code]

## 

​

6\. Run and view results

To run the evaluation experiment, you’ll call `evaluate(...)`, which:

  * Pulls example from the dataset you created in Step 3.
  * Sends each example’s inputs to your target function from Step 4.
  * Collects the outputs (the model’s answers).
  * Passes the outputs along with the `reference_outputs` to your evaluator from Step 5.
  * Records all results in LangSmith as an experiment, so you can view them in the UI.

  1. Add the highlighted code to your `eval` file:

Python

TypeScript
[code]from langsmith import Client, wrappers
         from openai import OpenAI
         from openevals.llm import create_llm_as_judge
         from openevals.prompts import CORRECTNESS_PROMPT
         
         # Wrap the OpenAI client for LangSmith tracing
         openai_client = wrappers.wrap_openai(OpenAI())
         
         # Define the application logic you want to evaluate inside a target function
         # The SDK will automatically send the inputs from the dataset to your target function
         def target(inputs: dict) -> dict:
             response = openai_client.chat.completions.create(
                 model="gpt-5-mini",
                 messages=[
                     {"role": "system", "content": "Answer the following question accurately"},
                     {"role": "user", "content": inputs["question"]},
                 ],
             )
             return {"answer": response.choices[0].message.content.strip()}
         
         def correctness_evaluator(inputs: dict, outputs: dict, reference_outputs: dict):
             evaluator = create_llm_as_judge(
                 prompt=CORRECTNESS_PROMPT,
                 model="openai:o3-mini",
                 feedback_key="correctness",
             )
             return evaluator(
                 inputs=inputs,
                 outputs=outputs,
                 reference_outputs=reference_outputs
             )
         
         # After running the evaluation, a link will be provided to view the results in langsmith
         def main():
             client = Client()
             experiment_results = client.evaluate(
                 target,
                 data="Sample dataset",
                 evaluators=[
                     correctness_evaluator,
                     # can add multiple evaluators here
                 ],
                 experiment_prefix="first-eval-in-langsmith",
                 max_concurrency=2,
             )
             print(experiment_results)
         
         if __name__ == "__main__":
             main()
         
[/code]
[code]import { evaluate } from "langsmith/evaluation";
         import { wrapOpenAI } from "langsmith/wrappers/openai";   // helper to wrap OpenAI client
         import OpenAI from "openai";                              // model provider
         import { createLLMAsJudge, CORRECTNESS_PROMPT } from "openevals"; // evaluator tools
         
         const openaiClient = wrapOpenAI(new OpenAI());
         
         async function target(inputs: Record<string, any>): Promise<Record<string, any>> {
         const question = String(inputs.question ?? "");
         const resp = await openaiClient.chat.completions.create({
             model: "gpt-5-mini",
             messages: [
             { role: "system", content: "Answer the following question accurately" },
             { role: "user", content: question },
             ],
         });
         return { answer: resp.choices[0].message.content?.trim() ?? "" };
         }
         
         const judge = createLLMAsJudge({
         prompt: CORRECTNESS_PROMPT,
         model: "openai:o3-mini",
         feedbackKey: "correctness",
         });
         
         async function correctnessEvaluator(run: {
         inputs: Record<string, any>;
         outputs: Record<string, any>;
         referenceOutputs?: Record<string, any>;
         }) {
         return judge({
             inputs: run.inputs,
             outputs: run.outputs,
             // OpenEvals expects snake_case here:
             reference_outputs: run.referenceOutputs,
         });
         }
         
         async function main() {
         const datasetName = process.env.DATASET_NAME ?? "Sample dataset";
         
         const results = await evaluate(target, {
             data: datasetName,
             evaluators: [correctnessEvaluator],
             experimentPrefix: "first-eval-in-langsmith",
             maxConcurrency: 2,
         });
         
         console.log(results);
         }
         
         if (require.main === module) {
         main().catch((e) => {
             console.error(e);
             process.exit(1);
         });
         }
         
[/code]

  2. Run your evaluator:

Python

TypeScript
[code]python eval.py
         
[/code]
[code]npx ts-node eval.ts
         
[/code]

  3. You’ll receive a link to view the evaluation results and metadata for the experiment results:
[code] View the evaluation results for experiment: 'first-eval-in-langsmith-00000000' at: https://smith.langchain.com/o/6551f9c4-2685-4a08-86b9-1b29643deb3d/datasets/e5fde557-c274-4e49-b39d-000000000000/compare?selectedSessions=70b11778-6a28-4cdb-be81-000000000000
         
         <ExperimentResults first-eval-in-langsmith-00000000>
         
[/code]

  4. Follow the link in the output of your evaluation run to access the **Datasets & Experiments** page in the [LangSmith UI](https://docs.langchain.com/langsmith/<https:/smith.langchain.com?utm_source=docs&utm_medium=cta&utm_campaign=langsmith-signup&utm_content=langsmith-evaluation-quickstart>), and explore the results of the experiment. This will direct you to the created experiment with a table showing the **Inputs** , **Reference Output** , and **Outputs**. You can select a dataset to open an expanded view of the results.

![Experiment results in the UI after following the link.](https://mintcdn.com/langchain-5e9cc07a/DDMvkseOvrCjx9sx/langsmith/images/experiment-results-link-light.png?fit=max&auto=format&n=DDMvkseOvrCjx9sx&q=85&s=94341c15219e46866589140d87efb8f6)![Experiment results in the UI after following the link.](https://mintcdn.com/langchain-5e9cc07a/DDMvkseOvrCjx9sx/langsmith/images/experiment-results-link-dark.png?fit=max&auto=format&n=DDMvkseOvrCjx9sx&q=85&s=d741b33219f7d130e80e1dfb7e743ac6)

## 

​

Next steps

Here are some topics you might want to explore next:

  * [Evaluation concepts](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts>) provides descriptions of the key terminology for evaluations in LangSmith.
  * [OpenEvals README](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/openevals>) to see all available prebuilt evaluators and how to customize them.
  * [Define custom evaluators](https://docs.langchain.com/langsmith/</langsmith/code-evaluator-ui>).
  * [Python](https://docs.langchain.com/langsmith/<https:/docs.smith.langchain.com/reference/python/reference>) or [TypeScript](https://docs.langchain.com/langsmith/<https:/docs.smith.langchain.com/reference/js>) SDK references for comprehensive descriptions of every class and function.

* * *

[Connect these docs](https://docs.langchain.com/langsmith/</use-these-docs>) to Claude, VSCode, and more via MCP for real-time answers.

[Edit this page on GitHub](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/edit/main/src/langsmith/evaluation-quickstart.mdx>) or [file an issue](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai/docs/issues/new/choose>).

Was this page helpful?

YesNo

[LangSmith EvaluationPrevious](https://docs.langchain.com/langsmith/</langsmith/evaluation>)[Evaluation conceptsNext](https://docs.langchain.com/langsmith/</langsmith/evaluation-concepts>)

[Docs by LangChain home page![light logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-dark-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=5babf1a1962208fd7eed942fa2432ecb)![dark logo](https://mintcdn.com/langchain-5e9cc07a/nQm-sjd_MByLhgeW/images/brand/langchain-docs-light-blue.png?fit=max&auto=format&n=nQm-sjd_MByLhgeW&q=85&s=0bcd2a1f2599ed228bcedf0f535b45b1)](https://docs.langchain.com/langsmith/</>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)

Resources

[Forum](https://docs.langchain.com/langsmith/<https:/forum.langchain.com/>)[Changelog](https://docs.langchain.com/langsmith/<https:/changelog.langchain.com/>)[LangChain Academy](https://docs.langchain.com/langsmith/<https:/academy.langchain.com/>)[Contact Sales](https://docs.langchain.com/langsmith/<https:/www.langchain.com/contact-sales>)

Company

[Home](https://docs.langchain.com/langsmith/<https:/langchain.com/>)[Trust Center](https://docs.langchain.com/langsmith/<https:/trust.langchain.com/>)[Careers](https://docs.langchain.com/langsmith/<https:/langchain.com/careers>)[Blog](https://docs.langchain.com/langsmith/<https:/blog.langchain.com/>)

[github](https://docs.langchain.com/langsmith/<https:/github.com/langchain-ai>)[x](https://docs.langchain.com/langsmith/<https:/x.com/LangChain>)[linkedin](https://docs.langchain.com/langsmith/<https:/www.linkedin.com/company/langchain>)[youtube](https://docs.langchain.com/langsmith/<https:/www.youtube.com/@LangChain>)
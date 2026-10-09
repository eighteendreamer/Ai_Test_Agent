import http from "k6/http";
import { check, sleep } from "k6";

const __expectedStatus = [200];
const __responseCallback = http.expectedStatuses(...__expectedStatus);
const __smoke = __ENV.QA_PERF_SMOKE === "true";
const __smokeVus = Number(__ENV.QA_PERF_SMOKE_VUS || 1);
const __smokeIterations = Number(__ENV.QA_PERF_SMOKE_ITERATIONS || 3);

const __loadOptions = {
  scenarios: {
    load: {
      executor: "ramping-arrival-rate",
      timeUnit: "1s",
      preAllocatedVUs: 1,
      maxVUs: 2000,
      stages: [
        { target: 25, duration: "0s" },
        { target: 50, duration: "0s" },
        { target: 50, duration: "0s" },
      ],
    },
  },
  thresholds: {
    "http_req_duration": [{ threshold: "p(99)<10000", abortOnFail: true, delayAbortEval: "10s" }],
    "http_req_failed": [{ threshold: "rate<0.01", abortOnFail: true, delayAbortEval: "10s" }],
  },
};

const __summaryTrendStats = ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"];
export const options = __smoke
  ? { vus: __smokeVus, iterations: __smokeIterations, summaryTrendStats: __summaryTrendStats }
  : { ...__loadOptions, summaryTrendStats: __summaryTrendStats };

export default function () {
  const __headers0 = {"Content-Type": "application/json"};
  if (true) {
    const res0 = http.post("http://host.docker.internal:64333/json", JSON.stringify({"enabled": false, "count": 0, "nullable": null, "nested": ["\u4e2d\u6587", {"literal": "${__groovy(1+1)}", "extra": "keep"}]}), {
      headers: __headers0,
      responseCallback: __responseCallback,
    });

    check(res0, {
      ["qa_status_0_" + res0.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  const __headers1 = {"Content-Type": "text/plain; charset=utf-8"};
  if (true) {
    const res1 = http.post("http://host.docker.internal:64333/text", `中文 ${1+1}\path
line`, {
      headers: __headers1,
      responseCallback: __responseCallback,
    });

    check(res1, {
      ["qa_status_1_" + res1.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  sleep(0.025);
}
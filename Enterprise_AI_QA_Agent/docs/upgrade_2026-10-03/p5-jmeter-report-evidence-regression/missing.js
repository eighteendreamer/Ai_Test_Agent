import http from "k6/http";
import { check, sleep } from "k6";

const __expectedStatus = [200, 201];
const __responseCallback = http.expectedStatuses(...__expectedStatus);
const __smoke = __ENV.QA_PERF_SMOKE === "true";
const __smokeVus = Number(__ENV.QA_PERF_SMOKE_VUS || 1);
const __smokeIterations = Number(__ENV.QA_PERF_SMOKE_ITERATIONS || 3);

const __loadOptions = {
  vus: 1,
  duration: "2s",
  thresholds: {
    "http_req_duration": [{ threshold: "p(99)<1000", abortOnFail: true, delayAbortEval: "10s" }],
  },
};

const __summaryTrendStats = ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"];
export const options = __smoke
  ? { vus: __smokeVus, iterations: __smokeIterations, summaryTrendStats: __summaryTrendStats }
  : { ...__loadOptions, summaryTrendStats: __summaryTrendStats };

export default function () {
  const __headers0 = {};
  if (true) {
    const res = http.get("http://172.17.0.6:5678/", {
      headers: __headers0,
      responseCallback: __responseCallback,
    });

    check(res, {
      ["qa_status_0_" + res.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  sleep(0.05);
}
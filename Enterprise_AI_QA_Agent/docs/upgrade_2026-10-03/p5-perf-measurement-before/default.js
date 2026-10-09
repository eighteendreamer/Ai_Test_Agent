import http from "k6/http";
import { check, sleep } from "k6";

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

export const options = __smoke
  ? { vus: __smokeVus, iterations: __smokeIterations }
  : __loadOptions;

export default function () {
  const res = http.get("http://172.17.0.5:5678/", {
  });

  check(res, {
    "status is expected": (r) => r.status >= 200 && r.status < 400,
  });

  sleep(0.05);
}
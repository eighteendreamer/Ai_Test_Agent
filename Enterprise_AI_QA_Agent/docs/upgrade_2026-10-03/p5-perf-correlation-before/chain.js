import http from "k6/http";
import { check, sleep } from "k6";

const __smoke = __ENV.QA_PERF_SMOKE === "true";
const __smokeVus = Number(__ENV.QA_PERF_SMOKE_VUS || 1);
const __smokeIterations = Number(__ENV.QA_PERF_SMOKE_ITERATIONS || 3);

const __loadOptions = {
  vus: 1,
  duration: "1s",
};

const __summaryTrendStats = ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"];
export const options = __smoke
  ? { vus: __smokeVus, iterations: __smokeIterations, summaryTrendStats: __summaryTrendStats }
  : { ...__loadOptions, summaryTrendStats: __summaryTrendStats };

export default function () {
  const res0 = http.get("http://host.docker.internal:51564/login", {
  });

  check(res0, {
    "status is expected": (r) => r.status >= 200 && r.status < 400,
  });
  const ticket = res0.json("session.credential");

  const res1 = http.get("http://host.docker.internal:51564/profile", {
  });

  check(res1, {
    "status is expected": (r) => r.status >= 200 && r.status < 400,
  });
  const ticket = res1.json("session.credential");

}